package app.ovrly.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MockVoiceTransportTest {
    @Test
    fun realOfflineFixtureExercisesSessionAndTabActionWithoutLiveResources() {
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
            { opened.add(it) },
            VoiceDiagnostics {}
        )
        session.start()
        assertEquals(listOf(VoiceTab.EXPLORE), opened)
        assertEquals("Offline simulation", session.state.value.status)
        assertTrue(session.state.value.active)
        session.start()
        assertEquals(1, opened.size)
        session.close()
        assertFalse(session.state.value.active)
        assertFalse(transport.send("{}"))
    }

    @Test
    fun stopDuringReadyPreventsSyntheticAction() {
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
