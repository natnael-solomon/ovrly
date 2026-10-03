package app.ovrly.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTransportTraceTest {
    @Test
    fun diagnosticsAreBoundedAndNeverIncludePayloadsOrUnknownTypes() {
        val logs = mutableListOf<String>()
        val trace = VoiceTransportTrace(VoiceDiagnostics(logs::add))
        repeat(1000) {
            trace.sent("""{"type":"audio_input","data":"private-audio"}""")
            trace.received("""{"type":"text","text":"private-speech vox_pub_test"}""")
        }
        trace.received("""{"type":"private-unknown-type","id":"private-id"}""")
        trace.received("private-invalid-json")
        trace.finish()
        trace.finish()
        trace.received("""{"type":"audio"}""")
        assertEquals(4, logs.size)
        assertTrue(logs.last().contains("queued/audio_input=1000"))
        assertTrue(logs.last().contains("received/text=1000"))
        assertTrue(logs.last().contains("received/other=2"))
        assertFalse(logs.any { it.contains("private") || it.contains("vox_pub") })
    }

    @Test
    fun readinessAndToolEventsRemainDistinguishableWithoutTheirContents() {
        val logs = mutableListOf<String>()
        val trace = VoiceTransportTrace(VoiceDiagnostics(logs::add))
        for (type in listOf("ready", "audio", "tool_call", "error", "interrupted")) {
            trace.received("""{"type":"$type","data":"secret","sessionId":"private"}""")
        }
        trace.sent("""{"type":"tool_result","result":"private"}""")
        trace.finish()
        assertEquals(7, logs.size)
        assertTrue(logs.last().contains("received/ready=1"))
        assertTrue(logs.last().contains("received/audio=1"))
        assertTrue(logs.last().contains("queued/tool_result=1"))
        assertFalse(logs.any { it.contains("secret") || it.contains("private") })
    }
}
