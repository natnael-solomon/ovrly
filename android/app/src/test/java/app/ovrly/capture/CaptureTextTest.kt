package app.ovrly.capture

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureTextTest {
    private val edge = SamplingPolicy.THUMBNAIL_EDGE

    /** A flat gray thumbnail with an optional bright card over rows 20..35. */
    private fun thumbnail(card: Boolean, background: Int = 50): IntArray =
        IntArray(edge * edge) { if (card && it / edge in 20..35) 200 else background }

    @Test fun probesOncePerSecondAndKeepsChangesAndHeartbeats() {
        val sampler = FrameSampler()
        assertTrue(sampler.probeDue(0))
        assertEquals(FrameSampler.Decision.CHANGED, sampler.decide(0, thumbnail(false)))
        assertFalse(sampler.probeDue(999))
        assertTrue(sampler.probeDue(1_000))
        assertEquals(FrameSampler.Decision.UNCHANGED, sampler.decide(1_000, thumbnail(false)))
        assertEquals(
            FrameSampler.Decision.UNCHANGED,
            sampler.decide(2_000, thumbnail(false, background = 61))
        )
        assertEquals(
            FrameSampler.Decision.CHANGED,
            sampler.decide(3_000, thumbnail(false, background = 62))
        )
        assertEquals(FrameSampler.Decision.UNCHANGED, sampler.decide(7_000, thumbnail(false, 62)))
        assertEquals(FrameSampler.Decision.HEARTBEAT, sampler.decide(8_000, thumbnail(false, 62)))
    }

    @Test fun keptFramesAreCappedPerMinute() {
        val sampler = FrameSampler()
        val decisions = (0 until 120).map { second ->
            sampler.decide(second * 1_000L, thumbnail(card = second % 2 == 1))
        }
        val keptAt = decisions.indices.filter {
            decisions[it] == FrameSampler.Decision.CHANGED ||
                decisions[it] == FrameSampler.Decision.HEARTBEAT
        }
        assertTrue(decisions.contains(FrameSampler.Decision.CAPPED))
        keptAt.forEach { start ->
            assertTrue(keptAt.count { it in start until start + 60 } <= 20)
        }
        assertEquals(40, keptAt.size)
    }

    @Test fun briefTitleCardIsCaughtByTheChangeTriggerButMissedByFixedFiveSecondSampling() {
        // A synthetic title card is visible from 6.2 s to 7.8 s of a 12-second clip.
        val visible = 6_200L until 7_800L
        val probes = (0L..12_000L step SamplingPolicy.PROBE_INTERVAL_MS)
        val sampler = FrameSampler()
        val kept = probes.filter {
            sampler.decide(it, thumbnail(it in visible)) != FrameSampler.Decision.UNCHANGED
        }
        val fixed = (0L..12_000L step 5_000L).toList()
        assertTrue(kept.any { it in visible })
        assertFalse(fixed.any { it in visible })

        // A card shorter than the probe interval that falls between probes is missed: the
        // documented limit of the policy.
        val brief = 2_100L until 2_900L
        val second = FrameSampler()
        val keptBrief = probes.filter {
            second.decide(it, thumbnail(it in brief)) != FrameSampler.Decision.UNCHANGED
        }
        assertFalse(keptBrief.any { it in brief })
    }

    @Test fun textRegionsAreCroppedAroundTextLikeRows() {
        val width = 100
        val height = 60
        val gray = IntArray(width * height) { index ->
            val x = index % width
            val y = index / width
            if (y in 20..29 && x in 10..60 && x % 2 == 0) 255 else 0
        }
        val regions = TextRegions.find(gray, width, height)
        // The 26 px band is grown around its centre to ML Kit's 32 px minimum.
        assertEquals(listOf(TextRegions.Region(1, 9, 70, 41)), regions)
        assertEquals(
            listOf(100, 1_500, 7_000, 6_833),
            TextRegions.normalize(regions[0], width, height).toList()
        )

        assertTrue(TextRegions.find(IntArray(width * height), width, height).isEmpty())
        val everywhere = IntArray(width * height) { if ((it % width) % 2 == 0) 255 else 0 }
        assertEquals(
            listOf(TextRegions.Region(0, 0, width, height)),
            TextRegions.find(everywhere, width, height)
        )
    }

    @Test fun smallRegionsGrowToTheRecognizerMinimumInsideTheFrame() {
        val min = TextRegions.MIN_EDGE
        assertEquals(
            TextRegions.Region(4, 0, 4 + min, min),
            TextRegions.atLeast(TextRegions.Region(10, 0, 30, 10), 100, 60)
        )
        assertEquals(
            TextRegions.Region(100 - min, 60 - min, 100, 60),
            TextRegions.atLeast(TextRegions.Region(95, 55, 100, 60), 100, 60)
        )
        val large = TextRegions.Region(0, 0, 80, 40)
        assertEquals(large, TextRegions.atLeast(large, 100, 60))
        assertEquals(
            TextRegions.Region(0, 0, 20, 20),
            TextRegions.atLeast(TextRegions.Region(5, 5, 10, 10), 20, 20)
        )
    }

    @Test fun aFailedRegionKeepsTheOthersAndATimeoutSkipsTheRest() {
        val regions = (0 until 4).map { TextRegions.Region(it * 40, 0, it * 40 + 40, 40) }
        val line = TextObservation("Live", listOf(0, 0, 100, 100), 9_800)
        val attempted = mutableListOf<Int>()
        val read = readRegions(regions) {
            attempted += it.left
            when (it.left) {
                0 -> RegionRead.Lines(listOf(line))
                40 -> RegionRead.Failed
                else -> RegionRead.TimedOut
            }
        }
        // After the timeout at 80 the region at 120 is not attempted; ML Kit may still be busy.
        assertEquals(listOf(0, 40, 80), attempted)
        assertEquals(listOf(line), read.observations)
        assertEquals(3, read.failed)
        assertTrue(read.timedOut)

        val clean = readRegions(regions) { RegionRead.Lines(emptyList()) }
        assertEquals(0, clean.failed)
        assertFalse(clean.timedOut)
    }

    @Test fun luminanceUsesTheStandardWeights() {
        val pixels = intArrayOf(0xFFFFFFFF.toInt(), 0xFF000000.toInt(), 0xFFFF0000.toInt())
        assertEquals(listOf(255, 0, 76), TextRegions.luminance(pixels).toList())
    }

    @Test fun recognizerVersionMatchesTheBundledDependency() {
        val build = File("build.gradle.kts").readText()
        assertTrue(build.contains("com.google.mlkit:text-recognition:${Recognizer.VERSION}"))
        assertTrue(Recognizer.NAME.endsWith("bundled"))
    }
}
