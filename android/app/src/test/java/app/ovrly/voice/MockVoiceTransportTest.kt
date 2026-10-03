package app.ovrly.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MockVoiceTransportTest {
    @Test
    fun realOfflineFixtureListensWithoutActionsOrLiveResources() {
        val opened = mutableListOf<VoiceTab>()
        val transport = MockVoiceTransport()
        val configuration = VoiceConfiguration(false, "", "", mock = true)
        val session = VoiceSession(
            configuration,
            { transport },
            { MockVoiceAudio() },
            { true },
            { it() },
            { _, _ -> VoiceCancellation {} },
            {
                opened.add(it)
                true
            },
            VoiceDiagnostics {}
        )
        session.start()
        assertTrue("The simulation must never drive app navigation", opened.isEmpty())
        assertEquals("Offline simulation", session.state.value.status)
        assertEquals(VoiceInteractionPhase.LISTENING, session.state.value.phase)
        assertTrue(session.state.value.active)
        session.close()
        assertFalse(session.state.value.active)
        assertFalse(transport.send("{}"))
    }

    @Test
    fun everyBuildWithoutLiveOptInSimulates() {
        assertTrue(voiceSimulated(live = false))
        assertFalse(voiceSimulated(live = true))
    }

    @Test
    fun stopDuringReadyEndsTheScript() {
        val transport = MockVoiceTransport()
        val messages = mutableListOf<String>()
        transport.start(object : VoiceTransport.Listener {
            override fun message(text: String) {
                messages.add(text)
                transport.close()
            }

            override fun failed(message: String) = error(message)
        })
        assertEquals(1, messages.size)
        assertEquals(VoiceEvent.Ready, VoiceProtocol.parse(messages.single()))
    }
}
