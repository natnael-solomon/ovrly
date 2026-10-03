package app.ovrly.voice

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test

class VoiceCommandContractTest {
    @Test
    fun exactlyFiveProductActionsWithCancellationOnlyConfirmation() {
        val names = setOf(
            "open_check",
            "save_report",
            "queue_cancel",
            "queue_retry",
            "queue_continue"
        )
        assertEquals(names, VoiceCommandAction.entries.map { it.wireName }.toSet())
        for (action in VoiceCommandAction.entries) {
            assertEquals(action == VoiceCommandAction.QUEUE_CANCEL, action.requiresConfirmation)
            assertEquals(
                VoiceCommandValidation.Accepted(action, "Target_01"),
                VoiceCommandContract.validate(action.wireName, """{"id":"Target_01"}""")
            )
        }
    }

    @Test
    fun opaqueIdentifiersAreBoundedAndNeverNormalized() {
        for (id in listOf("a", "A_0-b", "a".repeat(128))) {
            assertEquals(
                VoiceCommandValidation.Accepted(VoiceCommandAction.OPEN_CHECK, id),
                VoiceCommandContract.validate("open_check", JSONObject().put("id", id).toString())
            )
        }
        val invalidIds = listOf(
            "", "a".repeat(129), " a", "a ", "a\n", "a\tb", "a\u0000b", "a b",
            "\u200Ba", "caf\u00e9", "-a", "_a", "../a", "https://example.invalid", "a%20b"
        )
        for (action in VoiceCommandAction.entries) {
            for (id in invalidIds) {
                invalid(action.wireName, JSONObject().put("id", id).toString())
            }
        }
    }

    @Test
    fun rejectsMissingExtraMalformedAndCoercedArgumentsForEveryAction() {
        val arguments = listOf(
            null, "", "null", "[]", "{}", """{"id":null}""", """{"id":1}""",
            """{"id":true}""", """{"id":[]}""", """{"id":{}}""",
            """{"id":"a","extra":true}""", """{"id":"a","confirmed":true}""",
            """{"id":"a","owner":"someone"}""", """{"id":"a","id":"b"}""",
            """{"id":"a","\u0069d":"b"}""", """{"id":"a",}""",
            "{id:'a'}", """{"id":"a"} trailing"""
        )
        for (action in VoiceCommandAction.entries) {
            for (args in arguments) invalid(action.wireName, args)
        }
    }

    @Test
    fun unsupportedActionsCannotBecomeTabOrProductCommands() {
        for (name in listOf(
            "delete_report",
            "publish_report",
            "change_settings",
            "start_capture",
            "open_tab",
            "OPEN_CHECK",
            "open_check ",
            ""
        )) {
            assertEquals(
                VoiceCommandValidation.Rejected(VoiceCommandError.VOICE_ACTION_UNSUPPORTED),
                VoiceCommandContract.validate(name, """{"id":"check-demo"}""")
            )
        }
    }

    @Test
    fun plannedActionsRemainUnavailableAndAreNotAdvertised() {
        val manifest = JSONObject(VoiceProtocol.manifest()).getJSONArray("actions")
        assertEquals(1, manifest.length())
        assertEquals("open_tab", manifest.getJSONObject(0).getString("name"))
        for (action in VoiceCommandAction.entries) {
            val tool = parse(action.wireName, """{"id":"target-1"}""")
            assertEquals(null, VoiceTab.from(tool.arguments))
            val response = JSONObject(VoiceCommandContract.rejectForCurrentBuild(tool))
            assertEquals("tool_result", response.getString("type"))
            assertEquals("call-1", response.getString("id"))
            assertEquals(action.wireName, response.getString("name"))
            val result = response.getJSONObject("result")
            assertEquals("error", result.getString("status"))
            assertEquals("VOICE_ACTION_UNAVAILABLE", result.getString("code"))
            assertFalse(result.has("result"))
            assertEquals(0, response.getJSONObject("state").length())
        }
    }

    @Test
    fun malformedEnvelopesAndArgumentsCannotValidateThroughProtocol() {
        for (args in listOf("null", "\"id\"", "[]", "false")) {
            val tool = parse("save_report", args)
            assertEquals(
                "VOICE_ACTION_INVALID_ARGUMENTS",
                JSONObject(VoiceCommandContract.rejectForCurrentBuild(tool))
                    .getJSONObject("result").getString("code")
            )
        }
        val extra = VoiceProtocol.parse(
            """{"type":"tool_call","id":"call-1","name":"queue_cancel","args":{"id":"a"},
                "confirmed":true}"""
        ) as VoiceEvent.Tool
        invalid(extra.name, extra.arguments)
        assertThrows(VoiceProtocolException::class.java) {
            parse("open_check", """{"id":"a","id":"b"}""")
        }
    }

    @Test
    fun changedTargetWithSameCallIdHasDifferentIdentity() {
        val first = parse("save_report", """{"id":"first"}""")
        val second = parse("save_report", """{"id":"second"}""")
        assertFalse(first == second)
        assertEquals(first, parse("save_report", """{ "id" : "first" }"""))
    }

    private fun invalid(name: String, args: String?) {
        assertEquals(
            VoiceCommandValidation.Rejected(VoiceCommandError.VOICE_ACTION_INVALID_ARGUMENTS),
            VoiceCommandContract.validate(name, args)
        )
    }

    private fun parse(name: String, args: String): VoiceEvent.Tool = VoiceProtocol.parse(
        """{"type":"tool_call","id":"call-1","name":"$name","args":$args}"""
    ) as VoiceEvent.Tool
}
