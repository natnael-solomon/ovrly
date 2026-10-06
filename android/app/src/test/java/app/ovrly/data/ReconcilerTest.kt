package app.ovrly.data

import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.UploadCodec
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReconcilerTest {
    private val root: File = Files.createTempDirectory("ovrly-reconcile").toFile()
    private val bytes = ByteArray(50_000) { (it % 199).toByte() }
    private val sha = MessageDigest.getInstance("SHA-256").digest(bytes)
        .joinToString("") { "%02x".format(it) }

    @After
    fun cleanUp() {
        root.deleteRecursively()
    }

    private fun ApiTestServer.reconciler(live: LiveShares = LiveShares()) = Reconciler(
        api,
        jobs,
        repository(),
        root,
        maxBytes = 1_000_000,
        live = live
    )

    private fun ApiTestServer.seed(record: InvestigationRecord) =
        runBlocking { jobs.dao.upsert(record) }

    private fun ApiTestServer.state(localId: String) =
        runBlocking { jobs.dao.get(localId) }?.jobState

    private fun record(
        localId: String,
        state: LocalJobState,
        serverId: String? = null,
        sourceKind: String = "url",
        stagedPath: String? = null
    ) = InvestigationRecord(
        localId = localId,
        serverId = serverId,
        state = state.wireName,
        sourceKind = sourceKind,
        sourceUrl = "https://video.example/synthetic/clip-0001".takeIf { sourceKind == "url" },
        idempotencyKey = "stored-key-$localId",
        stagedPath = stagedPath,
        contentType = "video/mp4",
        durationMs = 60_000,
        sizeBytes = bytes.size.toLong(),
        sha256 = sha,

        createdAt = 1,
        updatedAt = 1
    )

    private fun staged(dir: String): File =
        File(File(root, dir).apply { mkdirs() }, "copy.part").apply { writeBytes(bytes) }

    @Test
    fun unfinishedAcceptedItemsTakeTheServerAnswer() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val id = "00000000-0000-4000-8000-000000000001"
        server.seed(record("a", LocalJobState.RUNNING, serverId = id))
        server.seed(record("done", LocalJobState.CANCELLED, serverId = "s-done"))
        server.json(200, ApiTestServer.result("complete").investigationPayload())
        val report = runBlocking { server.reconciler().reconcile() }!!
        assertEquals(1, report.refreshed)
        assertEquals(LocalJobState.SUCCEEDED, server.state("a"))
        assertEquals(LocalJobState.CANCELLED, server.state("done"))
        assertEquals(1, server.server.requestCount)
        assertFalse(runBlocking { server.jobs.cachedReport(id) }!!.stale)
    }

    @Test
    fun anUnreachableServerMarksOldProvisionalReportsStale() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val partial = ApiTestServer.result("partial").investigationPayload()
        server.json(200, partial)
        val id = "00000000-0000-4000-8000-000000000002"
        runBlocking { server.repository().refresh(id) }
        server.wall.addAndGet(StalenessPolicy().maxAgeMillis + 1)
        // OkHttp silently retries a dropped pooled connection; a stopped server cannot answer.
        server.server.shutdown()
        val report = runBlocking { server.reconciler().reconcile() }!!
        assertEquals(1, report.unreachable)
        assertEquals(LocalJobState.PARTIAL, server.state(id))
        assertTrue(runBlocking { server.jobs.cachedReport(id) }!!.stale)
    }

    @Test
    fun aPendingLinkIsCreatedWithItsStoredKey() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.seed(record("link", LocalJobState.LOCAL_PENDING))
        server.json(202, ApiTestServer.intakeResponse("investigation-create-url"))
        val report = runBlocking { server.reconciler().reconcile() }!!
        assertEquals(1, report.accepted)
        val create = server.take()
        assertEquals("stored-key-link", create.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        assertEquals(LocalJobState.QUEUED, server.state("link"))
        assertEquals(
            "00000000-0000-4000-8000-000000000401",
            runBlocking { server.jobs.dao.get("link") }!!.serverId
        )
    }

    @Test
    fun anInterruptedUploadCompletesTheDeclaredUploadFirst() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val declared = UploadCodec.encodeUpload(
            UploadCodec.parseUpload(ApiTestServer.intakeResponse("upload-declare"))
        )
        val file = staged("old-session")
        server.seed(
            record("upload", LocalJobState.UPLOADING, null, "upload", file.path)
                .copy(declaredUpload = declared)
        )
        server.json(200, ApiTestServer.intakeResponse("upload-complete"))
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        val report = runBlocking { server.reconciler().reconcile() }!!
        assertEquals(1, report.accepted)
        val paths = List(2) { server.take() }.map { "${it.method} ${it.path}" }
        assertEquals(
            listOf(
                "POST /v1/uploads/00000000-0000-4000-8000-000000000301/complete",
                "POST /v1/investigations"
            ),
            paths
        )
        assertEquals(LocalJobState.QUEUED, server.state("upload"))
        assertFalse("staged copy deleted once accepted", file.exists())
    }

    @Test
    fun aPendingUploadWithoutItsCopyFailsWithoutCallingTheServer() = withServer { server ->
        server.seed(
            record("lost", LocalJobState.LOCAL_PENDING, null, "upload", "/missing.part")
        )
        val report = runBlocking { server.reconciler().reconcile() }!!
        assertEquals(1, report.failed)
        assertEquals(LocalJobState.FAILED, server.state("lost"))
        assertEquals(0, server.server.requestCount)
    }

    @Test
    fun aRetryableFailureKeepsTheShareForTheNextPass() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.seed(record("link", LocalJobState.LOCAL_PENDING))
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true, action = "retry")
        runBlocking { server.reconciler().reconcile() }
        assertEquals(LocalJobState.LOCAL_PENDING, server.state("link"))
    }

    @Test
    fun sharesAnOpenSheetIsHandlingAreLeftAlone() = withServer { server ->
        server.seed(record("live", LocalJobState.UPLOADING))
        val live = LiveShares().apply { add("live") }
        val report = runBlocking { server.reconciler(live).reconcile() }!!
        assertEquals(0, report.retried)
        assertEquals(LocalJobState.UPLOADING, server.state("live"))
        assertEquals(0, server.server.requestCount)
    }

    @Test
    fun onlyStagingNothingWaitsForIsSwept() = withServer { server ->
        val waiting = staged("waiting")
        val orphan = staged("orphan")
        listOf(waiting.parentFile!!, orphan.parentFile!!).forEach { it.setLastModified(1_000) }
        server.seed(
            record("wait", LocalJobState.LOCAL_PENDING, null, "upload", waiting.path)
        )
        val live = LiveShares().apply { add("wait") }
        runBlocking { server.reconciler(live).reconcile() }
        assertTrue(waiting.exists())
        assertFalse(orphan.parentFile!!.exists())
    }

    @Test
    fun aLiveSheetsStagingIsNeverSweptEvenBeforeItHasARow() = withServer { server ->
        val sheet = staged("sheet")
        sheet.parentFile!!.setLastModified(1_000)
        val live = LiveShares().apply { addDirectory(sheet.parentFile!!) }
        runBlocking { server.reconciler(live).reconcile() }
        assertTrue(sheet.exists())
    }

    @Test
    fun aCompletedUploadIsNotSentAgainWhenOnlyTheCreateIsRetried() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val uploadId = "00000000-0000-4000-8000-000000000301"
        server.seed(
            record("done-upload", LocalJobState.LOCAL_PENDING, null, "upload", staged("s").path)
                .copy(uploadId = uploadId)
        )
        server.json(202, ApiTestServer.intakeResponse("investigation-create-upload"))
        runBlocking { server.reconciler().reconcile() }
        val create = server.take()
        assertEquals("/v1/investigations", create.path)
        assertEquals("stored-key-done-upload", create.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        assertTrue(uploadId in create.body.readUtf8())
        assertEquals(1, server.server.requestCount)
    }

    @Test
    fun aShareAbandonedDuringThePassIsNotRetried() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        // The sheet's background abandon lands between the snapshot and the retry.
        val racing = object : MemoryInvestigationDao() {
            override suspend fun all(): List<InvestigationRecord> =
                super.all().also { delete("link") }
        }
        val jobs = LocalJobs(racing, { server.wall.get() })
        runBlocking { racing.upsert(record("link", LocalJobState.LOCAL_PENDING)) }
        val reconciler = Reconciler(
            server.api,
            jobs,
            InvestigationRepository(server.api, jobs),
            root,
            1_000_000,
            LiveShares()
        )
        val report = runBlocking { reconciler.reconcile() }!!
        assertEquals(0, report.retried)
        assertEquals(0, server.server.requestCount)
    }

    @Test
    fun anAcceptedAnswerForAnAbandonedShareCachesNothing() = withServer { server ->
        val complete = InvestigationCodec.parseInvestigation(
            ApiTestServer.result("complete").investigationPayload()
        )
        assertNull(runBlocking { server.jobs.accepted("gone", complete) })
        assertNull(runBlocking { server.jobs.cachedReport(complete.id) })
    }
}
