package app.ovrly.capture

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import androidx.core.content.ContextCompat
import androidx.core.graphics.createBitmap
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.cancellation.CancellationException
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * The projection-backed media of one capture: sampled screen frames and playback audio,
 * written into [CaptureFiles] on the capture timeline. [onStop] is posted to the main thread
 * with a reason and, for a failure, the error.
 */
internal class CaptureMedia(
    private val context: Context,
    private val files: CaptureFiles,
    private val running: AtomicBoolean,
    private val main: Handler,
    private val projectionCallback: MediaProjection.Callback,
    private val onStop: (String, Exception?) -> Unit
) {
    private val audioLock = Any()
    private val frameLock = Any()
    private val audioGaps = AudioGapDetector()
    private var projection: MediaProjection? = null
    private var recorder: AudioRecord? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    private var imageThread: HandlerThread? = null
    private val sampler = FrameSampler()
    private val text = ScreenTextReader { SystemClock.elapsedRealtime() }

    @Volatile private var startedMs = 0L

    @Volatile var signal = false
        private set

    fun prepare(currentProjection: MediaProjection) {
        projection = currentProjection
        currentProjection.registerCallback(projectionCallback, main)
        prepareScreen(currentProjection)
        prepareAudio(currentProjection)
    }

    fun start(scope: CoroutineScope, startedAt: Long) {
        startedMs = startedAt
        scope.launch(Dispatchers.IO) {
            guardPlaybackLoop({ message, error ->
                if (running.get()) main.post { onStop(message, error) }
            }) {
                readPlayback()
            }
        }
    }

    /** Records a trailing stretch without playback audio before the capture is finished. */
    fun recordTrailingGap(durationMs: Long) {
        if (durationMs > 0) audioGaps.onFinish(durationMs)?.let(files::recordGap)
    }

    /** Releases recorder, display, reader and projection; returns the parts that failed. */
    fun release(): MutableList<String> {
        val cleanupErrors = mutableListOf<String>()
        fun release(name: String, block: () -> Unit) {
            try {
                block()
            } catch (error: IllegalStateException) {
                Log.e(TAG, "Could not release $name", error)
                cleanupErrors += name
            } catch (error: SecurityException) {
                Log.e(TAG, "Access changed while releasing $name", error)
                cleanupErrors += name
            }
        }
        synchronized(audioLock) {
            recorder?.let { audio ->
                release("playback recorder stop") {
                    if (audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) audio.stop()
                }
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
        text.close()
        release("projection callback") { projection?.unregisterCallback(projectionCallback) }
        release("projection") { projection?.stop() }
        projection = null
        return cleanupErrors
    }

    @Suppress("DEPRECATION")
    private fun prepareScreen(currentProjection: MediaProjection) {
        val wm = context.getSystemService(WindowManager::class.java)
        val size = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
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
            try {
                // Only the copy out of the reader holds frameLock; recognition runs after it,
                // so release() never waits for ML Kit.
                val probe = synchronized(frameLock) { source.acquireLatestImage()?.use(::probe) }
                probe?.let { (frame, started) ->
                    // Set as soon as recognition returns, so an error after it never recycles
                    // a frame ML Kit may still be reading.
                    val inUse = AtomicBoolean(false)
                    try {
                        readText(frame, started.elapsedMs, inUse)
                    } finally {
                        started.release()
                        if (!inUse.get()) frame.recycle()
                    }
                }
            } catch (error: IOException) {
                main.post { onStop("Screen sampling stopped: ${error.message}", error) }
            } catch (error: IllegalStateException) {
                main.post { onStop("Screen sampling was interrupted.", error) }
            }
        }, Handler(thread.looper))
        // One virtual display per consent token; fixed-size samples can be letterboxed on rotation.
        display = currentProjection.createVirtualDisplay(
            "ovrly-selected-interval",
            width,
            height,
            context.resources.displayMetrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            images.surface,
            null,
            main
        )
    }

    /**
     * A copy of the frame when a 1 Hz probe is due, with its registered capture time;
     * otherwise null. The registration holds the probe's chunk until it is written or released.
     */
    private fun probe(image: Image): Pair<Bitmap, CaptureFiles.Probe>? {
        val clock = { (SystemClock.elapsedRealtime() - startedMs).coerceAtLeast(0) }
        val elapsed = clock()
        val due = running.get() && elapsed < CaptureLimits.LIVE_MS && sampler.probeDue(elapsed)
        val started = if (due) files.frameStarted(clock) else null
        started ?: return null
        val plane = image.planes[0]
        val padded = createBitmap(plane.rowStride / plane.pixelStride, image.height)
        val frame = try {
            padded.copyPixelsFromBuffer(plane.buffer)
            Bitmap.createBitmap(padded, 0, 0, image.width, image.height)
        } catch (error: IllegalArgumentException) {
            padded.recycle()
            started.release()
            throw IllegalStateException("Screen frame copy failed.", error)
        }
        // createBitmap may return the padded bitmap itself when no crop is needed.
        if (frame !== padded) padded.recycle()
        return frame to started
    }

    /**
     * Applies the change trigger; recognizes kept frames and keeps their image on the device.
     * [inUse] is set when ML Kit may still read [frame] after recognition timed out.
     */
    private fun readText(frame: Bitmap, elapsed: Long, inUse: AtomicBoolean) {
        val result = when (sampler.decide(elapsed, text.thumbnail(frame))) {
            FrameSampler.Decision.CHANGED, FrameSampler.Decision.HEARTBEAT -> {
                var read: FrameText? = null
                try {
                    read = text.read(frame, elapsed)
                } finally {
                    // A frame that could not be read is written as failed, releasing its chunk.
                    if (read == null && running.get()) {
                        val unread = FrameText(elapsed, FrameStatus.FAILED, 0, emptyList())
                        files.writeFrame(null, unread)
                    }
                }
                checkNotNull(read).also { inUse.set(it.frameInUse) }
            }

            FrameSampler.Decision.CAPPED -> FrameText(elapsed, FrameStatus.CAPPED, 0, emptyList())

            // The caller releases the probe's registration.
            FrameSampler.Decision.UNCHANGED -> return
        }
        val jpeg = if (result.status == FrameStatus.CAPPED) {
            null
        } else {
            val output = ByteArrayOutputStream()
            if (!frame.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, output)) {
                if (running.get()) files.writeFrame(null, result)
                throw IOException("Screen frame encoding failed.")
            }
            output.toByteArray()
        }
        // After Stop the capture has already counted this frame as still being read.
        if (running.get()) files.writeFrame(jpeg, result)
    }
    private fun prepareAudio(currentProjection: MediaProjection) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("Playback-audio permission was revoked during setup.")
        }
        val config = AudioPlaybackCaptureConfiguration.Builder(currentProjection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .excludeUid(android.os.Process.myUid())
            .build()
        val minBuffer = AudioRecord.getMinBufferSize(
            CaptureLimits.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        check(minBuffer > 0) { "16 kHz playback capture is not supported." }
        val audio = AudioRecord.Builder()
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(CaptureLimits.SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build()
            )
            .setBufferSizeInBytes(max(minBuffer * 2, MIN_RECORDER_BUFFER))
            .setAudioPlaybackCaptureConfig(config)
            .build()
        recorder = audio
        check(audio.state == AudioRecord.STATE_INITIALIZED) {
            "Playback capture could not initialize."
        }
        audio.startRecording()
        check(audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
            "Playback capture did not start."
        }
    }

    private suspend fun readPlayback() {
        val buffer = ByteArray(READ_BUFFER)
        while (running.get()) {
            val elapsed = SystemClock.elapsedRealtime() - startedMs
            if (elapsed >= CaptureLimits.LIVE_MS) {
                main.post { onStop(LIMIT_REACHED, null) }
                break
            }
            val read = synchronized(audioLock) {
                if (running.get()) {
                    recorder?.read(buffer, 0, buffer.size, AudioRecord.READ_NON_BLOCKING) ?: 0
                } else {
                    0
                }
            }
            if (read < 0) throw IOException("Playback recorder returned error $read.")
            if (read > 0 && running.get()) {
                audioGaps.onAudio(elapsed)?.let(files::recordGap)
                files.writeAudio(buffer, read, elapsed)
                if (hasSignal(buffer, read)) signal = true
            }
            delay(READ_INTERVAL_MS)
        }
    }

    private fun hasSignal(buffer: ByteArray, read: Int): Boolean = (0 until read - 1 step 2).any {
        val low = buffer[it].toInt() and BYTE_MASK
        val sample = (low or (buffer[it + 1].toInt() shl Byte.SIZE_BITS)).toShort()
        abs(sample.toInt()) > SIGNAL_THRESHOLD
    }

    companion object {
        const val LIMIT_REACHED =
            "Stopped at the 3-minute capture limit. Research is not connected."
        private const val TAG = "OvrlyCapture"
        private const val JPEG_QUALITY = 72
        private const val READ_BUFFER = 3_200
        private const val MIN_RECORDER_BUFFER = 6_400
        private const val READ_INTERVAL_MS = 20L
        private const val BYTE_MASK = 0xff
        private const val SIGNAL_THRESHOLD = 32
    }
}

/**
 * Runs the playback loop and reports real failures. Stopping cancels the loop's coroutine,
 * and its `CancellationException` (an `IllegalStateException`) is rethrown, never reported.
 */
internal suspend fun guardPlaybackLoop(
    report: (String, Exception) -> Unit,
    block: suspend () -> Unit
) {
    try {
        block()
    } catch (error: CancellationException) {
        throw error
    } catch (error: IOException) {
        report("Playback capture stopped: ${error.message}", error)
    } catch (error: IllegalStateException) {
        report("Playback capture was interrupted.", error)
    } catch (error: SecurityException) {
        report("Playback capture permission was revoked.", error)
    }
}
