package app.ovrly.testing

import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.test.core.app.ActivityScenario
import app.ovrly.capture.CaptureService
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import org.junit.rules.ExternalResource

/**
 * Keeps an activity in the foreground, as the companion is when capture starts, and asks the
 * system for real screen-capture consent. With `PROJECT_MEDIA` allowed through app ops the
 * system consent activity answers without a dialog, so no UI is automated and nothing but the
 * emulator's own screen is projected.
 */
class ProjectionConsent : ExternalResource() {
    private var scenario: ActivityScenario<ComponentActivity>? = null
    private val keys = AtomicInteger()

    override fun before() {
        Device.wakeAndUnlock()
        Device.shell("appops set ${Device.PACKAGE} PROJECT_MEDIA allow")
        scenario = ActivityScenario.launch(ComponentActivity::class.java)
    }

    override fun after() {
        scenario?.close()
        scenario = null
        Device.shell("appops set ${Device.PACKAGE} PROJECT_MEDIA default")
    }

    /** One fresh consent token; each token can start one projection. */
    fun request(): ActivityResult {
        val result = AtomicReference<ActivityResult>()
        val latch = CountDownLatch(1)
        checkNotNull(scenario).onActivity { activity ->
            val manager = activity.getSystemService(MediaProjectionManager::class.java)
            val launcher = activity.activityResultRegistry.register(
                "consent-${keys.incrementAndGet()}",
                ActivityResultContracts.StartActivityForResult()
            ) {
                result.set(it)
                latch.countDown()
            }
            launcher.launch(manager.createScreenCaptureIntent())
        }
        check(latch.await(CONSENT_TIMEOUT_S, TimeUnit.SECONDS)) { "No consent answer" }
        val answer = result.get()
        check(answer.resultCode == Activity.RESULT_OK && answer.data != null) {
            "Consent was not granted: $answer"
        }
        return answer
    }

    /** The start intent the companion sends after consent. */
    fun startIntent(consent: ActivityResult, resultCode: Int = consent.resultCode): Intent =
        Intent(Device.context, CaptureService::class.java)
            .setAction(CaptureService.START)
            .putExtra(CaptureService.CONSENT, consent.data)
            .putExtra(CaptureService.RESULT_CODE, resultCode)

    private companion object {
        const val CONSENT_TIMEOUT_S = 20L
    }
}
