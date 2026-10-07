package app.ovrly.capture

import android.content.Context
import java.io.File
import java.io.IOException
import java.util.UUID
import org.json.JSONException

/**
 * Private storage of one capture as sequenced chunks. The writer methods are used by
 * [CaptureService] only; upload state lives in [CaptureLedger] files so the upload worker
 * and the overlay can update it without sharing this object.
 */
class CaptureFiles internal constructor(private val root: File, private val context: Context?) {
    constructor(context: Context) : this(rootOf(context), context.applicationContext)
    internal constructor(root: File) : this(root, null)

    private val ledger = CaptureLedger(root)
    private val storage = ChunkStorage(root, ledger)
    private val cursor = ChunkCursor(storage)

    /** Bytes currently stored on the device for this capture. */
    val bytes: Long get() = storage.bytes
    val frames: Int get() = storage.frames
    val sealedChunks: Int get() = storage.sealed
    val sessionId: String? get() = storage.manifest?.sessionId

    /**
     * Starts a new capture, replacing the previous one. Throws [UnsentCaptureException] instead
     * of deleting chunks that are still waiting to be sent. Returns the number of unsent chunks
     * of a previous capture whose upload had already stopped and that were deleted.
     */
    @Synchronized
    fun begin(): Int {
        val replaced = manifestOrNull(root)?.let { ledger.unsent(it) } ?: 0
        if ((replaced > 0 && !ledger.uploadStopped) || ledger.closePending) {
            throw UnsentCaptureException(replaced, ledger.closePending)
        }
        delete()
        if (!root.mkdirs() && !root.isDirectory) {
            throw IOException("Cannot create private capture storage.")
        }
        storage.manifest = LocalManifest(UUID.randomUUID().toString())
        storage.save()
        cursor.begin()
        return replaced
    }

    @Synchronized
    fun writeAudio(buffer: ByteArray, length: Int, elapsedMs: Long) {
        val chunk = cursor.chunkFor(elapsedMs, acceptLate = true) ?: return
        storage.ensureRoom(length.toLong())
        chunk.audio.write(buffer, 0, length)
        chunk.audioBytes += length
        chunk.rawBytes += length
        storage.bytes += length
        storage.mediaBytes += length
    }

    /**
     * Takes a probe's capture time from [elapsed] and registers it in one locked step, so no
     * audio write or tick can seal its chunk in between. Until the frame is written with
     * [writeFrame] or the probe is released, the chunk is held at its interval's end instead
     * of being sealed without the frame's text. Null when no capture is open or past 3:00.
     */
    @Synchronized
    internal fun frameStarted(elapsed: () -> Long): Probe? {
        val elapsedMs = elapsed()
        return if (cursor.frameStarted(elapsedMs)) Probe(elapsedMs) else null
    }

    /** A registered probe; [release] it when it is not kept or fails before it is written. */
    internal inner class Probe(val elapsedMs: Long) {
        /** Drops the registration; a no-op once the frame was written. Nothing is counted. */
        fun release() {
            synchronized(this@CaptureFiles) { cursor.release(elapsedMs) }
        }
    }

    /**
     * Records one frame the sampler wanted: its text goes into the chunk of
     * [FrameText.framePtsMs], held for it if that interval already ended, and the image [jpeg]
     * stays on the device only. A capped frame is only counted. A frame for a chunk that was
     * already sealed (it was never registered with [frameStarted]) is dropped and counted;
     * one that arrives after [finish] was already counted as unfinished.
     */
    @Synchronized
    internal fun writeFrame(jpeg: ByteArray?, text: FrameText) {
        if (cursor.open == null) return
        if (text.status == FrameStatus.CAPPED) {
            storage.cappedFrames++
            return
        }
        val elapsedMs = text.framePtsMs
        cursor.frameDone(elapsedMs) { chunk ->
            if (chunk == null) {
                if (elapsedMs < CaptureLimits.LIVE_MS) storage.droppedFrames++
            } else {
                jpeg?.let { storage.keepFrame(it, elapsedMs) }
                chunk.frames += text
                storage.frames++
                if (text.status == FrameStatus.FAILED) storage.failedFrames++
            }
        }
    }

    /** Seals every chunk whose interval ended before [elapsedMs]. */
    @Synchronized
    fun advance(elapsedMs: Long) {
        if (cursor.open != null && elapsedMs < CaptureLimits.LIVE_MS) {
            cursor.chunkFor(elapsedMs, acceptLate = true)
        }
    }

    @Synchronized
    internal fun recordGap(gap: CaptureGap) {
        storage.manifest = storage.manifest?.let { it.copy(gaps = it.gaps + gap) }
    }

    @Synchronized
    fun finish(durationMs: Long, reason: String, signal: Boolean) {
        val duration = durationMs.coerceIn(0, CaptureLimits.LIVE_MS)
        if (cursor.open != null) {
            // Frames still being read at Stop or 3:00 are lost; they are counted, not dropped.
            cursor.finish(duration)
        }
        storage.closeOpenDirs()
        storage.manifest = storage.manifest?.copy(
            finished = true,
            durationMs = duration,
            stopReason = reason,
            playbackSignal = signal
        )
        storage.save()
    }

    @Synchronized
    fun delete() {
        cursor.clear()
        if (root.exists() && !root.deleteRecursively()) {
            throw IOException("Could not delete local capture.")
        }
        storage.reset()
    }

    @Synchronized
    fun restoreOrExpire(nowMs: Long): CaptureState? {
        if (!root.exists()) return null
        if (expireIfNeeded(nowMs)) {
            return CaptureState(message = EXPIRED_MESSAGE)
        }
        if (LocalManifest.isLegacy(File(root, MANIFEST).readText())) {
            // A single-file capture from before chunking: never uploadable, so it is replaced.
            delete()
            return CaptureState(message = LEGACY_MESSAGE)
        }
        val stored = readManifestOrFail(root)
        val restored = if (stored.finished) stored else storage.finalizeInterrupted(stored)
        val storedBytes = root.walkBottomUp().filter { it.isFile }.sumOf { it.length() }
        if (restored.durationMs !in 0..CaptureLimits.LIVE_MS ||
            storedBytes > CaptureLimits.MAX_BYTES + METADATA_ALLOWANCE
        ) {
            throw IOException(
                "Capture metadata or storage exceeds its bound. Delete local capture."
            )
        }
        if (context != null && ledger.unsent(restored) > 0 && !ledger.uploadStopped) {
            CaptureUploads.schedule(context, restored.sessionId)
        }
        return CaptureState(
            phase = CapturePhase.FINISHED,
            seconds = (restored.durationMs / MS_PER_SECOND).toInt(),
            bytes = storedBytes,
            frames = restored.frames,
            playbackSignal = restored.playbackSignal,
            hasLocalCapture = true,
            message = "Local interval retained temporarily.",
            upload = UploadProgress.of(
                restored,
                ledger,
                CaptureApis.isTestServer,
                wifiOnly = context?.let(CapturePreferences::wifiOnly) == true
            )
        )
    }

    @Synchronized
    fun expireIfNeeded(nowMs: Long): Boolean {
        if (!root.exists()) return false
        val metadata = File(root, MANIFEST)
        if (!metadata.exists() || nowMs - metadata.lastModified() >= RETENTION_MS) {
            delete()
            return true
        }
        return false
    }

    companion object {
        const val RETENTION_MS = 24 * 60 * 60 * 1000L
        internal const val MANIFEST = "capture.json"
        private const val METADATA_ALLOWANCE = 64 * 1024L
        private const val MS_PER_SECOND = 1_000
        private const val LEGACY_MESSAGE =
            "A local capture from an earlier app version was deleted."
        private const val EXPIRED_MESSAGE =
            "Expired or interrupted temporary capture was deleted."

        internal fun rootOf(context: Context): File = File(context.noBackupFilesDir, "capture")

        internal fun readManifest(root: File): LocalManifest =
            LocalManifest.parse(File(root, MANIFEST).readText())

        /** The local manifest; an unreadable or unsupported one is an [IOException]. */
        private fun readManifestOrFail(root: File): LocalManifest = try {
            readManifest(root)
        } catch (error: JSONException) {
            throw IOException("Capture metadata is unreadable. Delete local capture.", error)
        } catch (error: IllegalStateException) {
            throw IOException("Capture metadata is unsupported. Delete local capture.", error)
        }

        /** The local manifest, or null when there is none or it cannot be read. */
        internal fun manifestOrNull(root: File): LocalManifest? = try {
            readManifest(root)
        } catch (_: IOException) {
            null
        } catch (_: JSONException) {
            null
        } catch (_: IllegalStateException) {
            null
        }
    }
}
