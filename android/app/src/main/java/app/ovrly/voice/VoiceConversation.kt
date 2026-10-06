package app.ovrly.voice

import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Session callbacks; posted and scheduled work is dropped once the connection is stale. */
internal interface VoiceHost {
    val current: Boolean
    var state: VoiceState
    fun send(text: String)
    fun finish(status: String, message: String, error: Boolean = true)
    fun post(action: () -> Unit)
    fun schedule(milliseconds: Long, action: () -> Unit): VoiceCancellation
}

/**
 * Turn-taking for one ready connection: half-duplex input, a five-minute input window,
 * a bounded finishing reply, the silence stop and presentation cues. Main thread only.
 */
internal class VoiceConversation(
    private val configuration: VoiceConfiguration,
    private val host: VoiceHost,
    private val presentation: VoicePresentation,
    private val actions: VoiceActions,
    private val permissionGranted: () -> Boolean,
    private var holding: Boolean
) {
    private val timers = VoiceTurnTimers(host::schedule) {
        host.finish("Voice ended", "No speech for 15 seconds. Start again when ready.", false)
    }
    private val endAfterReply = {
        host.finish(
            "Session ended",
            "The five-minute voice limit was reached. Start again when ready.",
            false
        )
    }
    var audio: VoiceAudio? = null
        private set
    private var speaking = false
    private var finishing = false

    // Set by user speech or a tool call; cleared once the assistant reply starts or completes.
    private var awaitingReply = false
    private val acceptingInput get() = !speaking && !finishing
    private val waitingForUser get() = acceptingInput && !awaitingReply && !holding

    fun start(output: VoiceAudio) {
        timers.inputWindow(::beginFinishing)
        host.state = listeningState(configuration, LISTENING)
        presentation.emit(VoiceOrbEvent.Snap)
        audio = output
        output.start(
            input = coalescedInput(host::post, ::microphone),
            drained = { host.post { if (speaking) listening(LISTENING) } },
            failed = { message -> host.post { host.finish("Audio unavailable", message) } },
            output = coalescedLevel(host::post) { if (speaking) presentation.level(it) }
        )
        if (host.current) timers.armSilence(waitingForUser)
    }

    fun receive(event: VoiceEvent) {
        when (event) {
            is VoiceEvent.Audio -> speak(event)

            is VoiceEvent.Tool -> {
                awaitingReply = true
                timers.cancelSilence()
                timers.cancelThinking()
                thinking()
                val accepted = actions.respond(event, finishing) { response ->
                    // Backend answers arrive later; a stale or finished session drops them.
                    if (host.current) {
                        host.send(response)
                        if (host.current) presentation.emit(VoiceOrbEvent.Snap)
                    }
                }
                if (!accepted) {
                    host.finish(
                        "Action limit reached",
                        "Voice stopped after the per-session action limit."
                    )
                }
            }

            is VoiceEvent.Text -> if (event.user) {
                actions.activity.userText(event.text, event.turnComplete)
            }

            VoiceEvent.Interrupted -> {
                audio?.interrupt()
                awaitingReply = false
                if (host.current) {
                    presentation.emit(VoiceOrbEvent.Interrupt)
                    listening("Assistant interrupted. Microphone resumes after the echo guard.")
                }
            }

            VoiceEvent.TurnComplete -> {
                actions.activity.turnComplete()
                awaitingReply = false
                presentation.emit(VoiceOrbEvent.Snap)
                if (!speaking) listening(LISTENING)
            }

            else -> Unit
        }
    }

    /** Push-to-talk keeps continuous listening; it only pauses the silence stop. */
    fun hold(pressed: Boolean) {
        if (pressed && acceptingInput) {
            holding = true
            timers.cancelSilence()
        } else if (!pressed && holding) {
            holding = false
            timers.armSilence(waitingForUser)
        }
    }

    /** User barge-in while the assistant speaks. Untested against the live provider. */
    fun interrupt() {
        if (!speaking) return
        host.send(VoiceProtocol.interrupt())
        if (!host.current) return
        audio?.interrupt()
        awaitingReply = false
        presentation.emit(VoiceOrbEvent.Interrupt)
        listening("Interrupted. Microphone resumes after the echo guard.")
    }

    fun cancelTimers() = timers.cancelAll()

    private fun microphone(pcm: ByteArray, rms: Float, speech: Boolean) {
        if (!permissionGranted()) {
            host.finish(
                "Microphone permission needed",
                "Microphone access was revoked. Voice stopped."
            )
        } else if (acceptingInput) {
            if (speech) {
                if (!awaitingReply) presentation.emit(VoiceOrbEvent.Onset)
                awaitingReply = true
                timers.cancelSilence()
                if (host.state.phase == VoiceInteractionPhase.THINKING) {
                    host.state = listeningState(configuration, LISTENING)
                }
                timers.thinking { if (awaitingReply) thinking() }
            }
            if (host.state.phase == VoiceInteractionPhase.LISTENING) presentation.level(rms)
            host.send(VoiceProtocol.input(pcm))
        }
    }

    private fun speak(event: VoiceEvent.Audio) {
        speaking = true
        // In finishing, keep waiting for turn_complete because chunks may arrive in gaps.
        if (!finishing) awaitingReply = false
        timers.cancelSilence()
        timers.cancelThinking()
        presentation.audio(event.pcm)
        if (!finishing && host.state.phase != VoiceInteractionPhase.SPEAKING) {
            host.state = VoiceState(
                "Speaking",
                "Assistant audio is playing. Microphone transmission is paused.",
                true,
                VoiceInteractionPhase.SPEAKING
            )
        }
        if (audio?.enqueue(event.pcm) != true) {
            host.finish(
                "Audio buffer full",
                "Assistant audio exceeded the bounded playback buffer. Voice stopped."
            )
        }
    }

    private fun listening(message: String) {
        speaking = false
        timers.cancelThinking()
        presentation.quiet()
        if (finishing) {
            if (!awaitingReply) endAfterReply()
        } else {
            host.state = listeningState(configuration, message)
            timers.armSilence(waitingForUser)
        }
    }

    /** Visual hint only: the user spoke or an action ran, and no reply audio has started. */
    private fun thinking() {
        if (!acceptingInput || host.state.phase == VoiceInteractionPhase.THINKING) return
        presentation.quiet()
        host.state = VoiceState(
            "Thinking",
            "Waiting for the assistant. Microphone stays active.",
            true,
            VoiceInteractionPhase.THINKING
        )
    }

    /** At the input cutoff the microphone closes and only an in-flight reply may finish. */
    private fun beginFinishing() {
        finishing = true
        timers.cancelSilence()
        timers.cancelThinking()
        presentation.quiet()
        audio?.stopInput()
        // A reply already playing may continue in later chunks; wait for its completion.
        if (speaking) awaitingReply = true
        if (!awaitingReply) {
            endAfterReply()
        } else {
            host.state = VoiceState(
                "Finishing reply · microphone off",
                "Voice ends after the current reply.",
                true,
                VoiceInteractionPhase.FINISHING
            )
            timers.grace(endAfterReply)
        }
    }

    private companion object {
        const val LISTENING =
            "Microphone active. Ask to open a check, save a report, or cancel, retry or " +
                "continue a check."
    }
}

private fun listeningState(configuration: VoiceConfiguration, message: String) =
    if (configuration.mock) {
        VoiceState(
            "Offline simulation",
            "Offline fixture active. No microphone, network or session usage.",
            true,
            VoiceInteractionPhase.LISTENING
        )
    } else {
        VoiceState("Listening", message, true, VoiceInteractionPhase.LISTENING)
    }

/** Capture-thread callback that keeps at most one main-thread task pending. */
private fun coalescedInput(
    post: (() -> Unit) -> Unit,
    deliver: (ByteArray, Float, Boolean) -> Unit
): (ByteArray) -> Unit {
    val pending = AtomicBoolean(false)
    val heardSpeech = AtomicBoolean(false)
    val latestRms = AtomicInteger(0)
    return { pcm ->
        val rms = VoiceLevel.rms(pcm)
        latestRms.set(rms.toBits())
        if (rms >= VoiceLevel.SPEECH_RMS) heardSpeech.set(true)
        if (pending.compareAndSet(false, true)) {
            post {
                try {
                    deliver(pcm, Float.fromBits(latestRms.get()), heardSpeech.getAndSet(false))
                } finally {
                    pending.set(false)
                }
            }
        }
    }
}

/** Playback-thread level callback that keeps at most one main-thread task pending. */
private fun coalescedLevel(post: (() -> Unit) -> Unit, deliver: (Float) -> Unit): (Float) -> Unit {
    val pending = AtomicBoolean(false)
    val latest = AtomicInteger(0)
    return { rms ->
        latest.set(rms.toBits())
        if (pending.compareAndSet(false, true)) {
            post {
                try {
                    deliver(Float.fromBits(latest.get()))
                } finally {
                    pending.set(false)
                }
            }
        }
    }
}
