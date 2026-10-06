package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ProcessingStatus
import java.io.File
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalStoreTest {
    private val clock = AtomicLong(1_700_000_000_000)
    private val dao = MemoryInvestigationDao()
    private val jobs = LocalJobs(dao, { clock.get() }, StalenessPolicy(maxAgeMillis = 600_000))

    private fun result(name: String): Investigation = InvestigationCodec.parseInvestigation(
        ApiTestServer.result(name).investigationPayload()
    )

    private fun payload(name: String) =
        ContractFixtures.element(ApiTestServer.result(name).investigationPayload())

    private fun record(localId: String, state: LocalJobState, serverId: String? = null) =
        InvestigationRecord(
            localId = localId,
            serverId = serverId,
            state = state.wireName,
            sourceKind = "url",
            sourceUrl = "https://video.example/synthetic/clip-0001",
            idempotencyKey = "key-$localId",
            shareKey = "url:$localId",
            createdAt = 1,
            updatedAt = 1
        )

    @Test
    fun aShareAcceptedAfterTheListImportedItKeepsOneRowAndItsOwn() = runBlocking {
        val partial = result("partial")
        jobs.startShare(record("share-1", LocalJobState.UPLOADING))
        // The create answer was slow or lost; a server list read the investigation first.
        jobs.recordListed(partial, import = true)
        assertEquals(2, dao.all().size)
        val stored = jobs.accepted("share-1", partial)!!
        val rows = dao.all()
        assertEquals(listOf("share-1"), rows.map { it.localId })
        assertEquals(partial.id, rows.single().serverId)
        assertEquals("key-share-1", rows.single().idempotencyKey)
        assertEquals(LocalJobState.PARTIAL, stored.jobState)
        // Later reads and accepts of the same id stay on the share's row.
        jobs.recordRead(partial)
        jobs.accepted("share-1", partial)
        assertEquals(listOf("share-1"), dao.all().map { it.localId })
    }

    @Test
    fun theMergeRunsInOneTransactionAndOnlyWhenAnImportExists() = runBlocking {
        var transactions = 0
        val merged = LocalJobs(
            dao,
            { clock.get() },
            transaction = { block ->
                transactions++
                block()
            }
        )
        val partial = result("partial")
        merged.startShare(record("share-1", LocalJobState.UPLOADING))
        merged.startShare(record("share-2", LocalJobState.UPLOADING))
        merged.accepted("share-2", result("complete"))
        assertEquals(0, transactions)
        merged.recordListed(partial, import = true)
        merged.accepted("share-1", partial)
        assertEquals(1, transactions)
        assertEquals(listOf("share-1", "share-2"), dao.all().map { it.localId }.sorted())
    }

    @Test
    fun aListedIdIsNotImportedWhileAShareWaitsForItsAnswer() = runBlocking {
        val partial = result("partial")
        jobs.startShare(record("share-1", LocalJobState.LOCAL_PENDING))
        assertTrue(jobs.hasUnacceptedShares())
        jobs.recordListed(partial, import = !jobs.hasUnacceptedShares())
        assertEquals(listOf("share-1"), dao.all().map { it.localId })
        jobs.accepted("share-1", partial)
        assertFalse(jobs.hasUnacceptedShares())
        // A known id is still updated while another share is pending; nothing new is imported.
        jobs.startShare(record("share-2", LocalJobState.LOCAL_PENDING))
        clock.addAndGet(1_000)
        jobs.recordListed(partial, import = false)
        jobs.recordListed(result("complete"), import = false)
        assertEquals(listOf("share-1", "share-2"), dao.all().map { it.localId })
        assertEquals(clock.get(), dao.get("share-1")!!.syncedAt)
    }

    @Test
    fun everyTransitionFollowsTheStateTable() {
        val waiting = JobEvent.Accepted(ProcessingStatus.WAITING)
        val unknownAccept = JobEvent.Accepted(ProcessingStatus.UNKNOWN)
        val complete = JobEvent.Server(ProcessingStatus.COMPLETE)
        val unknown = JobEvent.Server(ProcessingStatus.UNKNOWN)
        val expected = mapOf(
            LocalJobState.LOCAL_PENDING to listOf(
                JobEvent.UploadStarted to LocalJobState.UPLOADING,
                JobEvent.UploadInterrupted to LocalJobState.LOCAL_PENDING,
                JobEvent.Unrecoverable to LocalJobState.FAILED,
                waiting to LocalJobState.QUEUED,
                unknownAccept to LocalJobState.ACCEPTED,
                complete to null,
                JobEvent.Gone to null
            ),
            LocalJobState.UPLOADING to listOf(
                JobEvent.UploadStarted to null,
                JobEvent.UploadInterrupted to LocalJobState.LOCAL_PENDING,
                JobEvent.Unrecoverable to LocalJobState.FAILED,
                waiting to LocalJobState.QUEUED,
                complete to null
            ),
            LocalJobState.ACCEPTED to listOf(
                JobEvent.UploadStarted to null,
                JobEvent.UploadInterrupted to null,
                unknownAccept to LocalJobState.ACCEPTED,
                JobEvent.Server(ProcessingStatus.CHECKING) to LocalJobState.RUNNING,
                JobEvent.Server(ProcessingStatus.PARTIAL) to LocalJobState.PARTIAL,
                complete to LocalJobState.SUCCEEDED,
                unknown to LocalJobState.ACCEPTED,
                JobEvent.Gone to LocalJobState.FAILED
            ),
            LocalJobState.RUNNING to listOf(
                JobEvent.Server(ProcessingStatus.FAILED) to LocalJobState.FAILED,
                JobEvent.Server(ProcessingStatus.CANCELLED) to LocalJobState.CANCELLED,
                unknown to LocalJobState.RUNNING,
                JobEvent.Unrecoverable to null
            ),
            LocalJobState.SUCCEEDED to listOf(
                // Server wins: a correction can reopen a finished check.
                JobEvent.Server(ProcessingStatus.PARTIAL) to LocalJobState.PARTIAL,
                unknown to LocalJobState.SUCCEEDED,
                JobEvent.UploadStarted to null
            )
        )
        expected.forEach { (state, cases) ->
            cases.forEach { (event, next) ->
                assertEquals("$state on $event", next, state.next(event))
            }
        }
    }

    @Test
    fun wireNamesFollowTheIssueAndUnknownNeverMovesAState() {
        val unknown = JobEvent.Server(ProcessingStatus.UNKNOWN)
        LocalJobState.entries.forEach { state ->
            assertEquals(state, LocalJobState.fromWire(state.wireName))
            // An UNKNOWN server status never moves a state, least of all to success.
            if (state.accepted) assertEquals(state, state.next(unknown))
        }
        assertEquals(
            listOf(
                "local_pending",
                "uploading",
                "accepted",
                "queued",
                "running",
                "partial",
                "succeeded",
                "failed",
                "cancelled"
            ),
            LocalJobState.entries.map { it.wireName }
        )
    }

    @Test
    fun illegalTransitionsLeaveTheRecordAsItWas() = runBlocking {
        dao.upsert(record("a", LocalJobState.QUEUED, serverId = "s-a"))
        assertNull(jobs.apply("a", JobEvent.UploadStarted))
        assertEquals(LocalJobState.QUEUED, dao.get("a")!!.jobState)
        assertNull(jobs.apply("missing", JobEvent.UploadStarted))
    }

    @Test
    fun theServerReadWinsAndCachesTheReport() = runBlocking {
        val complete = result("complete")
        dao.upsert(record("a", LocalJobState.RUNNING, serverId = complete.id))
        val stored = jobs.recordRead(complete)!!
        assertEquals(LocalJobState.SUCCEEDED, stored.jobState)
        assertEquals("complete", stored.processingStatus)
        assertEquals(clock.get(), stored.syncedAt)
        assertEquals(complete, jobs.cachedInvestigation(complete.id))
        val cached = jobs.cachedReport(complete.id)!!
        assertEquals(complete.report, cached.report)
        assertFalse(cached.stale)
    }

    @Test
    fun aReadOfAnInvestigationThisDeviceNeverSawIsImported() = runBlocking {
        val failed = result("failed")
        val stored = jobs.recordRead(failed)!!
        assertEquals(LocalJobState.FAILED, stored.jobState)
        assertEquals("MEDIA_UNSUPPORTED", stored.errorCode)
        assertNull(jobs.cachedReport(failed.id))
    }

    @Test
    fun anUnknownStatusKeepsTheStateAndIsNotCachedAsJson() = runBlocking {
        val payload = payload("partial")
        val future = ContractFixtures.replace(
            payload,
            listOf("processing_status"),
            JsonPrimitive("__future__")
        )
        val investigation = InvestigationCodec.parseInvestigation(future.toString())
        dao.upsert(record("a", LocalJobState.RUNNING, serverId = investigation.id))
        val stored = jobs.recordRead(investigation)!!
        assertEquals(LocalJobState.RUNNING, stored.jobState)
        assertNull(stored.processingStatus)
        assertNull(stored.investigationJson)
    }

    @Test
    fun cachedReportsGoStaleWhenUnconfirmedOrSuperseded() = runBlocking {
        val partial = result("partial")
        jobs.recordRead(partial)
        clock.addAndGet(300_000)
        jobs.markUnconfirmed()
        assertFalse("confirmed five minutes ago", jobs.cachedReport(partial.id)!!.stale)
        clock.addAndGet(400_000)
        jobs.markUnconfirmed()
        assertTrue("provisional and unconfirmed", jobs.cachedReport(partial.id)!!.stale)

        val complete = result("complete")
        jobs.recordRead(complete)
        jobs.markUnconfirmed()
        clock.addAndGet(10 * 600_000)
        jobs.markUnconfirmed()
        assertFalse("a final version never ages", jobs.cachedReport(complete.id)!!.stale)
        var newer = payload("complete")
        listOf(
            "version" to JsonPrimitive(3),
            "report" to JsonNull,
            "processing_status" to JsonPrimitive("checking"),
            "state" to JsonPrimitive("running")
        ).forEach { (key, value) -> newer = ContractFixtures.replace(newer, listOf(key), value) }
        jobs.recordRead(InvestigationCodec.parseInvestigation(newer.toString()))
        assertTrue("version 3 exists", jobs.cachedReport(complete.id)!!.stale)
    }

    @Test
    fun goneClearsTheShareKeyAndAbandonKeepsAcceptedShares() = runBlocking {
        dao.upsert(record("a", LocalJobState.QUEUED, serverId = "s-a"))
        dao.upsert(record("b", LocalJobState.LOCAL_PENDING))
        assertEquals("s-a", jobs.findAccepted("url:a"))
        val gone = jobs.gone("s-a")!!
        assertEquals(LocalJobState.FAILED, gone.jobState)
        assertNull(jobs.findAccepted("url:a"))
        jobs.abandon("a")
        jobs.abandon("b")
        assertEquals(listOf("a"), dao.all().map { it.localId })
    }

    @Test
    fun processStartComesFromThePlatformNotFromFirstUse() {
        // Started 90 s of elapsed realtime ago: the wall start is 90 s before now.
        assertEquals(1_000_000L - 90_000, processStartMillis(1_000_000, 100_000, 10_000))
        assertEquals("clock skew never moves it ahead", 1_000L, processStartMillis(1_000, 5, 10))
    }

    @Test
    fun theExportedSchemaIsCommittedForMigrations() {
        val schema = File("schemas/app.ovrly.data.OvrlyDatabase/1.json")
        assertTrue("run a build to export the Room schema, then commit it", schema.isFile)
        val text = schema.readText()
        listOf("investigations", "report_versions", "pending_chunks").forEach {
            assertTrue(it, "\"tableName\": \"$it\"" in text)
        }
        assertTrue("\"version\": 1" in text)
    }

    @Test
    fun versionTwoAddsTheRetryColumnsWithAMigrationMatchingTheExport() {
        val schema = File("schemas/app.ovrly.data.OvrlyDatabase/2.json")
        assertTrue("run a build to export the Room schema, then commit it", schema.isFile)
        val text = schema.readText()
        assertTrue("\"version\": 2" in text)
        assertEquals(1, OvrlyDatabase.MIGRATION_1_2.startVersion)
        assertEquals(2, OvrlyDatabase.MIGRATION_1_2.endVersion)
        listOf("retry_key", "retried_as").forEach { column ->
            assertTrue(column, "\"columnName\": \"$column\"" in text)
            val statement = "ALTER TABLE investigations ADD COLUMN $column TEXT"
            assertTrue(column, statement in OvrlyDatabase.MIGRATION_1_2_SQL)
        }
    }
}
