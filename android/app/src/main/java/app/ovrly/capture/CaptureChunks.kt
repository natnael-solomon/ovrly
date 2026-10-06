package app.ovrly.capture

import app.ovrly.contract.Modality
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** The fixed chunk grid on the capture timeline; the server enforces the same grid. */
internal object ChunkGrid {
    val lastSeq: Int = seqAt(CaptureLimits.LIVE_MS - 1)

    fun seqAt(elapsedMs: Long): Int =
        (elapsedMs.coerceIn(0, CaptureLimits.LIVE_MS - 1) / CaptureLimits.CHUNK_MS).toInt()

    fun startOf(seq: Int): Long = seq * CaptureLimits.CHUNK_MS

    fun endOf(seq: Int): Long =
        (startOf(seq) + CaptureLimits.CHUNK_MS).coerceAtMost(CaptureLimits.LIVE_MS)
}

/** Why part of the capture timeline has no media. */
internal enum class GapKind(val wireName: String) {
    /** Playback audio stopped arriving for at least [CaptureLimits.AUDIO_GAP_MS]. */
    INTERRUPTED("interrupted");

    companion object {
        fun fromWire(name: String): GapKind = entries.first { it.wireName == name }
    }
}

/** A capture-relative interval without media, recorded in the local manifest. */
internal data class CaptureGap(val kind: GapKind, val startMs: Long, val endMs: Long) {
    init {
        require(startMs in 0 until endMs && endMs <= CaptureLimits.LIVE_MS) {
            "gap must satisfy 0 <= start < end <= ${CaptureLimits.LIVE_MS}"
        }
    }
}

/** One packaged chunk on the device. Offsets are capture-relative milliseconds. */
internal data class SealedChunk(
    val seq: Int,
    val startMs: Long,
    val endMs: Long,
    val modality: Modality,
    val sizeBytes: Long,
    val sha256: String,
    val audioBytes: Long,
    val frameOffsetsMs: List<Long>
) {
    val fileName: String get() = fileName(seq)

    companion object {
        const val CONTENT_TYPE = "application/zip"
        private const val SEQ_DIGITS = 3
        fun fileName(seq: Int): String = "chunk-${seq.toString().padStart(SEQ_DIGITS, '0')}.zip"
    }
}

/** The local manifest (`capture.json`). It holds no wall-clock or original-video times. */
internal data class LocalManifest(
    val sessionId: String,
    val finished: Boolean = false,
    val durationMs: Long = 0,
    val stopReason: String? = null,
    val playbackSignal: Boolean = false,
    val mediaBytes: Long = 0,
    val frames: Int = 0,
    val droppedFrames: Int = 0,
    val chunks: List<SealedChunk> = emptyList(),
    val skippedSeqs: List<Int> = emptyList(),
    val gaps: List<CaptureGap> = emptyList(),
    val evictedSeqs: List<Int> = emptyList()
) {
    fun toJson(): String = JSONObject()
        .put("manifestVersion", VERSION)
        .put("sessionId", sessionId)
        .put("timebase", "capture")
        .put("format", FORMAT)
        .put("chunkDurationMs", CaptureLimits.CHUNK_MS)
        .put("sampling", ChunkPackage.sampling())
        .put("recognizer", JSONObject.NULL)
        .put("framesUploaded", false)
        .put("finished", finished)
        .put("durationMs", durationMs)
        .put("stopReason", stopReason ?: JSONObject.NULL)
        .put("playbackSignalDetected", playbackSignal)
        .put("mediaBytes", mediaBytes)
        .put("frames", frames)
        .put("droppedFrames", droppedFrames)
        .put("chunks", JSONArray(chunks.map(::chunkJson)))
        .put("skippedSeqs", JSONArray(skippedSeqs))
        .put("gaps", JSONArray(gaps.map(::gapJson)))
        .put("evictedSeqs", JSONArray(evictedSeqs))
        .put("researchConnected", false)
        .toString(2)

    companion object {
        const val VERSION = 2
        const val FORMAT = "Chunks are ZIP files: chunk.json, " +
            "PCM signed 16-bit little-endian mono 16000 Hz audio; sampled frames stay on the device"

        /** A manifest written before chunking (no `manifestVersion`) or by an older version. */
        fun isLegacy(text: String): Boolean = try {
            JSONObject(text).optInt("manifestVersion", 1) < VERSION
        } catch (_: JSONException) {
            false
        }

        fun parse(text: String): LocalManifest {
            val json = JSONObject(text)
            check(json.getInt("manifestVersion") == VERSION) { "Unsupported capture manifest." }
            return LocalManifest(
                sessionId = json.getString("sessionId"),
                finished = json.getBoolean("finished"),
                durationMs = json.getLong("durationMs"),
                stopReason = json.optString("stopReason").takeUnless { json.isNull("stopReason") },
                playbackSignal = json.getBoolean("playbackSignalDetected"),
                mediaBytes = json.getLong("mediaBytes"),
                frames = json.getInt("frames"),
                droppedFrames = json.getInt("droppedFrames"),
                chunks = json.getJSONArray("chunks").objects().map(::parseChunk),
                skippedSeqs = json.getJSONArray("skippedSeqs").ints(),
                gaps = json.getJSONArray("gaps").objects().map {
                    CaptureGap(
                        GapKind.fromWire(it.getString("kind")),
                        it.getLong("startMs"),
                        it.getLong("endMs")
                    )
                },
                evictedSeqs = json.getJSONArray("evictedSeqs").ints()
            )
        }

        private fun chunkJson(chunk: SealedChunk) = JSONObject()
            .put("seq", chunk.seq)
            .put("startMs", chunk.startMs)
            .put("endMs", chunk.endMs)
            .put("modality", chunk.modality.wireName)
            .put("file", chunk.fileName)
            .put("sizeBytes", chunk.sizeBytes)
            .put("sha256", chunk.sha256)
            .put("audioBytes", chunk.audioBytes)
            .put("frameOffsetsMs", JSONArray(chunk.frameOffsetsMs))

        private fun parseChunk(json: JSONObject) = SealedChunk(
            seq = json.getInt("seq"),
            startMs = json.getLong("startMs"),
            endMs = json.getLong("endMs"),
            modality = Modality.fromWire(json.getString("modality")),
            sizeBytes = json.getLong("sizeBytes"),
            sha256 = json.getString("sha256"),
            audioBytes = json.getLong("audioBytes"),
            frameOffsetsMs = json.getJSONArray("frameOffsetsMs").let { offsets ->
                List(offsets.length()) { offsets.getLong(it) }
            }
        )

        private fun gapJson(gap: CaptureGap) = JSONObject()
            .put("kind", gap.kind.wireName)
            .put("startMs", gap.startMs)
            .put("endMs", gap.endMs)

        private fun JSONArray.objects(): List<JSONObject> = List(length()) { getJSONObject(it) }
        private fun JSONArray.ints(): List<Int> = List(length()) { getInt(it) }
    }
}

/**
 * Builds the bytes of one chunk: its audio and a description. Frame images are sampled every
 * [CaptureLimits.FRAME_INTERVAL_MS] and stay on the device; no text is recognized on the device,
 * so `text_observations` is always empty and no recognizer is named.
 */
internal object ChunkPackage {
    const val AUDIO_ENTRY = "audio-16000-mono-s16le.pcm"
    const val MANIFEST_ENTRY = "chunk.json"

    // Fixed entry time (1 January 1980, the ZIP epoch) so no wall-clock time leaves the device.
    private const val ENTRY_TIME = 315_532_800_000L

    /**
     * Only audio is uploaded, so a chunk declares `speech` coverage or nothing. Screen text is
     * not declared: frames are neither recognized nor uploaded.
     */
    fun modality(audioBytes: Long): Modality? = if (audioBytes > 0) Modality.SPEECH else null

    /** The fixed frame-sampling policy, recorded in every manifest and chunk. */
    fun sampling(): JSONObject = JSONObject()
        .put("policy", "fixed")
        .put("interval_ms", CaptureLimits.FRAME_INTERVAL_MS)
        .put("frame_long_edge", CaptureLimits.FRAME_LONG_EDGE)

    fun frameName(offsetMs: Long): String = "frame-${offsetMs}ms.jpg"

    fun build(
        seq: Int,
        endMs: Long,
        audio: File?,
        frameOffsetsMs: List<Long>,
        modality: Modality
    ): ByteArray {
        val output = ByteArrayOutputStream()
        ZipOutputStream(output).use { zip ->
            fun entry(name: String, bytes: ByteArray) {
                zip.putNextEntry(ZipEntry(name).apply { time = ENTRY_TIME })
                zip.write(bytes)
                zip.closeEntry()
            }
            val audioBytes = audio?.takeIf { it.length() > 0 }
            val description = JSONObject()
                .put("seq", seq)
                .put("start_ms", ChunkGrid.startOf(seq))
                .put("end_ms", endMs)
                .put("timebase", "capture")
                .put("modality", modality.wireName)
                .put(
                    "audio",
                    audioBytes?.let {
                        JSONObject()
                            .put("file", AUDIO_ENTRY)
                            .put("encoding", "pcm_s16le")
                            .put("sample_rate", CaptureLimits.SAMPLE_RATE)
                            .put("channels", 1)
                            .put("bytes", it.length())
                    } ?: JSONObject.NULL
                )
                .put("frames_uploaded", false)
                .put("local_frame_pts", JSONArray(frameOffsetsMs))
                .put("text_observations", JSONArray())
                .put("sampling", sampling())
                .put("recognizer", JSONObject.NULL)
            entry(MANIFEST_ENTRY, description.toString().toByteArray())
            audioBytes?.let { entry(AUDIO_ENTRY, it.readBytes()) }
        }
        return output.toByteArray()
    }

    fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).toHexString()
}

/**
 * Rolling deletion under the 32 MiB cap: only chunks already sent may go, oldest first.
 * Returns the sequences to delete, or null when even deleting every sent chunk is not enough.
 */
internal object RollingEviction {
    data class Stored(val seq: Int, val sizeBytes: Long, val sent: Boolean)

    fun select(stored: List<Stored>, currentBytes: Long, incoming: Long): List<Int>? {
        var bytes = currentBytes
        val evict = mutableListOf<Int>()
        for (chunk in stored.filter { it.sent }.sortedBy { it.seq }) {
            if (CaptureLimits.fitsStorage(bytes, incoming)) break
            bytes -= chunk.sizeBytes
            evict += chunk.seq
        }
        return evict.takeIf { CaptureLimits.fitsStorage(bytes, incoming) }
    }
}

/** Detects stretches without playback audio and turns them into [CaptureGap]s. */
internal class AudioGapDetector(private val thresholdMs: Long = CaptureLimits.AUDIO_GAP_MS) {
    private var lastAudioMs = 0L

    @Synchronized
    fun onAudio(elapsedMs: Long): CaptureGap? {
        val gap = gapUntil(elapsedMs)
        lastAudioMs = maxOf(lastAudioMs, elapsedMs)
        return gap
    }

    @Synchronized
    fun onFinish(durationMs: Long): CaptureGap? = gapUntil(durationMs)

    private fun gapUntil(endMs: Long): CaptureGap? {
        val end = endMs.coerceAtMost(CaptureLimits.LIVE_MS)
        return if (end - lastAudioMs >= thresholdMs) {
            CaptureGap(GapKind.INTERRUPTED, lastAudioMs, end)
        } else {
            null
        }
    }
}

class StorageLimitException(unsent: Int = 0) :
    IOException(
        "The 32 MiB local capture limit was reached." +
            if (unsent > 0) {
                " $unsent chunks are saved on device, not yet sent; none of them was deleted."
            } else {
                ""
            }
    )

/**
 * Starting a new capture would delete chunks that are still waiting to be sent, or a
 * continuation choice whose close has not reached the server yet.
 */
class UnsentCaptureException(unsent: Int, closing: Boolean = false) :
    IOException(
        if (unsent > 0) {
            "The previous capture still has $unsent chunks saved on device, not yet sent. "
        } else if (closing) {
            "The previous capture is still sending your continue-research choice. "
        } else {
            "The previous capture still has unsent work. "
        } + "Wait for the upload or delete the local capture first."
    )
