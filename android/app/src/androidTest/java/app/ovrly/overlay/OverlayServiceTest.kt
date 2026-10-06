package app.ovrly.overlay

import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.view.View
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import androidx.test.ext.junit.rules.ActivityScenarioRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.ovrly.capture.CaptureState
import app.ovrly.capture.CaptureStore
import app.ovrly.testing.Device
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * [OverlayService] and [OverlayWindow] on an emulator: every way the overlay opens leaves
 * exactly one window, and every way it closes (Hide, stopping the service, the overlay
 * permission refused or revoked) leaves none. Windows are counted from `dumpsys window`, so a
 * leaked window fails the test even when the service believes it is gone.
 */
@RunWith(AndroidJUnit4::class)
class OverlayServiceTest {
    /** The companion is visible whenever the overlay is opened or hidden from it. */
    @get:Rule val companion = ActivityScenarioRule(ComponentActivity::class.java)

    private val context: Context get() = Device.context

    @Before fun allowOverlay() {
        Device.wakeAndUnlock()
        CaptureStore.set(CaptureState())
        setOverlayPermission(true)
    }

    @After fun closeOverlay() {
        context.stopService(Intent(context, OverlayService::class.java))
        Device.await("the overlay to close") { !OverlayStore.visible.value }
        setOverlayPermission(null)
    }

    @Test fun showAndHideLeaveNoWindow() {
        show(OverlayService.SHOW)
        awaitWindows(1)
        assertFalse(OverlayStore.demo.value)

        send(OverlayService.HIDE)

        Device.await("the overlay to hide") { !OverlayStore.visible.value }
        awaitWindows(0)
    }

    @Test fun repeatedShowAndModeChangesKeepOneWindow() {
        show(OverlayService.SHOW)
        awaitWindows(1)

        show(OverlayService.SHOW)
        show(OverlayService.SHOW_DEMO)
        Device.await("demo mode") { OverlayStore.demo.value }
        show(OverlayService.RESET)
        show(OverlayService.SHOW_LIVE_FIXTURE)
        show(OverlayService.SHOW)
        Device.await("compact mode") { !OverlayStore.demo.value }

        assertEquals(1, Device.windowsTitled(TITLE))
        context.stopService(Intent(context, OverlayService::class.java))
        Device.await("the overlay to close") { !OverlayStore.visible.value }
        awaitWindows(0)
    }

    @Test fun deniedOverlayPermissionOpensNoWindow() {
        setOverlayPermission(false)

        // A plain start: the service refuses before it becomes a foreground service.
        send(OverlayService.SHOW)

        Device.await("the denial message") {
            CaptureStore.state.value.message.startsWith("Overlay permission is off.")
        }
        assertFalse(OverlayStore.visible.value)
        assertEquals(0, Device.windowsTitled(TITLE))
    }

    @Test fun overlayPermissionRevokedWhileShownClosesTheWindow() {
        show(OverlayService.SHOW)
        awaitWindows(1)

        setOverlayPermission(false)

        Device.await("the overlay to close after revocation") { !OverlayStore.visible.value }
        assertTrue(
            CaptureStore.state.value.message,
            CaptureStore.state.value.message.startsWith("Overlay permission was revoked.")
        )
        awaitWindows(0)
    }

    @Test fun overlayWindowShowsOnceAndClosesCleanly() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val modes = mutableListOf<BlurMode>()
        var window: OverlayWindow? = null
        instrumentation.runOnMainSync {
            val manager = context.getSystemService(WindowManager::class.java)
            window = OverlayWindow(context, manager, windowParams()) { modes += it }.also {
                it.show(View(context))
                it.appearance(dark = true, higherOpacity = true)
            }
        }
        awaitWindows(1, WINDOW_TITLE)
        assertEquals(BlurMode.OPAQUE, modes.last())

        instrumentation.runOnMainSync { window?.close() }

        awaitWindows(0, WINDOW_TITLE)
    }

    private fun show(action: String) {
        ContextCompat.startForegroundService(
            context,
            Intent(context, OverlayService::class.java).setAction(action)
        )
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
    }

    private fun send(action: String) {
        context.startService(Intent(context, OverlayService::class.java).setAction(action))
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
    }

    private fun awaitWindows(count: Int, title: String = TITLE) {
        Device.await("$count window(s) titled '$title'") { Device.windowsTitled(title) == count }
    }

    /** True allows, false denies, null restores the default overlay permission. */
    private fun setOverlayPermission(allowed: Boolean?) {
        val mode = when (allowed) {
            true -> "allow"
            false -> "deny"
            null -> "default"
        }
        Device.shell("appops set ${Device.PACKAGE} SYSTEM_ALERT_WINDOW $mode")
    }

    private fun windowParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
        PixelFormat.TRANSLUCENT
    ).apply { title = WINDOW_TITLE }

    private companion object {
        const val TITLE = "ovrly capture controls"
        const val WINDOW_TITLE = "ovrly instrumented window"
    }
}
