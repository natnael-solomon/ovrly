package app.ovrly.ui

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.data.ApiTestServer
import app.ovrly.data.ChecksService
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LocalJobState
import app.ovrly.data.withServer
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The open report: shown version, version list, staleness and per-claim changes (#34). */
class ReportLoaderTest {
    private val completeId = "00000000-0000-4000-8000-000000000001"

    private fun payload(name: String): JsonElement =
        ContractFixtures.element(ApiTestServer.result(name).investigationPayload())

    private fun fixture(name: String): Investigation =
        InvestigationCodec.parseInvestigation(payload(name).toString())

    /** Version 1 of the complete fixture: the meaning before the correction, assessed mixed. */
    private fun versionOne(): String = listOf(
        listOf("version") to JsonPrimitive(1),
        listOf("id") to JsonPrimitive("rpt_synthetic_0001_v1"),
        listOf("supersedes") to JsonNull,
        listOf("claims", "0", "proposition") to
            JsonPrimitive("Two thirds of the country's buses are electric."),
        listOf("claims", "0", "correction") to JsonNull,
        listOf("assessments", "0", "version") to JsonPrimitive(1),
        listOf("assessments", "0", "overall") to JsonPrimitive("mixed"),
        listOf("assessments", "1", "version") to JsonPrimitive(1)
    ).fold((payload("complete") as JsonObject).getValue("report")) { json, (path, value) ->
        ContractFixtures.replace(json, path, value)
    }.toString()

    private fun summary(version: Int, provisional: Boolean, fixture: Boolean) =
        """{"id":"rpt_synthetic_0001_v$version","version":$version,""" +
            """"created_at":"2026-10-04T12:0$version:00Z","provisional":$provisional,""" +
            """"change_summary":"Version $version.","supersedes":""" +
            (if (version == 1) "null" else "\"rpt_synthetic_0001_v${version - 1}\"") +
            ""","fixture":$fixture}"""

    private fun versionList(): String {
        val first = summary(1, provisional = true, fixture = true)
        val second = summary(2, provisional = false, fixture = false)
        return """{"investigation_id":"$completeId","items":[$first,$second]}"""
    }

    private fun ApiTestServer.loader(stale: Boolean = false) =
        ReportLoader(ChecksService(services())) { stale }

    @Test
    fun theLatestVersionComparesWithTheOneItSupersededAndListsEveryVersion() =
        withServer { server ->
            server.credentials.write("synthetic-token-1")
            server.json(200, versionList())
            server.json(200, versionOne())
            val report = runBlocking {
                server.loader().load(fixture("complete"), null, emptyList())
            }
            assertEquals(2, report.view.version)
            assertEquals(1, report.comparedWith)
            assertEquals(
                listOf("Version 1 (provisional, development fixture)", "Version 2 (latest)"),
                report.versions.map { it.label }
            )
            assertEquals(listOf(true, false), report.versions.map { it.fixture })
            assertNull(report.versionsNote)
            val coverage = report.view.coverage!!
            assertFalse(coverage.fixture)
            assertFalse(coverage.stale)
            assertTrue(report.view.canCorrect)
            val changed = report.changes.getValue("clm_synthetic_0001")
            assertTrue(changed.first().startsWith("Meaning in version 1:"))
            assertEquals("/v1/investigations/$completeId/reports", server.take().path)
            assertEquals("/v1/investigations/$completeId/reports/1", server.take().path)
        }

    @Test
    fun anEarlierVersionIsReadOnceAndShownReadOnlyWithItsFixtureFlag() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, versionList())
        server.json(200, versionOne())
        val loader = server.loader()
        val complete = fixture("complete")
        runBlocking { loader.load(complete, null, emptyList()) }
        val earlier = runBlocking { loader.load(complete, 1, emptyList()) }
        assertEquals(1, earlier.view.version)
        assertEquals(2, earlier.view.latestVersion)
        assertFalse("corrections start from the latest version", earlier.view.canCorrect)
        assertTrue(earlier.view.coverage!!.fixture)
        assertNull(earlier.comparedWith)
        assertTrue(earlier.changes.isEmpty())
        // The list and version 1 were read once; nothing new was requested.
        assertEquals(2, server.server.requestCount)
    }

    @Test
    fun invalidatingReadsTheVersionListAgain() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, versionList())
        server.json(200, versionOne())
        server.json(200, versionList())
        val loader = server.loader()
        val complete = fixture("complete")
        runBlocking { loader.load(complete, null, emptyList()) }
        loader.invalidate()
        val again = runBlocking { loader.load(complete, null, emptyList()) }
        assertEquals(2, again.versions.size)
        assertEquals(3, server.server.requestCount)
    }

    @Test
    fun withoutAConnectionTheLatestVersionStaysAndTheListSaysWhy() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.error(500, "INTERNAL_ERROR")
        server.error(500, "INTERNAL_ERROR")
        val report = runBlocking {
            server.loader(stale = true).load(fixture("complete"), null, emptyList())
        }
        assertEquals(2, report.view.version)
        assertTrue(report.versions.isEmpty())
        assertEquals("Earlier versions need a connection.", report.versionsNote)
        assertNull(report.comparedWith)
        assertTrue(report.view.coverage!!.stale)
    }

    @Test
    fun aVersionThatCannotBeLoadedFallsBackToTheLatestAndSaysSo() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.error(404, "NOT_FOUND")
        server.json(200, versionList())
        server.error(404, "NOT_FOUND")
        val report = runBlocking { server.loader().load(fixture("complete"), 1, emptyList()) }
        assertEquals(2, report.view.version)
        assertEquals("That version could not be loaded.", report.versionsNote)
    }

    @Test
    fun aCheckWithoutAReportAsksForNothing() = withServer { server ->
        val candidates = listOf(item(fixture("complete")))
        val report = runBlocking { server.loader().load(fixture("failed"), null, candidates) }
        assertNull(report.view.version)
        assertTrue(report.versions.isEmpty())
        assertNull(report.versionsNote)
        assertEquals(candidates, report.candidates)
        assertEquals(0, server.server.requestCount)
    }

    @Test
    fun theRetrievalTimeIsWhenTheShownVersionWasStoredOrFirstRead() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, versionList())
        server.json(200, versionOne())
        var now = 5_000L
        val loader = ReportLoader(
            ChecksService(server.services()),
            retrieved = { 1_000L },
            clock = { now }
        ) { false }
        val complete = fixture("complete")
        val latest = runBlocking { loader.load(complete, null, emptyList()) }
        assertEquals("the stored read of the latest version", 1_000L, latest.retrievedAt)
        val earlier = runBlocking { loader.load(complete, 1, emptyList()) }
        assertEquals("an earlier version: when it was first read", 5_000L, earlier.retrievedAt)
        now = 9_000L
        val again = runBlocking { loader.load(complete, 1, emptyList()) }
        assertEquals(5_000L, again.retrievedAt)
    }

    @Test
    fun theRetrievalTimeFallsBackToNowAndComesFromTheStore() = withServer { server ->
        val failed = fixture("failed")
        val noReport = runBlocking { server.loader().load(failed, null, emptyList()) }
        assertNull("nothing to date without a version", noReport.retrievedAt)
        val partial = fixture("partial")
        val retrieved = ReportLoader.retrievedFrom(server.jobs)
        assertNull(runBlocking { retrieved(partial.id) })
        runBlocking { server.jobs.recordRead(partial) }
        assertEquals(server.wall.get(), runBlocking { retrieved(partial.id) })
    }

    @Test
    fun stalenessComesFromTheCachedLatestVersion() = withServer { server ->
        val partial = fixture("partial")
        val stale = ReportLoader.staleFrom(server.jobs)
        runBlocking { server.jobs.recordRead(partial) }
        assertFalse(runBlocking { stale(partial.id) })
        server.wall.addAndGet(11 * 60_000L)
        runBlocking { server.jobs.markUnconfirmed() }
        assertTrue(runBlocking { stale(partial.id) })
        assertFalse("an unknown check is not stale", runBlocking { stale(completeId) })
    }

    private fun item(investigation: Investigation) = inboxItem(
        InvestigationRecord(
            localId = investigation.id,
            serverId = investigation.id,
            state = LocalJobState.ACCEPTED.wireName,
            sourceKind = investigation.source.kind.wireName,
            idempotencyKey = "synthetic-key",
            createdAt = 0,
            updatedAt = 0
        ),
        investigation,
        0
    )
}
