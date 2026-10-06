package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.VoiceAction
import app.ovrly.contract.VoiceActionCodec
import app.ovrly.contract.VoiceActionRequest
import app.ovrly.contract.VoiceActionResult
import app.ovrly.contract.VoiceTarget
import app.ovrly.contract.VoiceTargetKind
import app.ovrly.voice.VoiceBackend
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceApiTest {
    private val sent = ContractFixtures.load(ContractFixtures.SHARED)
        .filter { it.expectRequest == "valid" }

    private fun ApiTestServer.perform(request: VoiceActionRequest) =
        runBlocking { VoiceApi(api).perform(request) }

    private fun request(id: String = "req_synthetic_0001") = VoiceActionRequest(
        id,
        VoiceAction.OPEN_CHECK,
        VoiceTarget(VoiceTargetKind.INVESTIGATION, "inv_synthetic_0001")
    )

    @Test
    fun everySharedFixtureRoundTripsThroughTheClient() {
        assertTrue("fixtures with a valid request", sent.size >= 8)
        for (fixture in sent) {
            withServer { server ->
                server.credentials.write("synthetic-token-1")
                server.json(200, fixture.responsePayload())
                val request = VoiceActionCodec.parseRequest(fixture.requestPayload())
                val result = server.perform(request)
                val recorded = server.take()
                assertEquals(fixture.name, "POST", recorded.method)
                assertEquals(fixture.name, "/v1/voice/actions", recorded.path)
                assertEquals("Bearer synthetic-token-1", recorded.getHeader("Authorization"))
                assertEquals(
                    fixture.name,
                    Json.parseToJsonElement(fixture.requestPayload()),
                    Json.parseToJsonElement(recorded.body.readUtf8())
                )
                val response = (result as ApiResult.Success).value
                assertEquals(VoiceActionCodec.parseResponse(fixture.responsePayload()), response)
                val outcome = VoiceBackend.outcome(result)
                val accepted = response.result == VoiceActionResult.ACCEPTED
                assertEquals(fixture.name, accepted, outcome.success)
                fixture.expectedErrorCode?.let { assertEquals(fixture.name, it, outcome.code) }
                if (fixture.expectResponse == "unknown-enum") assertFalse(outcome.success)
            }
        }
    }

    @Test
    fun schemaFailuresAndReusedRequestIdsAreErrorsNotDenials() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.warmUp()
        server.error(422, "VALIDATION_FAILED", action = "fix_request")
        val invalid = VoiceBackend.outcome(server.perform(request()))
        assertFalse(invalid.success)
        assertEquals("VALIDATION_FAILED", invalid.code)
        server.error(409, "IDEMPOTENCY_KEY_REUSED", action = "fix_request")
        val reused = VoiceBackend.outcome(server.perform(request()))
        assertFalse(reused.success)
        assertEquals("IDEMPOTENCY_KEY_REUSED", reused.code)
        assertTrue("different action" in reused.message)
    }

    @Test
    fun aResponseForAnotherRequestIsNeverUsed() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val other = ContractFixtures.load(ContractFixtures.SHARED)
            .single { it.name == "open-check-accepted" }.responsePayload()
        server.json(200, other)
        val result = server.perform(request("req_synthetic_9999"))
        assertTrue((result as ApiResult.Failure).failure is ApiFailure.Incompatible)
        assertFalse(VoiceBackend.outcome(result).success)
    }

    @Test
    fun aColdStartRetryReplaysTheSameRequestId() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        val accepted = ContractFixtures.load(ContractFixtures.SHARED)
            .single { it.name == "open-check-accepted" }.responsePayload()
        server.json(200, accepted)
        val result = server.perform(request())
        assertTrue(result is ApiResult.Success)
        val bodies = List(2) { server.take().body.readUtf8() }
        assertEquals(bodies[0], bodies[1])
        assertTrue("req_synthetic_0001" in bodies[0])
    }

    @Test
    fun noResponseIsAVisibleNetworkFailure() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        repeat(2) {
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        }
        val outcome = VoiceBackend.outcome(server.perform(request()))
        assertFalse(outcome.success)
        assertEquals(VoiceBackend.NETWORK, outcome.code)
    }

    private fun ApiTestServer.warmUp() {
        json(200, "{}")
        runBlocking { client.send(ApiCall("test.warm", "GET", "v1/warm")) { it } }
        take()
    }
}
