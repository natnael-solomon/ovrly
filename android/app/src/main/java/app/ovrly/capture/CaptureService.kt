package app.ovrly.capture

import android.Manifest
import android.app.Activity
import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import app.ovrly.AppNotifications
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class CaptureService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val running = AtomicBoolean(false)
    private lateinit var files: CaptureFiles
    private var media: CaptureMedia? = null
    private var startedMs = 0L
    private var notifiedText: String? = null
    private var scheduledChunks = 0
    private val sessionLifecycle = CaptureLifecycle()
    private var receiverRegistered = false
    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            finish("Screen capture permission was revoked or interrupted.")
        }
    }
    private val screenOff = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_SCREEN_OFF) {
                finish("Capture stopped when the screen locked.")
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        files = CaptureFiles(this)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            CaptureControl.stopChoice(
                intent.hasExtra(EXTRA_CONTINUE_RESEARCH),
                intent.getBooleanExtra(EXTRA_CONTINUE_RESEARCH, false)
            )?.let { CaptureControl.recordChoice(this, it) }
            if (sessionLifecycle.stage == CaptureLifecycle.Stage.NEW) {
                // Nothing records in this instance; only the choice was delivered.
                sessionLifecycle.stop()
                stopSelf()
            } else {
                finish(CaptureState.STOPPED)
            }
            return START_NOT_STICKY
        }
        if (intent?.action != START) {
            finish("Capture was interrupted. Start again with fresh consent.")
            return START_NOT_STICKY
        }
        if (!sessionLifecycle.begin()) return START_NOT_STICKY
        try {
            ServiceCompat.startForeground(
                this,
                NOTIFICATION_ID,
                notification(this, STARTING_TEXT),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            )
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
                PackageManager.PERMISSION_GRANTED
            ) {
                throw SecurityException("Playback-audio permission is missing.")
            }
            if (getSystemService(KeyguardManager::class.java).isKeyguardLocked) {
                throw SecurityException("Unlock the device before starting capture.")
            }
            val consent = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(CONSENT, Intent::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(CONSENT)
            } ?: throw IllegalArgumentException("Fresh screen capture consent is required.")
            if (intent.getIntExtra(RESULT_CODE, Activity.RESULT_CANCELED) != Activity.RESULT_OK) {
                throw SecurityException("Screen capture consent was not granted.")
            }
            val replaced = files.begin()
            CaptureLive.disconnect()
            val manager = getSystemService(MediaProjectionManager::class.java)
            val projection = manager.getMediaProjection(Activity.RESULT_OK, consent)
                ?: throw IllegalStateException("The system did not grant a projection.")
            val capture =
                CaptureMedia(this, files, running, main, projectionCallback) { why, error ->
                    if (error == null) finish(why) else fail(why, error)
                }
            media = capture
            capture.prepare(projection)
            ContextCompat.registerReceiver(
                this,
                screenOff,
                IntentFilter(Intent.ACTION_SCREEN_OFF),
                ContextCompat.RECEIVER_NOT_EXPORTED
            )
            receiverRegistered = true
            startedMs = SystemClock.elapsedRealtime()
            check(sessionLifecycle.recording()) { "Capture was interrupted during setup." }
            running.set(true)
            CaptureStore.set(
                CaptureState(phase = CapturePhase.RECORDING, message = recordingMessage(replaced))
            )
            capture.start(scope, startedMs)
            startTicker()
        } catch (error: UnsentCaptureException) {
            refuse(error)
        } catch (error: SecurityException) {
            fail("Capture permission or source access was denied.", error)
        } catch (error: IllegalArgumentException) {
            fail("Capture setup is not supported: ${error.message}", error)
        } catch (error: IllegalStateException) {
            fail("Capture could not start: ${error.message}", error)
        } catch (error: IOException) {
            fail("Private capture storage failed: ${error.message}", error)
        } catch (error: UnsupportedOperationException) {
            fail("Playback capture is unavailable on this device.", error)
        }
        return START_NOT_STICKY
    }

    private fun startTicker() {
        scope.launch {
            while (running.get()) {
                val now = SystemClock.elapsedRealtime()
                val stopReason = when {
                    ContextCompat.checkSelfPermission(
                        this@CaptureService,
                        Manifest.permission.RECORD_AUDIO
                    ) != PackageManager.PERMISSION_GRANTED -> PERMISSION_REVOKED

                    CaptureLimits.expired(startedMs, now) -> CaptureMedia.LIMIT_REACHED

                    else -> null
                }
                if (stopReason != null) {
                    finish(stopReason, failed = stopReason == PERMISSION_REVOKED)
                    break
                }
                tick(now)
                delay(TICK_MS)
            }
        }
    }

    /** Seals finished chunks, schedules their upload and refreshes state and notification. */
    private suspend fun tick(now: Long) {
        try {
            withContext(Dispatchers.IO) { files.advance(now - startedMs) }
        } catch (error: StorageLimitException) {
            finish(error.message.orEmpty(), failed = true)
            return
        } catch (error: IOException) {
            fail("Private capture storage failed: ${error.message}", error)
            return
        }
        val session = files.sessionId
        if (session != null && files.sealedChunks != scheduledChunks) {
            scheduledChunks = files.sealedChunks
            CaptureUploads.schedule(this, session)
            progress(this)?.let(CaptureStore::upload)
        }
        CaptureStore.update {
            it.copy(
                seconds = CaptureLimits.elapsedSeconds(startedMs, now),
                bytes = files.bytes,
                frames = files.frames,
                playbackSignal = media?.signal == true
            )
        }
        val state = CaptureStore.state.value
        val text = "%d:%02d / 3:00 · %s".format(
            state.seconds / SECONDS_PER_MINUTE,
            state.seconds % SECONDS_PER_MINUTE,
            state.upload.summary() ?: "The first chunk is sealed after 10 seconds."
        )
        if (text != notifiedText && running.get()) {
            notifiedText = text
            getSystemService(NotificationManager::class.java)
                .notify(NOTIFICATION_ID, notification(this, text))
        }
    }

    private fun refuse(error: UnsentCaptureException) {
        sessionLifecycle.stop()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        val previous = try {
            files.restoreOrExpire(System.currentTimeMillis())
        } catch (_: IOException) {
            null
        }
        CaptureStore.set(
            (previous ?: CaptureState()).copy(
                phase = CapturePhase.ERROR,
                message = error.message.orEmpty()
            )
        )
    }

    private fun fail(message: String, error: Exception) {
        // A late error from a loop that is already stopping is not a capture failure.
        if (sessionLifecycle.stage == CaptureLifecycle.Stage.STOPPED) return
        Log.e("OvrlyCapture", message, error)
        finish(message, failed = true)
    }

    private fun finish(reason: String, failed: Boolean = false) {
        if (!sessionLifecycle.stop()) return
        running.set(false)
        scope.cancel()
        val cleanupErrors = media?.release() ?: mutableListOf()
        if (receiverRegistered) {
            unregisterReceiver(screenOff)
            receiverRegistered = false
        }
        val duration = if (startedMs == 0L) {
            0
        } else {
            (SystemClock.elapsedRealtime() - startedMs).coerceIn(0, CaptureLimits.LIVE_MS)
        }
        val signal = media?.signal == true
        try {
            media?.recordTrailingGap(duration)
            files.finish(duration, reason, signal)
        } catch (error: IOException) {
            Log.e("OvrlyCapture", "Could not finalize private capture", error)
            cleanupErrors += "capture metadata"
        }
        files.sessionId?.takeIf { hasUploadWork(this, files.sealedChunks) }?.let {
            CaptureUploads.schedule(this, it)
        }
        val message = buildString {
            append(reason)
            if (duration > 0 && !signal) append(NO_SIGNAL)
            if (cleanupErrors.isNotEmpty()) {
                append(" Cleanup issue: ${cleanupErrors.joinToString()}. ")
                append("Delete local capture before retrying.")
            }
        }
        CaptureStore.set(
            CaptureState(
                phase = if (failed || cleanupErrors.isNotEmpty()) {
                    CapturePhase.ERROR
                } else {
                    CapturePhase.FINISHED
                },
                seconds = (duration / 1000).toInt(),
                message = message,
                bytes = files.bytes,
                frames = files.frames,
                playbackSignal = signal,
                hasLocalCapture = files.bytes > 0,
                upload = progress(this) ?: UploadProgress()
            )
        )
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        finish("Capture stopped when the companion task was removed.")
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        finish("Capture service stopped. Media access has been released.")
        super.onDestroy()
    }

    companion object {
        const val START = "capture_start"
        const val STOP = "capture_stop"
        const val CONSENT = "consent"
        const val RESULT_CODE = "result_code"

        /** Optional Boolean extra of [STOP]: the user's continue-research choice (overlay, #31). */
        const val EXTRA_CONTINUE_RESEARCH = "app.ovrly.extra.CONTINUE_RESEARCH"
        private const val NOTIFICATION_ID = 101
        private const val TICK_MS = 200L
        private const val SECONDS_PER_MINUTE = 60
        private const val STARTING_TEXT =
            "Screen samples and playback audio in 10-second chunks. Maximum 3 minutes."
        private const val PERMISSION_REVOKED = "Playback-audio permission was revoked."
        private const val NO_SIGNAL = " No playback signal was detected; silence or source " +
            "policy may be responsible. Share a video instead."

        private fun recordingMessage(replaced: Int): String = buildString {
            append("Capturing only this interval. Chunks are sent as they are recorded; ")
            append("research is not connected.")
            if (replaced > 0) {
                append(" The previous capture's $replaced unsent chunks were deleted.")
            }
        }

        private fun notification(context: Context, text: String): Notification =
            AppNotifications.build(
                context,
                "ovrly is capturing",
                text,
                STOP,
                CaptureService::class.java
            )

        /** Nothing is sent or closed for a capture that sealed no chunk and has no choice. */
        private fun hasUploadWork(context: Context, sealedChunks: Int): Boolean =
            sealedChunks > 0 || CaptureLedger(CaptureFiles.rootOf(context)).choice != null

        private fun progress(context: Context): UploadProgress? {
            val root = CaptureFiles.rootOf(context)
            return CaptureFiles.manifestOrNull(root)?.let {
                UploadProgress.of(
                    it,
                    CaptureLedger(root),
                    CaptureApis.isTestServer,
                    wifiOnly = CapturePreferences.wifiOnly(context)
                )
            }
        }
    }
}
