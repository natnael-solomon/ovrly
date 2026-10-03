package app.ovrly.voice

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceCommandSessionTest {
    @Test
    fun sessionRejectsPlannedAndUnsupportedActionsWithoutSwitchingTabs() {
        val fixture = Fixture()
        fixture.session.start()
        fixture.transport.listener.message("""{"type":"ready"}""")
        for ((index, action) in VoiceCommandAction.entries.withIndex()) {
            fixture.tool("call-$index", action.wireName, """{"id":"target-1"}""")
        }
        fixture.tool("unknown", "delete_report", """{"id":"target-1"}""")
        fixture.tool("bad", "save_report", """{"id":"target-1","confirmed":true}""")
        val expected = List(5) { "VOICE_ACTION_UNAVAILABLE" } +
            listOf("VOICE_ACTION_UNSUPPORTED", "VOICE_ACTION_INVALID_ARGUMENTS")
        assertEquals(
            expected,
            fixture.transport.sent.map {
                JSONObject(it).getJSONObject("result").getString("code")
            }
        )
        assertTrue(
            fixture.transport.sent.all {
                JSONObject(it).getJSONObject("result").getString("status") == "error"
            }
        )
        assertEquals(0, fixture.opened)
        assertTrue(fixture.session.state.value.active)
        fixture.session.close()
    }

    @Test
    fun exactDuplicateReplaysButChangedTargetStopsSession() {
        val fixture = Fixture()
        fixture.session.start()
        fixture.transport.listener.message("""{"type":"ready"}""")
        fixture.tool("same", "save_report", """{"id":"first"}""")
        fixture.tool("same", "save_report", """{"id":"first"}""")
        assertEquals(2, fixture.transport.sent.size)
        assertEquals(fixture.transport.sent[0], fixture.transport.sent[1])
        fixture.tool("same", "save_report", """{"id":"second"}""")
        assertEquals("Protocol error", fixture.session.state.value.status)
        assertFalse(fixture.session.state.value.active)
        assertEquals(2, fixture.transport.sent.size)
        assertEquals(0, fixture.opened)
        assertTrue(fixture.transport.closed)
    }

    private class Fixture {
        val transport = FakeTransport()
        var opened = 0
        val session = VoiceSession(
            VoiceConfiguration(false, "", "", mock = true),
            { transport },
            { MockVoiceAudio() },
            { true },
            { it() },
            { _, _ -> VoiceCancellation {} },
            {
                opened++
                true
            },
            VoiceDiagnostics {}
        )

        fun tool(id: String, name: String, args: String) {
            transport.listener.message(
                """{"type":"tool_call","id":"$id","name":"$name","args":$args}"""
            )
        }
    }

    private class FakeTransport : VoiceTransport {
        lateinit var listener: VoiceTransport.Listener
        val sent = mutableListOf<String>()
        var closed = false

        override fun start(listener: VoiceTransport.Listener) {
            this.listener = listener
        }

        override fun send(text: String): Boolean {
            if (closed) return false
            sent.add(text)
            return true
        }

        override fun close() {
            closed = true
        }
    }
}
