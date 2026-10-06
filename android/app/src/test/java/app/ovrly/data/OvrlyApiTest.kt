package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.InvestigationCreateRequest
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.ProcessingStatus
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OvrlyApiTest {
    private val investigationId = "00000000-0000-4000-8000-000000000401"

    @Test
    fun mintsOneGuestCredentialOnFirstNeedAndReusesIt() = withServer { server ->
        server.guest()
        server.json(202, ApiTestServer.intakeResponse("investigation-create-url"))
        server.json(200, ApiTestServer.intakeResponse("investigation-create-url"))
        runBlocking {
            assertTrue(server.api.getInvestigation(investigationId) is ApiResult.Success)
            assertTrue(server.api.getInvestigation(investigationId) is ApiResult.Success)
        }
        val mint = server.take()
        assertEquals("/v1/principals/guest", mint.path)
        assertEquals("POST", mint.method)
        assertNull(mint.getHeader("Authorization"))
        assertEquals("{}", mint.body.readUtf8())
        repeat(2) {
            assertEquals("Bearer synthetic-token-1", server.take().getHeader("Authorization"))
        }
        assertEquals("synthetic-token-1", server.credentials.read())
        assertEquals(3, server.server.requestCount)
        server.log.forEach { assertFalse(it, "synthetic-token-1" in it) }
    }

    @Test
    fun aRefusedCredentialIsReplacedOnceWithANewGuest() = withServer { server ->
        server.credentials.write("expired-token")
        server.error(401, "INVALID_CREDENTIAL", action = "authenticate")
        server.guest("synthetic-token-2")
        server.json(200, ApiTestServer.intakeResponse("investigation-create-url"))
        val result = runBlocking { server.api.getInvestigation(investigationId) }
        assertTrue(result is ApiResult.Success)
        assertEquals("Bearer expired-token", server.take().getHeader("Authorization"))
        assertEquals("/v1/principals/guest", server.take().path)
        assertEquals("Bearer synthetic-token-2", server.take().getHeader("Authorization"))
        assertEquals("synthetic-token-2", server.credentials.read())
    }

    @Test
    fun aGuestMintFailureIsReturnedWithoutCallingTheEndpoint() = withServer { server ->
        server.error(500, "INTERNAL_ERROR")
        server.error(500, "INTERNAL_ERROR")
        val result = runBlocking { server.api.getInvestigation(investigationId) }
        val failure = (result as ApiResult.Failure).failure as ApiFailure.Server
        assertEquals(ApiErrorCode.INTERNAL_ERROR, failure.code)
        assertEquals(1, server.server.requestCount)
        assertNull(server.credentials.read())
    }

    @Test
    fun theSixResultFixturesParseThroughTheClientAndRepository() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val repository = server.repository()
        val fixtures = ContractFixtures.load(ContractFixtures.RESULTS)
        assertEquals(
            setOf(
                "cancelled",
                "complete",
                "failed",
                "insufficient-evidence",
                "no-claims",
                "partial"
            ),
            fixtures.map { it.name }.toSet()
        )
        fixtures.forEach { fixture ->
            server.json(200, fixture.investigationPayload())
            val id = (ContractFixtures.element(fixture.investigationPayload()) as JsonObject)
                .getValue("id").jsonPrimitive.content
            val loaded = runBlocking { repository.refresh(id) } as ApiResult.Success
            val investigation = loaded.value
            val name = fixture.name
            assertEquals(
                name,
                fixture.expectedProcessingStatus,
                investigation.processingStatus.wireName
            )
            assertEquals(name, fixture.expectedState, investigation.state.wireName)
            assertEquals(investigation, runBlocking { repository.cached(id) })
            val status = investigation.checkStatus
            assertEquals(name, investigation.processingStatus.name, status.name)
            assertEquals(status == CheckStatus.COMPLETE, investigation.isComplete)
            fixture.expectedClaimCount?.let {
                assertEquals(it, investigation.report?.claims?.size ?: 0)
            }
            fixture.expectedErrorCode?.let { assertEquals(it, investigation.error?.code) }
        }
    }

    private fun waitingPayload(): JsonObject =
        ContractFixtures.element(ApiTestServer.intakeResponse("investigation-create-url"))
            as JsonObject

    @Test
    fun anUnknownProcessingStatusIsNeverCompleteOrTerminal() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val future = ContractFixtures.replace(
            waitingPayload(),
            listOf("processing_status"),
            JsonPrimitive("__future__")
        )
        server.json(200, future.toString())
        val result = runBlocking { server.api.getInvestigation(investigationId) }
        val investigation = (result as ApiResult.Success).value
        assertEquals(ProcessingStatus.UNKNOWN, investigation.processingStatus)
        assertEquals(CheckStatus.UNKNOWN, investigation.checkStatus)
        assertFalse(investigation.checkStatus.terminal)
        assertFalse(investigation.isComplete)
    }

    @Test
    fun theLiveNineFieldResponseIsIncompatibleNotSuccess() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val legacy = JsonObject(
            waitingPayload().filterKeys { it !in setOf("processing_status", "job", "report") }
        )
        server.json(200, legacy.toString())
        val result = runBlocking { server.api.getInvestigation(investigationId) }
        assertTrue((result as ApiResult.Failure).failure is ApiFailure.Incompatible)
    }

    @Test
    fun aRepeatedCreateReplaysTheSameKeyAndBody() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val response = ApiTestServer.intakeResponse("investigation-create-url")
        server.json(202, response)
        server.json(202, response)
        val request = InvestigationCreateRequest(
            InvestigationSource.Url("https://video.example/synthetic/clip-0001", 185_000)
        )
        val (first, second) = runBlocking {
            server.api.createInvestigation(request, "synthetic-key-1") to
                server.api.createInvestigation(request, "synthetic-key-1")
        }
        assertEquals(
            (first as ApiResult.Success).value.id,
            (second as ApiResult.Success).value.id
        )
        val sent = List(2) { server.take() }
        sent.forEach {
            assertEquals("/v1/investigations", it.path)
            assertEquals("synthetic-key-1", it.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        }
        assertEquals(sent[0].body.readUtf8(), sent[1].body.readUtf8())
    }

    @Test
    fun trackingPollsUntilATerminalStatus() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val repository = server.repository(PollingPolicy(1, 1, 1))
        server.json(200, ApiTestServer.result("partial").investigationPayload())
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        val updates = runBlocking {
            repository.track("00000000-0000-4000-8000-000000000101").toList()
        }
        assertEquals(3, updates.size)
        assertEquals(CheckStatus.PARTIAL, (updates[0] as InvestigationUpdate.Loaded).status)
        assertTrue(updates[1] is InvestigationUpdate.Unavailable)
        assertEquals(CheckStatus.COMPLETE, (updates[2] as InvestigationUpdate.Loaded).status)
    }

    @Test
    fun trackingStopsWhenTheInvestigationIsGone() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val repository = server.repository(PollingPolicy(1, 1, 1))
        server.error(404, "NOT_FOUND")
        val updates = runBlocking { repository.track(investigationId).toList() }
        val gone = updates.single() as InvestigationUpdate.Unavailable
        assertEquals(ApiErrorCode.NOT_FOUND, (gone.failure as ApiFailure.Server).code)
        assertNull(gone.lastKnown)
    }
}
