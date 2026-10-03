package app.ovrly.ui.voice

import app.ovrly.voice.VoiceController
import app.ovrly.voice.VoiceOrbEvent as EngineEvent
import app.ovrly.voice.VoiceState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.shareIn
import kotlinx.coroutines.flow.stateIn

/**
 * Adapts [VoiceController] to [VoiceOrbSource]. Pure mapping; no policy. Starts go through
 * [begin] so the host keeps its capture, demo and microphone-permission guards.
 */
class VoiceOrbAdapter(
    private val controller: VoiceController,
    scope: CoroutineScope,
    private val begin: (hold: Boolean) -> Unit
) : VoiceOrbSource {
    override val state: StateFlow<VoiceOrbState> = controller.state
        .map(::orbState)
        .stateIn(scope, SharingStarted.Eagerly, orbState(controller.state.value))

    override val level: StateFlow<Float> get() = controller.level

    override val events: SharedFlow<VoiceOrbEvent> = controller.events
        .map { event ->
            when (event) {
                EngineEvent.Snap -> VoiceOrbEvent.Snap
                EngineEvent.Onset -> VoiceOrbEvent.Onset
                is EngineEvent.Burst -> VoiceOrbEvent.Burst(event.strength)
                EngineEvent.Interrupt -> VoiceOrbEvent.Interrupt
                EngineEvent.Shake -> VoiceOrbEvent.Shake
            }
        }
        .shareIn(scope, SharingStarted.Eagerly, replay = 0)

    override val configured: Boolean get() = controller.configured
    override val requiresMicrophone: Boolean get() = controller.requiresMicrophone

    override fun start() = begin(false)
    override fun stop() = controller.stop("Voice stopped by you.")
    override fun holdStart() = begin(true)
    override fun holdEnd() = controller.holdEnd()
    override fun interrupt() = controller.interrupt()

    private fun orbState(state: VoiceState) = VoiceOrbState(
        state.status,
        state.message,
        state.active,
        VoiceInteractionPhase.valueOf(state.phase.name)
    )
}
