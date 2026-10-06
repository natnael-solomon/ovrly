package app.ovrly.capture

import app.ovrly.contract.CaptureChunkRequest
import app.ovrly.contract.CaptureCreateRequest
import app.ovrly.contract.CaptureMetadata
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.Interval
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.Timebase
import java.io.File
import java.io.IOException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class CaptureUploaderTest {
    @get:Rule val temporary = TemporaryFolder()

    private val api = InMemoryCaptureSessionApi()
    private val root by lazy { File(temporary.root, "capture") }
    private val ledger by lazy { CaptureLedger(root) }

    /** A capture with [chunks] ten-second chunks; the last one is 7.5 s when [finished]. */
    private fun capture(chunks: Int, finished: Boolean = true): String {
        val files = CaptureFiles(root)
        files.begin()
        repeat(chunks) { seq ->
            files.writeAudio(byteArrayOf(1, 0, 2, 0), 4, seq * CaptureLimits.CHUNK_MS)
        }
        if (finished) {
            files.finish(chunks * CaptureLimits.CHUNK_MS - 2_500, "Stop", true)
        } else {
            files.advance(chunks * CaptureLimits.CHUNK_MS)
        }
        return requireNotNull(files.sessionId)
    }

    private fun sync(session: String, progress: MutableList<UploadProgress> = mutableListOf()) =
        runBlocking { CaptureUploader(api, root).sync(session) { progress += it } }

    private fun remote() = requireNotNull(ledger.remoteSessionId)

    private fun progressOf(): UploadProgress =
        UploadProgress.of(requireNotNull(CaptureFiles.manifestOrNull(root)), ledger, true)

    @Test fun everyChunkIsUploadedOnceInOrderAndReuploadIsIdempotent() {
        val session = capture(3)
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals(listOf("open", "put:0", "put:1", "put:2"), api.log)
        assertEquals(setOf(0, 1, 2), api.storedSeqs(remote()))
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals(4, api.log.size)
        assertEquals(UploadStatus.SENT, progressOf().status)

        // The acknowledgements were lost: the markers and the session id are gone locally.
        File(root, "sent").deleteRecursively()
        File(root, "remote.json").delete()
        val progress = mutableListOf<UploadProgress>()
        assertEquals(CaptureUploader.Outcome.DONE, sync(session, progress))
        assertEquals(setOf(0, 1, 2), api.storedSeqs(remote()))
        assertEquals(
            listOf("open", "put:0", "put:1", "put:2", "open", "put:0", "put:1", "put:2"),
            api.log
        )
        assertEquals(UploadStatus.SENDING, progress.first().status)
        assertEquals(UploadStatus.SENT, progress.last().status)
        assertTrue(progress.last().testServer)
    }

    @Test fun serverReplaysTheSessionAndRejectsDifferentBytesForASequence() {
        val session = capture(1)
        sync(session)
        val chunk = requireNotNull(CaptureFiles.manifestOrNull(root)).chunks.single()
        val opened = runBlocking { api.open(session, CaptureCreateRequest(10_000)) }
        assertEquals(remote(), opened.id)
        val other = byteArrayOf(9)
        val metadata = CaptureMetadata(
            CaptureChunkRequest(
                remote(),
                0,
                Interval(0, chunk.endMs, Timebase.CAPTURE),
                other.size.toLong(),
                ChunkPackage.sha256(other),
                SealedChunk.CONTENT_TYPE
            ),
            chunk.modality
        )
        val failure = runCatching {
            runBlocking { api.putChunk(remote(), 0, metadata, other) }
        }.exceptionOrNull() as CaptureApiException
        assertEquals("CAPTURE_CHUNK_CONFLICT", failure.code)
        assertFalse(failure.retryable)
    }

    @Test fun offlineChunksStayOnTheDeviceAndAreSentLater() {
        val session = capture(2)
        api.offline = true
        val progress = mutableListOf<UploadProgress>()
        assertEquals(CaptureUploader.Outcome.RETRY, sync(session, progress))
        assertEquals(UploadStatus.PENDING, progress.last().status)
        assertEquals(
            "Saved on device, not yet sent: 2 of 2 chunks.",
            progress.last().summary()
        )
        assertTrue(File(root, "chunk-000.zip").exists())
        assertTrue(File(root, "chunk-001.zip").exists())
        assertNull(ledger.failure)

        api.offline = false
        api.failingPuts = 1
        assertEquals(CaptureUploader.Outcome.RETRY, sync(session))
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals(setOf(0, 1), api.storedSeqs(remote()))
        assertTrue(File(root, "chunk-000.zip").exists())
    }

    @Test fun closeWithContinueWaitsForTheEndOfRecording() {
        val session = capture(2, finished = false)
        assertTrue(ledger.recordChoice(true))
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertFalse(api.log.any { it.startsWith("close") })

        val stopped = requireNotNull(CaptureFiles.manifestOrNull(root))
            .copy(finished = true, durationMs = 20_000)
        File(root, "capture.json").writeText(stopped.toJson())
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals("close:true", api.log.last())
        assertTrue(ledger.closed)
        val status = runBlocking { api.status(remote()) }
        assertEquals(true, status.continueResearch)
        assertEquals(CaptureSessionState.CLOSED, status.session.state)
        assertEquals(20_000, status.manifest.durationMs)
        assertEquals(UploadStatus.CLOSED, progressOf().status)

        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals(1, api.log.count { it.startsWith("close") })
    }

    @Test fun closeWithoutContinuingSendsNothingMoreAndCannotBeChanged() {
        val session = capture(3)
        sync(session)
        assertTrue(ledger.recordChoice(false))
        assertFalse(ledger.recordChoice(true))
        assertTrue(ledger.recordChoice(false))
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals("close:false", api.log.last())
        val status = runBlocking { api.status(remote()) }
        assertEquals(false, status.continueResearch)
        assertEquals(27_500, status.manifest.durationMs)
        assertTrue(status.work.all { it.processingStatus == ProcessingStatus.CANCELLED })
        assertEquals(UploadStatus.CLOSED, progressOf().status)
    }

    @Test fun notContinuingBeforeAnyUploadNeverContactsTheServer() {
        val session = capture(2)
        assertTrue(ledger.recordChoice(false))
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertTrue(api.log.isEmpty())
        assertTrue(ledger.closed)
        assertTrue(File(root, "chunk-000.zip").exists())
    }

    @Test fun anUnconfiguredServerStopsWithoutDeletingChunks() {
        val session = capture(2)
        val progress = mutableListOf<UploadProgress>()
        val outcome = runBlocking {
            CaptureUploader(ServerCaptureSessionApi(), root).sync(session) { progress += it }
        }
        assertEquals(CaptureUploader.Outcome.STOPPED, outcome)
        assertNotNull(ledger.failure)
        assertEquals(UploadStatus.NOT_SENT, progress.last().status)
        assertTrue(progress.last().summary().orEmpty().startsWith("Saved on device, not yet sent"))
        assertTrue(File(root, "chunk-001.zip").exists())
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertTrue(api.log.isEmpty())
    }

    @Test fun aCaptureWithoutChunksNeverOpensAServerSession() {
        val files = CaptureFiles(root)
        files.begin()
        files.finish(3_000, "Stopped before the first chunk", false)
        val session = requireNotNull(files.sessionId)
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertTrue(api.log.isEmpty())
        assertNull(ledger.failure)
        assertTrue(CaptureLedger(root, session).recordChoice(true))
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertTrue(api.log.isEmpty())
        assertTrue(ledger.closed)
        val unconfigured = runBlocking {
            CaptureUploader(ServerCaptureSessionApi(), root).sync(session)
        }
        assertEquals(CaptureUploader.Outcome.DONE, unconfigured)
        assertNull(ledger.failure)
    }

    @Test fun aNewCaptureWaitsForAChoiceThatHasNotBeenClosed() {
        val session = capture(1)
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertTrue(CaptureLedger(root, session).recordChoice(true))
        api.offline = true
        assertEquals(CaptureUploader.Outcome.RETRY, sync(session))
        val refused = runCatching { CaptureFiles(root).begin() }.exceptionOrNull()
        assertTrue(refused is UnsentCaptureException)
        assertTrue(refused?.message.orEmpty().contains("continue-research choice"))
        assertEquals(true, ledger.choice)
        assertNotNull(ledger.remoteSessionId)

        api.offline = false
        assertEquals(CaptureUploader.Outcome.DONE, sync(session))
        assertEquals("close:true", api.log.last())
        assertEquals(0, CaptureFiles(root).begin())
    }

    @Test fun aWorkerOfAReplacedCaptureCannotWriteIntoTheNewOne() {
        val old = capture(1)
        assertEquals(CaptureUploader.Outcome.DONE, sync(old))
        val stale = CaptureLedger(root, old)
        val files = CaptureFiles(root)
        files.begin()
        val current = requireNotNull(files.sessionId)
        assertTrue(runCatching { stale.markClosed(true) }.exceptionOrNull() is IOException)
        assertTrue(runCatching { stale.fail("X", "late") }.exceptionOrNull() is IOException)
        assertTrue(runCatching { stale.markSent(0) }.exceptionOrNull() is IOException)
        assertEquals(CaptureUploader.Outcome.DONE, sync(old))
        val fresh = CaptureLedger(root, current)
        assertFalse(fresh.closed)
        assertNull(fresh.failure)
        assertFalse(fresh.isSent(0))
        assertTrue(fresh.recordChoice(false))
    }

    @Test fun anOlderSessionIsIgnored() {
        capture(1)
        assertEquals(CaptureUploader.Outcome.DONE, sync("00000000-0000-4000-8000-000000000000"))
        assertTrue(api.log.isEmpty())
    }
}
