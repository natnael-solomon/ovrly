package app.ovrly.voice

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSessionTest {
    @Test fun mockConfigurationDoesNotNeedAKey() {
        val fixture = Fixture(
            configuration = VoiceConfiguration(false, "", "", mock = true)
        ).started()
        assertEquals("Offline simulation", fixture.session.state.value.status)
        assertTrue(fixture.session.state.value.message.contains("No microphone"))
        fixture.session.stop()
    }

    @Test fun disabledMissingAndInvalidConfigurationNeverAcquireResources() {
        for (configuration in listOf(
            VoiceConfiguration(false, "https://example.invalid", "vox_pub_test"),
            VoiceConfiguration(true, "https://example.invalid", ""),
            VoiceConfiguration(true, "http://example.invalid", "vox_pub_test"),
            VoiceConfiguration(true, "https://example.invalid", "vox_sk_secret")
        )) {
            val fixture = Fixture(configuration)
            fixture.session.start()
            assertFalse(fixture.session.state.value.active)
            assertEquals(0, fixture.transportCreations)
            assertEquals(0, fixture.audio.starts)
        }
    }

    @Test fun permissionIsCheckedBeforeNetworkAndAgainBeforeCapture() {
        val fixture = Fixture()
        fixture.permission = false
        fixture.session.start()
        assertEquals(0, fixture.transportCreations)
        fixture.permission = true
        fixture.session.start()
        fixture.permission = false
        fixture.ready()
        assertFalse(fixture.session.state.value.active)
        assertEquals(0, fixture.audio.starts)
        assertEquals(1, fixture.transport.closes)
    }

    @Test fun microphoneWaitsForProviderReadyAndStartIsIdempotent() {
        val fixture = Fixture()
        fixture.session.start()
        fixture.session.start()
        assertEquals(1, fixture.transportCreations)
        assertEquals("Connecting", fixture.session.state.value.status)
        assertTrue(fixture.session.state.value.active)
        assertEquals(0, fixture.audio.starts)
        fixture.ready()
        fixture.ready()
        assertEquals("Listening", fixture.session.state.value.status)
        assertEquals(1, fixture.audio.starts)
        assertTrue(fixture.timers.first().cancelled)
    }

    @Test fun onlyKnownTabsRunAndResultsReflectSuccess() {
        val fixture = Fixture().started()
        fixture.tool("bad", "save_account")
        fixture.tool("args", args = """{"anything":1}""")
        fixture.tool("settings", args = """{"tab":"settings"}""")
        fixture.tool("extra", args = """{"tab":"explore","x":1}""")
        fixture.tool("empty", args = "{}")
        fixture.tool("okay")
        assertEquals(1, fixture.navigations)
        assertEquals(VoiceTab.EXPLORE, fixture.current)
        assertEquals(
            listOf("error", "error", "error", "error", "error", "success"),
            fixture.transport.sent.map {
                JSONObject(it).getJSONObject("result").getString("status")
            }
        )
        assertEquals("okay", JSONObject(fixture.transport.sent.last()).getString("id"))
        assertEquals(
            "Switched to Explore.",
            JSONObject(fixture.transport.sent.last()).getJSONObject("result").getString("result")
        )
    }

    @Test fun openingTheCurrentTabRepliesAlreadyThere() {
        val fixture = Fixture().started()
        fixture.current = VoiceTab.SPACE
        fixture.tool("same-tab", args = """{"tab":"space"}""")
        fixture.tool("other-tab", args = """{"tab":"explore"}""")
        val results = fixture.transport.sent.map { JSONObject(it).getJSONObject("result") }
        assertEquals(listOf("success", "success"), results.map { it.getString("status") })
        assertTrue(results[0].getString("result").startsWith("The user is already on Your space."))
        assertEquals("Switched to Explore.", results[1].getString("result"))
        assertEquals(VoiceTab.EXPLORE, fixture.current)
    }

    @Test fun throwingActionReportsFailureNotPretendSuccessOrExceptionDetails() {
        val fixture = Fixture().started()
        fixture.throwOnOpen = true
        fixture.tool("failure")
        val result = JSONObject(fixture.transport.sent.single()).getJSONObject("result")
        assertEquals("error", result.getString("status"))
        assertFalse(result.toString().contains("sensitive"))
        assertTrue(fixture.session.state.value.active)
    }

    @Test fun duplicateToolIdIsIdempotentButChangedIdPayloadStopsSession() {
        val fixture = Fixture().started()
        fixture.tool("same")
        fixture.tool("same")
        assertEquals(1, fixture.navigations)
        assertEquals(2, fixture.transport.sent.size)
        assertEquals(fixture.transport.sent[0], fixture.transport.sent[1])
        fixture.tool("same", "unknown")
        assertFalse(fixture.session.state.value.active)
        assertEquals("Protocol error", fixture.session.state.value.status)
    }

    @Test fun capsActionsAndCleansUpAllResources() {
        val fixture = Fixture().started()
        repeat(33) { fixture.tool("call-$it") }
        assertEquals(32, fixture.navigations)
        assertEquals("Action limit reached", fixture.session.state.value.status)
        fixture.assertReleased()
    }

    @Test fun stopRejectsLateMessagesAudioAndFailureCallbacks() {
        val fixture = Fixture().started()
        fixture.session.stop("App moved to background.")
        fixture.tool("late")
        fixture.audio.input(byteArrayOf(0, 0))
        fixture.transport.listener.failed("Late error")
        fixture.audio.failed("Late audio error")
        assertEquals(0, fixture.navigations)
        assertEquals("App moved to background.", fixture.session.state.value.message)
        assertTrue(fixture.transport.sent.isEmpty())
        fixture.assertReleased()
        assertTrue(fixture.timers.all { it.cancelled })
    }

    @Test fun oldTransportCannotAffectNewSession() {
        val fixture = Fixture().started()
        val old = fixture.transport.listener
        fixture.session.stop()
        fixture.session.start()
        old.message("""{"type":"ready"}""")
        old.message(
            """{"type":"tool_call","id":"stale","name":"open_tab","args":{"tab":"space"}}"""
        )
        assertEquals(0, fixture.navigations)
        assertEquals("Connecting", fixture.session.state.value.status)
        fixture.ready()
        assertEquals(2, fixture.audio.starts)
    }

    @Test fun connectionTimeoutAndSessionLimitAreEnforced() {
        val connecting = Fixture()
        connecting.session.start()
        connecting.timers.single { it.delay == 30_000L }.fire()
        assertEquals("Connection timed out", connecting.session.state.value.status)
        assertEquals(1, connecting.transport.closes)
        assertEquals(0, connecting.audio.starts)
        val listening = Fixture().started()
        listening.timers.single { it.delay == VoiceConfiguration.INPUT_WINDOW_MILLIS }.fire()
        assertEquals("Session ended", listening.session.state.value.status)
        assertEquals(VoiceInteractionPhase.IDLE, listening.session.state.value.phase)
        listening.assertReleased()
    }

    @Test fun providerFailuresInvalidMessagesAndAudioFailureReleaseResources() {
        for (event in listOf(
            "{bad",
            """{"type":"error","message":"usage_limit"}""",
            """{"type":"audio","data":"invalid"}"""
        )) {
            val fixture = Fixture().started()
            fixture.transport.listener.message(event)
            fixture.assertReleased()
        }
        val fixture = Fixture().started()
        fixture.audio.failed("Microphone disconnected.")
        assertEquals("Audio unavailable", fixture.session.state.value.status)
        fixture.assertReleased()
    }

    @Test fun providerCannotExecuteToolsBeforeReady() {
        val fixture = Fixture()
        fixture.session.start()
        fixture.tool("early")
        assertEquals(0, fixture.navigations)
        assertEquals(0, fixture.audio.starts)
        assertFalse(fixture.session.state.value.active)
    }

    @Test fun speakingSuppressesMicrophoneAndInterruptionResumesInput() {
        val fixture = Fixture().started()
        fixture.transport.listener.message("""{"type":"audio","data":"AAA="}""")
        assertEquals("Speaking", fixture.session.state.value.status)
        fixture.audio.input(byteArrayOf(0, 0))
        assertTrue(fixture.transport.sent.isEmpty())
        fixture.transport.listener.message("""{"type":"interrupted"}""")
        assertEquals(1, fixture.audio.interrupts)
        fixture.audio.input(byteArrayOf(0, 0))
        assertEquals("audio_input", JSONObject(fixture.transport.sent.single()).getString("type"))
    }

    @Test fun completedPlaybackReturnsToListening() {
        val fixture = Fixture().started()
        fixture.transport.listener.message("""{"type":"audio","data":"AAA="}""")
        fixture.audio.drained()
        assertEquals("Listening", fixture.session.state.value.status)
    }

    @Test fun sendAndPlaybackBackpressureStopInsteadOfGrowingBuffers() {
        val output = Fixture().started()
        output.audio.acceptsOutput = false
        output.transport.listener.message("""{"type":"audio","data":"AAA="}""")
        output.assertReleased()
        val input = Fixture().started()
        input.transport.acceptsInput = false
        input.audio.input(byteArrayOf(0, 0))
        input.assertReleased()
    }

    @Test fun permissionRevocationDuringSessionStopsAudio() {
        val fixture = Fixture().started()
        fixture.permission = false
        fixture.audio.input(byteArrayOf(0, 0))
        fixture.assertReleased()
        assertTrue(fixture.transport.sent.isEmpty())
    }

    @Test fun closedControllerCannotRestart() {
        val fixture = Fixture().started()
        fixture.session.close()
        fixture.session.close()
        fixture.session.start()
        fixture.assertReleased()
        assertEquals(1, fixture.transportCreations)
        assertEquals("Closed", fixture.session.state.value.status)
    }

    @Test fun actionIsDispatchedRatherThanRunningOnTransportCallback() {
        val fixture = Fixture(defer = true)
        fixture.session.start()
        fixture.flush()
        fixture.ready()
        fixture.flush()
        fixture.tool("main-only")
        assertEquals(0, fixture.navigations)
        fixture.flush()
        assertEquals(1, fixture.navigations)
        assertTrue(fixture.actionWasDispatched)
    }

    @Test fun tabAndAudioBurstSurviveDelayedUiDispatch() {
        val fixture = Fixture(defer = true)
        fixture.session.start()
        fixture.flush()
        fixture.ready()
        fixture.flush()
        fixture.tool("tab-burst")
        repeat(61) {
            fixture.transport.listener.message("""{"type":"audio","data":"AAA="}""")
        }
        fixture.flush()
        assertEquals(1, fixture.navigations)
        assertTrue(fixture.session.state.value.active)
        fixture.session.stop()
        fixture.flush()
    }

    @Test fun pendingMessagesAreBoundedAndOverflowEndsSession() {
        val fixture = Fixture(defer = true)
        fixture.session.start()
        fixture.flush()
        repeat(10_000) { fixture.transport.listener.message("""{"type":"future_event"}""") }
        assertEquals(1, fixture.pending.size)
        fixture.flush()
        assertEquals("Message limit reached", fixture.session.state.value.status)
        assertFalse(fixture.session.state.value.active)
    }

    @Test fun pendingMicrophoneFramesAreBounded() {
        val fixture = Fixture(defer = true)
        fixture.session.start()
        fixture.flush()
        fixture.ready()
        fixture.flush()
        repeat(100) { fixture.audio.input(byteArrayOf(0, 0)) }
        assertEquals(1, fixture.pending.size)
        fixture.flush()
        assertEquals(1, fixture.transport.sent.size)
    }

    @Test fun unexpectedActionFaultPropagatesWithoutBeingLabeledProtocolError() {
        val fixture = Fixture().started()
        fixture.programmingFault = true
        assertThrows(UnsupportedOperationException::class.java) { fixture.tool("bug") }
        assertEquals("Stopped", fixture.session.state.value.status)
        assertTrue(fixture.transport.sent.isEmpty())
        fixture.assertReleased()
    }

    @Test fun cleanupFailuresAreLoggedAccumulatedAndDoNotSkipOtherResources() {
        val fixture = Fixture().started()
        fixture.audio.closeFailure = IllegalStateException("sensitive-device-detail")
        fixture.transport.closeFailure = java.io.IOException("sensitive-network-detail")
        fixture.session.stop()
        fixture.assertReleased()
        assertTrue(fixture.session.state.value.message.contains("2 resource failures"))
        assertTrue(fixture.logs.any { it.contains("audio") })
        assertTrue(fixture.logs.any { it.contains("transport") })
        assertTrue(fixture.logs.any { it.contains("Cleanup diagnostics") })
        assertFalse(fixture.logs.any { it.contains("sensitive") })
    }

    @Test fun unexpectedCleanupFaultPropagatesAfterOtherResourcesAreAttempted() {
        val fixture = Fixture().started()
        fixture.audio.closeFailure = UnsupportedOperationException("programming fault")
        assertThrows(UnsupportedOperationException::class.java) { fixture.session.close() }
        fixture.assertReleased()
        fixture.session.start()
        assertEquals(1, fixture.transportCreations)
        assertTrue(fixture.session.state.value.message.contains("unexpected fault"))
    }

    private class Fixture(
        configuration: VoiceConfiguration =
            VoiceConfiguration(true, "https://example.invalid", "vox_pub_test"),
        private val defer: Boolean = false
    ) {
        var permission = true
        var transportCreations = 0
        var navigations = 0
        var current: VoiceTab? = null
        var throwOnOpen = false
        var programmingFault = false
        var insideDispatch = false
        var actionWasDispatched = false
        val transport = FakeTransport()
        val audio = FakeAudio()
        val timers = mutableListOf<Timer>()
        val pending = ArrayDeque<() -> Unit>()
        val logs = mutableListOf<String>()
        val session = VoiceSession(
            configuration,
            {
                transportCreations++
                transport
            },
            { audio },
            { permission },
            { action ->
                if (defer) {
                    pending.addLast(action)
                } else {
                    insideDispatch = true
                    try {
                        action()
                    } finally {
                        insideDispatch = false
                    }
                }
            },
            { delay, action -> Timer(delay, action).also { timers.add(it) } },
            {
                actionWasDispatched = insideDispatch
                if (programmingFault) throw UnsupportedOperationException("programming fault")
                if (throwOnOpen) error("sensitive handler exception")
                navigations++
                val changed = current != it
                current = it
                changed
            },
            VoiceDiagnostics(logs::add)
        )

        fun flush() {
            while (pending.isNotEmpty()) {
                insideDispatch = true
                try {
                    pending.removeFirst()()
                } finally {
                    insideDispatch = false
                }
            }
        }
        fun started() = apply {
            session.start()
            ready()
        }
        fun ready() = transport.listener.message("""{"type":"ready","sessionId":"test"}""")
        fun tool(id: String, name: String = "open_tab", args: String = """{"tab":"explore"}""") =
            transport.listener.message(
                """{"type":"tool_call","id":"$id","name":"$name","args":$args}"""
            )
        fun assertReleased() {
            assertFalse(session.state.value.active)
            assertEquals(1, transport.closes)
            assertEquals(1, audio.closes)
        }
    }

    private class Timer(val delay: Long, val action: () -> Unit) : VoiceCancellation {
        var cancelled = false
        override fun cancel() {
            cancelled = true
        }
        fun fire() {
            if (!cancelled) action()
        }
    }

    private class FakeTransport : VoiceTransport {
        lateinit var listener: VoiceTransport.Listener
        val sent = mutableListOf<String>()
        var closes = 0
        var acceptsInput = true
        var closeFailure: Exception? = null
        override fun start(listener: VoiceTransport.Listener) {
            this.listener = listener
        }
        override fun send(text: String): Boolean = acceptsInput.also { if (it) sent.add(text) }
        override fun close() {
            closes++
            closeFailure?.let { throw it }
        }
    }

    private class FakeAudio : VoiceAudio {
        var starts = 0
        var closes = 0
        var interrupts = 0
        var inputStops = 0
        var acceptsOutput = true
        var closeFailure: Exception? = null
        lateinit var input: (ByteArray) -> Unit
        lateinit var drained: () -> Unit
        lateinit var failed: (String) -> Unit
        override fun start(
            input: (ByteArray) -> Unit,
            drained: () -> Unit,
            failed: (String) -> Unit,
            output: (Float) -> Unit
        ) {
            starts++
            this.input = input
            this.drained = drained
            this.failed = failed
        }
        override fun stopInput() {
            inputStops++
        }
        override fun enqueue(pcm: ByteArray): Boolean = acceptsOutput
        override fun interrupt() {
            interrupts++
        }
        override fun close() {
            closes++
            closeFailure?.let { throw it }
        }
    }
}
