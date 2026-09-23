package app.ovrly.voice

import android.util.Log
import java.io.IOException

internal class VoiceCleanupException(val failures: List<String>) :
    IOException("Voice cleanup reported ${failures.size} resource failures")

internal class VoiceDiagnostics(private val emit: (String) -> Unit) {
    fun warning(event: String, cause: Throwable? = null) {
        // Exception messages and stack traces can contain key-bearing WebSocket URLs.
        emit(if (cause == null) event else "$event [${cause.javaClass.simpleName}]")
    }

    fun cleanup(vararg steps: Pair<String, () -> Unit>): List<String> {
        val failures = mutableListOf<String>()
        fun record(resource: String, cause: Throwable) {
            failures.add(resource)
            warning("Could not release $resource", cause)
        }
        fun release(index: Int) {
            if (index == steps.size) return
            val (resource, action) = steps[index]
            var handled = false
            try {
                action()
                handled = true
            } catch (cause: VoiceCleanupException) {
                failures.addAll(cause.failures.map { "$resource: $it" })
                warning("Incomplete cleanup of $resource", cause)
                handled = true
            } catch (cause: IOException) {
                record(resource, cause)
                handled = true
            } catch (cause: SecurityException) {
                record(resource, cause)
                handled = true
            } catch (cause: IllegalStateException) {
                record(resource, cause)
                handled = true
            } catch (cause: IllegalArgumentException) {
                record(resource, cause)
                handled = true
            } finally {
                // Also attempt remaining resources when an unexpected fault must propagate.
                if (!handled) warning("Unexpected cleanup fault at $resource; propagating after remaining releases")
                release(index + 1)
            }
        }
        try {
            release(0)
        } finally {
            if (failures.isNotEmpty()) warning("Cleanup diagnostics: ${failures.joinToString()}")
        }
        return failures
    }

    companion object {
        fun android() = VoiceDiagnostics { message -> Log.w("OvrlyVoice", message) }
    }
}
