package app.ovrly.capture

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

class CaptureFiles internal constructor(private val root: File) {
    constructor(context: Context) : this(File(context.noBackupFilesDir, "capture"))
    private var audio: FileOutputStream? = null
    @Volatile var bytes: Long = 0
        private set
    @Volatile var frames: Int = 0
        private set

    @Synchronized
    fun begin() {
        delete()
        if (!root.mkdirs() && !root.isDirectory) throw IOException("Cannot create private capture storage.")
        audio = FileOutputStream(File(root, "playback-16000-mono-s16le.pcm"))
    }

    @Synchronized
    fun writeAudio(buffer: ByteArray, length: Int) {
        if (!CaptureLimits.fitsStorage(bytes, length.toLong())) throw StorageLimitException()
        val output = audio ?: throw IOException("Capture output is closed.")
        output.write(buffer, 0, length)
        bytes += length
    }

    @Synchronized
    fun writeFrame(jpeg: ByteArray, elapsedMs: Long) {
        if (!CaptureLimits.fitsStorage(bytes, jpeg.size.toLong())) throw StorageLimitException()
        if (audio == null) throw IOException("Capture output is closed.")
        File(root, "frame-${elapsedMs.coerceAtLeast(0)}ms.jpg").outputStream().use { it.write(jpeg) }
        bytes += jpeg.size
        frames++
    }

    @Synchronized
    fun finish(durationMs: Long, reason: String, signal: Boolean) {
        audio?.close()
        audio = null
        if (!root.isDirectory) return
        val metadata = JSONObject()
            .put("format", "PCM signed 16-bit little-endian, mono, 16000 Hz; sampled JPEG screen frames")
            .put("durationMs", durationMs.coerceIn(0, CaptureLimits.LIVE_MS))
            .put("frameIntervalMs", CaptureLimits.FRAME_INTERVAL_MS)
            .put("frameLongEdge", CaptureLimits.FRAME_LONG_EDGE)
            .put("mediaBytes", bytes)
            .put("frames", frames)
            .put("playbackSignalDetected", signal)
            .put("stopReason", reason)
            .put("researchConnected", false)
        File(root, "capture.json").writeText(metadata.toString(2))
    }

    @Synchronized
    fun delete() {
        audio?.close()
        audio = null
        if (root.exists() && !root.deleteRecursively()) throw IOException("Could not delete local capture.")
        bytes = 0
        frames = 0
    }

    @Synchronized
    fun restoreOrExpire(nowMs: Long): CaptureState? {
        if (!root.exists()) return null
        if (expireIfNeeded(nowMs)) {
            return CaptureState(message = "Expired or interrupted temporary capture was deleted. Research is not connected.")
        }
        val metadataFile = File(root, "capture.json")
        val metadata = JSONObject(metadataFile.readText())
        val duration = metadata.getLong("durationMs")
        val storedBytes = root.listFiles()?.sumOf { it.length() } ?: throw IOException("Cannot inspect capture storage.")
        if (duration !in 0..CaptureLimits.LIVE_MS || storedBytes > CaptureLimits.MAX_BYTES + 4096) {
            throw IOException("Capture metadata or storage exceeds its bound. Delete local capture.")
        }
        return CaptureState(phase = CapturePhase.FINISHED, seconds = (duration / 1000).toInt(),
            bytes = metadata.getLong("mediaBytes"), frames = metadata.getInt("frames"),
            playbackSignal = metadata.getBoolean("playbackSignalDetected"), hasLocalCapture = true,
            message = "Local interval retained temporarily. Research is not connected.")
    }

    @Synchronized
    fun expireIfNeeded(nowMs: Long): Boolean {
        if (!root.exists()) return false
        val metadata = File(root, "capture.json")
        if (!metadata.exists() || nowMs - metadata.lastModified() >= RETENTION_MS) {
            delete()
            return true
        }
        return false
    }

    companion object {
        const val RETENTION_MS = 24 * 60 * 60 * 1000L
    }
}

class StorageLimitException : IOException("The 32 MiB local capture limit was reached.")
