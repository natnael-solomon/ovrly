package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ReportVersion
import java.io.File
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.MockResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Mirrors the Room `SavedReportDao` for unit tests. */
internal class MemorySavedReportDao : SavedReportDao() {
    val rows = linkedMapOf<String, SavedReportEntry>()

    override suspend fun all(): List<SavedReportEntry> = synchronized(rows) { rows.values.toList() }

    override suspend fun upsert(entry: SavedReportEntry) {
        synchronized(rows) { rows[entry.reportId] = entry }
    }

    override suspend fun delete(reportId: String) {
        synchronized(rows) { rows.remove(reportId) }
    }

    override suspend fun clear() = synchronized(rows) { rows.clear() }

    override suspend fun replaceAll(entries: List<SavedReportEntry>) = synchronized(rows) {
        rows.clear()
        entries.forEach { rows[it.reportId] = it }
    }
}

/** A saved-report body built from the shared `complete` result fixture. */
internal object SavedFixtures {
    val report: ReportVersion by lazy { fixtureReport("complete") }

    /** A second report, of another check, for a second save. */
    val other: ReportVersion by lazy { fixtureReport("partial") }

    fun fixtureReport(name: String): ReportVersion {
        val payload = ContractFixtures.element(
            ApiTestServer.result(name).investigationPayload()
        ) as JsonObject
        return InvestigationCodec.parseReportVersion(payload.getValue("report").toString())
    }

    fun saved(
        savedAt: String = "2026-10-07T00:00:00Z",
        report: ReportVersion = this.report
    ): String = savedObject(savedAt, report).toString()

    fun savedObject(savedAt: String, report: ReportVersion = this.report): JsonObject =
        buildJsonObject {
            put("report_id", report.id)
            put("investigation_id", report.investigationId)
            put("version", report.version)
            put("saved_at", savedAt)
            put(
                "report",
                ContractFixtures.element(InvestigationCodec.encodeReportVersion(report))
            )
        }

    fun list(vararg items: JsonObject): String =
        JsonObject(mapOf("items" to JsonArray(items.toList()))).toString()
}

class SavedReportsTest {
    private val reportId = SavedFixtures.report.id

    private fun ApiTestServer.saved(dao: MemorySavedReportDao = MemorySavedReportDao()) =
        SavedReports(api, dao) { 42L } to dao

    @Test
    fun anExplicitSavePostsNoBodyAndStoresTheServersCopy() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, SavedFixtures.saved())
        val (saved, dao) = server.saved()
        val result = runBlocking { saved.save(reportId) }

        val value = (result as ApiResult.Success).value
        assertEquals(reportId, value.reportId)
        assertEquals(2, value.version)
        val request = server.take()
        assertEquals("POST", request.method)
        assertEquals("/v1/reports/$reportId/save", request.path)
        assertEquals(0L, request.bodySize)
        assertEquals("Bearer synthetic-token-1", request.getHeader("Authorization"))
        val stored = runBlocking { saved.stored() }.single()
        assertEquals(reportId, stored.entry.reportId)
        assertEquals(SavedFixtures.report, stored.report)
        assertEquals(42L, stored.entry.storedAt)
        assertEquals(1, dao.rows.size)
    }

    @Test
    fun removingASaveSendsDeleteAndForgetsOnlyThatSave() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, SavedFixtures.saved())
        server.json(200, SavedFixtures.saved(report = SavedFixtures.other))
        server.enqueue(MockResponse().setResponseCode(204))
        val (saved, dao) = server.saved()
        runBlocking {
            saved.save(reportId)
            saved.save(SavedFixtures.other.id)
            assertTrue(saved.unsave(reportId) is ApiResult.Success)
        }
        server.take()
        server.take()
        val delete = server.take()
        assertEquals("DELETE", delete.method)
        assertEquals("/v1/reports/$reportId/save", delete.path)
        assertEquals(0L, delete.bodySize)
        assertEquals(setOf(SavedFixtures.other.id), dao.rows.keys)
    }

    @Test
    fun aFailedSaveOrRemovalChangesNothingOnTheDevice() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.error(404, "NOT_FOUND")
        server.error(503, "DATABASE_UNAVAILABLE", retryable = true)
        val dao = MemorySavedReportDao()
        runBlocking { dao.upsert(entry()) }
        val (saved) = server.saved(dao)
        val save = runBlocking { saved.save("rpt_missing") } as ApiResult.Failure
        assertEquals(ApiErrorCode.NOT_FOUND, (save.failure as ApiFailure.Server).code)
        assertTrue(runBlocking { saved.unsave(reportId) } is ApiResult.Failure)
        assertEquals(setOf(reportId), dao.rows.keys)
    }

    @Test
    fun aListReadReplacesTheStoredSavesNewestFirst() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val older = SavedFixtures.other
        server.json(
            200,
            SavedFixtures.list(
                SavedFixtures.savedObject("2026-10-07T00:00:00.5Z"),
                SavedFixtures.savedObject("2026-10-06T23:59:59.999999+00:00", older)
            )
        )
        val dao = MemorySavedReportDao()
        runBlocking { dao.upsert(entry(reportId = "rpt_removed_elsewhere")) }
        val (saved) = server.saved(dao)
        assertNull(runBlocking { saved.sync() })

        assertEquals("/v1/reports/saved", server.take().path)
        val stored = runBlocking { saved.stored() }
        assertEquals(listOf(reportId, older.id), stored.map { it.entry.reportId })
        assertTrue(stored.all { it.report != null })
    }

    @Test
    fun offlineOrAnUnreadableListKeepsWhatTheDeviceHas() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val broken = SavedFixtures.savedObject("2026-10-07T00:00:00Z").let {
            JsonObject(it + ("version" to JsonPrimitive(3)))
        }
        val good = SavedFixtures.savedObject("2026-10-07T00:00:00Z")
        server.json(200, SavedFixtures.list(good, broken))
        server.json(200, """{"items":"not a list"}""")
        val dao = MemorySavedReportDao()
        runBlocking { dao.upsert(entry(reportId = "rpt_kept")) }
        val (saved) = server.saved(dao)
        assertTrue(runBlocking { saved.sync() } is ApiFailure.Incompatible)
        assertTrue(runBlocking { saved.sync() } is ApiFailure.Incompatible)
        server.server.shutdown()
        assertTrue(runBlocking { saved.sync() } is ApiFailure.Network)
        assertEquals(setOf("rpt_kept"), dao.rows.keys)
    }

    @Test
    fun aSaveMustCarryTheVersionItNames() {
        val mismatched = SavedFixtures.savedObject("2026-10-07T00:00:00Z").let {
            JsonObject(it + ("report_id" to JsonPrimitive("rpt_other")))
        }
        val failures = listOf(
            mismatched.toString(),
            JsonObject(
                SavedFixtures.savedObject("2026-10-07T00:00:00Z") +
                    ("saved_at" to JsonPrimitive("yesterday"))
            ).toString(),
            JsonObject(
                SavedFixtures.savedObject("2026-10-07T00:00:00Z") - "report"
            ).toString()
        )
        failures.forEach { payload ->
            val error = runCatching { SavedReportCodec.parseSaved(payload) }.exceptionOrNull()
            assertNotNull(payload, error)
        }
        val extra = JsonObject(
            SavedFixtures.savedObject("2026-10-07T00:00:00Z") + ("owner" to JsonPrimitive("x"))
        )
        assertEquals(reportId, SavedReportCodec.parseSaved(extra.toString()).reportId)
    }

    @Test
    fun aStoredCopyThisVersionCannotReadIsKeptButHasNoReport() {
        val dao = MemorySavedReportDao()
        runBlocking { dao.upsert(entry(json = "{}")) }
        withServer { server ->
            val (saved) = server.saved(dao)
            val stored = runBlocking { saved.stored() }.single()
            assertNull(stored.report)
        }
    }

    @Test
    fun versionThreeAddsTheSavedReportsTableWithAMigrationMatchingTheExport() {
        val schema = File("schemas/app.ovrly.data.OvrlyDatabase/3.json")
        assertTrue("run a build to export the Room schema, then commit it", schema.isFile)
        val export = ContractFixtures.element(schema.readText()) as JsonObject
        val database = export.getValue("database") as JsonObject
        assertEquals(JsonPrimitive(3), database["version"])
        val table = (database.getValue("entities") as JsonArray)
            .map { it as JsonObject }
            .single { (it["tableName"] as JsonPrimitive).content == "saved_reports" }
        val created = (table.getValue("createSql") as JsonPrimitive).content
            .replace("\${TABLE_NAME}", "saved_reports")
        assertEquals(2, OvrlyDatabase.MIGRATION_2_3.startVersion)
        assertEquals(3, OvrlyDatabase.MIGRATION_2_3.endVersion)
        assertEquals(listOf(created), OvrlyDatabase.MIGRATION_2_3_SQL)
        // Version 2's tables are unchanged, so the migration only creates the new one.
        val previous = File("schemas/app.ovrly.data.OvrlyDatabase/2.json").readText()
        assertTrue("saved_reports" !in previous)
    }

    private fun entry(reportId: String = this.reportId, json: String? = null) = SavedReportEntry(
        reportId = reportId,
        investigationId = SavedFixtures.report.investigationId,
        version = 2,
        savedAt = "2026-10-01T00:00:00Z",
        json = json ?: InvestigationCodec.encodeReportVersion(SavedFixtures.report),
        storedAt = 1L
    )
}
