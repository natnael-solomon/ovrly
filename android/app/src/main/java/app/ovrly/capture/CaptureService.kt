package app.ovrly.capture

import android.Manifest
import android.app.Activity
import android.app.KeyguardManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.core.graphics.createBitmap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import app.ovrly.AppNotifications
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

class CaptureService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val running = AtomicBoolean(false)
    private val audioLock = Any()
    private val frameLock = Any()
    private var recorder: AudioRecord? = null
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    private var imageThread: HandlerThread? = null
    private lateinit var files: CaptureFiles
    private var startedMs = 0L
    private var lastFrameMs = -CaptureLimits.FRAME_INTERVAL_MS
    @Volatile private var signal = false
    private val sessionLifecycle = CaptureLifecycle()
    private var receiverRegistered = false
    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() { finish("Screen capture permission was revoked or interrupted.") }
    }
    private val screenOff = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_SCREEN_OFF) finish("Capture stopped when the screen locked.")
        }
    }

    override fun onCreate() {
        super.onCreate()
        files = CaptureFiles(this)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            finish("Capture stopped. Research is not connected.")
            return START_NOT_STICKY
        }
        if (intent?.action != START) {
            finish("Capture was interrupted. Start again with fresh consent.")
            return START_NOT_STICKY
        }
        if (!sessionLifecycle.begin()) return START_NOT_STICKY
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID,
                AppNotifications.build(this, "ovrly is capturing", "Screen samples + eligible playback audio. Maximum 3 minutes.", STOP, CaptureService::class.java),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
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
            files.begin()
            val manager = getSystemService(MediaProjectionManager::class.java)
            projection = manager.getMediaProjection(Activity.RESULT_OK, consent)
                ?: throw IllegalStateException("The system did not grant a projection.")
            val currentProjection = requireNotNull(projection)
            currentProjection.registerCallback(projectionCallback, main)
            ContextCompat.registerReceiver(this, screenOff, IntentFilter(Intent.ACTION_SCREEN_OFF), ContextCompat.RECEIVER_NOT_EXPORTED)
            receiverRegistered = true
            prepareScreen(currentProjection)
            prepareAudio(currentProjection)
            startedMs = SystemClock.elapsedRealtime()
            check(sessionLifecycle.recording()) { "Capture was interrupted during setup." }
            running.set(true)
            CaptureStore.set(CaptureState(phase = CapturePhase.RECORDING,
                message = "Capturing only this interval. No research or upload is running."))
            startAudioLoop()
            scope.launch {
                while (running.get()) {
                    val now = SystemClock.elapsedRealtime()
                    if (ContextCompat.checkSelfPermission(this@CaptureService, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        finish("Playback-audio permission was revoked.", failed = true)
                        break
                    }
                    if (CaptureLimits.expired(startedMs, now)) {
                        finish("Stopped at the 3-minute capture limit. Research is not connected.")
                        break
                    }
                    CaptureStore.update { it.copy(seconds = CaptureLimits.elapsedSeconds(startedMs, now),
                        bytes = files.bytes, frames = files.frames, playbackSignal = signal) }
                    delay(200)
                }
            }
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

    @Suppress("DEPRECATION")
    private fun prepareScreen(currentProjection: MediaProjection) {
        val wm = getSystemService(WindowManager::class.java)
        val size = if (Build.VERSION.SDK_INT >= 30) {
            val bounds = wm.maximumWindowMetrics.bounds
            bounds.width() to bounds.height()
        } else {
            val metrics = android.util.DisplayMetrics()
            wm.defaultDisplay.getRealMetrics(metrics)
            metrics.widthPixels to metrics.heightPixels
        }
        val scale = CaptureLimits.FRAME_LONG_EDGE.toFloat() / max(size.first, size.second)
        val width = (size.first * scale).roundToInt().coerceAtLeast(1)
        val height = (size.second * scale).roundToInt().coerceAtLeast(1)
        val thread = HandlerThread("ovrly-screen-samples").also { it.start() }
        imageThread = thread
        val images = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        reader = images
        images.setOnImageAvailableListener({ source ->
            synchronized(frameLock) {
                try {
                    source.acquireLatestImage()?.use { image ->
                        if (!running.get()) return@use
                        val elapsed = (SystemClock.elapsedRealtime() - startedMs).coerceAtLeast(0)
                        if (elapsed >= CaptureLimits.LIVE_MS || elapsed - lastFrameMs < CaptureLimits.FRAME_INTERVAL_MS) return@use
                        val plane = image.planes[0]
                        val paddedWidth = plane.rowStride / plane.pixelStride
                        val padded = createBitmap(paddedWidth, image.height)
                        try {
                            padded.copyPixelsFromBuffer(plane.buffer)
                            val cropped = Bitmap.createBitmap(padded, 0, 0, image.width, image.height)
                            try {
                                val output = ByteArrayOutputStream()
                                if (!cropped.compress(Bitmap.CompressFormat.JPEG, 72, output)) throw IOException("Screen frame encoding failed.")
                                if (running.get()) files.writeFrame(output.toByteArray(), elapsed)
                                lastFrameMs = elapsed
                            } finally {
                                if (cropped !== padded) cropped.recycle()
                            }
                        } finally {
                            padded.recycle()
                        }
                    }
                } catch (error: IOException) {
                    main.post { fail("Screen sampling stopped: ${error.message}", error) }
                } catch (error: IllegalStateException) {
                    main.post { fail("Screen sampling was interrupted.", error) }
                }
            }
        }, Handler(thread.looper))
        // One virtual display per consent token; fixed-size samples can be letterboxed on rotation.
        display = currentProjection.createVirtualDisplay("ovrly-selected-interval", width, height,
            resources.displayMetrics.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            images.surface, null, main)
    }

    private fun prepareAudio(currentProjection: MediaProjection) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            throw SecurityException("Playback-audio permission was revoked during setup.")
        }
        val config = AudioPlaybackCaptureConfiguration.Builder(currentProjection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .excludeUid(android.os.Process.myUid())
            .build()
        val minBuffer = AudioRecord.getMinBufferSize(CaptureLimits.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        check(minBuffer > 0) { "16 kHz playback capture is not supported." }
        val audio = AudioRecord.Builder()
            .setAudioFormat(AudioFormat.Builder().setSampleRate(CaptureLimits.SAMPLE_RATE)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
            .setBufferSizeInBytes(max(minBuffer * 2, 6400))
            .setAudioPlaybackCaptureConfig(config)
            .build()
        recorder = audio
        check(audio.state == AudioRecord.STATE_INITIALIZED) { "Playback capture could not initialize." }
        audio.startRecording()
        check(audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "Playback capture did not start." }
    }

    private fun startAudioLoop() {
        scope.launch(Dispatchers.IO) {
            val buffer = ByteArray(3200)
            try {
                while (running.get()) {
                    if (CaptureLimits.expired(startedMs, SystemClock.elapsedRealtime())) {
                        main.post { finish("Stopped at the 3-minute capture limit. Research is not connected.") }
                        break
                    }
                    val read = synchronized(audioLock) {
                        if (!running.get()) 0 else recorder?.read(buffer, 0, buffer.size, AudioRecord.READ_NON_BLOCKING) ?: 0
                    }
                    if (read < 0) throw IOException("Playback recorder returned error $read.")
                    if (read > 0 && running.get()) {
                        files.writeAudio(buffer, read)
                        for (i in 0 until read - 1 step 2) {
                            val sample = ((buffer[i].toInt() and 0xff) or (buffer[i + 1].toInt() shl 8)).toShort()
                            if (abs(sample.toInt()) > 32) signal = true
                        }
                    }
                    delay(20)
                }
            } catch (error: IOException) {
                main.post { fail("Playback capture stopped: ${error.message}", error) }
            } catch (error: IllegalStateException) {
                main.post { fail("Playback capture was interrupted.", error) }
            } catch (error: SecurityException) {
                main.post { fail("Playback capture permission was revoked.", error) }
            }
        }
    }

    private fun fail(message: String, error: Exception) {
        Log.e("OvrlyCapture", message, error)
        finish(message, failed = true)
    }

    private fun finish(reason: String, failed: Boolean = false) {
        if (!sessionLifecycle.stop()) return
        running.set(false)
        scope.cancel()
        val cleanupErrors = mutableListOf<String>()
        fun release(name: String, block: () -> Unit) {
            try { block() }
            catch (error: IllegalStateException) {
                Log.e("OvrlyCapture", "Could not release $name", error)
                cleanupErrors += name
            } catch (error: SecurityException) {
                Log.e("OvrlyCapture", "Access changed while releasing $name", error)
                cleanupErrors += name
            }
        }
        synchronized(audioLock) {
            recorder?.let { audio ->
                release("playback recorder stop") { if (audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) audio.stop() }
                release("playback recorder") { audio.release() }
            }
            recorder = null
        }
        release("virtual display") { display?.release() }
        display = null
        synchronized(frameLock) {
            release("screen surface") { reader?.close() }
            reader = null
        }
        imageThread?.quitSafely()
        imageThread = null
        release("projection callback") { projection?.unregisterCallback(projectionCallback) }
        release("projection") { projection?.stop() }
        projection = null
        if (receiverRegistered) {
            unregisterReceiver(screenOff)
            receiverRegistered = false
        }
        val duration = if (startedMs == 0L) 0 else (SystemClock.elapsedRealtime() - startedMs).coerceIn(0, CaptureLimits.LIVE_MS)
        try {
            files.finish(duration, reason, signal)
        } catch (error: IOException) {
            Log.e("OvrlyCapture", "Could not finalize private capture", error)
            cleanupErrors += "capture metadata"
        }
        val message = buildString {
            append(reason)
            if (duration > 0 && !signal) append(" No playback signal was detected; silence or source policy may be responsible. Share a video instead.")
            if (cleanupErrors.isNotEmpty()) append(" Cleanup issue: ${cleanupErrors.joinToString()}. Delete local capture before retrying.")
        }
        CaptureStore.set(CaptureState(
            phase = if (failed || cleanupErrors.isNotEmpty()) CapturePhase.ERROR else CapturePhase.FINISHED,
            seconds = (duration / 1000).toInt(), message = message,
            bytes = files.bytes, frames = files.frames, playbackSignal = signal,
            hasLocalCapture = files.bytes > 0,
        ))
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
        private const val NOTIFICATION_ID = 101
    }
}
