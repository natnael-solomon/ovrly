package app.ovrly.voice

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
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
    /** [output] reports playback RMS from the playback thread every few 20 ms writes. */
    fun start(
        input: (ByteArray) -> Unit,
        drained: () -> Unit,
        failed: (String) -> Unit,
        output: (Float) -> Unit
    )
    fun enqueue(pcm: ByteArray): Boolean
    fun interrupt()

    /** Releases the microphone while playback continues; idempotent. */
    fun stopInput()
}

/**
 * Connection lifecycle and public controls. All transitions, including actions, are serialized
 * on the supplied main dispatcher. Generation checks reject callbacks from cancelled handshakes
 * and previous sessions; turn-taking after ready lives in [VoiceConversation].
 */
internal class VoiceSession(
    private val configuration: VoiceConfiguration,
    private val transportFactory: () -> VoiceTransport,
    private val audioFactory: () -> VoiceAudio,
    private val permissionGranted: () -> Boolean,
    private val dispatch: (() -> Unit) -> Unit,
    private val schedule: (Long, () -> Unit) -> VoiceCancellation,
    private val navigator: VoiceNavigator,
    private val diagnostics: VoiceDiagnostics,
    private val presentation: VoicePresentation =
        VoicePresentation { TimeUnit.NANOSECONDS.toMillis(System.nanoTime()) }
) : AutoCloseable {
    private val mutableState = MutableStateFlow(
        configuration.unavailableState() ?: VoiceState(
            if (configuration.mock) "Offline voice simulation" else "Experimental voice",
            if (configuration.mock) {
                "Offline fixture: Start switches to Explore. No microphone, network or session usage."
            } else {
                "Ready to try Voxide. Native Android support is unverified; " +
                    "starting sends microphone audio to Voxide."
            }
        )
    )
    val state: StateFlow<VoiceState> = mutableState.asStateFlow()
    val level: StateFlow<Float> = presentation.level
    val events: SharedFlow<VoiceOrbEvent> = presentation.events
    private var generation = 0
    private var closed = false
    private var transport: VoiceTransport? = null
    private var deadline: VoiceCancellation? = null
    private var inbox: VoiceInbox? = null
    private var conversation: VoiceConversation? = null

    // Push-to-talk began this connection; releasing before ready cancels it.
    private var heldStart = false

    fun start() = dispatch { begin() }

    /** Push-to-talk. Holding while idle starts voice; releasing before ready cancels it. */
    fun holdStart() = dispatch {
        if (closed) return@dispatch
        val active = conversation
        if (active != null) {
            active.hold(true)
        } else if (!state.value.active) {
            begin()
            heldStart = state.value.active
        }
    }

    fun holdEnd() = dispatch {
        if (closed) return@dispatch
        val active = conversation
        if (active != null) {
            active.hold(false)
        } else if (heldStart && state.value.active) {
            finish("Stopped", "Voice start cancelled.", error = false)
        }
        heldStart = false
    }

    fun interrupt() = dispatch {
        if (!closed) conversation?.interrupt()
    }

    private fun begin() {
        if (closed || state.value.active) return
        val blocked = configuration.unavailableState() ?: VoiceState(
            "Microphone permission needed",
            "Allow microphone access before starting voice."
        ).takeUnless { permissionGranted() }
        if (blocked != null) {
            mutableState.value = blocked
            return
        }
        val token = ++generation
        mutableState.value = VoiceState(
            "Connecting",
            if (configuration.mock) {
                "Running the offline protocol fixture. No microphone or network."
            } else {
                "Checking the experimental Voxide connection. Microphone is still off."
            },
            true,
            VoiceInteractionPhase.CONNECTING
        )
        var started = false
        try {
            transport = transportFactory()
            deadline = schedule(VoiceConfiguration.SETUP_MILLIS) {
                ifCurrent(token) {
                    finish(
                        "Connection timed out",
                        "Voxide did not become ready. Native connections may not be supported."
                    )
                }
            }
            transport?.start(transportListener(token))
            started = true
        } catch (cause: IOException) {
            diagnostics.warning("Voice connection startup failed", cause)
            finish(
                "Connection failed",
                "The voice connection could not start because of a network error."
            )
        } catch (cause: SecurityException) {
            diagnostics.warning("Voice connection permission denied", cause)
            finish(
                "Connection denied",
                "Android denied the voice connection. Check network permissions."
            )
        } finally {
            if (!started && state.value.active) finish("Stopped", "Voice startup did not complete.")
        }
    }

    private fun transportListener(token: Int) = object : VoiceTransport.Listener {
        private val incoming = VoiceInbox(
            dispatch,
            { event -> ifCurrent(token) { receiveEvent(event) } },
            { status, message -> ifCurrent(token) { finish(status, message) } },
            diagnostics
        ).also { inbox = it }

        private val host = object : VoiceHost {
            override val current: Boolean
                get() = !closed && generation == token && mutableState.value.active

            override var state: VoiceState
                get() = mutableState.value
                set(value) {
                    mutableState.value = value
                }

            override fun send(text: String) {
                if (transport?.send(text) != true) {
                    finish(
                        "Connection interrupted",
                        "The voice connection could not accept data. Voice stopped."
                    )
                }
            }

            override fun finish(status: String, message: String, error: Boolean) =
                this@VoiceSession.finish(status, message, error)

            override fun post(action: () -> Unit) = dispatch { ifCurrent(token, action) }

            override fun schedule(milliseconds: Long, action: () -> Unit) =
                this@VoiceSession.schedule(milliseconds) { ifCurrent(token, action) }
        }

        override fun message(text: String) = incoming.message(text)

        override fun failed(message: String) = dispatch {
            ifCurrent(token) { finish("Voice unavailable", message) }
        }

        private fun receiveEvent(event: VoiceEvent) {
            var handled = false
            try {
                route(event)
                handled = true
            } catch (cause: VoiceProtocolException) {
                diagnostics.warning("Rejected Voxide protocol message", cause)
                finish(
                    "Protocol error",
                    "Voxide sent an invalid or oversized message. Voice stopped safely."
                )
                handled = true
            } finally {
                if (!handled) {
                    finish("Stopped", "An unexpected voice fault interrupted the session.")
                }
            }
        }

        private fun route(event: VoiceEvent) {
            val active = conversation
            when {
                event is VoiceEvent.Error -> finish(
                    "Voice unavailable",
                    if (event.usageLimit) {
                        "Voxide usage limit reached."
                    } else {
                        "Voxide reported an error. " +
                            "Check project configuration and native-client support."
                    }
                )

                event == VoiceEvent.Ready -> if (active == null) ready()

                active != null -> active.receive(event)

                event != VoiceEvent.Unknown -> throw VoiceProtocolException()
            }
        }

        private fun ready() {
            if (!permissionGranted()) {
                finish(
                    "Microphone permission needed",
                    "Microphone access was revoked. Voice stopped."
                )
                return
            }
            deadline?.cancel()
            deadline = null
            val created = VoiceConversation(
                configuration,
                host,
                presentation,
                VoiceActions(navigator, diagnostics),
                permissionGranted,
                heldStart
            )
            conversation = created
            created.start(audioFactory())
        }
    }

    fun stop(reason: String = "Voice stopped.") = dispatch {
        if (!closed) {
            if (state.value.active) {
                finish("Stopped", reason.take(240), error = false)
            } else {
                configuration.unavailableState()?.let { mutableState.value = it }
            }
        }
    }

    override fun close() = dispatch {
        if (!closed) {
            try {
                finish("Closed", "Voice closed.", error = false)
            } finally {
                closed = true
            }
        }
    }

    private inline fun ifCurrent(token: Int, block: () -> Unit) {
        if (!closed && generation == token && state.value.active) block()
    }

    private fun finish(status: String, message: String, error: Boolean = true) {
        generation++
        heldStart = false
        inbox?.close()
        inbox = null
        val oldDeadline = deadline
        val oldConversation = conversation
        val oldTransport = transport
        deadline = null
        conversation = null
        transport = null
        var completed = false
        var failures = emptyList<String>()
        try {
            failures = diagnostics.cleanup(
                "session timers" to {
                    oldDeadline?.cancel()
                    oldConversation?.cancelTimers()
                },
                "audio" to { oldConversation?.audio?.close() },
                "transport" to { oldTransport?.close() }
            )
            completed = true
        } finally {
            val suffix = when {
                !completed -> " Cleanup was interrupted by an unexpected fault; see diagnostics."

                failures.isNotEmpty() ->
                    " Cleanup reported ${failures.size} resource failures; " +
                        "see OvrlyVoice diagnostics."

                else -> ""
            }
            mutableState.value = VoiceState(
                status,
                message + suffix,
                phase = if (error) VoiceInteractionPhase.ERROR else VoiceInteractionPhase.IDLE
            )
            presentation.reset()
            if (error) presentation.emit(VoiceOrbEvent.Shake)
        }
    }
}
