package app.ovrly.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceEnduranceTest {
    @Test
    fun playbackBufferAcceptsBurstsThroughTenMegabytesWithoutGrowing() {
        val capacity = VoicePlaybackBuffer.CAPACITY
        val buffer = VoicePlaybackBuffer(capacity)
        for (burst in listOf(2, 960, 96_000, 480_000, 1_000_000, capacity)) {
            for (fragment in listOf(120, 1920, 48_000)) {
                var offered = 0
                while (offered < burst) {
                    val length = minOf(fragment, burst - offered)
                    val pcm = ByteArray(length) { ((offered + it) % 127).toByte() }
                    assertTrue(buffer.offer(pcm))
                    offered += length
                }
                assertEquals(burst, buffer.size)
                if (burst == capacity) assertFalse(buffer.offer(byteArrayOf(0, 0)))
                var played = 0
                while (buffer.size > 0) {
                    buffer.write(960) { bytes, offset, length ->
                        for (index in 0 until length) {
                            assertEquals(((played + index) % 127).toByte(), bytes[offset + index])
                        }
                        played += length
                        length
                    }
                }
                assertEquals(burst, played)
                assertEquals(capacity, buffer.capacity)
            }
        }
    }

    @Test
    fun pendingDurationIncludesDeviceBufferAndStallUsesPlaybackNotWrites() {
        val progress = VoicePlaybackProgress()
        assertEquals(2000L, progress.sample(48_000, 24_000, 0, 0).queuedMillis)
        val before = progress.sample(24_000, 36_000, 0, 4999)
        assertEquals(2000L, before.queuedMillis)
        assertFalse(before.stalled)
        val stalled = progress.sample(0, 48_000, 0, 5000)
        assertEquals(5000L, stalled.noProgressMillis)
        assertTrue(stalled.stalled)
        assertFalse(progress.sample(0, 48_000, 24_000, 5001).stalled)
    }

    @Test
    fun idleAndInterruptResetWatchdogButSlowProgressKeepsSessionUsable() {
        val progress = VoicePlaybackProgress()
        assertFalse(progress.sample(0, 0, 0, 100_000).stalled)
        assertFalse(progress.sample(960, 0, 0, 200_000).stalled)
        progress.reset()
        assertFalse(progress.sample(960, 0, 0, 300_000).stalled)
        repeat(900) { second ->
            val played = second * 24_000L
            val sample = progress.sample(48_000, played + 24_000, played, 301_000L + second * 1000)
            assertEquals(2000L, sample.queuedMillis)
            assertFalse(sample.stalled)
        }
    }
}
