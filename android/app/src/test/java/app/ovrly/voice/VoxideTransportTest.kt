package app.ovrly.voice

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoxideTransportTest {
    @Test fun initAndManifestUsePublishableBearerWithoutBrowserOrigin() {
        val requests = mutableListOf<Request>()
        val (error, _) = intercepted { request ->
            synchronized(requests) { requests.add(request) }
            if (request.url.encodedPath == "/api/sdk/init") 200 to """{"config":{}}"""
            else 403 to """{"message":"untrusted-origin"}"""
        }
        assertTrue(error.contains("403"))
        assertEquals(2, requests.size)
        assertEquals("GET", requests[0].method)
        assertEquals("/api/sdk/init", requests[0].url.encodedPath)
        assertEquals("POST", requests[1].method)
        assertEquals("/api/sdk/manifest", requests[1].url.encodedPath)
        for (request in requests) {
            assertEquals("Bearer vox_pub_test", request.header("Authorization"))
            assertEquals(null, request.header("Origin"))
            assertEquals("https", request.url.scheme)
        }
        val body = Buffer().also { requests[1].body!!.writeTo(it) }.readUtf8()
        assertEquals("open_design_gallery",
            JSONObject(body).getJSONArray("actions").getJSONObject(0).getString("name"))
    }

    @Test fun redirectsAreRejectedWithoutForwardingKeyOrFollowingLocation() {
        val (error, requests) = intercepted {
            302 to """{"message":"redirect"}"""
        }
        assertEquals(1, requests)
        assertTrue(error.contains("Redirects are disabled"))
    }

    @Test fun initMustBeBoundedJsonWithConfiguration() {
        for (body in listOf("{invalid", "{}", """{"config":null}""",
            """{"config":"${"x".repeat(VoiceProtocol.MAX_MESSAGE_BYTES)}"}""")) {
            val (error, requests) = intercepted { 200 to body }
            assertEquals(1, requests)
            assertTrue(error.contains("invalid or oversized"))
        }
    }

    @Test fun providerFailureDoesNotExposeResponseContentsOrKey() {
        val (error, count) = intercepted { 401 to """{"message":"sensitive-server-detail"}""" }
        assertEquals(1, count)
        assertFalse(error.contains("sensitive-server-detail"))
        assertFalse(error.contains("vox_pub_test"))
        assertTrue(error.contains("native"))
    }

    private fun intercepted(response: (Request) -> Pair<Int, String>): Pair<String, Int> {
        val failed = CountDownLatch(1)
        val failure = AtomicReference<String>()
        val diagnostics = mutableListOf<String>()
        var requests = 0
        // This interceptor returns synthetic responses without chain.proceed: no DNS or network.
        val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .addInterceptor { chain ->
                requests++
                val (code, body) = response(chain.request())
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                    .code(code).message("Synthetic test response")
                    .header("Location", "https://never-contact.invalid/")
                    .body(body.toResponseBody()).build()
            }.build()
        val transport = VoxideTransport(
            VoiceConfiguration(true, "https://example.invalid", "vox_pub_test"), client,
            VoiceDiagnostics(diagnostics::add),
        )
        try {
            transport.start(object : VoiceTransport.Listener {
                override fun message(text: String) { error("Unexpected WebSocket message") }
                override fun failed(message: String) { failure.set(message); failed.countDown() }
            })
            assertTrue("Synthetic response did not complete", failed.await(5, TimeUnit.SECONDS))
            assertFalse(transport.send("{}"))
            assertTrue(diagnostics.isNotEmpty())
            assertFalse(diagnostics.any { it.contains("vox_pub_test") || it.contains("sensitive-server-detail") })
            return failure.get() to requests
        } finally {
            transport.close()
        }
    }
}
