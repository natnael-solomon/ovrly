package app.ovrly.capture

import android.content.Context
import app.ovrly.BuildConfig
import app.ovrly.contract.CaptureChunk
import app.ovrly.contract.CaptureCloseRequest
import app.ovrly.contract.CaptureCreateRequest
import app.ovrly.contract.CaptureManifest
import app.ovrly.contract.CaptureMetadata
import app.ovrly.contract.CaptureModalityCoverage
import app.ovrly.contract.CaptureSession
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.CaptureWork
import app.ovrly.contract.ChunkDisposition
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Interval
import app.ovrly.contract.Modality
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.SeqRange
import app.ovrly.contract.Timebase
import app.ovrly.data.ApiFailure
import app.ovrly.data.ApiResult
import app.ovrly.data.ApiServices
import app.ovrly.data.CaptureApi
import java.io.IOException
import java.security.MessageDigest
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID

/**
 * The `/v1/captures` operations used while recording, in contract models (BE-06, #24).
 * Implementations throw [CaptureApiException] for a contract error and [IOException] for a
 * transport failure; both are retried only when [CaptureApiException.retryable] or transport.
 */
internal interface CaptureSessionApi {
    /** True for the in-memory server, so status text never claims a real upload. */
    val isTestServer: Boolean get() = false

    /** `POST /v1/captures` with `Idempotency-Key`; a replay returns the same session. */
    suspend fun open(idempotencyKey: String, request: CaptureCreateRequest): CaptureSession

    /** `PUT /v1/captures/{id}/chunks/{seq}`; idempotent by `(session_id, seq)`. */
    suspend fun putChunk(
        sessionId: String,
        seq: Int,
        metadata: CaptureMetadata,
        content: ByteArray
    ): CaptureChunk

    /** `POST /v1/captures/{id}/close` with the user's `continue_research` choice. */
    suspend fun close(sessionId: String, request: CaptureCloseRequest): CaptureSession

    /** `GET /v1/captures/{id}`. */
    suspend fun status(sessionId: String): CaptureStatus
}

/** A contract error from the capture endpoints, using the shared error codes. */
internal class CaptureApiException(val code: String, message: String, val retryable: Boolean) :
    IOException(message)

/**
 * The production adapter over the app's API client ([CaptureApi], AN-03). A transport failure
 * becomes an [IOException] (retried with backoff); an error in the shared error shape becomes
 * a [CaptureApiException] with the server's code and `retryable` flag; a response this client
 * cannot read is a non-retryable `INCOMPATIBLE_RESPONSE`. Without a usable API base URL every
 * call fails with `CAPTURE_API_NOT_CONFIGURED`, which is not retried, and the chunks stay on
 * the device.
 */
internal class ServerCaptureSessionApi(private val api: CaptureApi? = null) : CaptureSessionApi {
    override suspend fun open(
        idempotencyKey: String,
        request: CaptureCreateRequest
    ): CaptureSession = configured().open(idempotencyKey, request).orThrow()

    override suspend fun putChunk(
        sessionId: String,
        seq: Int,
        metadata: CaptureMetadata,
        content: ByteArray
    ): CaptureChunk = configured().putChunk(sessionId, seq, metadata, content).orThrow()

    override suspend fun close(sessionId: String, request: CaptureCloseRequest): CaptureSession =
        configured().close(sessionId, request).orThrow()

    override suspend fun status(sessionId: String): CaptureStatus =
        configured().status(sessionId).orThrow()

    private fun configured(): CaptureApi = api ?: throw CaptureApiException(
        NOT_CONFIGURED,
        "The capture server is not configured in this build.",
        retryable = false
    )

    companion object {
        const val NOT_CONFIGURED = "CAPTURE_API_NOT_CONFIGURED"
        const val INCOMPATIBLE = "INCOMPATIBLE_RESPONSE"
    }
}

/** The value of a successful call; a failure becomes the exception the uploader expects. */
internal fun <T> ApiResult<T>.orThrow(): T = when (this) {
    is ApiResult.Success -> value
    is ApiResult.Failure -> throw failure.toException()
}

/** A transport failure is a plain [IOException]; anything else a [CaptureApiException]. */
internal fun ApiFailure.toException(): IOException = when (this) {
    is ApiFailure.Network -> IOException("The capture server could not be reached.")

    is ApiFailure.Server -> CaptureApiException(error.code, error.message, retryable)

    is ApiFailure.Incompatible -> CaptureApiException(
        ServerCaptureSessionApi.INCOMPATIBLE,
        "The capture server sent a response this app cannot read.",
        retryable = false
    )
}

/**
 * Chooses the capture API. Every build uses the server through [ServerCaptureSessionApi];
 * `captureApi=memory` in the ignored `api.local.properties` switches a local build to the
 * labelled [InMemoryCaptureSessionApi] for offline demonstrations.
 */
internal object CaptureApis {
    @Volatile private var override: CaptureSessionApi? = null

    @Volatile private var created: CaptureSessionApi? = null

    private val useMemory: Boolean get() = BuildConfig.OVRLY_CAPTURE_API == MEMORY

    /** True when uploads go to the in-memory server, so status text never claims a real upload. */
    val isTestServer: Boolean get() = override?.isTestServer ?: useMemory

    fun current(context: Context): CaptureSessionApi = override ?: created ?: synchronized(this) {
        created ?: create(context.applicationContext).also { created = it }
    }

    /** Replaces the API for tests; pass null to restore the default. */
    fun replace(api: CaptureSessionApi?) {
        override = api
    }

    private fun create(context: Context): CaptureSessionApi = if (useMemory) {
        InMemoryCaptureSessionApi()
    } else {
        ServerCaptureSessionApi(ApiServices.get(context)?.api?.let(::CaptureApi))
    }

    private const val MEMORY = "memory"
}

/**
 * An in-memory capture server with the BE-06 rules that matter to the client: the chunk grid,
 * idempotent replay by `(session_id, seq)`, conflicts for different bytes, out-of-order gaps,
 * no new chunks after close and a close that cannot change its choice. Used by unit tests and,
 * labelled as a test server, by local builds with `captureApi=memory`.
 */
internal class InMemoryCaptureSessionApi : CaptureSessionApi {
    override val isTestServer: Boolean = true

    /** When true every call fails as a transport error, as if the device were offline. */
    @Volatile var offline: Boolean = false

    /** Number of upcoming chunk uploads that fail with a retryable server error. */
    @Volatile var failingPuts: Int = 0

    private val sessions = mutableMapOf<String, Session>()
    private val keys = mutableMapOf<String, String>()
    private val calls = mutableListOf<String>()

    private class Session(
        val id: String,
        val chunkDurationMs: Int,
        val startedAt: String,
        val chunks: MutableMap<Int, Pair<CaptureChunk, Modality>> = sortedMapOf(),
        var closed: CaptureCloseRequest? = null,
        var closedAt: String? = null
    )

    /** Every call as `open`, `put:<seq>`, `close:<choice>` or `status`, in order. */
    val log: List<String> get() = synchronized(this) { calls.toList() }

    /** The sequences stored for a session. */
    fun storedSeqs(sessionId: String): Set<Int> =
        synchronized(this) { sessions[sessionId]?.chunks?.keys?.toSet().orEmpty() }

    override suspend fun open(
        idempotencyKey: String,
        request: CaptureCreateRequest
    ): CaptureSession = call("open") {
        val existing = keys[idempotencyKey]?.let { sessions.getValue(it) }
        if (existing != null && existing.chunkDurationMs != request.chunkDurationMs) {
            reject("IDEMPOTENCY_KEY_REUSED", "The key names a different capture request")
        }
        val session = existing ?: Session(
            UUID.randomUUID().toString(),
            request.chunkDurationMs,
            now()
        ).also {
            sessions[it.id] = it
            keys[idempotencyKey] = it.id
        }
        sessionModel(session)
    }

    override suspend fun putChunk(
        sessionId: String,
        seq: Int,
        metadata: CaptureMetadata,
        content: ByteArray
    ): CaptureChunk = call("put:$seq") {
        if (failingPuts > 0) {
            failingPuts--
            reject("SERVICE_UNAVAILABLE", "Try again later", retryable = true)
        }
        val session = find(sessionId)
        val chunk = metadata.chunk
        validate(session, seq, metadata, content)
        val existing = session.chunks[seq]
        if (existing != null) {
            val (stored, modality) = existing
            val same = stored.sha256 == chunk.sha256 && stored.interval == chunk.interval
            if (!same || modality != metadata.modality) {
                reject("CAPTURE_CHUNK_CONFLICT", "The sequence has different content")
            }
            return@call stored.copy(
                disposition = ChunkDisposition.DUPLICATE,
                gaps = seqGaps(session.chunks.keys)
            )
        }
        if (session.closed != null) reject("CAPTURE_CLOSED", "The capture is closed")
        val inOrder = session.chunks.size == seq && seqGaps(session.chunks.keys).isEmpty()
        val stored = CaptureChunk(
            sessionId = sessionId,
            seq = seq,
            interval = chunk.interval,
            sizeBytes = chunk.sizeBytes,
            sha256 = chunk.sha256,
            disposition = if (inOrder) ChunkDisposition.STORED else ChunkDisposition.OUT_OF_ORDER,
            receivedAt = now(),
            gaps = emptyList()
        )
        session.chunks[seq] = stored to metadata.modality
        stored.copy(gaps = seqGaps(session.chunks.keys))
    }

    override suspend fun close(sessionId: String, request: CaptureCloseRequest): CaptureSession =
        call("close:${request.continueResearch}") {
            val session = find(sessionId)
            val received = session.chunks.values.maxOfOrNull { it.first.interval.endMs } ?: 0
            val duration = request.durationMs?.toLong() ?: received
            val effective = request.copy(durationMs = duration.toInt())
            val previous = session.closed
            if (previous != null && previous != effective) {
                reject("CAPTURE_CLOSE_CONFLICT", "The capture already has a different close")
            }
            if (duration < received) {
                reject("VALIDATION_FAILED", "Duration truncates chunks")
            }
            if (previous == null) {
                session.closed = effective
                session.closedAt = now()
            }
            sessionModel(session)
        }

    override suspend fun status(sessionId: String): CaptureStatus = call("status") {
        val session = find(sessionId)
        val chunks = session.chunks.values
        fun coverage(vararg kinds: Modality) = chunks.filter { it.second in kinds }
            .map { it.first.interval }
        val duration = session.closed?.durationMs
            ?: chunks.maxOfOrNull { it.first.interval.endMs }?.toInt() ?: 0
        CaptureStatus(
            session = sessionModel(session),
            continueResearch = session.closed?.continueResearch,
            expiresAt = session.startedAt,
            manifest = CaptureManifest(
                durationMs = duration,
                missingIntervals = missingIntervals(
                    session.chunks.keys,
                    session.chunkDurationMs.toLong(),
                    duration.toLong()
                ),
                declaredCoverage = CaptureModalityCoverage(
                    speech = coverage(Modality.SPEECH, Modality.BOTH),
                    text = coverage(Modality.TEXT, Modality.BOTH)
                )
            ),
            work = chunks.map {
                CaptureWork(
                    it.first.seq,
                    null,
                    if (session.closed?.continueResearch == false) {
                        ProcessingStatus.CANCELLED
                    } else {
                        ProcessingStatus.WAITING
                    },
                    null
                )
            },
            claims = emptyList(),
            claimExtractionStatus = CoverageStatus.NOT_STARTED
        )
    }

    private inline fun <T> call(name: String, block: () -> T): T {
        if (offline) throw IOException("The device is offline.")
        return synchronized(this) {
            calls += name
            block()
        }
    }

    private fun validate(
        session: Session,
        seq: Int,
        metadata: CaptureMetadata,
        content: ByteArray
    ) {
        val chunk = metadata.chunk
        val start = seq.toLong() * session.chunkDurationMs
        val end = minOf(start + session.chunkDurationMs, CaptureLimits.LIVE_MS)
        val onGrid = chunk.interval.startMs == start && chunk.interval.endMs <= end
        if (chunk.sessionId != session.id || chunk.seq != seq || !onGrid) {
            reject("VALIDATION_FAILED", "Chunk does not match its sequence")
        }
        if (content.size.toLong() != chunk.sizeBytes || sha256(content) != chunk.sha256) {
            reject("CAPTURE_CHUNK_CONFLICT", "Chunk bytes do not match their declaration")
        }
    }
    private fun find(sessionId: String): Session = sessions[sessionId]
        ?: reject("NOT_FOUND", "The capture does not exist")

    private fun reject(code: String, message: String, retryable: Boolean = false): Nothing =
        throw CaptureApiException(code, message, retryable)

    private fun sessionModel(session: Session): CaptureSession {
        val chunks = session.chunks.values.map { it.first }
        return CaptureSession(
            id = session.id,
            investigationId = session.id,
            state = if (session.closed == null) {
                CaptureSessionState.OPEN
            } else {
                CaptureSessionState.CLOSED
            },
            timebase = Timebase.CAPTURE,
            startedAt = session.startedAt,
            closedAt = session.closedAt,
            maxDurationMs = CaptureLimits.LIVE_MS,
            chunkDurationMs = session.chunkDurationMs.toLong(),
            chunksReceived = chunks.size,
            highestSeq = chunks.maxOfOrNull { it.seq },
            receivedMs = chunks.sumOf { it.interval.endMs - it.interval.startMs },
            gaps = seqGaps(session.chunks.keys),
            duplicateHandling = CaptureSession.DUPLICATE_HANDLING,
            outOfOrderHandling = CaptureSession.OUT_OF_ORDER_HANDLING
        )
    }
}

/** Missing sequences below the highest one, as inclusive ranges. */
private fun seqGaps(seqs: Set<Int>): List<SeqRange> {
    val highest = seqs.maxOrNull() ?: return emptyList()
    val ranges = mutableListOf<SeqRange>()
    for (seq in 0..highest) {
        if (seq in seqs) continue
        val last = ranges.lastOrNull()
        if (last != null && last.toSeq == seq - 1) {
            ranges[ranges.lastIndex] = last.copy(toSeq = seq)
        } else {
            ranges += SeqRange(seq, seq)
        }
    }
    return ranges
}

private fun missingIntervals(seqs: Set<Int>, stepMs: Long, durationMs: Long): List<Interval> =
    (0 until ((durationMs + stepMs - 1) / stepMs).toInt())
        .filter { it !in seqs }
        .map { Interval(it * stepMs, minOf((it + 1) * stepMs, durationMs), Timebase.CAPTURE) }

/** RFC 3339 with millisecond precision, as the contract allows at most six digits. */
private fun now(): String = Instant.now().truncatedTo(ChronoUnit.MILLIS).toString()

private fun sha256(bytes: ByteArray): String =
    MessageDigest.getInstance("SHA-256").digest(bytes).toHexString()
