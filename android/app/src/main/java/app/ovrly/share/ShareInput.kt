package app.ovrly.share

import android.content.Context
import android.content.Intent
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.os.BadParcelableException
import app.ovrly.capture.CaptureLimits
import java.io.IOException
import java.net.URI

data class SharedInput(
    val title: String,
    val detail: String,
    val accepted: Boolean,
)

object SharePolicy {
    fun webReference(text: String): String? {
        val trimmed = text.trim()
        if (trimmed.length !in 1..4096) return null
        val uri = try { URI(trimmed) } catch (_: java.net.URISyntaxException) { return null }
        if (uri.scheme?.lowercase() !in setOf("https", "http") || uri.host.isNullOrBlank() ||
            uri.userInfo != null || trimmed.any { it.isWhitespace() }) return null
        return trimmed
    }
}

object ShareInputReader {
    fun read(context: Context, intent: Intent): SharedInput {
        return try {
            readChecked(context, intent)
        } catch (_: BadParcelableException) {
            rejected("The shared item contains malformed Android data. Share it again from the source app.")
        }
    }

    private fun readChecked(context: Context, intent: Intent): SharedInput {
        if (intent.action != Intent.ACTION_SEND) return rejected("Only single-item sharing is supported.")
        if (intent.type == "text/plain") {
            val url = SharePolicy.webReference(intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty())
                ?: return rejected("Share one HTTP or HTTPS video URL without additional text.")
            return SharedInput("URL reference received", "$url\n\nThis is a reference, not a downloadable video. No download, upload, queue, or research was started.", true)
        }
        if (intent.type?.startsWith("video/") != true) return rejected("Share a video content URI or a text URL.")
        val uri = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }
        if (uri?.scheme != "content") return rejected("The video must be shared through a readable content URI.")
        try {
            val type = context.contentResolver.getType(uri)
            if (type?.startsWith("video/") != true) return rejected("The provider did not identify this item as a video.")
            context.contentResolver.openAssetFileDescriptor(uri, "r")?.use { descriptor ->
                MediaMetadataRetriever().use { retriever ->
                    if (descriptor.declaredLength < 0) {
                        retriever.setDataSource(descriptor.fileDescriptor)
                    } else {
                        retriever.setDataSource(descriptor.fileDescriptor, descriptor.startOffset, descriptor.declaredLength)
                    }
                    val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
                        ?: return rejected("Video duration is unavailable, so the 10-minute limit cannot be checked.")
                    if (!CaptureLimits.acceptsSharedDuration(duration)) {
                        return rejected("Shared videos must be longer than zero and no longer than 10 minutes.")
                    }
                    return SharedInput("Video reference received", "Duration: ${duration / 1000}s. The provider granted temporary read access for validation only.\n\nNo media was copied, retained, uploaded, queued, or analyzed. Research is not connected.", true)
                }
            }
            return rejected("The provider did not supply readable video data.")
        } catch (_: SecurityException) {
            return rejected("Video access was denied or expired. Share it again from the source app.")
        } catch (_: IOException) {
            return rejected("The shared video could not be read.")
        } catch (_: IllegalArgumentException) {
            return rejected("The provider's video format could not be inspected.")
        } catch (_: IllegalStateException) {
            return rejected("Video metadata inspection was interrupted.")
        }
    }

    private fun rejected(message: String) = SharedInput("Share not accepted", message, false)
}
