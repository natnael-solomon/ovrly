package app.ovrly.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceInboxTest {
    @Test
    fun producerAndUiDrainConcurrentlyWithoutLossOrDuplicateDelivery() {
        val tasks = java.util.concurrent.LinkedBlockingQueue<() -> Unit>()
        val received = mutableListOf<String>()
        val failure = java.util.concurrent.atomic.AtomicReference<Throwable>()
        val done = java.util.concurrent.CountDownLatch(1)
        val inbox = VoiceInbox(
            { tasks.add(it) },
            { received.add((it as VoiceEvent.Tool).id) },
            { _, _ -> error("In-budget concurrent burst failed") },
            VoiceDiagnostics {}
        )
        val producer = kotlin.concurrent.thread {
            try {
                repeat(500) {
                    inbox.message(
                        """{"type":"tool_call","id":"$it","name":"open_tab","args":{}}"""
                    )
                }
            } catch (cause: Throwable) {
                failure.set(cause)
            } finally {
                done.countDown()
            }
        }
        try {
            val deadline = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(10)
            while (done.count > 0 || tasks.isNotEmpty()) {
                assertTrue("Inbox delivery stalled", System.nanoTime() < deadline)
                tasks.poll(10, java.util.concurrent.TimeUnit.MILLISECONDS)?.invoke()
            }
            failure.get()?.let { throw it }
            assertEquals((0 until 500).map(Int::toString), received)
        } finally {
            inbox.close()
            producer.join(5000)
        }
    }

    @Test
    fun mixedBurstHasOnePendingDrainAndYieldsWithoutReordering() {
        val tasks = ArrayDeque<() -> Unit>()
        val received = mutableListOf<VoiceEvent>()
        val inbox =
            VoiceInbox({
                tasks.addLast(it)
            }, received::add, { _, _ -> error("Overflow") }, VoiceDiagnostics {})
        inbox.message("""{"type":"ready"}""")
        repeat(61) { inbox.message("""{"type":"audio","data":"AAA="}""") }
        inbox.message("""{"type":"tool_call","id":"1","name":"open_tab","args":{}}""")
        inbox.message("""{"type":"interrupted"}""")
        inbox.message("""{"type":"turn_complete"}""")
        assertEquals(1, tasks.size)
        tasks.removeFirst()()
        assertEquals(4, received.size)
        assertEquals(1, tasks.size)
        while (tasks.isNotEmpty()) tasks.removeFirst()()
        assertEquals(65, received.size)
        assertEquals(VoiceEvent.Ready, received.first())
        assertTrue(received.subList(1, 62).all { it is VoiceEvent.Audio })
        assertTrue(received[62] is VoiceEvent.Tool)
        assertEquals(VoiceEvent.Interrupted, received[63])
        assertEquals(VoiceEvent.TurnComplete, received[64])
        inbox.close()
    }

    @Test
    fun stopBetweenBatchesDiscardsPendingAudioAndLateTools() {
        val tasks = ArrayDeque<() -> Unit>()
        var received = 0
        val inbox =
            VoiceInbox({
                tasks.addLast(it)
            }, { received++ }, { _, _ -> error("Failure") }, VoiceDiagnostics {})
        repeat(100) { inbox.message("""{"type":"audio","data":"AAA="}""") }
        tasks.removeFirst()()
        inbox.close()
        inbox.message("""{"type":"tool_call","id":"late","name":"open_tab","args":{}}""")
        while (tasks.isNotEmpty()) tasks.removeFirst()()
        assertEquals(4, received)
    }

    @Test
    fun actualByteOverflowFailsOnceAndReleasesPendingEvents() {
        val tasks = ArrayDeque<() -> Unit>()
        val failures = mutableListOf<String>()
        val inbox = VoiceInbox(
            { tasks.addLast(it) },
            { error("Must discard on overflow") },
            { status, _ -> failures.add(status) },
            VoiceDiagnostics {},
            1024
        )
        repeat(100) { inbox.message("""{"type":"audio","data":"AAA="}""") }
        assertEquals(1, tasks.size)
        while (tasks.isNotEmpty()) tasks.removeFirst()()
        assertEquals(listOf("Message limit reached"), failures)
    }

    @Test
    fun malformedJsonFailsClosedWithoutEchoingPayload() {
        val tasks = ArrayDeque<() -> Unit>()
        val logs = mutableListOf<String>()
        val inbox = VoiceInbox(
            { tasks.addLast(it) },
            { error("Invalid event") },
            { status, _ -> assertEquals("Protocol error", status) },
            VoiceDiagnostics(logs::add)
        )
        inbox.message("""{"type":"ready","type":"private"}""")
        while (tasks.isNotEmpty()) tasks.removeFirst()()
        assertFalse(logs.any { it.contains("private") })
    }

    @Test
    fun decodeOccursBeforeUiDispatchAndLargeBurstRemainsBounded() {
        val tasks = ArrayDeque<() -> Unit>()
        val logs = mutableListOf<String>()
        var received = 0
        val inbox = VoiceInbox(
            { tasks.addLast(it) },
            { received++ },
            { _, _ -> error("In-budget burst rejected") },
            VoiceDiagnostics(logs::add),
            VoiceInbox.CAPACITY
        )
        val encoded = java.util.Base64.getEncoder().encodeToString(ByteArray(48_000))
        repeat(80) { inbox.message("""{"type":"audio","data":"$encoded"}""") }
        assertEquals(0, received)
        assertTrue(logs.any { it == "Voice first received/audio" })
        assertEquals(1, tasks.size)
        while (tasks.isNotEmpty()) tasks.removeFirst()()
        assertEquals(80, received)
        inbox.close()
        assertTrue(logs.last().contains("messages=80"))
    }
}
