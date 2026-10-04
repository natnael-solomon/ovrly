package app.ovrly.contract

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Proves the parser rejects incompatible payloads and that the Kotlin enums still match the
 * committed schemas. A schema or parser drift that breaks compatibility fails here.
 */
class VoiceActionCompatibilityTest {
    private val incompatible = ContractFixtures.load(ContractFixtures.INCOMPATIBLE)

    @Test
    fun everyIncompatibleFixtureDeclaresOneOutcome() {
        assertEquals(INCOMPATIBLE_FIXTURES, incompatible.map { it.name }.toSet())
        for (fixture in incompatible) {
            assertTrue(fixture.name, fixture.synthetic)
            val outcomes = listOfNotNull(fixture.expectRequest, fixture.expectResponse)
            assertEquals(fixture.name, 1, outcomes.size)
            assertTrue(fixture.name, outcomes.single() in setOf("invalid", "unknown-enum"))
            assertEquals(fixture.name, outcomes.single() == "invalid", fixture.reason != null)
        }
    }

    @Test
    fun incompatibleRequestsAreRejected() {
        val requests = incompatible.filter { it.expectRequest == "invalid" }
        assertEquals(REQUEST_REJECTIONS, requests.size)
        for (fixture in requests) {
            val failure = assertThrows(fixture.name, ContractParseException::class.java) {
                VoiceActionCodec.parseRequest(fixture.requestPayload())
            }
            assertMentions(fixture.name, failure, fixture.reason)
        }
    }

    @Test
    fun incompatibleResponsesAreRejected() {
        val responses = incompatible.filter { it.expectResponse == "invalid" }
        assertEquals(RESPONSE_REJECTIONS, responses.size)
        for (fixture in responses) {
            val failure = assertThrows(fixture.name, ContractParseException::class.java) {
                VoiceActionCodec.parseResponse(fixture.responsePayload())
            }
            assertMentions(fixture.name, failure, fixture.reason)
        }
    }

    @Test
    fun renamedOrUnknownEnumValuesNeverBecomeSuccess() {
        val relaxed = incompatible.filter { it.expectResponse == "unknown-enum" }
        assertEquals(UNKNOWN_ENUM_RESPONSES, relaxed.size)
        for (fixture in relaxed) {
            val response = VoiceActionCodec.parseResponse(fixture.responsePayload())
            assertFalse(fixture.name, response.isAccepted)
            val expectedCode = fixture.expectedErrorCode
            if (expectedCode != null) {
                assertEquals(fixture.name, expectedCode, response.errorCode?.wireName)
            }
        }
        val renamed = VoiceActionCodec.parseResponse(
            fixture("response-renamed-result-case").responsePayload()
        )
        assertEquals(VoiceActionResult.UNKNOWN, renamed.result)
        val newerKind = VoiceActionCodec.parseResponse(
            fixture("response-unknown-target-kind").responsePayload()
        )
        assertEquals(VoiceTargetKind.UNKNOWN, newerKind.target?.kind)
        assertEquals("cap_synthetic_9109", newerKind.target?.id)
        assertEquals(VoiceActionResult.DENIED, newerKind.result)
    }

    @Test
    fun malformedPayloadsFailParsing() {
        val valid = """{"request_id":"r1","action":"open_check",""" +
            """"target":{"kind":"investigation","id":"i1"}}"""
        VoiceActionCodec.parseRequest(valid)
        val malformed = listOf(
            "", " ", "null", "true", "7", "\"text\"", "[]", "{}", "{",
            valid.dropLast(1), "$valid trailing", "$valid{}", "$valid$valid",
            valid.replace("\"open_check\"", "open_check"),
            valid.replace("\"request_id\"", "'request_id'"),
            valid.replace("\"r1\"", "null"),
            valid.replace("\"r1\"", "[\"r1\"]")
        )
        for (payload in malformed) {
            assertThrows(payload, ContractParseException::class.java) {
                VoiceActionCodec.parseRequest(payload)
            }
            assertThrows(payload, ContractParseException::class.java) {
                VoiceActionCodec.parseResponse(payload)
            }
        }
    }

    @Test
    fun responsesTolerateAdditiveFieldsButRequestsDoNot() {
        val response = VoiceActionCodec.parseResponse(
            """{"request_id":"r1","result":"denied","action":"open_check","message":"No.",
               "server_time":"2026-10-04T08:00:00Z","error":{"code":"VOICE_TARGET_NOT_FOUND",
               "message":"Gone.","retryable":false,"action":"fix_request","request_id":"r1"}}"""
        )
        assertEquals(VoiceActionErrorCode.VOICE_TARGET_NOT_FOUND, response.errorCode)
        assertEquals(ContractErrorAction.FIX_REQUEST, response.error?.action)
        val failure = assertThrows(ContractParseException::class.java) {
            VoiceActionCodec.parseRequest(
                """{"request_id":"r1","action":"open_check",
                   "target":{"kind":"investigation","id":"i1"},"retryable":false}"""
            )
        }
        assertMentions("request", failure, "retryable")
    }

    @Test
    fun modelsEnforceTheContractWhenBuiltInCode() {
        val job = VoiceTarget(VoiceTargetKind.JOB, "j1")
        rejected { VoiceActionRequest("r1", VoiceAction.UNKNOWN, job) }
        rejected { VoiceActionRequest("r1", VoiceAction.OPEN_CHECK, job) }
        rejected { VoiceTarget(VoiceTargetKind.JOB, "j 1") }
        rejected { VoiceTarget(VoiceTargetKind.JOB, "") }
        rejected { VoiceTarget(VoiceTargetKind.JOB, "j".repeat(OpaqueId.MAX_LENGTH + 1)) }
        rejected { contractError("lower_case", "x") }
        rejected { contractError("OK", "x") }
        rejected { contractError("CODE", "") }
        rejected { contractError("CODE", "x", requestId = "") }
        rejected { contractError("CODE", "x", requestId = "r 1") }
        rejected { contractError("CODE", "x", requestId = "r".repeat(OpaqueId.MAX_LENGTH + 1)) }
        rejected { denied(contractError("C_1", "x", requestId = "r2")) }
        rejected { VoiceActionResponse("r1", VoiceActionResult.ACCEPTED, "open_check", "Done.") }
        rejected { VoiceActionResponse("r1", VoiceActionResult.DENIED, "open_check", "No.") }
        val unknown = VoiceActionResponse("r1", VoiceActionResult.UNKNOWN, "open_check", "Later.")
        assertFalse(unknown.isAccepted)
        rejected { VoiceActionCodec.encodeResponse(unknown) }
        val futureAction = denied(contractError("C_1", "x", ContractErrorAction.UNKNOWN))
        rejected { VoiceActionCodec.encodeResponse(futureAction) }
    }

    @Test
    fun kotlinEnumsMatchTheCommittedRequestSchema() {
        val schema = schema("voice-action-request.schema.json")
        val properties = schema.getValue("properties").jsonObject
        assertEquals(setOf("request_id", "action", "target"), strings(schema, "required"))
        assertEquals(known(VoiceAction.entries.map { it.wireName }), strings(properties, "action"))
        val target = schema.getValue("\$defs").jsonObject.getValue("target").jsonObject
        assertEquals(setOf("kind", "id"), strings(target, "required"))
        assertEquals(
            known(VoiceTargetKind.entries.map { it.wireName }),
            strings(properties(target), "kind")
        )
        val pairs = schema.getValue("oneOf").jsonArray.map { properties(it) }
        assertEquals(VoiceAction.entries.size - 1, pairs.size)
        for (pair in pairs) {
            val action = VoiceAction.fromWire(const(pair, "action"))
            val kind = const(properties(pair.getValue("target")), "kind")
            assertEquals(action.wireName, action.targetKind.wireName, kind)
        }
    }

    @Test
    fun kotlinEnumsMatchTheCommittedResponseAndErrorSchemas() {
        val schema = schema("voice-action-response.schema.json")
        val properties = schema.getValue("properties").jsonObject
        val required = setOf("request_id", "result", "action", "message")
        assertEquals(required, strings(schema, "required"))
        assertEquals(
            known(VoiceActionResult.entries.map { it.wireName }),
            strings(properties, "result")
        )
        val restriction = properties.getValue("error").jsonObject.getValue("allOf").jsonArray[1]
        assertEquals(
            known(VoiceActionErrorCode.entries.map { it.wireName }),
            strings(properties(restriction), "code")
        )
        val error = schema("error.schema.json")
        assertEquals(
            setOf("code", "message", "retryable", "action", "request_id"),
            strings(error, "required")
        )
        assertEquals(
            known(ContractErrorAction.entries.map { it.wireName }),
            strings(error.getValue("properties").jsonObject, "action")
        )
        assertEquals(
            VoiceActionCodec.CONTRACT_VERSION,
            ContractFixtures.resourceText("VERSION").trim()
        )
    }

    private fun fixture(name: String) = incompatible.single { it.name == name }

    private fun rejected(build: () -> Any) {
        assertThrows(IllegalArgumentException::class.java) { build() }
    }

    private fun contractError(
        code: String,
        message: String,
        action: ContractErrorAction = ContractErrorAction.NONE,
        requestId: String = "r1"
    ) = ContractError(code, message, false, action, requestId)

    private fun denied(error: ContractError) =
        VoiceActionResponse("r1", VoiceActionResult.DENIED, "open_check", "No.", null, error)

    private fun assertMentions(name: String, failure: Throwable, fragment: String?) {
        val message = failure.message.orEmpty()
        assertTrue(
            "$name: expected '$fragment' in '$message'",
            message.contains(checkNotNull(fragment))
        )
    }

    private fun schema(name: String): JsonObject =
        ContractFixtures.element(ContractFixtures.resourceText("schemas/$name")).jsonObject

    private fun properties(node: JsonElement): JsonObject =
        node.jsonObject.getValue("properties").jsonObject

    private fun strings(container: JsonObject, key: String): Set<String> {
        val node = container.getValue(key)
        val array = if (node is JsonObject) node.getValue("enum").jsonArray else node.jsonArray
        return array.map { it.jsonPrimitive.content }.toSet()
    }

    private fun const(container: JsonObject, key: String): String =
        container.getValue(key).jsonObject.getValue("const").jsonPrimitive.content

    /** Wire names of every entry except the trailing UNKNOWN fallback. */
    private fun known(wireNames: List<String>): Set<String> = wireNames.dropLast(1).toSet()
    private companion object {
        val INCOMPATIBLE_FIXTURES = setOf(
            "request-extra-required-field",
            "request-null-target",
            "request-renamed-enum-value",
            "request-unknown-target-kind",
            "request-wrong-id-type",
            "response-accepted-unknown-action",
            "response-accepted-with-error",
            "response-accepted-without-target",
            "response-denied-without-error",
            "response-error-missing-code",
            "response-error-missing-retryable",
            "response-error-request-id-mismatch",
            "response-error-retryable-string",
            "response-malformed-request-id",
            "response-missing-message",
            "response-renamed-result-case",
            "response-unknown-target-kind"
        )
        const val REQUEST_REJECTIONS = 5
        const val RESPONSE_REJECTIONS = 10
        const val UNKNOWN_ENUM_RESPONSES = 2
    }
}
