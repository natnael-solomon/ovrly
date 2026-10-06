package app.ovrly.share

import android.content.Context
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.CancellationException

/** Why a share cannot be checked; [offersFile] adds the permitted-file alternative. */
internal enum class ShareProblem(val title: String, val message: String, val offersFile: Boolean) {
    UNSUPPORTED(
        "This item cannot be checked",
        "ovrly checks one video file or one web link at a time. Share a single video or link.",
        true
    ),
    INVALID_LINK(
        "Link not supported",
        "Share one http or https link to a video, without any other text.",
        true
    ),
    NOT_VIDEO(
        "Not a readable video",
        "This file is not a video ovrly can read.",
        true
    ),
    PRIVATE(
        "Video is private or protected",
        "The app that shared this did not let ovrly read it. It may be private or copy-protected.",
        true
    ),
    EXPIRED(
        "Access expired",
        "Access to this video ended before ovrly saved a private copy. Share it again from the " +
            "source app.",
        true
    ),
    TOO_LONG(
        "Video is longer than 10 minutes",
        "ovrly checks videos of up to 10 minutes across their full length. Nothing was cut.",
        true
    ),
    NO_DURATION(
        "Video length unknown",
        "ovrly could not read this video's length, so the 10-minute limit cannot be checked.",
        true
    ),
    TOO_LARGE(
        "File is over the upload limit",
        "This file is larger than the service accepts. Nothing was cut or uploaded.",
        true
    ),
    UNREADABLE(
        "Could not read the video",
        "The shared video could not be read. Share it again from the source app.",
        true
    ),
    MALFORMED(
        "Share not accepted",
        "The shared item contains malformed Android data. Share it again from the source app.",
        false
    );

    companion object {
        const val FILE_ALTERNATIVE =
            "You can choose a video file you are allowed to share, up to 10 minutes long."
    }
}

/** A private copy of a shared video, made while the URI grant was valid. */
internal class StagedFile(val file: File, val sizeBytes: Long, val sha256: String)

/** The stream grew past the byte cap; the partial copy was deleted. */
internal class StagingLimitExceeded : IOException("Shared video exceeds the upload limit")

/**
 * Copies shared videos into app-private `no_backup` storage, streaming in fixed chunks and
 * hashing on the way so the whole file is never held in memory. The declared size is not
 * trusted: the copy stops as soon as the cap is passed. Each intake owns its own [directory],
 * so one intake never deletes another's copy; [sweepOrphans] removes what an earlier
 * process left behind once nothing waits for it.
 */
internal class ShareStaging(val directory: File) {
    /**
     * Copies [open] into a new file. [cancelled] is polled between chunks; when it turns true
     * the copy stops with a [CancellationException]. Any partial file is deleted.
     */
    fun stage(
        open: () -> InputStream,
        maxBytes: Long,
        cancelled: () -> Boolean = { false },
        progress: (Long) -> Unit = {}
    ): StagedFile {
        directory.mkdirs()
        val target = File(directory, "${UUID.randomUUID()}.part")
        val digest = MessageDigest.getInstance("SHA-256")
        var complete = false
        try {
            val limits = CopyLimits(maxBytes, cancelled, progress)
            val copied = open().use { input ->
                target.outputStream().use { copy(input, it, digest, limits) }
            }
            complete = true
            return StagedFile(target, copied, digest.digest().toHex())
        } finally {
            if (!complete) target.delete()
        }
    }

    private class CopyLimits(
        val maxBytes: Long,
        val cancelled: () -> Boolean,
        val progress: (Long) -> Unit
    )

    private fun copy(
        input: InputStream,
        output: OutputStream,
        digest: MessageDigest,
        limits: CopyLimits
    ): Long {
        val buffer = ByteArray(CHUNK_BYTES)
        var copied = 0L
        var read = input.read(buffer)
        while (read >= 0) {
            if (limits.cancelled()) throw CancellationException("Staging cancelled")
            copied += read
            if (copied > limits.maxBytes) throw StagingLimitExceeded()
            digest.update(buffer, 0, read)
            output.write(buffer, 0, read)
            limits.progress(copied)
            read = input.read(buffer)
        }
        return copied
    }

    /** Removes this intake's directory and every copy in it. */
    fun clear() {
        directory.deleteRecursively()
    }

    companion object {
        private const val CHUNK_BYTES = 64 * 1024
        private const val ROOT = "share-staging"

        /** The directory holding every intake's own staging directory. */
        fun root(context: Context): File = File(context.noBackupFilesDir, ROOT)

        /** A fresh directory for one intake. */
        fun forSession(context: Context): ShareStaging =
            ShareStaging(File(root(context), UUID.randomUUID().toString()))

        /**
         * Deletes entries of [root] last modified before [cutoffMillis], except [keep]: the
         * directories of shares still waiting to be retried. Newer entries belong to live
         * intakes of this process.
         */
        fun sweepOrphans(root: File, cutoffMillis: Long, keep: Set<File> = emptySet()) {
            root.listFiles()
                ?.filter { it.lastModified() < cutoffMillis && it !in keep }
                ?.forEach { it.deleteRecursively() }
        }
    }
}
internal fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

internal fun sha256Hex(text: String): String =
    MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8)).toHex()
