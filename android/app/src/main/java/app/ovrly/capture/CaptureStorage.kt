package app.ovrly.capture

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import org.json.JSONObject

/** The chunk being recorded: raw audio and frames in a private working directory. */
internal class OpenChunk(val seq: Int, val dir: File) {
    val audioFile = File(dir, "audio.pcm")
    val audio = FileOutputStream(audioFile)
    var audioBytes = 0L
    var rawBytes = 0L

    /** Frames kept in this interval with the text read from them; images are stored apart. */
    val frames = mutableListOf<FrameText>()
}

/**
 * The open chunk on the capture timeline and the chunks held for frames still being read. A
 * chunk whose interval ends while one of its frames is being read is held: it takes no more
 * audio and is sealed once that frame is written. Later chunks queue behind it, so chunks are
 * still sealed in order.
 */
internal class ChunkCursor(private val storage: ChunkStorage) {
    var open: OpenChunk? = null
        private set

    /** Capture times of kept frames whose text is still being read. */
    private val reading = mutableListOf<Long>()

    /** Frames given up on by [expireStale]; a late result for them is ignored. */
    private val expired = mutableSetOf<Long>()
    private val held = ArrayDeque<OpenChunk>()

    fun begin() {
        open = storage.open(0)
    }

    /** Registers a frame being read; false when no capture is open or past 3:00. */
    fun frameStarted(elapsedMs: Long): Boolean {
        if (open == null || elapsedMs >= CaptureLimits.LIVE_MS) return false
        reading += elapsedMs
        return true
    }

    /** Forgets the frame at [elapsedMs] without writing it and seals what it held. */
    fun release(elapsedMs: Long) {
        expired.remove(elapsedMs)
        if (reading.remove(elapsedMs)) sealReleased()
    }

    /**
     * The open chunk for [elapsedMs], sealing or holding the chunks before it. Audio that
     * arrives just after its chunk ended goes into the open chunk ([acceptLate]); a frame does
     * not, because its offset would fall outside the open chunk. Null past the 3-minute limit.
     */
    fun chunkFor(elapsedMs: Long, acceptLate: Boolean): OpenChunk? {
        var chunk = open ?: throw IOException("Capture output is closed.")
        expireStale(elapsedMs)
        val seq = ChunkGrid.seqAt(elapsedMs)
        while (elapsedMs < CaptureLimits.LIVE_MS && chunk.seq < seq) {
            if (held.isNotEmpty() || waitsForFrame(chunk)) {
                chunk.audio.close()
                held.addLast(chunk)
            } else {
                storage.seal(chunk, ChunkGrid.endOf(chunk.seq))
            }
            chunk = storage.open(chunk.seq + 1)
            open = chunk
        }
        return chunk.takeIf {
            elapsedMs < CaptureLimits.LIVE_MS && (acceptLate || it.seq == seq)
        }
    }

    /**
     * Passes [write] the chunk the frame at [elapsedMs] belongs to (held or open), or null when
     * that chunk was already sealed; then seals the held chunks no longer waiting for a frame.
     * A frame that was already given up on is ignored; it was counted as unfinished.
     */
    fun frameDone(elapsedMs: Long, write: (OpenChunk?) -> Unit) {
        if (expired.remove(elapsedMs)) return
        val registered = reading.remove(elapsedMs)
        val seq = ChunkGrid.seqAt(elapsedMs)
        write(held.firstOrNull { it.seq == seq } ?: chunkFor(elapsedMs, acceptLate = false))
        if (registered) sealReleased()
    }

    /**
     * Seals every held chunk and the open chunk up to [durationMs]. Frames still being read
     * are counted as unfinished; their text is lost.
     */
    fun finish(durationMs: Long) {
        storage.unfinishedFrames += reading.size
        reading.clear()
        sealReleased()
        if (durationMs > 0) chunkFor(durationMs - 1, acceptLate = true)
        open?.let { storage.seal(it, maxOf(durationMs, ChunkGrid.startOf(it.seq))) }
        open = null
    }

    fun clear() {
        open?.audio?.close()
        open = null
        held.clear()
        reading.clear()
        expired.clear()
    }

    private fun waitsForFrame(chunk: OpenChunk): Boolean =
        reading.any { ChunkGrid.seqAt(it) == chunk.seq }

    /**
     * Hard bound on holding: a frame still being read [HOLD_LIMIT_MS] after its probe time
     * (ML Kit hung past every timeout, or the reader closed mid-task) is given up on and
     * counted as unfinished, so its chunk seals and uploads do not stall.
     */
    private fun expireStale(nowMs: Long) {
        val stale = reading.filter { nowMs - it > HOLD_LIMIT_MS }
        if (stale.isEmpty()) return
        reading.removeAll(stale)
        expired += stale
        storage.unfinishedFrames += stale.size
        sealReleased()
    }

    /** Seals held chunks, oldest first, until one still waits for a frame. */
    private fun sealReleased() {
        while (held.isNotEmpty() && !waitsForFrame(held.first())) {
            val chunk = held.removeFirst()
            storage.seal(chunk, ChunkGrid.endOf(chunk.seq))
        }
    }

    companion object {
        /** The longest a frame can take: every region timing out, plus a margin. */
        const val HOLD_LIMIT_MS =
            Recognizer.TIMEOUT_MS * TextRegions.MAX_REGIONS + Recognizer.TIMEOUT_MS
    }
}

/**
 * Sealed chunks, the local manifest and the 32 MiB budget of one capture. Only chunks the
 * server already holds are deleted to make room, oldest first; unsent chunks are never deleted.
 */
internal class ChunkStorage(private val root: File, private val ledger: CaptureLedger) {
    var manifest: LocalManifest? = null

    @Volatile var bytes = 0L

    @Volatile var frames = 0

    @Volatile var sealed = 0
    var mediaBytes = 0L
    var droppedFrames = 0
    var cappedFrames = 0
    var failedFrames = 0
    var unfinishedFrames = 0
    private val localFrames = ArrayDeque<File>()

    fun reset() {
        manifest = null
        bytes = 0
        frames = 0
        sealed = 0
        mediaBytes = 0
        droppedFrames = 0
        cappedFrames = 0
        failedFrames = 0
        unfinishedFrames = 0
        localFrames.clear()
    }

    /** Keeps a frame image on the device only, for diagnosis; it is never uploaded. */
    fun keepFrame(jpeg: ByteArray, elapsedMs: Long) {
        ensureRoom(jpeg.size.toLong())
        val dir = File(root, FRAMES_DIR)
        if (!dir.mkdirs() && !dir.isDirectory) throw IOException("Cannot store a frame.")
        val file = File(dir, ChunkPackage.frameName(elapsedMs))
        file.writeBytes(jpeg)
        localFrames.addLast(file)
        bytes += jpeg.size
    }

    /** A chunk's working directory; a held chunk keeps its own until it is sealed. */
    fun open(seq: Int): OpenChunk {
        val dir = File(File(root, OPEN_DIR), seq.toString())
        dir.deleteRecursively()
        if (!dir.mkdirs()) throw IOException("Cannot create private chunk storage.")
        return OpenChunk(seq, dir)
    }

    /** Removes the working directories once no chunk is open or held. */
    fun closeOpenDirs() {
        File(root, OPEN_DIR).deleteRecursively()
    }

    /** Packages [chunk] up to [endMs]; a chunk without media is recorded as skipped. */
    fun seal(chunk: OpenChunk, endMs: Long) {
        chunk.audio.close()
        val current = requireNotNull(manifest)
        val start = ChunkGrid.startOf(chunk.seq)
        val read = chunk.frames.count { it.status != FrameStatus.FAILED }
        val modality = ChunkPackage.modality(chunk.audioBytes, read)
        manifest = if (endMs <= start) {
            current
        } else if (modality == null) {
            current.copy(skippedSeqs = current.skippedSeqs + chunk.seq)
        } else {
            val audio = chunk.audioFile.takeIf { chunk.audioBytes > 0 }
            val packaged =
                ChunkPackage.build(chunk.seq, endMs, audio, chunk.frames, modality)
            // The raw files are deleted right after, so only the difference must fit.
            ensureRoom((packaged.size - chunk.rawBytes).coerceAtLeast(0))
            File(root, SealedChunk.fileName(chunk.seq)).writeBytesAtomically(packaged)
            bytes += packaged.size
            sealed++
            requireNotNull(manifest).let {
                it.copy(
                    chunks = it.chunks + SealedChunk(
                        seq = chunk.seq,
                        startMs = start,
                        endMs = endMs,
                        modality = modality,
                        sizeBytes = packaged.size.toLong(),
                        sha256 = ChunkPackage.sha256(packaged),
                        audioBytes = chunk.audioBytes,
                        frameOffsetsMs = chunk.frames.map { frame -> frame.framePtsMs },
                        observations = chunk.frames.sumOf { frame -> frame.observations.size }
                    )
                )
            }
        }
        chunk.dir.deleteRecursively()
        bytes -= chunk.rawBytes
        save()
    }

    fun ensureRoom(incoming: Long) {
        // Local-only frame images go first; they are never uploaded.
        while (!CaptureLimits.fitsStorage(bytes, incoming) && localFrames.isNotEmpty()) {
            val frame = localFrames.removeFirst()
            val size = frame.length()
            if (frame.delete()) bytes -= size
        }
        if (CaptureLimits.fitsStorage(bytes, incoming)) return
        val current = requireNotNull(manifest)
        val stored = current.chunks.filter { it.seq !in current.evictedSeqs }.map {
            RollingEviction.Stored(it.seq, it.sizeBytes, ledger.isSent(it.seq))
        }
        val evict = RollingEviction.select(stored, bytes, incoming)
            ?: throw StorageLimitException(stored.count { !it.sent })
        evict.forEach { seq ->
            val file = File(root, SealedChunk.fileName(seq))
            val size = file.length()
            if (file.exists() && !file.delete()) throw IOException("Could not delete a sent chunk.")
            bytes -= size
        }
        manifest = current.copy(evictedSeqs = current.evictedSeqs + evict)
        save()
    }

    fun save() {
        val current = manifest ?: return
        File(root, CaptureFiles.MANIFEST).writeTextAtomically(
            current.copy(
                mediaBytes = mediaBytes,
                frames = frames,
                droppedFrames = droppedFrames,
                cappedFrames = cappedFrames,
                failedFrames = failedFrames,
                unfinishedFrames = unfinishedFrames
            ).toJson()
        )
    }

    /** A capture whose process died while recording keeps its sealed chunks. */
    fun finalizeInterrupted(stored: LocalManifest): LocalManifest {
        File(root, OPEN_DIR).deleteRecursively()
        val end = maxOf(
            stored.chunks.maxOfOrNull { it.endMs } ?: 0,
            stored.skippedSeqs.maxOfOrNull { ChunkGrid.endOf(it) } ?: 0
        )
        val finalized = stored.copy(
            finished = true,
            durationMs = end,
            stopReason = "Capture ended unexpectedly; the chunks sealed before that were kept."
        )
        File(root, CaptureFiles.MANIFEST).writeTextAtomically(finalized.toJson())
        return finalized
    }

    private companion object {
        const val OPEN_DIR = "open"
        const val FRAMES_DIR = "frames"
    }
}

/**
 * Upload bookkeeping next to the chunks: which sequences the server holds, the server session,
 * the user's continuation choice, the close and a permanent upload failure. Each is a small
 * file, so the service, the worker and the overlay can each use their own instance.
 *
 * A ledger given a [sessionId] writes only while the local capture is still that session, so
 * a worker that outlives its capture cannot mark a newer capture sent, closed or failed.
 */
internal class CaptureLedger(private val root: File, private val sessionId: String? = null) {
    private val sentDir = File(root, "sent")

    val remoteSessionId: String? get() = read(REMOTE)?.getString("sessionId")
    val choice: Boolean? get() = read(CHOICE)?.getBoolean("continueResearch")
    val closed: Boolean get() = File(root, CLOSED).exists()
    val failure: String? get() = read(FAILURE)?.getString("message")

    /** No further upload will happen: closed, not continued or failed for good. */
    val uploadStopped: Boolean get() = closed || choice == false || failure != null

    /** A choice was made but the session has not been closed yet and has not failed. */
    val closePending: Boolean get() = choice != null && !closed && failure == null

    fun isSent(seq: Int): Boolean = File(sentDir, seq.toString()).exists()

    /** Chunks still on the device that the server does not hold. */
    fun unsent(manifest: LocalManifest): Int = manifest.chunks.count {
        !isSent(it.seq) && File(root, it.fileName).exists()
    }

    fun markSent(seq: Int) {
        requireSession()
        if (!sentDir.mkdirs() && !sentDir.isDirectory) throw IOException("Cannot record upload.")
        File(sentDir, seq.toString()).writeTextAtomically("")
    }

    fun saveRemoteSessionId(sessionId: String) {
        write(REMOTE, JSONObject().put("sessionId", sessionId))
    }

    /** Records the first choice; returns false when a different choice is already recorded. */
    fun recordChoice(continueResearch: Boolean): Boolean = synchronized(LOCK) {
        val existing = choice
        if (existing == null) {
            write(CHOICE, JSONObject().put("continueResearch", continueResearch))
        }
        existing == null || existing == continueResearch
    }

    fun markClosed(continueResearch: Boolean) {
        write(CLOSED, JSONObject().put("continueResearch", continueResearch))
    }

    fun fail(code: String, message: String) {
        write(FAILURE, JSONObject().put("code", code).put("message", message))
    }

    private fun read(name: String): JSONObject? {
        val file = File(root, name)
        return if (file.exists()) JSONObject(file.readText()) else null
    }

    private fun write(name: String, json: JSONObject) {
        requireSession()
        File(root, name).writeTextAtomically(json.toString())
    }

    private fun requireSession() {
        val current = CaptureFiles.manifestOrNull(root)?.sessionId
        if (!root.isDirectory || (sessionId != null && current != sessionId)) {
            throw IOException("The local capture was deleted or replaced.")
        }
    }

    private companion object {
        const val REMOTE = "remote.json"
        const val CHOICE = "choice.json"
        const val CLOSED = "closed.json"
        const val FAILURE = "upload-error.json"
        val LOCK = Any()
    }
}

private fun File.writeBytesAtomically(bytes: ByteArray) {
    val temporary = File(parentFile, "$name.tmp")
    temporary.writeBytes(bytes)
    Files.move(
        temporary.toPath(),
        toPath(),
        StandardCopyOption.REPLACE_EXISTING,
        StandardCopyOption.ATOMIC_MOVE
    )
}

private fun File.writeTextAtomically(text: String) = writeBytesAtomically(text.toByteArray())
