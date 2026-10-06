package app.ovrly.testing

import android.content.Context
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry

/** Shell access, polling and window inspection for instrumented tests on an emulator. */
object Device {
    const val PACKAGE = "app.ovrly"
    private const val POLL_MS = 100L

    val context: Context get() = InstrumentationRegistry.getInstrumentation().targetContext

    /** Runs [command] as the shell user; arguments are split on spaces, never interpreted. */
    fun shell(command: String): String {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        return ParcelFileDescriptor.AutoCloseInputStream(automation.executeShellCommand(command))
            .use { it.readBytes().decodeToString() }
    }

    /** Polls [condition] until it holds, or fails naming [what] after [timeoutMs]. */
    fun await(what: String, timeoutMs: Long = 15_000, condition: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        while (!condition()) {
            check(SystemClock.elapsedRealtime() < deadline) { "Timed out waiting for $what" }
            SystemClock.sleep(POLL_MS)
        }
    }

    /** Windows the window manager currently holds with [title], across every app. */
    fun windowsTitled(title: String): Int = shell("dumpsys window windows").lineSequence()
        .count { it.contains("Window{") && it.contains(" $title}") }

    fun wakeAndUnlock() {
        shell("input keyevent KEYCODE_WAKEUP")
        shell("wm dismiss-keyguard")
        shell("svc power stayon true")
    }
}
