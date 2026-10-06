package app.ovrly.data

import android.util.Log
import app.ovrly.contract.ContractParseException
import app.ovrly.contract.ErrorCodec
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.Response

/** Request-id-only diagnostics. Never pass URLs, bodies, headers or tokens. */
internal fun interface ApiLog {
    fun event(message: String)

    companion object {
        val Android = ApiLog { Log.i("OvrlyApi", it) }
    }
}

/**
 * The free host sleeps after 30 minutes without traffic. A call made [idleMillis] after the
 * last response (or the first call of the process) is treated as cold: it uses the longer
 * timeouts below and is retried once if no response arrives or the gateway answers 502-504.
 * After [wakingHintMillis] without an answer the client reports [ApiClient.waking].
 */
internal data class ColdStartPolicy(
    val idleMillis: Long = TimeUnit.MINUTES.toMillis(IDLE_MINUTES),
    val connectTimeoutMillis: Long = TimeUnit.SECONDS.toMillis(COLD_TIMEOUT_SECONDS),
    val readTimeoutMillis: Long = TimeUnit.SECONDS.toMillis(COLD_TIMEOUT_SECONDS),
    val wakingHintMillis: Long = WAKING_HINT_MILLIS
)

private const val IDLE_MINUTES = 25L
private const val COLD_TIMEOUT_SECONDS = 60L
private const val WAKING_HINT_MILLIS = 1_500L

/** One HTTP exchange; [route] is a fixed log label such as `uploads.declare`, never a URL. */
internal class ApiCall(
    val route: String,
    val method: String,
    val path: String,
    val body: RequestBody? = null,
    val token: String? = null,
    val headers: Map<String, String> = emptyMap()
)

/**
 * Thin OkHttp wrapper shared by every endpoint: base URL resolution, bearer header,
 * `X-Request-Id`, request-id logging, cold-start handling and the mapping of every response
 * to [ApiResult]. Success bodies are parsed by the caller with the contract codecs; error
 * bodies by [ErrorCodec]. Redirects are refused so a credential never leaves the base origin.
 */
internal class ApiClient(
    private val baseUrl: HttpUrl,
    private val http: OkHttpClient = defaultHttpClient(),
    private val log: ApiLog = ApiLog.Android,
    private val coldStart: ColdStartPolicy = ColdStartPolicy(),
    private val clock: () -> Long = { System.nanoTime() / NANOS_PER_MILLI },
    private val requestIds: () -> String = { UUID.randomUUID().toString() }
) {
    private val coldHttp: OkHttpClient by lazy {
        http.newBuilder()
            .connectTimeout(coldStart.connectTimeoutMillis, TimeUnit.MILLISECONDS)
            .readTimeout(coldStart.readTimeoutMillis, TimeUnit.MILLISECONDS)
            .build()
    }
    private val mutableWaking = MutableStateFlow(false)

    @Volatile
    private var lastResponseAt: Long? = null

    /** True while a cold call is waiting for the service to wake; the UI says so, not an error. */
    val waking: StateFlow<Boolean> = mutableWaking.asStateFlow()

    /** Runs [call] and parses a 2xx body with [parse]; every other outcome is a failure. */
    suspend fun <T> send(call: ApiCall, parse: (String) -> T): ApiResult<T> {
        val requestId = requestIds()
        val url = resolve(call.path)
            ?: return failure(ApiFailure.Incompatible(requestId, 0, "Target outside the API"))
        val request = Request.Builder()
            .url(url)
            .method(call.method, call.body)
            .header(REQUEST_ID_HEADER, requestId)
            .apply { call.token?.let { header("Authorization", "Bearer $it") } }
            .apply { call.headers.forEach { (name, value) -> header(name, value) } }
            .build()
        val raw = if (isCold()) {
            sendCold(call.route, request, requestId)
        } else {
            exchange(http, call.route, request, requestId, 1)
        }
        return interpret(raw, requestId, parse)
    }

    private fun isCold(): Boolean {
        val last = lastResponseAt ?: return true
        return clock() - last >= coldStart.idleMillis
    }

    private suspend fun sendCold(route: String, request: Request, requestId: String): RawOutcome =
        coroutineScope {
            log.event("$route: service may be asleep, using cold-start timeouts ($requestId)")
            val hint = launch {
                delay(coldStart.wakingHintMillis)
                mutableWaking.value = true
            }
            try {
                val first = exchange(coldHttp, route, request, requestId, 1)
                if (first.wakeRetry) {
                    mutableWaking.value = true
                    exchange(coldHttp, route, request, requestId, 2)
                } else {
                    first
                }
            } finally {
                hint.cancel()
                mutableWaking.value = false
            }
        }

    private suspend fun exchange(
        client: OkHttpClient,
        route: String,
        request: Request,
        requestId: String,
        attempt: Int
    ): RawOutcome {
        val started = clock()
        val outcome = suspendCancellableCoroutine { continuation ->
            val call = client.newCall(request)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(
                object : Callback {
                    override fun onFailure(call: Call, e: IOException) {
                        continuation.resume(RawOutcome.NoResponse(e.javaClass.simpleName))
                    }

                    override fun onResponse(call: Call, response: Response) {
                        continuation.resume(read(response))
                    }
                }
            )
        }
        val elapsed = clock() - started
        val context = "in $elapsed ms ($requestId, attempt $attempt)"
        when (outcome) {
            is RawOutcome.Answered -> {
                lastResponseAt = clock()
                log.event("$route -> ${outcome.status} $context")
            }

            is RawOutcome.NoResponse -> log.event("$route failed: ${outcome.reason} $context")
        }
        return outcome
    }

    private fun read(response: Response): RawOutcome = response.use {
        try {
            val source = it.body.source()
            if (source.request(MAX_BODY_BYTES + 1)) {
                RawOutcome.Answered(it.code, null)
            } else {
                RawOutcome.Answered(it.code, source.buffer.readUtf8())
            }
        } catch (e: IOException) {
            RawOutcome.NoResponse(e.javaClass.simpleName)
        }
    }

    private fun <T> interpret(
        raw: RawOutcome,
        requestId: String,
        parse: (String) -> T
    ): ApiResult<T> = when (raw) {
        is RawOutcome.NoResponse -> failure(ApiFailure.Network(requestId, raw.reason))

        is RawOutcome.Answered -> when {
            raw.body == null ->
                failure(ApiFailure.Incompatible(requestId, raw.status, "Response too large"))

            raw.status in SUCCESS -> parseSuccess(raw.status, raw.body, requestId, parse)

            else -> parseError(raw.status, raw.body, requestId)
        }
    }

    private fun <T> parseSuccess(
        status: Int,
        body: String,
        requestId: String,
        parse: (String) -> T
    ): ApiResult<T> = try {
        ApiResult.Success(parse(body), requestId)
    } catch (e: ContractParseException) {
        failure(ApiFailure.Incompatible(requestId, status, e.message ?: "Contract violation"))
    }

    private fun parseError(status: Int, body: String, requestId: String): ApiResult<Nothing> = try {
        failure(ApiFailure.Server(status, ErrorCodec.parseError(body)))
    } catch (_: ContractParseException) {
        failure(ApiFailure.Incompatible(requestId, status, "Error body outside the error shape"))
    }

    /** Resolves [path] against the base URL; anything leaving the base origin is refused. */
    private fun resolve(path: String): HttpUrl? = baseUrl.resolve(path)?.takeIf {
        it.scheme == baseUrl.scheme && it.host == baseUrl.host && it.port == baseUrl.port
    }

    private sealed interface RawOutcome {
        val wakeRetry: Boolean

        data class Answered(val status: Int, val body: String?) : RawOutcome {
            override val wakeRetry: Boolean get() = status in GATEWAY_WAKING
        }

        data class NoResponse(val reason: String) : RawOutcome {
            override val wakeRetry: Boolean get() = true
        }
    }

    companion object {
        const val REQUEST_ID_HEADER = "X-Request-Id"
        private const val CONNECT_SECONDS = 15L
        private const val READ_SECONDS = 30L
        private const val WRITE_SECONDS = 60L
        private const val NANOS_PER_MILLI = 1_000_000L
        private const val MAX_BODY_BYTES = 4L * 1024 * 1024
        private val SUCCESS = 200..299
        private val GATEWAY_WAKING = 502..504

        private fun failure(failure: ApiFailure) = ApiResult.Failure(failure)

        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(CONNECT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(READ_SECONDS, TimeUnit.SECONDS)
            .writeTimeout(WRITE_SECONDS, TimeUnit.SECONDS)
            .followRedirects(false)
            .followSslRedirects(false)
            .retryOnConnectionFailure(false)
            .build()
    }
}
