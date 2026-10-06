package app.ovrly.share

import app.ovrly.data.ApiTestServer
import app.ovrly.data.CheckStatus
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LocalJobState
import app.ovrly.data.OvrlyApi
import app.ovrly.data.jobState
import app.ovrly.data.withServer
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileNotFoundException
import java.io.InputStream
import java.nio.file.Files
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareIntakeTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val directory: File = Files.createTempDirectory("ovrly-staging").toFile()
    private val bytes = ByteArray(200_000) { (it % 251).toByte() }
    private val sha = MessageDigest.getInstance("SHA-256").digest(bytes).toHex()
    private val limits = ShareLimits(maxBytes = 1_000_000)
    private val keys = AtomicInteger()

    @After
    fun cleanUp() {
        scope.cancel()
        directory.deleteRecursively()
    }

    private fun facts(
        type: String? = "video/mp4",
        container: String? = "video/mp4",
        video: Boolean = true,
        duration: Long? = 60_000,
        size: Long? = 1_000
    ) = VideoFacts(type, container, video, duration, size)

    private fun check(facts: VideoFacts) = SharePolicy.checkVideo(facts, limits)

    @Test
    fun deviceChecksRejectBeforeAnyUpload() {
        assertNull(check(facts()))
        assertNull(check(facts(duration = 600_000, size = null)))
        assertEquals(ShareProblem.NOT_VIDEO, check(facts(type = "image/png")))
        assertEquals(ShareProblem.NOT_VIDEO, check(facts(type = null)))
        assertEquals(ShareProblem.NOT_VIDEO, check(facts(video = false)))
        assertEquals(ShareProblem.NOT_VIDEO, check(facts(container = "audio/mp4")))
        assertEquals(ShareProblem.NO_DURATION, check(facts(duration = null)))
        assertEquals(ShareProblem.NO_DURATION, check(facts(duration = 0)))
        assertEquals(ShareProblem.TOO_LONG, check(facts(duration = 600_001)))
        assertEquals(ShareProblem.TOO_LARGE, check(facts(size = 1_000_001)))
        assertEquals(ShareProblem.UNREADABLE, check(facts(size = 0)))
        assertEquals("video/webm", facts(type = "video/mp4", container = "video/webm").contentType)
        assertEquals("video/mp4", facts(container = null).contentType)
    }

    @Test
    fun stagingStreamsHashesAndStopsPastTheCap() {
        val staging = ShareStaging(directory)
        val staged = staging.stage({ ByteArrayInputStream(bytes) }, bytes.size.toLong())
        assertEquals(bytes.size.toLong(), staged.sizeBytes)
        assertEquals(sha, staged.sha256)
        assertTrue(staged.file.readBytes().contentEquals(bytes))
        val failure = runCatching {
            staging.stage({ ByteArrayInputStream(bytes) }, bytes.size - 1L)
        }.exceptionOrNull()
        assertTrue(failure is StagingLimitExceeded)
        assertEquals(listOf(staged.file), directory.listFiles()!!.toList())
        staging.clear()
        assertFalse(directory.exists())
    }

    @Test
    fun eachIntakeOwnsItsStagingAndOnlyOldOrphansAreSwept() {
        val first = ShareStaging(File(directory, "first"))
        val kept = first.stage({ ByteArrayInputStream(bytes) }, limits.maxBytes)
        // A second intake (another share target instance) must not touch the first one's copy.
        controller(null, staging = ShareStaging(File(directory, "second")))
        assertTrue(kept.file.exists())
        val orphan = File(directory, "orphan").apply { mkdirs() }
        File(orphan, "left.part").writeBytes(bytes)
        orphan.setLastModified(1_000)
        ShareStaging.sweepOrphans(directory, cutoffMillis = 2_000_000)
        assertFalse(orphan.exists())
        assertTrue(kept.file.exists())
    }

    @Test
    fun dismissingDuringStagingStopsTheCopyDeletesItAndPublishesNothingStale() {
        val total = 4L * 1024 * 1024
        val read = AtomicLong()
        val slow = object : InputStream() {
            override fun read(): Int = error("single-byte reads are not used")

            override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                if (read.get() >= total) return -1
                Thread.sleep(5)
                val count = minOf(minOf(length, SLOW_CHUNK).toLong(), total - read.get()).toInt()
                read.addAndGet(count.toLong())
                return count
            }
        }
        val intake = controller(null, limits = ShareLimits(maxBytes = total))
        scope.launch { runCatching { intake.accept { video(total) { slow } } } }
        intake.awaitState { it is IntakeState.Staging && it.copiedBytes > 0 }
        intake.onAction(IntakeAction.DISMISS)
        Thread.sleep(500)
        assertEquals(IntakeState.Idle, intake.state.value)
        assertTrue("copy kept running after dismiss", read.get() < total)
        assertTrue(directory.walkBottomUp().none { it.isFile })
    }

    @Test
    fun aLostPutResponseIsCompletedOnRetryWithoutUploadingAgain() = withServer { server ->
        server.guest()
        server.json(201, ApiTestServer.intakeResponse("upload-declare"))
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        assertTrue((intake.state.value as IntakeState.Failed).retryable)
        val interrupted = runBlocking { server.jobs.dao.all() }.single()
        assertEquals(LocalJobState.LOCAL_PENDING, interrupted.jobState)
        assertTrue("declared upload kept for the retry", interrupted.declaredUpload != null)
        server.json(200, ApiTestServer.intakeResponse("upload-complete"))
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        intake.onAction(IntakeAction.RETRY)
        intake.awaitState { it is IntakeState.Tracking }
        val paths = List(6) { server.take() }.map { "${it.method} ${it.path}" }
        assertEquals(1, paths.count { it == "POST /v1/uploads" })
        assertEquals(1, paths.count { it.startsWith("PUT ") })
        assertEquals(1, paths.count { it.endsWith("/complete") })
    }

    @Test
    fun missingBytesAreSentAgainToTheSameUpload() = withServer { server ->
        server.guest()
        server.json(201, ApiTestServer.intakeResponse("upload-declare"))
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        server.error(409, "UPLOAD_CONTENT_MISSING", action = "upload_again")
        server.enqueue(MockResponse().setResponseCode(204))
        server.json(200, ApiTestServer.intakeResponse("upload-complete"))
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        intake.onAction(IntakeAction.RETRY)
        intake.awaitState { it is IntakeState.Tracking }
        val paths = List(8) { server.take() }.map { "${it.method} ${it.path}" }
        assertEquals(1, paths.count { it == "POST /v1/uploads" })
        assertEquals(2, paths.count { it.startsWith("PUT ") })
    }

    @Test
    fun anExpiredUploadIsDeclaredAgain() = withServer { server ->
        server.guest()
        server.json(201, ApiTestServer.intakeResponse("upload-declare"))
        server.error(410, "UPLOAD_EXPIRED", action = "upload_again")
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        assertTrue((intake.state.value as IntakeState.Failed).retryable)
        server.error(410, "UPLOAD_EXPIRED", action = "upload_again")
        server.uploadSequence(mint = false)
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        intake.onAction(IntakeAction.RETRY)
        intake.awaitState { it is IntakeState.Tracking }
        val paths = List(9) { server.take() }.map { "${it.method} ${it.path}" }
        assertEquals(2, paths.count { it == "POST /v1/uploads" })
    }

    private fun controller(
        server: ApiTestServer?,
        staging: ShareStaging = ShareStaging(directory),
        limits: ShareLimits = this.limits
    ) = ShareIntakeController(
        scope,
        IntakeDependencies(
            service = server?.services(),
            staging = staging,
            limits = limits,
            idempotencyKeys = { "synthetic-key-${keys.incrementAndGet()}" }
        ),
        Dispatchers.IO
    )

    private fun video(
        size: Long = bytes.size.toLong(),
        open: () -> InputStream = { ByteArrayInputStream(bytes) }
    ) = ShareRead.Accepted(ShareCandidate.Video("video/mp4", 60_000, size, open))

    private fun ShareIntakeController.awaitState(predicate: (IntakeState) -> Boolean) =
        runBlocking { withTimeout(5_000) { state.first(predicate) } }

    private fun ApiTestServer.seedAccepted(shareKey: String, serverId: String) = runBlocking {
        jobs.dao.upsert(
            InvestigationRecord(
                localId = "seed",
                serverId = serverId,
                state = LocalJobState.QUEUED.wireName,
                sourceKind = "upload",
                idempotencyKey = "seed-key",
                shareKey = shareKey,
                createdAt = 1,
                updatedAt = 1
            )
        )
    }

    private fun ApiTestServer.uploadSequence(mint: Boolean = true) {
        if (mint) guest()
        json(201, ApiTestServer.intakeResponse("upload-declare"))
        enqueue(MockResponse().setResponseCode(204))
        json(200, ApiTestServer.intakeResponse("upload-complete"))
    }

    @Test
    fun aSharedFileIsStagedUploadedAndCreatedWithAKey() = withServer { server ->
        server.uploadSequence()
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        val intake = controller(server)
        val summary = runBlocking { intake.accept { video() } }
        assertTrue(summary.accepted)
        server.take()
        val declare = server.take()
        assertEquals("/v1/uploads", declare.path)
        assertEquals(
            """{"size_bytes":${bytes.size},"sha256":"$sha","content_type":"video/mp4"}""",
            declare.body.readUtf8()
        )
        val put = server.take()
        assertEquals("PUT", put.method)
        val uploadPath = "/v1/uploads/00000000-0000-4000-8000-000000000301"
        assertEquals("$uploadPath/content", put.path)
        assertTrue(put.body.readByteArray().contentEquals(bytes))
        assertEquals("$uploadPath/complete", server.take().path)
        val create = server.take()
        assertEquals("synthetic-key-1", create.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        assertEquals(
            """{"source":{"kind":"upload","upload_id":"00000000-0000-4000-8000-000000000301",""" +
                """"duration_ms":60000}}""",
            create.body.readUtf8()
        )
        val tracked = intake.awaitState {
            (it as? IntakeState.Tracking)?.status == CheckStatus.COMPLETE
        } as IntakeState.Tracking
        assertEquals("00000000-0000-4000-8000-000000000402", tracked.investigationId)
        val record = runBlocking { server.jobs.dao.byServerId(tracked.investigationId) }!!
        assertEquals(LocalJobState.QUEUED, record.jobState)
        assertEquals("synthetic-key-1", record.idempotencyKey)
        assertNull("accepted shares keep no staged path", record.stagedPath)
        assertEquals(
            tracked.investigationId,
            runBlocking { server.jobs.findAccepted(ShareKeys.file(sha)) }
        )
        assertEquals("staged copy deleted after upload", 0, directory.listFiles()!!.size)
    }

    @Test
    fun aDuplicateShareOffersTheExistingInvestigation() = withServer { server ->
        server.seedAccepted(ShareKeys.file(sha), "00000000-0000-4000-8000-000000000402")
        server.guest()
        server.json(200, ApiTestServer.intakeResponse("investigation-create-upload"))
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        val duplicate = intake.state.value as IntakeState.Duplicate
        assertEquals("00000000-0000-4000-8000-000000000402", duplicate.investigationId)
        assertEquals(CheckStatus.WAITING, duplicate.status)
        assertEquals(2, server.server.requestCount)
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        intake.onAction(IntakeAction.OPEN_EXISTING)
        intake.awaitState { it is IntakeState.Tracking }
    }

    @Test
    fun anExpiredDuplicateIsForgottenAndTheShareContinues() = withServer { server ->
        server.seedAccepted(ShareKeys.file(sha), "00000000-0000-4000-8000-000000000499")
        server.guest()
        server.error(404, "NOT_FOUND")
        server.json(201, ApiTestServer.intakeResponse("upload-declare"))
        server.enqueue(MockResponse().setResponseCode(204))
        server.json(200, ApiTestServer.intakeResponse("upload-complete"))
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        assertTrue(intake.state.value is IntakeState.Tracking)
        val found = runBlocking { server.jobs.findAccepted(ShareKeys.file(sha)) }
        assertEquals("00000000-0000-4000-8000-000000000402", found)
        val gone = runBlocking { server.jobs.dao.get("seed") }!!
        assertEquals(LocalJobState.FAILED, gone.jobState)
        assertEquals("NOT_FOUND", gone.errorCode)
    }

    @Test
    fun dismissingBeforeAcceptanceForgetsTheShare() = withServer { server ->
        server.guest()
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        val intake = controller(server)
        runBlocking {
            intake.accept {
                ShareRead.Accepted(ShareCandidate.Link("https://video.example/synthetic/clip-0001"))
            }
        }
        assertEquals(1, runBlocking { server.jobs.dao.all() }.size)
        intake.onAction(IntakeAction.DISMISS)
        runBlocking { withTimeout(5_000) { while (server.jobs.dao.all().isNotEmpty()) delay(10) } }
    }

    @Test
    fun uploadProgressIsStoredBeforeTheNextCall() = withServer { server ->
        server.guest()
        server.json(201, ApiTestServer.intakeResponse("upload-declare"))
        server.enqueue(MockResponse().setResponseCode(204).setHeadersDelay(1, TimeUnit.SECONDS))
        server.json(200, ApiTestServer.intakeResponse("upload-complete"))
        server.enqueue(
            MockResponse().setResponseCode(202).setHeadersDelay(1, TimeUnit.SECONDS)
                .setHeader("Content-Type", "application/json")
                .setBody(ApiTestServer.intakeResponse("investigation-create-upload"))
        )
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        val intake = controller(server)
        scope.launch { intake.accept { video() } }
        // While the PUT is in flight, a dead process could already resume the declared upload.
        val declared = awaitRecord(server) { it.declaredUpload != null }
        assertEquals(LocalJobState.UPLOADING, declared.jobState)
        assertNull(declared.uploadId)
        // While the create is in flight, a retry would reuse the completed upload and the key.
        val completed = awaitRecord(server) { it.uploadId != null }
        assertEquals("00000000-0000-4000-8000-000000000301", completed.uploadId)
        assertNull(completed.serverId)
        intake.awaitState { it is IntakeState.Tracking }
    }

    private fun awaitRecord(server: ApiTestServer, predicate: (InvestigationRecord) -> Boolean) =
        runBlocking {
            withTimeout(5_000) {
                var found: InvestigationRecord? = null
                while (found == null) {
                    found = server.jobs.dao.all().firstOrNull(predicate)
                    if (found == null) delay(10)
                }
                found
            }
        }

    @Test
    fun aSharedLinkCreatesAUrlSourceInvestigation() = withServer { server ->
        server.guest()
        server.json(202, ApiTestServer.intakeResponse("investigation-create-url"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        val intake = controller(server)
        runBlocking {
            intake.accept {
                ShareRead.Accepted(ShareCandidate.Link("https://video.example/synthetic/clip-0001"))
            }
        }
        server.take()
        val create = server.take()
        assertEquals(
            """{"source":{"kind":"url","url":"https://video.example/synthetic/clip-0001"}}""",
            create.body.readUtf8()
        )
        assertTrue(create.getHeader(OvrlyApi.IDEMPOTENCY_HEADER)!!.isNotEmpty())
    }

    @Test
    fun aRetryReplaysTheSameKeyWithoutUploadingAgain() = withServer { server ->
        server.uploadSequence()
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        val failed = intake.state.value as IntakeState.Failed
        assertTrue(failed.retryable)
        assertEquals("synthetic-request", failed.requestId)
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        intake.onAction(IntakeAction.RETRY)
        intake.awaitState { it is IntakeState.Tracking }
        val sent = List(7) { server.take() }
        val creates = sent.filter { it.path == "/v1/investigations" }
        assertEquals(2, creates.size)
        creates.forEach {
            assertEquals("synthetic-key-1", it.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        }
        assertEquals(1, sent.count { it.path == "/v1/uploads" })
    }

    @Test
    fun rejectionsNeverReachTheServer() = withServer { server ->
        val intake = controller(server)
        runBlocking { intake.accept { ShareRead.Rejected(ShareProblem.PRIVATE) } }
        val rejected = intake.state.value as IntakeState.Rejected
        assertEquals(ShareProblem.PRIVATE, rejected.problem)
        assertTrue(rejected.problem.offersFile)
        runBlocking { intake.accept { video { throw FileNotFoundException("grant expired") } } }
        assertEquals(ShareProblem.EXPIRED, (intake.state.value as IntakeState.Rejected).problem)
        runBlocking { intake.accept { video { throw SecurityException("revoked") } } }
        assertEquals(ShareProblem.EXPIRED, (intake.state.value as IntakeState.Rejected).problem)
        val small = ShareIntakeController(
            scope,
            IntakeDependencies(null, ShareStaging(directory), ShareLimits(10))
        )
        runBlocking { small.accept { video() } }
        assertEquals(ShareProblem.TOO_LARGE, (small.state.value as IntakeState.Rejected).problem)
        runBlocking { small.accept { video { ByteArrayInputStream(ByteArray(0)) } } }
        assertEquals(ShareProblem.UNREADABLE, (small.state.value as IntakeState.Rejected).problem)
        assertEquals(0, server.server.requestCount)
        assertEquals(0, directory.listFiles()!!.size)
    }

    @Test
    fun serverLimitsMapToTheSameCopyAsDeviceLimits() = withServer { server ->
        server.guest()
        server.error(413, "UPLOAD_TOO_LARGE", action = "fix_request")
        val intake = controller(server)
        runBlocking { intake.accept { video() } }
        assertEquals(ShareProblem.TOO_LARGE, (intake.state.value as IntakeState.Rejected).problem)
        val tooLong = failureState(serverFailure("DURATION_LIMIT_EXCEEDED"), 1)
        assertEquals(ShareProblem.TOO_LONG, (tooLong as IntakeState.Rejected).problem)
        val expired = failureState(serverFailure("UPLOAD_EXPIRED"), 1) as IntakeState.Failed
        assertTrue(expired.retryable)
        val unknown = failureState(serverFailure("FUTURE_CODE"), 1) as IntakeState.Failed
        assertFalse(unknown.retryable)
        assertTrue("FUTURE_CODE" in unknown.message)
    }

    @Test
    fun aBuildWithoutAServiceStopsBeforeUploading() {
        val intake = controller(null)
        runBlocking {
            intake.accept {
                ShareRead.Accepted(ShareCandidate.Link("https://video.example/synthetic/clip-0001"))
            }
        }
        val failed = intake.state.value as IntakeState.Failed
        assertFalse(failed.retryable)
        assertFalse(summary(failed).accepted)
    }

    private companion object {
        const val SLOW_CHUNK = 16 * 1024
    }

    private fun serverFailure(code: String) = app.ovrly.data.ApiFailure.Server(
        409,
        app.ovrly.contract.ErrorCodec.parseError(
            """{"code":"$code","message":"m","retryable":false,"action":"none","request_id":"r"}"""
        )
    )
}
