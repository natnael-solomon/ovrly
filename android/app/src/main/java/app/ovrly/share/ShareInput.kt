package app.ovrly.share

import android.content.Context
import android.content.Intent
import android.media.MediaMetadataRetriever
import android.media.MediaMetadataRetriever.METADATA_KEY_DURATION
import android.media.MediaMetadataRetriever.METADATA_KEY_HAS_VIDEO
import android.media.MediaMetadataRetriever.METADATA_KEY_MIMETYPE
import android.net.Uri
import android.os.BadParcelableException
import android.os.Build
import android.provider.OpenableColumns
import app.ovrly.capture.CaptureLimits
import app.ovrly.contract.ContractSyntax
import java.io.FileNotFoundException
import java.io.IOException
import java.io.InputStream
import java.net.URI
import java.net.URISyntaxException

/** Settings summary of the latest share; the intake sheet shows the details. */
data class SharedInput(val title: String, val detail: String, val accepted: Boolean)

/** On-device limits checked before any upload. [maxBytes] mirrors the backend upload cap. */
internal data class ShareLimits(
    val maxBytes: Long,
    val maxDurationMs: Long = CaptureLimits.SHARED_MS
)

/** A share that passed the on-device checks. */
internal sealed interface ShareCandidate {
    data class Link(val url: String) : ShareCandidate

    /** [open] streams the bytes while the URI grant is valid; nothing is read up front. */
    class Video(
        val contentType: String,
        val durationMs: Long,
        val sizeBytes: Long?,
        val open: () -> InputStream
    ) : ShareCandidate
}

internal sealed interface ShareRead {
    data class Accepted(val candidate: ShareCandidate) : ShareRead

    data class Rejected(val problem: ShareProblem) : ShareRead
}

/** What the device and the provider report about a shared video. Display names are ignored. */
internal data class VideoFacts(
    val providerType: String?,
    val containerType: String?,
    val hasVideo: Boolean,
    val durationMs: Long?,
    val sizeBytes: Long?
) {
    /** The sniffed container type wins over the provider's claim; never the file name. */
    val contentType: String
        get() = containerType?.takeIf { it.startsWith(VIDEO) } ?: providerType.orEmpty()
}

private const val VIDEO = "video/"

object SharePolicy {
    private val webSchemes = setOf("https", "http")

    /** One http(s) link with a host, no credentials and no surrounding text, or null. */
    fun webReference(text: String): String? = text.trim().takeIf(::isWebReference)

    private fun isWebReference(text: String): Boolean {
        val uri = parse(text)
        return when {
            uri == null -> false
            text.length !in ContractSyntax.MIN_URL_LENGTH..ContractSyntax.MAX_URL_LENGTH -> false
            text.any { it.isWhitespace() } -> false
            uri.scheme?.lowercase() !in webSchemes -> false
            uri.host.isNullOrBlank() -> false
            else -> uri.userInfo == null
        }
    }

    private fun parse(text: String): URI? = try {
        URI(text)
    } catch (_: URISyntaxException) {
        null
    }

    /** The first rule [facts] break, or null when the video may be staged and uploaded. */
    internal fun checkVideo(facts: VideoFacts, limits: ShareLimits): ShareProblem? {
        val duration = facts.durationMs
        val size = facts.sizeBytes
        return when {
            facts.providerType?.startsWith(VIDEO) != true -> ShareProblem.NOT_VIDEO
            facts.containerType?.startsWith(VIDEO) == false -> ShareProblem.NOT_VIDEO
            !facts.hasVideo || !facts.contentType.startsWith(VIDEO) -> ShareProblem.NOT_VIDEO
            duration == null || duration <= 0 -> ShareProblem.NO_DURATION
            duration > limits.maxDurationMs -> ShareProblem.TOO_LONG
            size != null && size > limits.maxBytes -> ShareProblem.TOO_LARGE
            size == 0L -> ShareProblem.UNREADABLE
            else -> null
        }
    }
}

/**
 * Reads a share intent or a picked document into a [ShareRead]. MIME type, video track,
 * duration and size are checked here before anything is copied or uploaded.
 */
internal class ShareInputReader(private val context: Context, private val limits: ShareLimits) {
    fun read(intent: Intent): ShareRead = try {
        readIntent(intent)
    } catch (_: BadParcelableException) {
        ShareRead.Rejected(ShareProblem.MALFORMED)
    }

    /** A video the user picked or shared; only `content` URIs are accepted. */
    fun readVideo(uri: Uri): ShareRead = if (uri.scheme != "content") {
        ShareRead.Rejected(ShareProblem.UNSUPPORTED)
    } else {
        try {
            inspect(uri)
        } catch (_: SecurityException) {
            ShareRead.Rejected(ShareProblem.PRIVATE)
        } catch (_: FileNotFoundException) {
            ShareRead.Rejected(ShareProblem.EXPIRED)
        } catch (_: IOException) {
            ShareRead.Rejected(ShareProblem.UNREADABLE)
        } catch (_: IllegalArgumentException) {
            ShareRead.Rejected(ShareProblem.NOT_VIDEO)
        } catch (_: IllegalStateException) {
            ShareRead.Rejected(ShareProblem.UNREADABLE)
        }
    }

    private fun readIntent(intent: Intent): ShareRead {
        val stream = streamOf(intent)
        return when {
            intent.action != Intent.ACTION_SEND -> ShareRead.Rejected(ShareProblem.UNSUPPORTED)

            intent.type == "text/plain" -> readLink(intent.getStringExtra(Intent.EXTRA_TEXT))

            intent.type?.startsWith(VIDEO) != true || stream == null ->
                ShareRead.Rejected(ShareProblem.UNSUPPORTED)

            else -> readVideo(stream)
        }
    }

    private fun readLink(text: String?): ShareRead = SharePolicy.webReference(text.orEmpty())
        ?.let { ShareRead.Accepted(ShareCandidate.Link(it)) }
        ?: ShareRead.Rejected(ShareProblem.INVALID_LINK)

    private fun streamOf(intent: Intent): Uri? = if (Build.VERSION.SDK_INT >= TIRAMISU) {
        intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
    } else {
        @Suppress("DEPRECATION")
        intent.getParcelableExtra(Intent.EXTRA_STREAM)
    }

    private fun inspect(uri: Uri): ShareRead {
        val resolver = context.contentResolver
        val facts = resolver.openAssetFileDescriptor(uri, "r")?.use { descriptor ->
            MediaMetadataRetriever().use { retriever ->
                if (descriptor.declaredLength < 0) {
                    retriever.setDataSource(descriptor.fileDescriptor)
                } else {
                    retriever.setDataSource(
                        descriptor.fileDescriptor,
                        descriptor.startOffset,
                        descriptor.declaredLength
                    )
                }
                VideoFacts(
                    providerType = resolver.getType(uri),
                    containerType = retriever.metadata(METADATA_KEY_MIMETYPE),
                    hasVideo = retriever.metadata(METADATA_KEY_HAS_VIDEO) == "yes",
                    durationMs = retriever.metadata(METADATA_KEY_DURATION)?.toLongOrNull(),
                    sizeBytes = descriptor.declaredLength.takeIf { it >= 0 } ?: querySize(uri)
                )
            }
        } ?: return ShareRead.Rejected(ShareProblem.UNREADABLE)
        val problem = SharePolicy.checkVideo(facts, limits)
        val duration = facts.durationMs
        return if (problem != null || duration == null) {
            ShareRead.Rejected(problem ?: ShareProblem.NO_DURATION)
        } else {
            ShareRead.Accepted(
                ShareCandidate.Video(facts.contentType, duration, facts.sizeBytes) {
                    resolver.openInputStream(uri) ?: throw FileNotFoundException("No stream")
                }
            )
        }
    }

    private fun MediaMetadataRetriever.metadata(key: Int): String? = extractMetadata(key)

    private fun querySize(uri: Uri): Long? = context.contentResolver
        .query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)
        ?.use { cursor ->
            val column = cursor.getColumnIndex(OpenableColumns.SIZE)
            if (cursor.moveToFirst() && column >= 0 && !cursor.isNull(column)) {
                cursor.getLong(column)
            } else {
                null
            }
        }

    private companion object {
        const val TIRAMISU = 33
    }
}
