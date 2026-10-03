package app.ovrly.voice

import java.io.ByteArrayOutputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class VoicePlaybackBufferTest {
    @Test
    fun seventeenChunkBurstWithinByteBudgetIsAcceptedAndPlayedInOrder() {
        val buffer = VoicePlaybackBuffer(SMALL)
        val expected = ByteArrayOutputStream()
        repeat(17) { index ->
            val pcm = ByteArray(960) { index.toByte() }
            expected.write(pcm)
            assertTrue(
                "Burst chunk $index rejected despite available byte capacity",
                buffer.offer(pcm)
            )
        }
        val actual = ByteArrayOutputStream()
        while (buffer.size > 0) {
            buffer.write(960) { data, offset, length ->
                actual.write(data, offset, length)
                length
            }
        }
        assertArrayEquals(expected.toByteArray(), actual.toByteArray())
    }

    @Test
    fun identicalAudioIsIndependentOfChunkBoundaries() {
        val pcm = ByteArray(96_000) { (it % 127).toByte() }
        for (chunkSize in listOf(2, 120, 960, 6000, 48_000)) {
            val buffer = VoicePlaybackBuffer(SMALL)
            for (offset in pcm.indices step chunkSize) {
                assertTrue(buffer.offer(pcm.copyOfRange(offset, offset + chunkSize)))
            }
            assertEquals(SMALL, buffer.size)
            assertFalse(buffer.offer(byteArrayOf(0, 0)))
            assertArrayEquals(pcm, drain(buffer))
        }
    }

    @Test
    fun partialWritesAndWraparoundRetainEverySample() {
        val buffer = VoicePlaybackBuffer(SMALL)
        val first = ByteArray(90_000) { 1 }
        val second = ByteArray(10_000) { 2 }
        assertTrue(buffer.offer(first))
        val played = ByteArrayOutputStream()
        repeat(10) {
            assertEquals(
                600,
                buffer.write(960) { bytes, offset, _ ->
                    played.write(bytes, offset, 600)
                    600
                }
            )
        }
        assertTrue(buffer.offer(second))
        played.write(drain(buffer))
        assertArrayEquals(first + second, played.toByteArray())
    }

    @Test
    fun fullSinkDoesNotDiscardOrMutateBufferedAudio() {
        val buffer = VoicePlaybackBuffer(SMALL)
        val pcm = ByteArray(960) { 42 }
        assertTrue(buffer.offer(pcm))
        repeat(100) { assertEquals(0, buffer.write(960) { _, _, _ -> 0 }) }
        assertEquals(pcm.size, buffer.size)
        assertArrayEquals(pcm, drain(buffer))
    }

    @Test
    fun invalidWritesAndOversizedOffersPreservePendingAudio() {
        val buffer = VoicePlaybackBuffer(SMALL)
        val pcm = byteArrayOf(1, 2, 3, 4)
        assertTrue(buffer.offer(pcm))
        for (invalid in listOf(-1, 1, 6)) {
            assertThrows(IllegalStateException::class.java) {
                buffer.write(4) { _, _, _ -> invalid }
            }
            assertEquals(4, buffer.size)
        }
        for (invalid in listOf(ByteArray(0), ByteArray(1), ByteArray(96_000))) {
            assertFalse(buffer.offer(invalid))
        }
        assertArrayEquals(pcm, drain(buffer))
    }

    @Test
    fun clearDiscardsOldSpeechAndInputArraysAreNotRetained() {
        val buffer = VoicePlaybackBuffer(SMALL)
        val pcm = byteArrayOf(1, 2)
        assertTrue(buffer.offer(pcm))
        pcm.fill(9)
        assertArrayEquals(byteArrayOf(1, 2), drain(buffer))
        assertTrue(buffer.offer(ByteArray(12_000) { 3 }))
        buffer.clear()
        assertEquals(0, buffer.size)
        assertEquals(0, buffer.write(960) { _, _, _ -> error("Must not write after stop") })
        assertTrue(buffer.offer(byteArrayOf(4, 5)))
        assertArrayEquals(byteArrayOf(4, 5), drain(buffer))
    }

    private fun drain(buffer: VoicePlaybackBuffer): ByteArray {
        val out = ByteArrayOutputStream()
        while (buffer.size > 0) {
            buffer.write(960) { bytes, offset, length ->
                out.write(bytes, offset, length)
                length
            }
        }
        return out.toByteArray()
    }

    private companion object {
        const val SMALL = 96_000
    }
}
