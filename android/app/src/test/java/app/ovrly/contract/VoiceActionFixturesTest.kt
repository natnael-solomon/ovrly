package app.ovrly.contract

import app.ovrly.contract.ContractFixtures.Fixture
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Drives the production parser with the committed fixtures in `packages/contracts`. */
class VoiceActionFixturesTest {
    private val fixtures = ContractFixtures.load(ContractFixtures.SHARED)
    private val byName = fixtures.associateBy { it.name }

    private data class Accepted(
        val action: VoiceAction,
        val kind: VoiceTargetKind,
        val targetId: String,
        val requestId: String,
        val message: String
    )

    private val accepted = mapOf(
        "open-check-accepted" to Accepted(
            VoiceAction.OPEN_CHECK,
            VoiceTargetKind.INVESTIGATION,
            "inv_synthetic_0001",
            "req_synthetic_0001",
            "Opened the check."
        ),
        "save-report-accepted" to Accepted(
            VoiceAction.SAVE_REPORT,
            VoiceTargetKind.REPORT,
            "rep_synthetic_0001",
            "req_synthetic_0002",
            "Saved the report."
        ),
        "queue-cancel-accepted" to Accepted(
            VoiceAction.QUEUE_CANCEL,
            VoiceTargetKind.JOB,
            "job_synthetic_0001",
            "req_synthetic_0003",
            "Cancelled the queued check."
        ),
        "queue-retry-accepted" to Accepted(
            VoiceAction.QUEUE_RETRY,
            VoiceTargetKind.JOB,
            "job_synthetic_0002",
            "req_synthetic_0004",
            "Retrying the failed check."
        ),
        "queue-continue-accepted" to Accepted(
            VoiceAction.QUEUE_CONTINUE,
            VoiceTargetKind.JOB,
            "job_synthetic_0003",
            "req_synthetic_0005",
            "Continuing the paused check."
        )
    )

    private val denied = mapOf(
        "unsupported-action" to VoiceActionErrorCode.VOICE_ACTION_UNSUPPORTED,
        "queue-continue-invalid-state" to VoiceActionErrorCode.VOICE_ACTION_INVALID_STATE,
        "cross-owner-denied" to VoiceActionErrorCode.VOICE_TARGET_NOT_OWNED,
        "target-not-found" to VoiceActionErrorCode.VOICE_TARGET_NOT_FOUND
    )

    /** Client guidance each denial carries; no voice denial is retryable unchanged. */
    private val guidance = mapOf(
        "unsupported-action" to ContractErrorAction.FIX_REQUEST,
        "queue-continue-invalid-state" to ContractErrorAction.NONE,
        "cross-owner-denied" to ContractErrorAction.NONE,
        "target-not-found" to ContractErrorAction.FIX_REQUEST
    )

    /** Each invalid request fixture and the fragment the parse failure must mention. */
    private val invalidRequests = mapOf(
        "missing-target" to "target",
        "mismatched-target-kind" to "target.kind",
        "free-form-argument" to "confirmed",
        "unsupported-action" to "allowlist"
    )

    @Test
    fun everyCommittedFixtureIsClassifiedByTheseTests() {
        val classified =
            accepted.keys + denied.keys + invalidRequests.keys + "unknown-enum-response"
        assertEquals(classified, byName.keys)
        for (fixture in fixtures) {
            assertTrue("${fixture.name} must be synthetic", fixture.synthetic)
            assertEquals(fixture.name, fixture.request != null, fixture.expectRequest != null)
            assertEquals(fixture.name, fixture.response != null, fixture.expectResponse != null)
        }
        assertEquals(
            VoiceActionCodec.CONTRACT_VERSION,
            ContractFixtures.resourceText("VERSION").trim()
        )
    }

    @Test
    fun acceptedFixturesParseToTheExpectedTypedValues() {
        for ((name, expected) in accepted) {
            val fixture = fixture(name)
            assertEquals("valid", fixture.expectRequest)
            assertEquals("valid", fixture.expectResponse)
            val request = VoiceActionCodec.parseRequest(fixture.requestPayload())
            assertEquals(name, expected.action, request.action)
            assertEquals(name, VoiceTarget(expected.kind, expected.targetId), request.target)
            assertEquals(name, expected.requestId, request.requestId)
            val response = VoiceActionCodec.parseResponse(fixture.responsePayload())
            assertEquals(name, VoiceActionResult.ACCEPTED, response.result)
            assertTrue(name, response.isAccepted)
            assertEquals(name, expected.action, response.knownAction)
            assertEquals(name, expected.action.wireName, response.action)
            assertEquals(name, request.target, response.target)
            assertEquals(name, request.requestId, response.requestId)
            assertEquals(name, expected.message, response.message)
            assertNull(name, response.error)
            assertNull(name, response.errorCode)
        }
    }

    @Test
    fun deniedFixturesCarryTheExpectedErrorCode() {
        for ((name, code) in denied) {
            val fixture = fixture(name)
            assertEquals(name, code.wireName, fixture.expectedErrorCode)
            val response = VoiceActionCodec.parseResponse(fixture.responsePayload())
            assertEquals(name, VoiceActionResult.DENIED, response.result)
            assertFalse(name, response.isAccepted)
            assertEquals(name, code, response.errorCode)
            assertEquals(name, code.wireName, response.error?.code)
            assertFalse(name, response.error?.message.isNullOrEmpty())
            assertFalse(name, response.message.isEmpty())
            val requestObject = checkNotNull(fixture.request).jsonObject
            assertEquals(name, content(requestObject, "request_id"), response.requestId)
            assertEquals(name, content(requestObject, "action"), response.action)
            assertEquals(name, requestObject["target"], response.target?.let { target(it) })
        }
    }

    @Test
    fun deniedErrorsCarryRetryGuidanceAndEchoTheRequestId() {
        assertEquals(denied.keys, guidance.keys)
        for ((name, action) in guidance) {
            val response = VoiceActionCodec.parseResponse(fixture(name).responsePayload())
            val error = checkNotNull(response.error)
            assertEquals(name, false, error.retryable)
            assertEquals(name, action, error.action)
            assertEquals(name, response.requestId, error.requestId)
        }
        val echoed = """"request_id":"req_synthetic_0104""""
        val changed = """"request_id":"req_other""""
        val mismatch = fixture("target-not-found").responsePayload()
            .replace(echoed, changed)
            .replaceFirst(changed, echoed)
        assertTrue(mismatch, mismatch.indexOf(echoed) < mismatch.indexOf(changed))
        val failure = assertThrows(ContractParseException::class.java) {
            VoiceActionCodec.parseResponse(mismatch)
        }
        assertTrue(failure.message, failure.message.orEmpty().contains("echo"))
    }

    @Test
    fun invalidRequestFixturesFailParsingForTheStatedReason() {
        for ((name, reason) in invalidRequests) {
            val fixture = fixture(name)
            assertEquals(name, "invalid", fixture.expectRequest)
            val failure = assertThrows(ContractParseException::class.java) {
                VoiceActionCodec.parseRequest(fixture.requestPayload())
            }
            assertTrue("$name: ${failure.message}", failure.message.orEmpty().contains(reason))
        }
    }

    @Test
    fun unsupportedActionDenialNamesTheActionWithoutRecognisingIt() {
        val response =
            VoiceActionCodec.parseResponse(fixture("unsupported-action").responsePayload())
        assertEquals("delete_report", response.action)
        assertEquals(VoiceAction.UNKNOWN, response.knownAction)
        assertEquals(VoiceActionErrorCode.VOICE_ACTION_UNSUPPORTED, response.errorCode)
        assertFalse(response.isAccepted)
    }

    @Test
    fun unknownEnumResponseMapsToUnknownAndIsNeverSuccess() {
        val fixture = fixture("unknown-enum-response")
        assertEquals("unknown-enum", fixture.expectResponse)
        val request = VoiceActionCodec.parseRequest(fixture.requestPayload())
        val response = VoiceActionCodec.parseResponse(fixture.responsePayload())
        assertEquals(VoiceActionResult.UNKNOWN, response.result)
        assertFalse(response.isAccepted)
        assertEquals(VoiceActionErrorCode.UNKNOWN, response.errorCode)
        assertEquals("FUTURE_VALUE", response.error?.code)
        assertEquals(ContractErrorAction.UNKNOWN, response.error?.action)
        assertEquals(false, response.error?.retryable)
        assertEquals(request.requestId, response.requestId)
        assertEquals(request.target, response.target)
        assertEquals(VoiceAction.QUEUE_RETRY, response.knownAction)
        assertEquals("The check was scheduled for later.", response.message)
    }

    @Test
    fun everyEnumMapsAFutureValueToUnknown() {
        val future = "__future_value__"
        assertEquals(VoiceAction.UNKNOWN, VoiceAction.fromWire(future))
        assertEquals(VoiceTargetKind.UNKNOWN, VoiceTargetKind.fromWire(future))
        assertEquals(VoiceActionResult.UNKNOWN, VoiceActionResult.fromWire(future))
        assertEquals(VoiceActionErrorCode.UNKNOWN, VoiceActionErrorCode.fromWire(future))
        assertEquals(ContractErrorAction.UNKNOWN, ContractErrorAction.fromWire(future))
        for (name in listOf("", "ACCEPTED", "Accepted", " accepted", "accepted ")) {
            assertEquals(name, VoiceActionResult.UNKNOWN, VoiceActionResult.fromWire(name))
        }
        val response = VoiceActionCodec.parseResponse(
            """{"request_id":"r1","result":"$future","action":"$future",
               "target":{"kind":"$future","id":"t1"},"message":"Later.",
               "error":{"code":"FUTURE_VALUE","message":"Newer code.","retryable":true,
               "action":"$future","request_id":"r1"}}"""
        )
        assertEquals(VoiceActionResult.UNKNOWN, response.result)
        assertEquals(VoiceAction.UNKNOWN, response.knownAction)
        assertEquals(VoiceTargetKind.UNKNOWN, response.target?.kind)
        assertEquals(VoiceActionErrorCode.UNKNOWN, response.errorCode)
        assertEquals(ContractErrorAction.UNKNOWN, response.error?.action)
        assertFalse(response.isAccepted)
    }

    @Test
    fun validPayloadsRoundTripThroughTheEncoder() {
        for (fixture in fixtures.filter { it.expectRequest == "valid" }) {
            val request = VoiceActionCodec.parseRequest(fixture.requestPayload())
            val encoded = VoiceActionCodec.encodeRequest(request)
            assertEquals(fixture.name, fixture.request, ContractFixtures.element(encoded))
            assertEquals(fixture.name, request, VoiceActionCodec.parseRequest(encoded))
        }
        for (fixture in fixtures.filter { it.expectResponse == "valid" }) {
            val response = VoiceActionCodec.parseResponse(fixture.responsePayload())
            val encoded = VoiceActionCodec.encodeResponse(response)
            assertEquals(fixture.name, fixture.response, ContractFixtures.element(encoded))
            assertEquals(fixture.name, response, VoiceActionCodec.parseResponse(encoded))
        }
    }

    private fun fixture(name: String): Fixture =
        checkNotNull(byName[name]) { "$name fixture is missing" }

    private fun content(container: JsonObject, key: String): String =
        container.getValue(key).jsonPrimitive.content

    private fun target(target: VoiceTarget) = ContractFixtures.element(
        """{"kind":"${target.kind.wireName}","id":"${target.id}"}"""
    )
}
