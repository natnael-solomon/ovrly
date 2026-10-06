package app.ovrly.data

import app.ovrly.capture.CaptureApiException
import app.ovrly.capture.CaptureLiveResultsFetcher
import app.ovrly.capture.InMemoryCaptureSessionApi
import app.ovrly.contract.CaptureChunkRequest
import app.ovrly.contract.CaptureCloseRequest
import app.ovrly.contract.CaptureCreateRequest
import app.ovrly.contract.CaptureMetadata
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.Interval
import app.ovrly.contract.Modality
import app.ovrly.contract.Timebase
import app.ovrly.overlay.LiveConnection
import app.ovrly.overlay.LivePollPolicy
import app.ovrly.overlay.LiveSessionPhase
import app.ovrly.overlay.PollingLiveResultsSource
import java.security.MessageDigest
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureLiveResultsFetcherTest {
    private val investigationId = "00000000-0000-4000-8000-000000000401"

    private fun openWithOneChunk(capture: InMemoryCaptureSessionApi): String = runBlocking {
        val session = capture.open("local-1", CaptureCreateRequest(10_000)).id
        val content = byteArrayOf(1, 2, 3)
        val sha = MessageDigest.getInstance("SHA-256").digest(content).toHexString()
        capture.putChunk(
            session,
            0,
            CaptureMetadata(
                CaptureChunkRequest(
                    session,
                    0,
                    Interval(0, 10_000, Timebase.CAPTURE),
                    content.size.toLong(),
                    sha,
                    "application/zip"
                ),
                Modality.SPEECH
            ),
            content
        )
        session
    }

    @Test
    fun readsTheCaptureStatusAndStoresTheInvestigationRead() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val capture = InMemoryCaptureSessionApi()
        val session = openWithOneChunk(capture)
        val repository = server.repository()
        val fetcher = CaptureLiveResultsFetcher(capture, repository)

        val status = runBlocking { fetcher.captureStatus(session) }
        assertEquals(CaptureSessionState.OPEN, status.session.state)
        assertEquals(10_000, status.manifest.durationMs)

        server.json(200, ApiTestServer.intakeResponse("investigation-create-url"))
        val investigation = runBlocking { fetcher.investigation(investigationId) }
        assertEquals(investigationId, investigation.id)
        assertEquals("/v1/investigations/$investigationId", server.take().path)
        assertNotNull(runBlocking { repository.cached(investigationId) })

        server.error(404, "NOT_FOUND", action = "none")
        val missing = runCatching { runBlocking { fetcher.investigation(investigationId) } }
            .exceptionOrNull() as CaptureApiException
        assertEquals("NOT_FOUND", missing.code)
    }

    @Test
    fun pollingThroughTheFetcherEndsWhenTheSessionClosesWithoutResearch() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val capture = InMemoryCaptureSessionApi()
        val session = openWithOneChunk(capture)
        runBlocking { capture.close(session, CaptureCloseRequest(false, 10_000)) }
        server.json(200, ApiTestServer.intakeResponse("investigation-create-url"))
        val source = PollingLiveResultsSource(
            CaptureLiveResultsFetcher(capture, server.repository()),
            LivePollPolicy(intervalMs = 1, closedIntervalMs = 1, maxBackoffMs = 1),
            sleep = {}
        )
        runBlocking { source.poll(session) }
        val results = source.results.value
        assertNotEquals(LiveSessionPhase.NOT_CONNECTED, results.phase)
        assertEquals(LiveConnection.OK, results.connection)
        assertEquals(1, server.server.requestCount)
        assertTrue(capture.log.contains("status"))
    }

    @Test
    fun pollingReportsALostConnectionWhenTheServerStaysDown() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val capture = InMemoryCaptureSessionApi().apply { offline = true }
        val source = PollingLiveResultsSource(
            CaptureLiveResultsFetcher(capture, server.repository()),
            LivePollPolicy(maxFailures = 3),
            sleep = {}
        )
        runBlocking { source.poll("00000000-0000-4000-8000-000000000501") }
        assertEquals(LiveConnection.LOST, source.results.value.connection)
        assertEquals(0, server.server.requestCount)
    }
}
