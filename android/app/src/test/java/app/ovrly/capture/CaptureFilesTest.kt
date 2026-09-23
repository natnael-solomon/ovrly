package app.ovrly.capture

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

class CaptureFilesTest {
    @get:Rule val temporary = TemporaryFolder()

    private fun root() = File(temporary.root, "capture")

    @Test fun writesOnlySelectedPcmFramesAndMetadataThenClosesOutput() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(byteArrayOf(1, 0, 2, 0), 4)
        files.writeFrame(byteArrayOf(42, 43), 5000)
        files.finish(6000, "Explicit stop", true)
        assertEquals(6, files.bytes)
        assertEquals(1, files.frames)
        assertArrayEquals(byteArrayOf(1, 0, 2, 0), File(root, "playback-16000-mono-s16le.pcm").readBytes())
        assertTrue(File(root, "frame-5000ms.jpg").exists())
        assertTrue(File(root, "capture.json").readText().contains("\"researchConnected\": false"))
        assertThrows(IOException::class.java) { files.writeAudio(byteArrayOf(1), 1) }
        val restored = files.restoreOrExpire(System.currentTimeMillis())
        assertEquals(CapturePhase.FINISHED, restored?.phase)
        assertEquals(6, restored?.seconds)
        assertTrue(restored?.hasLocalCapture == true)
        files.delete()
        assertFalse(root.exists())
    }

    @Test fun newCaptureReplacesPreviousAndInterruptedDataExpiresOnOpen() {
        val root = root()
        val files = CaptureFiles(root)
        files.begin()
        files.writeFrame(byteArrayOf(1), 0)
        files.finish(1, "Stop", false)
        files.begin()
        assertFalse(File(root, "frame-0ms.jpg").exists())
        assertEquals(0, files.frames)
        files.finish(1, "Stop", false)
        assertTrue(File(root, "capture.json").delete())
        assertNotNull(CaptureFiles(root).restoreOrExpire(System.currentTimeMillis()))
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

    @Test fun oversizeWriteIsRejectedBeforeStorageChanges() {
        val files = CaptureFiles(root())
        files.begin()
        assertThrows(StorageLimitException::class.java) {
            files.writeAudio(byteArrayOf(1), CaptureLimits.MAX_BYTES.toInt() + 1)
        }
        assertEquals(0, files.bytes)
        files.delete()
    }
}
