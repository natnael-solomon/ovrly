package app.ovrly.voice

import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.HttpUrl.Companion.toHttpUrl
import okio.Buffer
import okio.ByteString

/**
 * No Origin header, warm-up request, retries, stored visitor identity or secret key.
 * A provider rejection is surfaced, never worked around by impersonating a browser.
 */
internal class VoxideTransport(
    private val configuration: VoiceConfiguration,
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(false)
        .build(),
    private val diagnostics: VoiceDiagnostics,
) : VoiceTransport {
    private val lock = Any()
    private val ended = AtomicBoolean(false)
    private var listener: VoiceTransport.Listener? = null
    private var call: Call? = null
    private var socket: WebSocket? = null

    override fun start(listener: VoiceTransport.Listener) {
        check(configuration.unavailableState() == null)
        synchronized(lock) {
            check(this.listener == null && !ended.get())
            this.listener = listener
        }
        request("init", Request.Builder()
            .url("${configuration.httpsOrigin}/api/sdk/init")
            .header("Authorization", "Bearer ${configuration.publishableKey}")
            .get().build()) { response ->
            val body = response.body ?: throw VoiceProtocolException()
            val buffer = Buffer()
            val source = body.source()
            while (!source.exhausted()) {
                val remaining = VoiceProtocol.MAX_MESSAGE_BYTES + 1L - buffer.size
                if (remaining <= 0) throw VoiceProtocolException()
                source.read(buffer, minOf(8192, remaining))
            }
            val init = VoiceProtocol.objectFrom(buffer.readUtf8())
            if (init.opt("config") !is org.json.JSONObject) throw VoiceProtocolException()
            registerManifest()
        }
    }

    private fun registerManifest() {
        request("manifest", Request.Builder()
            .url("${configuration.httpsOrigin}/api/sdk/manifest")
            .header("Authorization", "Bearer ${configuration.publishableKey}")
            .post(VoiceProtocol.manifest().toRequestBody("application/json".toMediaType()))
            .build()) {
            openSocket()
        }
    }

    private fun request(stage: String, request: Request, success: (Response) -> Unit) {
        synchronized(lock) {
            if (ended.get()) return
            call = client.newCall(request).also { next ->
                next.enqueue(object : Callback {
                    override fun onFailure(call: Call, e: IOException) {
                        fail("Voxide $stage request failed. Check connectivity and native-client support.", e)
                    }

                    override fun onResponse(call: Call, response: Response) {
                        response.use {
                            if (ended.get()) return
                            if (!response.isSuccessful) {
                                fail(httpFailure(stage, response.code))
                                return
                            }
                            var handled = false
                            try {
                                success(response)
                                handled = true
                            } catch (cause: VoiceProtocolException) {
                                fail("Voxide $stage returned an invalid or oversized response.", cause)
                                handled = true
                            } catch (cause: IOException) {
                                fail("Voxide $stage response could not be read. Check connectivity.", cause)
                                handled = true
                            } catch (cause: SecurityException) {
                                fail("Android denied the Voxide $stage request.", cause)
                                handled = true
                            } finally {
                                if (!handled) fail("An unexpected fault interrupted the Voxide $stage request.")
                            }
                        }
                    }
                })
            }
        }
    }

    private fun openSocket() {
        val url = "${configuration.httpsOrigin}/api/sdk/live".toHttpUrl().newBuilder()
            .addQueryParameter("key", configuration.publishableKey)
            .addQueryParameter("anon", UUID.randomUUID().toString())
            .build()
        synchronized(lock) {
            if (ended.get()) return
            socket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    if (ended.get()) return
                    if (text.length > VoiceProtocol.MAX_MESSAGE_BYTES ||
                        text.toByteArray(Charsets.UTF_8).size > VoiceProtocol.MAX_MESSAGE_BYTES
                    ) {
                        fail("Voxide sent an oversized message. Voice stopped.")
                    } else listener?.message(text)
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    fail("Voxide sent an unsupported binary message. Voice stopped.")
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    if (t !is IOException && t !is SecurityException) {
                        try {
                            fail("An unexpected WebSocket fault stopped voice.", t)
                        } finally {
                            throw t
                        }
                    }
                    fail(response?.let { httpFailure("WebSocket", it.code) }
                        ?: "Voxide disconnected. Check connectivity and native-client support.", t)
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    fail("Voxide ended the connection. Microphone and speaker stopped.")
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    fail("Voxide ended the connection. Microphone and speaker stopped.")
                }
            })
        }
    }

    override fun send(text: String): Boolean = synchronized(lock) {
        val ws = socket ?: return false
        if (ended.get() || text.length > VoiceProtocol.MAX_MESSAGE_BYTES ||
            ws.queueSize() + text.toByteArray(Charsets.UTF_8).size > 64 * 1024
        ) return false
        ws.send(text)
    }

    private fun fail(message: String, cause: Throwable? = null) {
        if (ended.compareAndSet(false, true)) {
            diagnostics.warning(message, cause)
            var completed = false
            var failures = emptyList<String>()
            try {
                failures = release()
                completed = true
            } finally {
                val suffix = when {
                    !completed -> " Connection cleanup encountered an unexpected fault."
                    failures.isNotEmpty() -> " Connection cleanup reported ${failures.size} resource failures; see OvrlyVoice diagnostics."
                    else -> ""
                }
                listener?.failed(message + suffix)
            }
        }
    }

    override fun close() {
        if (ended.compareAndSet(false, true)) {
            val failures = release()
            if (failures.isNotEmpty()) throw VoiceCleanupException(failures)
        }
    }

    private fun release(): List<String> {
        val (oldCall, oldSocket) = synchronized(lock) {
            val resources = call to socket
            call = null
            socket = null
            resources
        }
        // Cancel rather than drain queued microphone frames after the user's stop.
        return diagnostics.cleanup(
            "HTTP call" to { oldCall?.cancel() },
            "WebSocket" to { oldSocket?.cancel() },
            "HTTP dispatcher calls" to { client.dispatcher.cancelAll() },
            "HTTP connection pool" to { client.connectionPool.evictAll() },
            "HTTP executor" to { client.dispatcher.executorService.shutdown() },
        )
    }

    private fun httpFailure(stage: String, code: Int): String = when (code) {
        401, 403 -> "Voxide refused the $stage request (HTTP $code). Check the publishable key and provider-approved native access; no browser Origin is impersonated."
        429 -> "Voxide rate or usage limit reached (HTTP 429)."
        in 300..399 -> "Voxide redirected the $stage request. Redirects are disabled to protect the configured key."
        else -> "Voxide $stage failed (HTTP $code). Native Android support is unverified."
    }
}
