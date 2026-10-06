package app.ovrly.data

import app.ovrly.capture.CaptureApiException
import app.ovrly.capture.CaptureFiles
import app.ovrly.capture.CaptureLedger
import app.ovrly.capture.CaptureUploader
import app.ovrly.capture.ChunkPackage
import app.ovrly.capture.ServerCaptureSessionApi
import app.ovrly.contract.CaptureApiCodec
import app.ovrly.contract.CaptureChunkRequest
import app.ovrly.contract.CaptureCloseRequest
import app.ovrly.contract.CaptureCreateRequest
import app.ovrly.contract.CaptureMetadata
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.ChunkDisposition
import app.ovrly.contract.Interval
import app.ovrly.contract.Modality
import app.ovrly.contract.Timebase
import java.io.File
import java.io.IOException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class CaptureApiTest {
    @get:Rule val temporary = TemporaryFolder()

    private val sessionId = "00000000-0000-4000-8000-000000000501"

    private fun ApiTestServer.capture() = CaptureApi(api)

    private fun ApiTestServer.signedIn() = apply { credentials.write("synthetic-token-1") }

    private fun session(id: String = sessionId, state: String = "open") =
        """{"id":"$id","investigation_id":"$id","state":"$state","timebase":"capture",""" +
            """"started_at":"2026-10-06T08:00:00Z",""" +
            """"closed_at":${if (state == "open") "null" else "\"2026-10-06T08:01:00Z\""},""" +
            """"max_duration_ms":180000,"chunk_duration_ms":10000,"chunks_received":0,""" +
            """"highest_seq":null,"received_ms":0,"gaps":[],""" +
            """"duplicate_handling":"replay_acknowledgement",""" +
            """"out_of_order_handling":"accept_and_record_gaps"}"""

    private fun chunk(seq: Int, endMs: Long, size: Long, sha: String, disposition: String) =
        """{"session_id":"$sessionId","seq":$seq,""" +
            """"interval":{"start_ms":${seq * 10_000},"end_ms":$endMs,"timebase":"capture"},""" +
            """"size_bytes":$size,"sha256":"$sha","disposition":"$disposition",""" +
            """"received_at":"2026-10-06T08:00:11Z","gaps":[]}"""

    private fun metadata(content: ByteArray, seq: Int = 0) = CaptureMetadata(
        CaptureChunkRequest(
            sessionId,
            seq,
            Interval(seq * 10_000L, seq * 10_000L + 10_000, Timebase.CAPTURE),
            content.size.toLong(),
            ChunkPackage.sha256(content),
            "application/zip"
        ),
        Modality.SPEECH
    )

    @Test
    fun openSendsTheIdempotencyKeyAndAReplayReturnsTheSameSession() = withServer { raw ->
        val server = raw.signedIn()
        server.json(201, session())
        server.json(201, session())
        val request = CaptureCreateRequest(10_000)
        val first = runBlocking { server.capture().open("local-session-1", request) }
        val replay = runBlocking { server.capture().open("local-session-1", request) }
        assertEquals(
            (first as ApiResult.Success).value,
            (replay as ApiResult.Success).value
        )
        assertEquals(CaptureSessionState.OPEN, first.value.state)
        repeat(2) {
            val sent = server.take()
            assertEquals("POST", sent.method)
            assertEquals("/v1/captures", sent.path)
            assertEquals("local-session-1", sent.getHeader("Idempotency-Key"))
            assertEquals("Bearer synthetic-token-1", sent.getHeader("Authorization"))
            assertTrue(sent.getHeader("X-Request-Id").orEmpty().isNotEmpty())
            // The contract encoder omits defaults; the server reads {} as 10000 ms.
            val body = CaptureApiCodec.parseCreateRequest(sent.body.readUtf8())
            assertEquals(10_000, body.chunkDurationMs)
        }
    }

    @Test
    fun aChunkIsSentAsMultipartAndARetryIsAcknowledgedAsDuplicate() = withServer { raw ->
        val server = raw.signedIn()
        val content = byteArrayOf(1, 2, 3, 4)
        val sha = ChunkPackage.sha256(content)
        server.json(200, ApiTestServer.intakeResponse("capture-status-waiting"))
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true)
        server.json(200, chunk(0, 10_000, 4, sha, "duplicate"))
        val adapter = ServerCaptureSessionApi(server.capture())
        // A first call is "cold" and would retry a 503 itself; warm the client first.
        runBlocking { adapter.status(sessionId) }
        server.take()
        val busy = runCatching {
            runBlocking { adapter.putChunk(sessionId, 0, metadata(content), content) }
        }.exceptionOrNull() as CaptureApiException
        assertEquals("DATABASE_UNAVAILABLE", busy.code)
        assertTrue(busy.retryable)
        val stored = runBlocking { adapter.putChunk(sessionId, 0, metadata(content), content) }
        assertEquals(ChunkDisposition.DUPLICATE, stored.disposition)
        assertTrue(stored.isStored)
        repeat(2) {
            val sent = server.take()
            assertEquals("PUT", sent.method)
            assertEquals("/v1/captures/$sessionId/chunks/0", sent.path)
            val type = sent.getHeader("Content-Type").orEmpty()
            assertTrue(type.startsWith("multipart/form-data"))
            val body = sent.body.readUtf8()
            assertTrue(body.contains("""name="metadata""""))
            assertTrue(body.contains(""""sha256":"$sha""""))
            assertTrue(body.contains(""""modality":"speech""""))
            assertTrue(body.contains("""name="content"; filename="chunk-0""""))
            assertTrue(body.contains("Content-Type: application/zip"))
        }
    }

    @Test
    fun closeSendsTheChoiceAndTheDuration() = withServer { raw ->
        val server = raw.signedIn()
        server.json(200, session(state = "closed"))
        val closed = runBlocking {
            ServerCaptureSessionApi(server.capture())
                .close(sessionId, CaptureCloseRequest(false, 27_500))
        }
        assertEquals(CaptureSessionState.CLOSED, closed.state)
        val sent = server.take()
        assertEquals("/v1/captures/$sessionId/close", sent.path)
        assertEquals("""{"continue_research":false,"duration_ms":27500}""", sent.body.readUtf8())
    }

    @Test
    fun statusParsesTheContractFixture() = withServer { raw ->
        val server = raw.signedIn()
        server.json(200, ApiTestServer.intakeResponse("capture-status-waiting"))
        val status = runBlocking { ServerCaptureSessionApi(server.capture()).status(sessionId) }
        assertEquals(sessionId, status.session.id)
        assertEquals(true, status.continueResearch)
        assertEquals("GET", server.take().method)
    }

    @Test
    fun failuresMapToRetryableOrPermanentErrors() = withServer { raw ->
        val server = raw.signedIn()
        val adapter = ServerCaptureSessionApi(server.capture())
        server.error(409, "CAPTURE_CLOSED", action = "fix_request")
        val closed = runCatching { runBlocking { adapter.status(sessionId) } }
            .exceptionOrNull() as CaptureApiException
        assertEquals("CAPTURE_CLOSED", closed.code)
        assertFalse(closed.retryable)

        server.json(200, """{"unexpected":true}""")
        val odd = runCatching { runBlocking { adapter.status(sessionId) } }
            .exceptionOrNull() as CaptureApiException
        assertEquals(ServerCaptureSessionApi.INCOMPATIBLE, odd.code)
        assertFalse(odd.retryable)

        val unconfigured = runCatching {
            runBlocking { ServerCaptureSessionApi().status(sessionId) }
        }.exceptionOrNull() as CaptureApiException
        assertEquals(ServerCaptureSessionApi.NOT_CONFIGURED, unconfigured.code)

        server.server.shutdown()
        val offline = runCatching { runBlocking { adapter.status(sessionId) } }.exceptionOrNull()
        assertTrue("got $offline", offline is IOException && offline !is CaptureApiException)
    }

    @Test
    fun theUploaderSendsARecordedChunkAndClosesThroughTheRealClient() = withServer { raw ->
        val server = raw.signedIn()
        val root = File(temporary.root, "capture")
        val files = CaptureFiles(root)
        files.begin()
        files.writeAudio(byteArrayOf(1, 0, 2, 0), 4, 0)
        files.finish(7_500, "Stop", true)
        val local = requireNotNull(files.sessionId)
        val sealed = requireNotNull(CaptureFiles.manifestOrNull(root)).chunks.single()
        server.json(201, session())
        server.json(200, chunk(0, 7_500, sealed.sizeBytes, sealed.sha256, "stored"))
        server.json(200, session(state = "closed"))
        val uploader = CaptureUploader(ServerCaptureSessionApi(server.capture()), root)
        assertTrue(CaptureLedger(root, local).recordChoice(true))
        assertEquals(CaptureUploader.Outcome.DONE, runBlocking { uploader.sync(local) })
        val open = server.take()
        assertEquals(local, open.getHeader("Idempotency-Key"))
        val put = server.take()
        assertEquals("/v1/captures/$sessionId/chunks/0", put.path)
        assertEquals(sealed.sizeBytes, File(root, sealed.fileName).length())
        val close = server.take()
        assertEquals("""{"continue_research":true,"duration_ms":7500}""", close.body.readUtf8())
        assertTrue(CaptureLedger(root, local).closed)
    }
}
