package app.ovrly.data

import app.ovrly.contract.ContractErrorAction
import app.ovrly.contract.UploadCodec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiClientTest {
    private val ping = ApiCall("test.ping", "GET", "v1/ping", token = "synthetic-secret-token")

    private fun ApiTestServer.send(call: ApiCall = ping): ApiResult<String> =
        runBlocking { client.send(call) { it } }

    /** The first call of a process is cold; prime the client so the next one is warm. */
    private fun ApiTestServer.warm() {
        json(200, "{}")
        assertTrue(send() is ApiResult.Success)
        take()
    }

    @Test
    fun sendsBearerAndAFreshRequestIdAndLogsOnlyRequestIds() = withServer { server ->
        server.json(200, """{"body":"synthetic-body-text"}""")
        server.json(200, "{}")
        val first = server.send() as ApiResult.Success
        val second = server.send() as ApiResult.Success
        val recorded = server.take()
        assertEquals("Bearer synthetic-secret-token", recorded.getHeader("Authorization"))
        assertEquals(first.requestId, recorded.getHeader(ApiClient.REQUEST_ID_HEADER))
        assertTrue(Regex("[A-Za-z0-9][A-Za-z0-9_-]{0,127}").matches(first.requestId))
        assertNotEquals(first.requestId, second.requestId)
        assertEquals(second.requestId, server.take().getHeader(ApiClient.REQUEST_ID_HEADER))
        assertTrue(server.log.any { first.requestId in it })
        server.log.forEach { line ->
            assertFalse(line, "synthetic-secret-token" in line)
            assertFalse(line, "synthetic-body-text" in line)
            assertFalse(line, "127.0.0.1" in line || "/v1/" in line)
        }
    }

    @Test
    fun coldStartRetriesOnceWithLongerTimeoutsAndReportsWaking() =
        withServer(ColdStartPolicy(wakingHintMillis = 0)) { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
            server.json(200, "{}")
            val states = runBlocking {
                val seen = mutableListOf<Boolean>()
                val collector = launch(Dispatchers.Unconfined) { server.client.waking.toList(seen) }
                val result = server.client.send(ping) { it }
                collector.cancel()
                assertTrue(result is ApiResult.Success)
                seen
            }
            assertEquals(2, server.server.requestCount)
            assertTrue("waking must be shown, not an error", true in states)
            assertFalse(server.client.waking.value)
            assertTrue(server.log.any { "cold-start" in it })
        }

    @Test
    fun coldStartRetriesAGatewayErrorOnlyOnce() = withServer { server ->
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        server.error(502, "REQUEST_FAILED")
        val result = server.send() as ApiResult.Failure
        assertEquals(2, server.server.requestCount)
        assertEquals(ApiErrorCode.REQUEST_FAILED, (result.failure as ApiFailure.Server).code)
    }

    @Test
    fun coldStartGivesUpAfterOneRetryAsANetworkFailure() = withServer { server ->
        repeat(2) {
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))
        }
        val result = server.send() as ApiResult.Failure
        assertEquals(2, server.server.requestCount)
        assertTrue(result.failure is ApiFailure.Network)
        assertTrue(result.failure.retryable)
    }

    @Test
    fun warmCallIsNotRetriedAndIdleMakesItColdAgain() = withServer { server ->
        server.warm()
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        assertTrue((server.send() as ApiResult.Failure).failure is ApiFailure.Server)
        assertEquals(2, server.server.requestCount)
        server.clock.addAndGet(ColdStartPolicy().idleMillis)
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        server.json(200, "{}")
        assertTrue(server.send() is ApiResult.Success)
        assertEquals(4, server.server.requestCount)
    }

    @Test
    fun everyKnownErrorCodeMapsToItsTypedFailure() = withServer { server ->
        server.warm()
        ApiErrorCode.entries.filter { it != ApiErrorCode.UNKNOWN }.forEach { code ->
            server.error(409, code.name, retryable = true, action = "upload_again")
            val failure = (server.send() as ApiResult.Failure).failure as ApiFailure.Server
            assertEquals(code, failure.code)
            assertEquals(409, failure.status)
            assertEquals(ContractErrorAction.UPLOAD_AGAIN, failure.action)
            assertEquals("synthetic-request", failure.requestId)
            assertTrue(failure.retryable)
        }
    }

    @Test
    fun unknownCodesAndActionsStayFailuresWithTheServerMessage() = withServer { server ->
        server.warm()
        server.error(409, "FUTURE_CODE", action = "future_action")
        val failure = (server.send() as ApiResult.Failure).failure as ApiFailure.Server
        assertEquals(ApiErrorCode.UNKNOWN, failure.code)
        assertEquals("FUTURE_CODE", failure.error.code)
        assertEquals("Synthetic FUTURE_CODE", failure.error.message)
        assertEquals(ContractErrorAction.UNKNOWN, failure.action)
        assertFalse(failure.isCredentialRejected)
    }

    @Test
    fun bodiesOutsideTheContractAreIncompatibleNeverSuccess() = withServer { server ->
        server.warm()
        server.enqueue(MockResponse().setResponseCode(502).setBody("<html>Bad gateway</html>"))
        val html = (server.send() as ApiResult.Failure).failure
        assertTrue(html is ApiFailure.Incompatible)
        assertFalse(html.retryable)
        server.error(400, "lowercase_code")
        assertTrue((server.send() as ApiResult.Failure).failure is ApiFailure.Incompatible)
        server.json(200, """{"unexpected":true}""")
        val parsed = runBlocking { server.client.send(ping, UploadCodec::parseUpload) }
        assertTrue((parsed as ApiResult.Failure).failure is ApiFailure.Incompatible)
        server.enqueue(
            MockResponse().setResponseCode(302).setHeader("Location", "https://elsewhere.example/")
        )
        assertTrue((server.send() as ApiResult.Failure).failure is ApiFailure.Incompatible)
    }

    @Test
    fun refusesTargetsOutsideTheBaseOrigin() = withServer { server ->
        val result = server.send(ApiCall("test.escape", "GET", "https://elsewhere.example/x"))
        assertTrue((result as ApiResult.Failure).failure is ApiFailure.Incompatible)
        assertEquals(0, server.server.requestCount)
    }

    @Test
    fun baseUrlAlwaysEndsInASlashAndRejectsCredentialsOrQueries() {
        val base = "http://10.0.2.2:8000"
        assertEquals("$base/", ApiServices.baseUrl(base).toString())
        val nested = "https://api.example/ovrly"
        assertEquals("$nested/", ApiServices.baseUrl(nested).toString())
        assertNull(ApiServices.baseUrl("https://user:pw@api.example/"))
        assertNull(ApiServices.baseUrl("https://api.example/?a=1"))
        assertNull(ApiServices.baseUrl("ftp://api.example/"))
        assertNull(ApiServices.baseUrl(""))
    }
}
