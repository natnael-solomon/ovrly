package app.ovrly.voice

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import kotlin.math.max

/**
 * 16 kHz mono PCM16 capture and 24 kHz mono PCM16 playback, as in core.js 0.8.0.
 * Half-duplex transmission plus a 250 ms tail avoids feeding assistant speech back
 * into the server. Hardware AEC is best-effort; speaker acoustics are not guaranteed.
 */
internal class AndroidVoiceAudio(
    context: Context,
    private val diagnostics: VoiceDiagnostics,
) : VoiceAudio {
    private val context = context.applicationContext
    private val manager = this.context.getSystemService(AudioManager::class.java)
    private val lock = Any()
    private val running = AtomicBoolean(false)
    private val queue = ArrayBlockingQueue<Chunk>(8)
    private var recorder: AudioRecord? = null
    private var speaker: AudioTrack? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var focus: AudioFocusRequest? = null
    private var captureThread: Thread? = null
    private var playbackThread: Thread? = null
    private var bufferedBytes = 0
    private var epoch = 0
    private var writtenFrames = 0L
    private var resumeInputAt = 0L
    private var playbackPending = false
    private var failed: (String) -> Unit = {}
    private data class Chunk(val epoch: Int, val bytes: ByteArray)

    @SuppressLint("MissingPermission")
    override fun start(input: (ByteArray) -> Unit, drained: () -> Unit, failed: (String) -> Unit) {
        check(!running.get())
        this.failed = failed
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            reportFailure("Microphone permission is no longer granted.")
            return
        }
        var startupComplete = false
        try {
            val attributes = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(attributes)
                .setAcceptsDelayedFocusGain(false)
                .setOnAudioFocusChangeListener({ change ->
                    if (change != AudioManager.AUDIOFOCUS_GAIN && running.get()) {
                        reportFailure("Audio focus changed. Voice stopped; start again when ready.")
                    }
                }, Handler(Looper.getMainLooper())).build()
            focus = request
            check(manager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
            val inputMinimum = AudioRecord.getMinBufferSize(
                16_000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            )
            val outputMinimum = AudioTrack.getMinBufferSize(
                24_000, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT,
            )
            check(inputMinimum in 1..64_000 && outputMinimum in 1..96_000)
            val record = AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
                .setAudioFormat(format(16_000, AudioFormat.CHANNEL_IN_MONO))
                .setBufferSizeInBytes(max(inputMinimum, 2560)).build()
            recorder = record
            check(record.state == AudioRecord.STATE_INITIALIZED && record.sampleRate == 16_000)
            if (AcousticEchoCanceler.isAvailable()) {
                enableEchoCancellation(record.audioSessionId)
            }
            val track = AudioTrack.Builder().setAudioAttributes(attributes)
                .setAudioFormat(format(24_000, AudioFormat.CHANNEL_OUT_MONO))
                .setBufferSizeInBytes(max(outputMinimum, 4800))
                .setTransferMode(AudioTrack.MODE_STREAM).build()
            speaker = track
            check(track.state == AudioTrack.STATE_INITIALIZED && track.sampleRate == 24_000)
            track.play()
            record.startRecording()
            check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING)
            running.set(true)
            captureThread = thread(name = "ovrly-voice-capture", isDaemon = true) {
                capture(record, input)
            }
            playbackThread = thread(name = "ovrly-voice-playback", isDaemon = true) {
                play(track, drained)
            }
            startupComplete = true
        } catch (cause: SecurityException) {
            reportFailure("Android denied microphone or audio focus access. Check microphone permission.", cause)
        } catch (cause: IllegalArgumentException) {
            reportFailure("This device rejected the required 16 kHz microphone or 24 kHz speaker format.", cause)
        } catch (cause: IllegalStateException) {
            reportFailure("Microphone or speaker could not start. Another app may own audio focus, or the audio device is unavailable.", cause)
        } finally {
            if (!startupComplete) {
                val failures = diagnostics.cleanup("incomplete audio startup" to { close() })
                if (failures.isNotEmpty()) {
                    failed("Audio startup stopped with ${failures.size} cleanup failures; see OvrlyVoice diagnostics.")
                }
            }
        }
    }

    private fun enableEchoCancellation(sessionId: Int) {
        try {
            echoCanceler = AcousticEchoCanceler.create(sessionId)
            val effect = echoCanceler
            if (effect == null || effect.setEnabled(true) != AudioEffect.SUCCESS) {
                diagnostics.warning("Hardware echo cancellation unavailable; half-duplex transmission remains enabled")
            }
        } catch (cause: IllegalArgumentException) {
            diagnostics.warning("Hardware echo cancellation rejected the audio session; using half-duplex transmission", cause)
        } catch (cause: UnsupportedOperationException) {
            diagnostics.warning("Hardware echo cancellation unsupported; using half-duplex transmission", cause)
        } catch (cause: IllegalStateException) {
            diagnostics.warning("Hardware echo cancellation unavailable in this device state; using half-duplex transmission", cause)
        }
    }

    private fun reportFailure(message: String, cause: Throwable? = null) {
        diagnostics.warning(message, cause)
        failed(message)
    }

    private fun capture(record: AudioRecord, input: (ByteArray) -> Unit) {
        val samples = ShortArray(320)
        while (running.get()) {
            val count = try {
                record.read(samples, 0, samples.size, AudioRecord.READ_BLOCKING)
            } catch (cause: IllegalStateException) {
                if (running.get()) reportFailure("The microphone is no longer in a recording state. Voice stopped.", cause)
                else diagnostics.warning("Microphone read ended during requested cleanup", cause)
                return
            } catch (cause: SecurityException) {
                reportFailure("Android revoked microphone access. Voice stopped.", cause)
                return
            }
            if (!running.get()) return
            if (count <= 0) {
                reportFailure("Microphone read failed (audio error $count). Voice stopped.")
                return
            }
            val transmit = synchronized(lock) {
                !playbackPending && SystemClock.elapsedRealtime() >= resumeInputAt
            }
            if (!transmit) continue
            val bytes = ByteArray(count * 2)
            for (i in 0 until count) {
                bytes[i * 2] = samples[i].toByte()
                bytes[i * 2 + 1] = (samples[i].toInt() shr 8).toByte()
            }
            input(bytes)
        }
    }

    override fun enqueue(pcm: ByteArray): Boolean = synchronized(lock) {
        if (!running.get() || pcm.isEmpty() || pcm.size % 2 != 0 ||
            pcm.size > VoiceProtocol.MAX_AUDIO_BYTES || bufferedBytes + pcm.size > 96_000
        ) return false
        if (!queue.offer(Chunk(epoch, pcm))) return false
        bufferedBytes += pcm.size
        playbackPending = true
        true
    }

    private fun play(track: AudioTrack, drained: () -> Unit) {
        while (running.get()) {
            val finished = try {
                val chunk = queue.poll(20, TimeUnit.MILLISECONDS)
                if (chunk != null) {
                    var offset = 0
                    while (running.get() && offset < chunk.bytes.size) {
                        val count = synchronized(lock) {
                            if (chunk.epoch != epoch || !running.get()) -1
                            else track.write(
                                chunk.bytes, offset, minOf(960, chunk.bytes.size - offset),
                                AudioTrack.WRITE_NON_BLOCKING,
                            )
                        }
                        if (count == -1 && synchronized(lock) { chunk.epoch != epoch || !running.get() }) break
                        if (count < 0 || count % 2 != 0) throw IllegalStateException()
                        synchronized(lock) {
                            if (chunk.epoch == epoch) writtenFrames += count / 2
                        }
                        offset += count
                        if (count == 0) Thread.sleep(10)
                    }
                    synchronized(lock) {
                        if (chunk.epoch == epoch) bufferedBytes -= chunk.bytes.size
                    }
                }
                synchronized(lock) {
                    if (running.get() && playbackPending && bufferedBytes == 0 &&
                        (track.playbackHeadPosition.toLong() and 0xffffffffL) >= writtenFrames
                    ) {
                        playbackPending = false
                        resumeInputAt = SystemClock.elapsedRealtime() + 250
                        true
                    } else false
                }
            } catch (cause: InterruptedException) {
                Thread.currentThread().interrupt()
                if (running.get()) reportFailure("Assistant playback was unexpectedly interrupted. Voice stopped.", cause)
                else diagnostics.warning("Playback worker received requested cancellation")
                return
            } catch (cause: IllegalStateException) {
                if (running.get()) reportFailure("The speaker is no longer in a playback state. Voice stopped.", cause)
                else diagnostics.warning("Speaker write ended during requested cleanup", cause)
                return
            } catch (cause: IllegalArgumentException) {
                reportFailure("The speaker rejected the PCM playback buffer. Voice stopped.", cause)
                return
            }
            if (finished) drained()
        }
    }

    override fun interrupt() {
        try {
            synchronized(lock) {
                epoch++
                queue.clear()
                bufferedBytes = 0
                writtenFrames = 0
                playbackPending = false
                resumeInputAt = SystemClock.elapsedRealtime() + 250
                speaker?.let {
                    // stop resets playbackHeadPosition; flush discards all queued speech.
                    it.pause()
                    it.flush()
                    it.stop()
                    if (running.get()) it.play()
                }
            }
        } catch (cause: IllegalStateException) {
            reportFailure("The speaker could not be interrupted safely. Voice stopped.", cause)
        }
    }

    override fun close() {
        running.set(false)
        val oldCaptureThread = captureThread
        val oldPlaybackThread = playbackThread
        captureThread = null
        playbackThread = null
        val oldSpeaker = synchronized(lock) {
            epoch++
            queue.clear()
            bufferedBytes = 0
            playbackPending = false
            speaker.also { speaker = null }
        }
        val oldRecorder = recorder
        val oldEchoCanceler = echoCanceler
        val oldFocus = focus
        recorder = null
        echoCanceler = null
        focus = null
        val failures = diagnostics.cleanup(
            "capture worker" to { oldCaptureThread?.interrupt() },
            "playback worker" to { oldPlaybackThread?.interrupt() },
            "speaker pause" to { oldSpeaker?.pause() },
            "speaker flush" to { oldSpeaker?.flush() },
            "speaker release" to { oldSpeaker?.release() },
            "microphone stop" to {
                if (oldRecorder?.recordingState == AudioRecord.RECORDSTATE_RECORDING) oldRecorder.stop()
            },
            "microphone release" to { oldRecorder?.release() },
            "echo cancellation" to { oldEchoCanceler?.release() },
            "audio focus" to {
                if (oldFocus != null) {
                    check(manager.abandonAudioFocusRequest(oldFocus) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
                }
            },
        )
        if (failures.isNotEmpty()) throw VoiceCleanupException(failures)
    }

    private fun format(rate: Int, channel: Int) = AudioFormat.Builder()
        .setSampleRate(rate).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
        .setChannelMask(channel).build()
}
