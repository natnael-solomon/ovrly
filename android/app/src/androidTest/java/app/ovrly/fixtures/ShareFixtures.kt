package app.ovrly.fixtures

import android.net.Uri
import app.ovrly.testing.Device
import org.junit.rules.ExternalResource

/** How [ShareFixtureProvider] describes a file's length to `openAssetFileDescriptor`. */
enum class FixtureLength(val segment: String) {
    DECLARED("declared"),
    UNKNOWN("unknown")
}

/** Whether [ShareFixtureProvider] reports `OpenableColumns.SIZE`. */
enum class FixtureSize(val segment: String) {
    COLUMN("column"),
    NONE("none")
}

/**
 * Builds fixture URIs and grants or revokes them for app.ovrly through the shell, the way a
 * source app's grant reaches a share target. A marker grant makes the provider visible to the
 * app first, so an ungranted URI fails as a permission denial rather than a missing provider.
 * Every grant is revoked after the test.
 */
class ShareFixtures : ExternalResource() {
    private val granted = mutableSetOf<Uri>()

    fun uri(
        file: String,
        type: String = "video/mp4",
        length: FixtureLength = FixtureLength.UNKNOWN,
        size: FixtureSize = FixtureSize.COLUMN
    ): Uri {
        val (top, sub) = type.split('/', limit = 2)
        return Uri.Builder().scheme("content").authority(ShareFixtureProvider.AUTHORITY)
            .appendPath(length.segment).appendPath(size.segment)
            .appendPath(top).appendPath(sub).appendPath(file).build()
    }

    fun grant(uri: Uri): Uri {
        shell(ShareFixtureProvider.GRANT, uri)
        granted += uri
        return uri
    }

    fun revoke(uri: Uri) {
        shell(ShareFixtureProvider.REVOKE, uri)
        granted -= uri
    }

    override fun before() {
        grant(uri(FixtureMedia.TEXT, type = "text/plain"))
    }

    override fun after() {
        granted.toList().forEach(::revoke)
    }

    private fun shell(method: String, uri: Uri) {
        val output = Device.shell(
            "content call --uri content://${ShareFixtureProvider.AUTHORITY} " +
                "--method $method --arg $uri"
        )
        check("Result:" in output && "Exception" !in output) {
            "Shell $method failed for $uri: $output"
        }
    }
}
