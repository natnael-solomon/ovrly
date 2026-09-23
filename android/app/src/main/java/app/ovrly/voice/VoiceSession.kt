package app.ovrly.voice

import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

internal fun interface VoiceCancellation {
    fun cancel()
}

internal interface VoiceTransport : AutoCloseable {
    interface Listener {
        fun message(text: String)
        fun failed(message: String)
    }

    fun start(listener: Listener)
    fun send(text: String): Boolean
}

internal interface VoiceAudio : AutoCloseable {
    fun start(input: (ByteArray) -> Unit, drained: () -> Unit, failed: (String) -> Unit)
    fun enqueue(pcm: ByteArray): Boolean
    fun interrupt()
}

/**
 * All transitions, including actions, are serialized on the supplied main dispatcher.
 * Generation checks reject callbacks from cancelled handshakes and previous sessions.
 */
internal class VoiceSession(
    private val configuration: VoiceConfiguration,
    private val transportFactory: () -> VoiceTransport,
    private val audioFactory: () -> VoiceAudio,
    private val permissionGranted: () -> Boolean,
    private val dispatch: (() -> Unit) -> Unit,
    private val schedule: (Long, () -> Unit) -> VoiceCancellation,
    private val openGallery: () -> Unit,
    private val diagnostics: VoiceDiagnostics,
) : AutoCloseable {
    private val mutableState = MutableStateFlow(configuration.unavailableState() ?: VoiceState(
        "Experimental voice",
        "Ready to try Voxide. Native Android support is unverified; starting sends microphone audio to Voxide.",
    ))
    val state: StateFlow<VoiceState> = mutableState.asStateFlow()
    private var generation = 0
    private var closed = false
    private var ready = false
    private var transport: VoiceTransport? = null
    private var audio: VoiceAudio? = null
    private var deadline: VoiceCancellation? = null
    private var duration: VoiceCancellation? = null
    private val results = mutableMapOf<String, Pair<VoiceEvent.Tool, String>>()

    fun start() = dispatch {
        if (closed || state.value.active) return@dispatch
        configuration.unavailableState()?.let { mutableState.value = it; return@dispatch }
        if (!permissionGranted()) {
            mutableState.value = VoiceState("Microphone permission needed", "Allow microphone access before starting voice.")
            return@dispatch
        }
        val token = ++generation
        val pendingMessages = AtomicInteger()
        val overflow = AtomicBoolean(false)
        mutableState.value = VoiceState("Connecting", "Checking the experimental Voxide connection. Microphone is still off.", true)
        var started = false
        try {
            transport = transportFactory()
            deadline = schedule(30_000) { ifCurrent(token) {
                finish("Connection timed out", "Voxide did not become ready. Native connections may not be supported.")
            } }
            duration = schedule(180_000) { ifCurrent(token) {
                finish("Session ended", "The three-minute voice limit was reached. Start again when ready.")
            } }
            transport?.start(object : VoiceTransport.Listener {
                override fun message(text: String) {
                    if (pendingMessages.incrementAndGet() > 16) {
                        pendingMessages.decrementAndGet()
                        if (overflow.compareAndSet(false, true)) {
                            dispatch { ifCurrent(token) {
                                finish("Message limit reached", "Voxide messages arrived faster than they could be processed. Voice stopped.")
                            } }
                        }
                        return
                    }
                    dispatch {
                        try {
                            ifCurrent(token) {
                                var handled = false
                                try {
                                    receive(VoiceProtocol.parse(text), token)
                                    handled = true
                                } catch (cause: VoiceProtocolException) {
                                    diagnostics.warning("Rejected Voxide protocol message", cause)
                                    finish("Protocol error", "Voxide sent an invalid or oversized message. Voice stopped safely.")
                                    handled = true
                                } finally {
                                    if (!handled) finish("Stopped", "An unexpected voice fault interrupted the session.")
                                }
                            }
                        } finally {
                            pendingMessages.decrementAndGet()
                        }
                    }
                }
                override fun failed(message: String) = dispatch {
                    ifCurrent(token) { finish("Voice unavailable", message) }
                }
            })
            started = true
        } catch (cause: IOException) {
            diagnostics.warning("Voice connection startup failed", cause)
            finish("Connection failed", "The voice connection could not start because of a network error.")
        } catch (cause: SecurityException) {
            diagnostics.warning("Voice connection permission denied", cause)
            finish("Connection denied", "Android denied the voice connection. Check network permissions.")
        } finally {
            if (!started && state.value.active) finish("Stopped", "Voice startup did not complete.")
        }
    }

    fun stop(reason: String = "Voice stopped.") = dispatch {
        if (!closed) {
            if (state.value.active) finish("Stopped", reason.take(240))
            else configuration.unavailableState()?.let { mutableState.value = it }
        }
    }

    override fun close() = dispatch {
        if (!closed) {
            try {
                finish("Closed", "Voice closed.")
            } finally {
                closed = true
            }
        }
    }

    private fun receive(event: VoiceEvent, token: Int) {
        if (event == VoiceEvent.Ready) {
            if (ready) return
            if (!permissionGranted()) {
                finish("Microphone permission needed", "Microphone access was revoked. Voice stopped.")
                return
            }
            ready = true
            deadline?.cancel()
            deadline = null
            mutableState.value = VoiceState("Listening", "Microphone active. Only opening the design gallery is supported.", true)
            audio = audioFactory()
            val pendingInput = AtomicBoolean(false)
            audio?.start(
                input = { pcm ->
                    if (pendingInput.compareAndSet(false, true)) dispatch {
                        try {
                            ifCurrent(token) {
                                if (!permissionGranted()) {
                                    finish("Microphone permission needed", "Microphone access was revoked. Voice stopped.")
                                } else if (ready && state.value.status != "Speaking") {
                                    send(VoiceProtocol.input(pcm))
                                }
                            }
                        } finally {
                            pendingInput.set(false)
                        }
                    }
                },
                drained = { dispatch {
                    ifCurrent(token) {
                        if (state.value.status == "Speaking") {
                            mutableState.value = VoiceState("Listening", "Microphone active. Ask to open the design gallery.", true)
                        }
                    }
                } },
                failed = { message -> dispatch {
                    ifCurrent(token) { finish("Audio unavailable", message) }
                } },
            )
            return
        }
        if (event is VoiceEvent.Error) {
            finish("Voice unavailable", if (event.usageLimit) "Voxide usage limit reached."
                else "Voxide reported an error. Check project configuration and native-client support.")
            return
        }
        if (!ready) {
            if (event != VoiceEvent.Unknown) throw VoiceProtocolException()
            return
        }
        when (event) {
            is VoiceEvent.Audio -> {
                mutableState.value = VoiceState("Speaking", "Assistant audio is playing. Microphone transmission is paused.", true)
                if (audio?.enqueue(event.pcm) != true) {
                    finish("Audio buffer full", "Assistant audio exceeded the bounded playback buffer. Voice stopped.")
                }
            }
            is VoiceEvent.Tool -> execute(event, token)
            VoiceEvent.Interrupted -> {
                audio?.interrupt()
                ifCurrent(token) {
                    mutableState.value = VoiceState("Listening", "Assistant interrupted. Microphone resumes after the echo guard.", true)
                }
            }
            else -> Unit
        }
    }

    private fun execute(tool: VoiceEvent.Tool, token: Int) {
        results[tool.id]?.let { (original, result) ->
            if (tool != original) throw VoiceProtocolException()
            send(result)
            return
        }
        if (results.size >= 32) {
            finish("Action limit reached", "Voice stopped after the per-session action limit.")
            return
        }
        val response = when {
            tool.name != VoiceProtocol.ACTION ->
                VoiceProtocol.toolResult(tool, false, "This action is not allowed. Only open_design_gallery is supported.")
            !tool.validArguments ->
                VoiceProtocol.toolResult(tool, false, "open_design_gallery requires exactly an empty args object.")
            else -> try {
                openGallery()
                VoiceProtocol.toolResult(tool, true, "Design gallery opened.")
            } catch (cause: IllegalStateException) {
                diagnostics.warning("Gallery navigation unavailable in the current activity state", cause)
                VoiceProtocol.toolResult(tool, false, "The design gallery cannot open in the current app state.")
            } catch (cause: SecurityException) {
                diagnostics.warning("Gallery navigation denied", cause)
                VoiceProtocol.toolResult(tool, false, "Android denied opening the design gallery.")
            }
        }
        ifCurrent(token) {
            results[tool.id] = tool to response
            send(response)
        }
    }

    private fun send(text: String) {
        if (transport?.send(text) != true) finish("Connection interrupted", "The voice connection could not accept data. Voice stopped.")
    }

    private inline fun ifCurrent(token: Int, block: () -> Unit) {
        if (!closed && generation == token && state.value.active) block()
    }

    private fun finish(status: String, message: String) {
        generation++
        ready = false
        val oldDeadline = deadline
        val oldDuration = duration
        deadline = null
        duration = null
        val oldAudio = audio
        val oldTransport = transport
        audio = null
        transport = null
        results.clear()
        var completed = false
        var failures = emptyList<String>()
        try {
            failures = diagnostics.cleanup(
                "connection deadline" to { oldDeadline?.cancel() },
                "session deadline" to { oldDuration?.cancel() },
                "audio" to { oldAudio?.close() },
                "transport" to { oldTransport?.close() },
            )
            completed = true
        } finally {
            val suffix = when {
                !completed -> " Cleanup was interrupted by an unexpected fault; see diagnostics."
                failures.isNotEmpty() -> " Cleanup reported ${failures.size} resource failures; see OvrlyVoice diagnostics."
                else -> ""
            }
            mutableState.value = VoiceState(status, message + suffix)
        }
    }
}
