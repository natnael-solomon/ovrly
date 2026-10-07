package app.ovrly.data

import android.content.Context
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** BC-D07 account link on the device (AN-10, #36), against a fake ID token source. */
class AccountLinkTest {
    private val idToken = "synthetic.google.id-token"
    private val accountId = "00000000-0000-4000-8000-0000000000a1"

    private class FakeTokens(private val result: IdTokenResult) : IdTokenSource {
        var asked = 0
        override val available: Boolean = true

        override suspend fun idToken(activity: Context?): IdTokenResult {
            asked++
            return result
        }
    }

    /** A credential store whose writes fail, like a Keystore that refuses the key. */
    private class FailingStore(initial: String) : CredentialStore {
        private var token: String? = initial

        override fun read(): String? = token

        override fun write(token: String): Boolean = false

        override fun clear() {
            token = null
        }
    }

    private fun linked(credential: String? = null, merged: Int = 0): String {
        val tokenJson = credential?.let { """{"token":"$it","token_type":"bearer"}""" } ?: "null"
        return """{"principal_id":"$accountId","kind":"account","linked":true,""" +
            """"merged_saved_reports":$merged,"credential":$tokenJson}"""
    }

    private fun linker(server: ApiTestServer, account: AccountStore, tokens: IdTokenSource) =
        AccountLinker(OvrlyApi(server.client, server.credentials, account), tokens)

    @Test
    fun aGuestIsUpgradedInPlaceAndKeepsItsCredential() = withServer { server ->
        server.credentials.write("guest-token")
        server.json(200, linked())
        val account = MemoryAccountStore()
        val tokens = FakeTokens(IdTokenResult.Token(idToken))
        val outcome = runBlocking { linker(server, account, tokens).link() }

        assertEquals(LinkOutcome.Linked(switched = false, merged = 0, stored = true), outcome)
        val request = server.take()
        assertEquals("POST", request.method)
        assertEquals("/v1/principals/link", request.path)
        assertEquals("Bearer guest-token", request.getHeader("Authorization"))
        assertEquals(
            """{"provider":"google","id_token":"$idToken"}""",
            request.body.readUtf8()
        )
        assertEquals("guest-token", server.credentials.read())
        assertTrue(account.linked())
        server.log.forEach { assertFalse(it, idToken in it) }
    }

    @Test
    fun aSecondDeviceSwapsToTheAccountCredentialBeforeTheNextCall() = withServer { server ->
        server.credentials.write("guest-token")
        server.json(200, linked(credential = "account-token", merged = 2))
        server.json(200, """{"items":[]}""")
        val account = MemoryAccountStore()
        val api = OvrlyApi(server.client, server.credentials, account)
        val outcome = runBlocking {
            AccountLinker(api, FakeTokens(IdTokenResult.Token(idToken))).link()
        }

        assertEquals(LinkOutcome.Linked(switched = true, merged = 2, stored = true), outcome)
        assertEquals("account-token", server.credentials.read())
        assertTrue(api.linked)
        runBlocking { SavedReports(api, MemorySavedReportDao()).sync() }
        server.take()
        assertEquals("Bearer account-token", server.take().getHeader("Authorization"))
    }

    @Test
    fun anUnstorableAccountCredentialIsStillUsedAndReported() = withServer { server ->
        server.json(200, linked(credential = "account-token", merged = 1))
        server.json(200, """{"items":[]}""")
        val store = FailingStore("guest-token")
        val api = OvrlyApi(server.client, store, MemoryAccountStore())
        val outcome = runBlocking {
            AccountLinker(api, FakeTokens(IdTokenResult.Token(idToken))).link()
        }
        assertEquals(LinkOutcome.Linked(switched = true, merged = 1, stored = false), outcome)
        runBlocking { SavedReports(api, MemorySavedReportDao()).sync() }
        server.take()
        assertEquals("Bearer account-token", server.take().getHeader("Authorization"))
    }

    @Test
    fun aSecondDeviceRecoversTheAccountsSavedReportsAndForgetsTheGuestsChecks() =
        withServer { server ->
            server.credentials.write("guest-token")
            server.json(200, linked(credential = "account-token", merged = 0))
            server.json(
                200,
                SavedFixtures.list(SavedFixtures.savedObject("2026-10-06T08:00:00Z"))
            )
            var forgotten = 0
            val api = OvrlyApi(server.client, server.credentials, MemoryAccountStore())
            val linker = AccountLinker(api, FakeTokens(IdTokenResult.Token(idToken))) {
                forgotten++
            }
            val dao = MemorySavedReportDao()
            val saved = SavedReports(api, dao)
            val outcome = runBlocking { linker.link() } as LinkOutcome.Linked
            assertNull(runBlocking { saved.sync() })

            assertTrue(outcome.switched)
            assertEquals(1, forgotten)
            server.take()
            val list = server.take()
            assertEquals("/v1/reports/saved", list.path)
            assertEquals("Bearer account-token", list.getHeader("Authorization"))
            // The report saved on the first device is listed here and readable offline.
            val stored = runBlocking { saved.stored() }.single()
            assertEquals(SavedFixtures.report.id, stored.entry.reportId)
            assertEquals(SavedFixtures.report, stored.report)
        }

    @Test
    fun anInPlaceUpgradeKeepsThisDevicesChecks() = withServer { server ->
        server.credentials.write("guest-token")
        server.json(200, linked())
        var forgotten = 0
        val api = OvrlyApi(server.client, server.credentials, MemoryAccountStore())
        val linker = AccountLinker(api, FakeTokens(IdTokenResult.Token(idToken))) { forgotten++ }
        assertTrue(runBlocking { linker.link() } is LinkOutcome.Linked)
        assertEquals(0, forgotten)
    }

    @Test
    fun everyRefusalIsReportedWithoutChangingTheIdentity() {
        val refusals = listOf(
            Triple(409, "ACCOUNT_ALREADY_LINKED", "none"),
            Triple(401, "INVALID_ID_TOKEN", "authenticate"),
            Triple(503, "ACCOUNT_LINK_UNAVAILABLE", "none"),
            Triple(401, "INVALID_CREDENTIAL", "authenticate")
        )
        refusals.forEach { (status, code, action) ->
            withServer { server ->
                server.credentials.write("guest-token")
                // A cold first call repeats once on a gateway answer (502 to 504).
                val attempts = if (status in 502..504) 2 else 1
                repeat(attempts) { server.error(status, code, action = action) }
                val account = MemoryAccountStore()
                val outcome = runBlocking {
                    linker(server, account, FakeTokens(IdTokenResult.Token(idToken))).link()
                }
                val failure = (outcome as LinkOutcome.Failed).failure as ApiFailure.Server
                assertEquals(code, ApiErrorCode.valueOf(code), failure.code)
                // A refused credential is not replaced by a new guest during a link.
                assertEquals(code, attempts, server.server.requestCount)
                assertEquals(code, "guest-token", server.credentials.read())
                assertFalse(code, account.linked())
            }
        }
    }

    @Test
    fun noTokenMeansNoRequest() = withServer { server ->
        val cancelled = FakeTokens(IdTokenResult.Cancelled)
        val account = MemoryAccountStore()
        val outcome = runBlocking { linker(server, account, cancelled).link() }
        assertEquals(LinkOutcome.Cancelled, outcome)
        val build = linker(server, account, NoIdTokenSource)
        assertFalse(build.available)
        assertEquals(
            LinkOutcome.Unavailable(SIGN_IN_UNAVAILABLE),
            runBlocking { build.link() }
        )
        assertEquals(0, server.server.requestCount)
        assertNull(server.credentials.read())
    }

    @Test
    fun aStaleGuestRefusalAfterALinkKeepsTheAccountCredential() = withServer { server ->
        server.credentials.write("guest-token")
        val guestCallArrived = CountDownLatch(1)
        val linkDone = CountDownLatch(1)
        val seen = CopyOnWriteArrayList<String>()
        server.server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val bearer = request.getHeader("Authorization").orEmpty()
                seen += "${request.path} $bearer"
                fun json(status: Int, body: String) = MockResponse().setResponseCode(status)
                    .setHeader("Content-Type", "application/json").setBody(body)
                return when {
                    request.path == "/v1/principals/link" ->
                        json(200, linked(credential = "account-token", merged = 1))

                    bearer == "Bearer guest-token" -> {
                        // In flight with the guest token while the link revokes it.
                        guestCallArrived.countDown()
                        linkDone.await(5, TimeUnit.SECONDS)
                        json(
                            401,
                            """{"code":"INVALID_CREDENTIAL","message":"Revoked",""" +
                                """"retryable":false,"action":"authenticate",""" +
                                """"request_id":"synthetic-request"}"""
                        )
                    }

                    else -> json(200, """{"items":[]}""")
                }
            }
        }
        val account = MemoryAccountStore()
        val api = OvrlyApi(server.client, server.credentials, account)
        val saved = SavedReports(api, MemorySavedReportDao())
        val result = runBlocking(Dispatchers.IO) {
            val poll = async { saved.sync() }
            assertTrue(guestCallArrived.await(5, TimeUnit.SECONDS))
            val outcome = AccountLinker(api, FakeTokens(IdTokenResult.Token(idToken))).link()
            linkDone.countDown()
            assertTrue(outcome is LinkOutcome.Linked)
            poll.await()
        }

        // The stale refusal is retried with the account credential, never a new guest.
        assertNull(result)
        assertEquals("account-token", server.credentials.read())
        assertTrue(account.linked())
        assertTrue(api.linked)
        assertFalse(seen.toString(), seen.any { it.startsWith("/v1/principals/guest") })
        assertEquals("/v1/reports/saved Bearer account-token", seen.last())
    }

    @Test
    fun aLinkedDeviceWhoseCredentialIsRefusedBecomesAGuestAgain() = withServer { server ->
        server.credentials.write("account-token")
        server.error(401, "INVALID_CREDENTIAL", action = "authenticate")
        server.guest("synthetic-token-2")
        server.json(200, """{"items":[]}""")
        val account = MemoryAccountStore(linked = true)
        val api = OvrlyApi(server.client, server.credentials, account)
        assertNull(runBlocking { SavedReports(api, MemorySavedReportDao()).sync() })
        assertFalse(api.linked)
        assertEquals("synthetic-token-2", server.credentials.read())
    }

    @Test
    fun malformedLinkAnswersAreNotSuccess() {
        val bodies = listOf(
            """{"principal_id":"$accountId","kind":"guest","linked":true,""" +
                """"merged_saved_reports":0,"credential":null}""",
            """{"principal_id":"$accountId","kind":"account","linked":false,""" +
                """"merged_saved_reports":0,"credential":null}""",
            """{"principal_id":"$accountId","kind":"account","linked":true,""" +
                """"merged_saved_reports":-1,"credential":null}""",
            """{"principal_id":"not-a-uuid","kind":"account","linked":true,""" +
                """"merged_saved_reports":0,"credential":null}""",
            """{"principal_id":"$accountId","kind":"account","linked":true,""" +
                """"merged_saved_reports":0}"""
        )
        bodies.forEach { body ->
            withServer { server ->
                server.credentials.write("guest-token")
                server.json(200, body)
                val account = MemoryAccountStore()
                val outcome = runBlocking {
                    linker(server, account, FakeTokens(IdTokenResult.Token(idToken))).link()
                }
                val failure = (outcome as LinkOutcome.Failed).failure
                assertTrue(body, failure is ApiFailure.Incompatible)
                assertFalse(body, account.linked())
                assertEquals(body, "guest-token", server.credentials.read())
            }
        }
    }

    @Test
    fun theRequestAndTokenAreNeverPrinted() {
        val request = AccountLinkRequest(AccountLinkRequest.GOOGLE, idToken)
        assertFalse(idToken in request.toString())
        assertFalse(idToken in IdTokenResult.Token(idToken).toString())
        assertTrue(runCatching { AccountLinkRequest("apple", idToken) }.isFailure)
        assertTrue(runCatching { AccountLinkRequest(AccountLinkRequest.GOOGLE, "") }.isFailure)
    }
}
