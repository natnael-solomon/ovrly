package app.ovrly.capture

import app.ovrly.contract.Modality
import java.io.File
import java.io.IOException
import java.util.zip.ZipFile
import kotlin.random.Random
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class CaptureFilesTest {
    @get:Rule val temporary = TemporaryFolder()

    private fun root() = File(temporary.root, "capture")
    private fun manifest(root: File) = LocalManifest.parse(File(root, "capture.json").readText())
    private val audio = byteArrayOf(1, 0, 2, 0)

    private fun text(pts: Long, status: FrameStatus = FrameStatus.RECOGNIZED) = FrameText(
        pts,
        status,
        1,
        if (status == FrameStatus.RECOGNIZED) {
            listOf(TextObservation("Breaking", listOf(100, 200, 9_000, 800), pts))
        } else {
            emptyList()
        },
        recognitionMs = 42
    )

    @Test fun audioAndFramesBecomeSequencedChunksWithCaptureRelativeOffsets() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.writeFrame(byteArrayOf(42, 43), text(4_000))
        files.writeAudio(audio, 4, 9_990)
        files.writeAudio(audio, 4, 10_010)
        files.writeFrame(byteArrayOf(44), text(15_000))
        files.advance(20_000)
        files.writeAudio(audio, 4, 21_000)
        files.finish(25_500, "Explicit stop", true)

        val manifest = manifest(root)
        assertTrue(manifest.finished)
        assertEquals(25_500L, manifest.durationMs)
        assertEquals("Explicit stop", manifest.stopReason)
        assertEquals(listOf(0, 1, 2), manifest.chunks.map { it.seq })
        assertEquals(
            listOf(0L to 10_000L, 10_000L to 20_000L, 20_000L to 25_500L),
            manifest.chunks.map { it.startMs to it.endMs }
        )
        assertEquals(
            listOf(Modality.BOTH, Modality.BOTH, Modality.SPEECH),
            manifest.chunks.map { it.modality }
        )
        assertEquals(
            listOf(listOf(4_000L), listOf(15_000L), emptyList()),
            manifest.chunks.map { it.frameOffsetsMs }
        )
        assertEquals(listOf(8L, 4L, 4L), manifest.chunks.map { it.audioBytes })
        assertEquals(2, manifest.frames)
        manifest.chunks.zipWithNext().forEach { (previous, next) ->
            assertEquals(previous.endMs, next.startMs)
            assertTrue(next.seq > previous.seq)
        }
        assertEquals(3, files.sealedChunks)
        assertFalse(File(root, "open").exists())
        assertThrows(IOException::class.java) { files.writeAudio(audio, 4, 26_000) }
    }

    private fun assertPolicyAndRecognizer(description: JSONObject) {
        val sampling = description.getJSONObject("sampling")
        assertEquals("change_triggered", sampling.getString("policy"))
        assertEquals(1_000, sampling.getInt("probe_interval_ms"))
        assertEquals(5_000, sampling.getInt("heartbeat_ms"))
        assertEquals(20, sampling.getInt("max_frames_per_minute"))
        val recognizer = description.getJSONObject("recognizer")
        assertEquals("mlkit-text-recognition-latin-bundled", recognizer.getString("name"))
        assertEquals("16.0.1", recognizer.getString("version"))
    }

    @Test fun chunkPackageHoldsOnlyCaptureTimesAndItsHashMatches() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 100)
        files.writeFrame(byteArrayOf(42, 43), text(5_000))
        files.writeFrame(null, text(6_000, FrameStatus.CAPPED))
        files.writeFrame(byteArrayOf(9), text(6_500, FrameStatus.FAILED))
        files.finish(7_000, "Stop", true)
        val chunk = manifest(root).chunks.single()
        val file = File(root, chunk.fileName)
        assertEquals("chunk-000.zip", chunk.fileName)
        assertEquals(file.length(), chunk.sizeBytes)
        assertEquals(ChunkPackage.sha256(file.readBytes()), chunk.sha256)
        ZipFile(file).use { zip ->
            val names = zip.entries().toList().map { it.name }
            assertEquals(
                listOf("chunk.json", "audio-16000-mono-s16le.pcm"),
                names
            )
            zip.entries().toList().forEach { assertEquals(315_532_800_000L, it.time) }
            val description = JSONObject(
                zip.getInputStream(zip.getEntry("chunk.json")).readBytes().decodeToString()
            )
            assertEquals(0, description.getInt("start_ms"))
            assertEquals(7_000, description.getInt("end_ms"))
            assertEquals("capture", description.getString("timebase"))
            assertEquals("both", description.getString("modality"))
            assertFalse(description.getBoolean("frames_uploaded"))
            val frames = description.getJSONArray("frames")
            assertEquals(2, frames.length())
            assertEquals(5_000, frames.getJSONObject(0).getInt("frame_pts"))
            assertEquals("recognized", frames.getJSONObject(0).getString("status"))
            assertEquals(42, frames.getJSONObject(0).getInt("recognition_ms"))
            assertEquals("failed", frames.getJSONObject(1).getString("status"))
            val observation = description.getJSONArray("text_observations").getJSONObject(0)
            assertEquals("Breaking", observation.getString("text"))
            assertEquals(5_000, observation.getInt("frame_pts"))
            assertEquals(9_000, observation.getJSONArray("box").getInt(2))
            assertPolicyAndRecognizer(description)
            assertArrayEquals(
                audio,
                zip.getInputStream(zip.getEntry("audio-16000-mono-s16le.pcm")).readBytes()
            )
        }
        assertTrue(File(root, "frames/frame-5000ms.jpg").exists())
        assertFalse(File(root, "frames/frame-6000ms.jpg").exists())
        val manifest = manifest(root)
        assertEquals(2, manifest.frames)
        assertEquals(1, manifest.cappedFrames)
        assertEquals(1, manifest.failedFrames)
        assertEquals(1, manifest.chunks.single().observations)
        val local = JSONObject(File(root, "capture.json").readText())
        assertEquals("16.0.1", local.getJSONObject("recognizer").getString("version"))
    }

    @Test fun aChunkWithReadFramesButNoAudioDeclaresTextAndShipsNoImage() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeFrame(byteArrayOf(7), text(0))
        files.writeFrame(byteArrayOf(8), text(5_000, FrameStatus.NO_TEXT_REGIONS))
        files.advance(10_500)
        files.writeFrame(byteArrayOf(9), text(12_000, FrameStatus.FAILED))
        files.finish(19_000, "Stop", false)
        val manifest = manifest(root)
        assertEquals(listOf(0), manifest.chunks.map { it.seq })
        assertEquals(Modality.TEXT, manifest.chunks.single().modality)
        assertEquals(listOf(1), manifest.skippedSeqs)
        assertEquals(3, manifest.frames)
        assertTrue(File(root, "frames/frame-0ms.jpg").exists())
        ZipFile(File(root, "chunk-000.zip")).use { zip ->
            assertEquals(listOf("chunk.json"), zip.entries().toList().map { it.name })
        }
    }

    @Test fun lateFramesAreDroppedAndCountedButLateAudioIsKept() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.advance(10_500)
        files.writeFrame(byteArrayOf(1), text(9_900))
        files.writeAudio(audio, 4, 9_995)
        files.finish(12_000, "Stop", false)
        val manifest = manifest(root)
        assertEquals(1, manifest.droppedFrames)
        assertEquals(0, manifest.frames)
        assertEquals(listOf(4L, 4L), manifest.chunks.map { it.audioBytes })
    }

    @Test fun aFrameStillBeingReadAtTheChunkBoundaryKeepsItsTextInItsChunk() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.frameStarted { 9_800 }
        // The ticker passes the 10 s boundary and audio moves on while the frame is read.
        files.advance(10_200)
        files.writeAudio(audio, 4, 10_300)
        assertFalse(File(root, "chunk-000.zip").exists())
        assertTrue(manifest(root).chunks.isEmpty())
        files.writeFrame(byteArrayOf(5), text(9_800))
        assertTrue(File(root, "chunk-000.zip").exists())
        files.advance(10_500)
        files.finish(15_000, "Stop", true)

        val manifest = manifest(root)
        assertEquals(listOf(0, 1), manifest.chunks.map { it.seq })
        assertEquals(listOf(listOf(9_800L), emptyList()), manifest.chunks.map { it.frameOffsetsMs })
        assertEquals(listOf(4L, 4L), manifest.chunks.map { it.audioBytes })
        assertEquals(listOf(1, 0), manifest.chunks.map { it.observations })
        assertEquals(Modality.BOTH, manifest.chunks.first().modality)
        assertEquals(0, manifest.droppedFrames)
        assertEquals(1, manifest.frames)
        assertFalse(File(root, "open").exists())
    }

    @Test fun laterChunksWaitBehindAHeldChunkSoChunksAreSealedInOrder() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.frameStarted { 9_000 }
        files.writeAudio(audio, 4, 15_000)
        files.advance(21_000)
        assertTrue(manifest(root).chunks.isEmpty())
        files.writeFrame(byteArrayOf(5), text(9_000))
        assertEquals(listOf(0, 1), manifest(root).chunks.map { it.seq })

        files.frameStarted { 22_000 }
        files.advance(31_000)
        // A frame that could not be read is written as failed, which releases its chunk.
        files.writeFrame(null, FrameText(22_000, FrameStatus.FAILED, 0, emptyList()))
        files.finish(32_000, "Stop", false)
        val manifest = manifest(root)
        assertEquals(listOf(0, 1), manifest.chunks.map { it.seq })
        assertEquals(listOf(2, 3), manifest.skippedSeqs)
        assertEquals(1, manifest.failedFrames)
        assertEquals(0, manifest.droppedFrames)
    }

    @Test fun aProbeRegisteredBeforeTheBoundaryKeepsItsChunkUntilOcrFinishes() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        // The probe's time is taken and registered at 9.99 s; the 10 s boundary passes (audio
        // at 10.005 s) during the bitmap copy and thumbnail, before OCR starts.
        val probe = checkNotNull(files.frameStarted { 9_990 })
        assertEquals(9_990L, probe.elapsedMs)
        files.writeAudio(audio, 4, 10_005)
        files.advance(10_200)
        assertTrue(manifest(root).chunks.isEmpty())
        files.writeFrame(byteArrayOf(5), text(9_990))
        probe.release()
        files.finish(12_000, "Stop", true)

        val manifest = manifest(root)
        assertEquals(listOf(listOf(9_990L), emptyList()), manifest.chunks.map { it.frameOffsetsMs })
        assertEquals(listOf(4L, 4L), manifest.chunks.map { it.audioBytes })
        assertEquals(0, manifest.droppedFrames)
        assertEquals(1, manifest.frames)
    }

    @Test fun aReleasedProbeFreesItsChunkWithoutBeingCounted() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        val unchanged = checkNotNull(files.frameStarted { 9_990 })
        files.advance(10_200)
        assertTrue(manifest(root).chunks.isEmpty())
        // The change trigger kept nothing (or OCR never started): the caller releases it.
        unchanged.release()
        assertEquals(listOf(0), manifest(root).chunks.map { it.seq })
        unchanged.release()
        files.finish(12_000, "Stop", false)
        assertNull(files.frameStarted { 1_000 })

        val manifest = manifest(root)
        assertEquals(0, manifest.frames)
        assertEquals(0, manifest.droppedFrames)
        assertEquals(0, manifest.unfinishedFrames)
        assertEquals(0, manifest.cappedFrames)
    }

    @Test fun aFrameThatNeverFinishesReleasesItsChunkAfterTheHoldLimit() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.frameStarted { 9_000 }
        val limit = ChunkCursor.HOLD_LIMIT_MS
        assertEquals(45_000L, limit)
        files.advance(9_000 + limit)
        // Still within the bound: chunk 0 and every chunk after it wait.
        assertTrue(manifest(root).chunks.isEmpty())
        assertTrue(manifest(root).skippedSeqs.isEmpty())
        files.advance(9_000 + limit + 200)
        val released = manifest(root)
        assertEquals(listOf(0), released.chunks.map { it.seq })
        assertEquals(listOf(1, 2, 3, 4), released.skippedSeqs)
        // A result that finally arrives is ignored; the frame was counted once, as unfinished.
        files.writeFrame(byteArrayOf(5), text(9_000))
        files.finish(56_000, "Stop", true)
        val manifest = manifest(root)
        assertEquals(1, manifest.unfinishedFrames)
        assertEquals(0, manifest.droppedFrames)
        assertEquals(0, manifest.frames)
        assertEquals(emptyList<Long>(), manifest.chunks.single().frameOffsetsMs)
    }

    @Test fun framesStillBeingReadAtStopAreCountedAsUnfinished() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.frameStarted { 8_000 }
        files.advance(12_000)
        files.frameStarted { 12_500 }
        files.finish(13_000, "Stop", true)
        // A result that arrives after Stop changes nothing; the frame is already counted.
        files.writeFrame(byteArrayOf(5), text(12_500))
        files.writeFrame(byteArrayOf(6), text(8_000))

        val manifest = manifest(root)
        assertEquals(2, manifest.unfinishedFrames)
        assertEquals(0, manifest.droppedFrames)
        assertEquals(0, manifest.frames)
        assertEquals(listOf(0), manifest.chunks.map { it.seq })
        assertEquals(listOf(1), manifest.skippedSeqs)
        assertEquals(
            2,
            JSONObject(File(root, "capture.json").readText()).getInt("unfinishedFrames")
        )
    }

    @Test fun gapsAndChunksWithoutMediaAreRecordedExplicitly() {
        val detector = AudioGapDetector()
        assertNull(detector.onAudio(0))
        assertNull(detector.onAudio(500))
        assertEquals(CaptureGap(GapKind.INTERRUPTED, 500, 2_600), detector.onAudio(2_600))
        assertNull(detector.onAudio(3_000))
        assertEquals(CaptureGap(GapKind.INTERRUPTED, 3_000, 34_000), detector.onFinish(34_000))

        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.recordGap(CaptureGap(GapKind.INTERRUPTED, 500, 2_600))
        files.advance(25_000)
        files.recordGap(CaptureGap(GapKind.INTERRUPTED, 3_000, 34_000))
        files.finish(34_000, "Stop", true)
        val manifest = manifest(root)
        assertEquals(listOf(0), manifest.chunks.map { it.seq })
        assertEquals(listOf(1, 2, 3), manifest.skippedSeqs)
        assertEquals(
            listOf(
                CaptureGap(GapKind.INTERRUPTED, 500, 2_600),
                CaptureGap(GapKind.INTERRUPTED, 3_000, 34_000)
            ),
            manifest.gaps
        )
        assertEquals(34_000L, manifest.durationMs)
    }

    @Test fun threeMinuteStopEndsTheLastChunkExactlyAtTheLimit() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        var elapsed = 0L
        while (elapsed < 185_000) {
            files.writeAudio(audio, 4, elapsed)
            files.advance(elapsed)
            elapsed += 250
        }
        files.finish(190_000, "Stopped at the 3-minute capture limit.", true)
        val manifest = manifest(root)
        assertEquals(CaptureLimits.LIVE_MS, manifest.durationMs)
        assertEquals((0..17).toList(), manifest.chunks.map { it.seq })
        assertEquals(180_000L, manifest.chunks.last().endMs)
        assertTrue(manifest.chunks.all { it.endMs - it.startMs == CaptureLimits.CHUNK_MS })
        assertTrue(manifest.chunks.all { it.audioBytes == 160L })
        assertFalse(File(root, SealedChunk.fileName(18)).exists())
    }

    @Test fun rollingDeletionRemovesOnlySentChunksOldestFirst() {
        val stored = listOf(
            RollingEviction.Stored(0, 10, sent = true),
            RollingEviction.Stored(1, 10, sent = false),
            RollingEviction.Stored(2, 10, sent = true)
        )
        val full = CaptureLimits.MAX_BYTES
        assertEquals(emptyList<Int>(), RollingEviction.select(stored, full - 10, 5))
        assertEquals(listOf(0), RollingEviction.select(stored, full, 5))
        assertEquals(listOf(0, 2), RollingEviction.select(stored, full, 15))
        assertNull(RollingEviction.select(stored, full, 25))

        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        val block = Random(7).nextBytes(11 * 1024 * 1024)
        files.writeAudio(block, block.size, 0)
        files.writeAudio(block, block.size, 10_000)
        val refused = assertThrows(StorageLimitException::class.java) {
            files.writeAudio(block, block.size, 20_000)
        }
        assertTrue(refused.message.orEmpty().contains("2 chunks are saved on device"))
        assertTrue(File(root, "chunk-000.zip").exists())
        assertTrue(files.bytes <= CaptureLimits.MAX_BYTES)

        CaptureLedger(root).markSent(0)
        files.writeAudio(block, block.size, 20_000)
        assertFalse(File(root, "chunk-000.zip").exists())
        assertTrue(File(root, "chunk-001.zip").exists())
        assertEquals(listOf(0), manifest(root).evictedSeqs)
        assertTrue(files.bytes <= CaptureLimits.MAX_BYTES)
        files.delete()
    }

    @Test fun localFrameImagesAreDeletedBeforeAnyChunk() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        val random = Random(3)
        files.writeFrame(random.nextBytes(12 * 1024 * 1024), text(1_000))
        val block = random.nextBytes(11 * 1024 * 1024)
        files.writeAudio(block, block.size, 2_000)
        files.writeAudio(block, block.size, 10_000)
        assertFalse(File(root, "frames/frame-1000ms.jpg").exists())
        assertTrue(File(root, "chunk-000.zip").exists())
        assertEquals(emptyList<Int>(), manifest(root).evictedSeqs)
        assertTrue(files.bytes <= CaptureLimits.MAX_BYTES)
        files.delete()
    }

    @Test fun newCaptureNeverSilentlyReplacesUnsentChunks() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.finish(5_000, "Stop", true)
        assertThrows(UnsentCaptureException::class.java) { CaptureFiles(root).begin() }
        assertTrue(File(root, "chunk-000.zip").exists())

        CaptureLedger(root).fail("CAPTURE_API_NOT_CONFIGURED", "Not configured.")
        assertEquals(1, CaptureFiles(root).begin())
        assertFalse(File(root, "chunk-000.zip").exists())
    }

    @Test fun restoreReportsUnsentChunksAndKeepsAnInterruptedCapture() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(audio, 4, 0)
        files.writeFrame(byteArrayOf(1), text(5_000))
        files.writeAudio(audio, 4, 10_100)
        // The process dies here: no finish().

        val restored = CaptureFiles(root).restoreOrExpire(System.currentTimeMillis())
        assertNotNull(restored)
        assertEquals(CapturePhase.FINISHED, restored?.phase)
        assertEquals(10, restored?.seconds)
        assertTrue(restored?.hasLocalCapture == true)
        assertEquals(UploadStatus.PENDING, restored?.upload?.status)
        assertEquals(1, restored?.upload?.chunks)
        assertTrue(restored?.displayMessage.orEmpty().contains("Saved on device, not yet sent"))
        val manifest = manifest(root)
        assertTrue(manifest.finished)
        assertEquals(10_000L, manifest.durationMs)
        assertEquals(listOf(0), manifest.chunks.map { it.seq })
    }

    @Test fun aCaptureFromBeforeChunkingIsReplacedOnUpgrade() {
        val root = root().apply { mkdirs() }
        File(root, "playback-16000-mono-s16le.pcm").writeBytes(audio)
        File(root, "frame-91ms.jpg").writeBytes(byteArrayOf(1))
        File(root, "capture.json").writeText(
            """{"format":"PCM","durationMs":17000,"mediaBytes":4,"frames":1,""" +
                """"playbackSignalDetected":true,"stopReason":"Stop","researchConnected":false}"""
        )
        val restored = CaptureFiles(root).restoreOrExpire(System.currentTimeMillis())
        assertEquals(CapturePhase.IDLE, restored?.phase)
        assertTrue(restored?.message.orEmpty().contains("earlier app version"))
        assertFalse(root.exists())
    }

    @Test fun expiredCaptureIsRemovedWithoutTouchingSiblings() {
        val root = root()
        val sibling = File(temporary.root, "unrelated").apply { writeText("keep") }
        val files = CaptureFiles(root)
        files.begin()
        files.finish(1000, "Stop", false)
        val metadata = File(root, "capture.json")
        files.restoreOrExpire(metadata.lastModified() + CaptureFiles.RETENTION_MS)
        assertFalse(root.exists())
        assertEquals("keep", sibling.readText())
    }

    @Test fun missingManifestIsTreatedAsInterruptedAndDeleted() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.finish(1, "Stop", false)
        assertTrue(File(root, "capture.json").delete())
        assertNotNull(CaptureFiles(root).restoreOrExpire(System.currentTimeMillis()))
        assertFalse(root.exists())
    }

    @Test fun oversizeWriteIsRejectedBeforeStorageChanges() {
        val files = CaptureFiles(root())
        files.begin()
        assertThrows(StorageLimitException::class.java) {
            files.writeAudio(byteArrayOf(1), CaptureLimits.MAX_BYTES.toInt() + 1, 0)
        }
        assertEquals(0, files.bytes)
        files.delete()
    }
}
