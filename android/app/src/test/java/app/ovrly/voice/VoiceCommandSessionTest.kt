package app.ovrly.voice

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceCommandSessionTest {
    @Test
    fun theAllowlistGoesToTheBackendAndEverythingElseIsRefusedWithoutSwitchingTabs() {
        val fixture = Fixture()
        fixture.ready()
        for ((index, action) in VoiceCommandAction.entries.withIndex()) {
            fixture.tool("call-$index", action.wireName, """{"id":"target-1"}""")
        }
        fixture.tool("unknown", "delete_report", """{"id":"target-1"}""")
        fixture.tool("bad", "save_report", """{"id":"target-1","confirmed":true}""")
        assertEquals(
            VoiceCommandAction.entries.map { VoiceCommand(it, "target-1") },
            fixture.commands
        )
        assertEquals(
            listOf("VOICE_ACTION_UNSUPPORTED", "VOICE_ACTION_INVALID_ARGUMENTS"),
            fixture.transport.sent.map {
                JSONObject(it).getJSONObject("result").getString("code")
            }
        )
        assertTrue(
            fixture.transport.sent.all {
                JSONObject(it).getJSONObject("result").getString("status") == "error"
            }
        )
        assertEquals(0, fixture.opened)
        assertTrue(fixture.session.state.value.active)
        fixture.session.close()
    }

    @Test
    fun exactDuplicateReplaysButChangedTargetStopsSession() {
        val fixture = Fixture()
        fixture.session.start()
        fixture.transport.listener.message("""{"type":"ready"}""")
        fixture.tool("same", "save_report", """{"id":"first"}""")
        fixture.answer(VoiceCommandOutcome(true, "Saved."))
        fixture.tool("same", "save_report", """{"id":"first"}""")
        assertEquals(1, fixture.commands.size)
        assertEquals(2, fixture.transport.sent.size)
        assertEquals(fixture.transport.sent[0], fixture.transport.sent[1])
        fixture.tool("same", "save_report", """{"id":"second"}""")
        assertEquals("Protocol error", fixture.session.state.value.status)
        assertFalse(fixture.session.state.value.active)
        assertEquals(2, fixture.transport.sent.size)
        assertEquals(0, fixture.opened)
        assertTrue(fixture.transport.closed)
    }

    @Test
    fun backendOutcomesArriveLaterWithTheAppStateAndAreShown() {
        val fixture = Fixture()
        fixture.ready()
        fixture.tool("call-1", "open_check", """{"id":"latest"}""")
        assertEquals("nothing is sent before the backend answers", 0, fixture.transport.sent.size)
        val expected = VoiceCommand(VoiceCommandAction.OPEN_CHECK, "latest")
        assertEquals(expected, fixture.commands.single())
        fixture.answer(VoiceCommandOutcome(true, "Opened the check."))
        val reply = JSONObject(fixture.transport.sent.single())
        assertEquals("success", reply.getJSONObject("result").getString("status"))
        assertEquals("Opened the check.", reply.getJSONObject("result").getString("result"))
        assertEquals(Fixture.STATE.toString(), reply.getJSONObject("state").toString())
        val shown = fixture.session.activity.value.result
        assertEquals(VoiceResult("Open check", "Opened the check.", true), shown)
        fixture.session.close()
    }

    @Test
    fun deniedAndRefusedCommandsAreVisibleErrors() {
        val fixture = Fixture()
        fixture.ready()
        fixture.tool("call-1", "queue_continue", """{"id":"job-1"}""")
        val code = "VOICE_ACTION_INVALID_STATE"
        fixture.answer(VoiceCommandOutcome(false, "That check already finished.", code))
        val denied = JSONObject(fixture.transport.sent.single()).getJSONObject("result")
        assertEquals("error", denied.getString("status"))
        assertEquals("VOICE_ACTION_INVALID_STATE", denied.getString("code"))
        assertFalse(fixture.session.activity.value.result!!.success)
        fixture.tool("call-2", "delete_report", """{"id":"report-1"}""")
        val refused = fixture.session.activity.value.result!!
        assertEquals("VOICE_ACTION_UNSUPPORTED", refused.code)
        assertFalse(refused.success)
        assertEquals("unsupported commands never reach the backend", 1, fixture.commands.size)
        fixture.session.close()
    }

    @Test
    fun aRepeatedCallWhilePendingRunsOnceAndRepliesTwice() {
        val fixture = Fixture()
        fixture.ready()
        fixture.tool("call-1", "save_report", """{"id":"report-1"}""")
        fixture.tool("call-1", "save_report", """{"id":"report-1"}""")
        assertEquals(1, fixture.commands.size)
        fixture.answer(VoiceCommandOutcome(true, "Saved."))
        assertEquals(2, fixture.transport.sent.size)
        assertEquals(fixture.transport.sent[0], fixture.transport.sent[1])
        fixture.session.close()
    }

    @Test
    fun anAnswerAfterVoiceStoppedIsDroppedAndPendingApprovalsAreDiscarded() {
        val fixture = Fixture()
        fixture.ready()
        fixture.tool("call-1", "queue_cancel", """{"id":"job-1"}""")
        fixture.session.stop("Voice stopped because the companion left the foreground.")
        assertEquals(1, fixture.ended)
        assertFalse(fixture.session.state.value.active)
        fixture.answer(VoiceCommandOutcome(true, "Cancelled."))
        assertTrue(fixture.transport.sent.isEmpty())
        assertTrue(fixture.transport.closed)
        assertEquals("no reconnect", 1, fixture.transport.starts)
    }

    @Test
    fun theUsersSpeechIsShownAndTheAssistantsIsNot() {
        val fixture = Fixture()
        fixture.ready()
        fixture.transport.listener.message("""{"type":"text","text":"Sure, opening it."}""")
        assertNull(fixture.session.activity.value.heard)
        fixture.transport.listener.message("""{"type":"text_user","text":"Open my "}""")
        fixture.transport.listener.message("""{"type":"text_user","text":"latest check"}""")
        assertEquals("Open my latest check", fixture.session.activity.value.heard)
        fixture.transport.listener.message("""{"type":"turn_complete"}""")
        fixture.transport.listener.message("""{"type":"text_user","text":"Save it"}""")
        assertEquals("Save it", fixture.session.activity.value.heard)
        fixture.session.stop()
        fixture.session.start()
        assertNull("a new session starts with an empty panel", fixture.session.activity.value.heard)
        fixture.session.close()
    }

    private class Fixture {
        companion object {
            val STATE: JSONObject = JSONObject().put("checks", JSONArray())
        }

        val commands = mutableListOf<VoiceCommand>()
        private val pending = mutableListOf<(VoiceCommandOutcome) -> Unit>()
        var ended = 0
        val transport = FakeTransport()
        var opened = 0
        val session = VoiceSession(
            VoiceConfiguration(false, "", "", mock = true),
            { transport },
            { MockVoiceAudio() },
            { true },
            { it() },
            { _, _ -> VoiceCancellation {} },
            {
                opened++
                true
            },
            VoiceDiagnostics {}
        ).also { session ->
            session.commands = VoiceCommands(
                { command, done ->
                    commands += command
                    pending += done
                },
                { STATE },
                { ended++ }
            )
        }

        fun ready() {
            session.start()
            transport.listener.message("""{"type":"ready"}""")
        }

        fun answer(outcome: VoiceCommandOutcome) = pending.removeAt(0)(outcome)

        fun tool(id: String, name: String, args: String) {
            transport.listener.message(
                """{"type":"tool_call","id":"$id","name":"$name","args":$args}"""
            )
        }
    }

    private class FakeTransport : VoiceTransport {
        lateinit var listener: VoiceTransport.Listener
        val sent = mutableListOf<String>()
        var closed = false

        var starts = 0

        override fun start(listener: VoiceTransport.Listener) {
            starts++
            this.listener = listener
        }

        override fun send(text: String): Boolean {
            if (closed) return false
            sent.add(text)
            return true
        }

        override fun close() {
            closed = true
        }
    }
}
