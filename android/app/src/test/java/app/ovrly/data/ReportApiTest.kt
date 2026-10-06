package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.InvestigationCodec
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The BE-10 report routes and the Inbox and Report actions on top of them (#34). */
class ReportApiTest {
    private val complete = ApiTestServer.result("complete").investigationPayload()
    private val partial = ApiTestServer.result("partial").investigationPayload()
    private val completeId = "00000000-0000-4000-8000-000000000001"

    private fun job(): String =
        (ContractFixtures.element(partial) as JsonObject).getValue("job").toString()

    private fun receipt(reason: String, published: Int?) =
        """{"id":"00000000-0000-4000-8000-000000000901",""" +
            """"investigation_id":"$completeId","reason":"$reason","base_version":2,""" +
            """"published_version":${published ?: "null"},"job":${job()},""" +
            """"created_at":"2026-10-04T12:20:00Z"}"""

    private fun report(payload: String): String =
        (ContractFixtures.element(payload) as JsonObject).getValue("report").toString()

    @Test
    fun aCorrectionSendsTheMeaningWithTheBaseVersionAndAKey() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(202, receipt("correction", 3))
        val request = ReanalysisRequest.Correction(2, "clm_synthetic_0002", "Scheduled buses only")
        val result = runBlocking {
            server.services().reports.reanalyze(completeId, request, "synthetic-key-9")
        }
        assertEquals(3, (result as ApiResult.Success).value.publishedVersion)
        val sent = server.take()
        assertEquals("POST", sent.method)
        assertEquals("/v1/investigations/$completeId/reanalyze", sent.path)
        assertEquals("synthetic-key-9", sent.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        val body = ContractFixtures.element(sent.body.readUtf8()).jsonObject
        assertEquals(
            setOf("reason", "base_version", "claim_id", "proposition"),
            body.keys
        )
        assertEquals("\"correction\"", body.getValue("reason").toString())
        assertEquals("2", body.getValue("base_version").toString())
        assertEquals("\"Scheduled buses only\"", body.getValue("proposition").toString())
    }

    @Test
    fun anExpansionAlwaysCarriesTheConfirmationAndADeeperSearchNothingElse() {
        val video = "00000000-0000-4000-8000-000000000401"
        val expansion =
            ContractFixtures.element(ReanalysisRequest.Expansion(1, video).encode()).jsonObject
        assertEquals("true", expansion.getValue("match_confirmed").toString())
        assertEquals(
            setOf("reason", "base_version", "match_confirmed", "source_investigation_id"),
            expansion.keys
        )
        val deeper = ContractFixtures.element(ReanalysisRequest.Deeper(1).encode()).jsonObject
        assertEquals(setOf("reason", "base_version"), deeper.keys)
    }

    @Test
    fun aRefusedReanalysisIsATypedFailure() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.error(409, "REPORT_VERSION_STALE")
        val result = runBlocking {
            server.services().reports.reanalyze(
                completeId,
                ReanalysisRequest.Deeper(1),
                "synthetic-key-10"
            )
        }
        val failure = (result as ApiResult.Failure).failure as ApiFailure.Server
        assertEquals(ApiErrorCode.REPORT_VERSION_STALE, failure.code)
    }

    @Test
    fun cancelPostsNoBodyToTheJob() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(
            202,
            """{"job_id":"00000000-0000-4000-8000-000000000102",""" +
                """"cancellation":"requested"}"""
        )
        server.json(200, partial)
        val investigation = InvestigationCodec.parseInvestigation(partial)
        val result = runBlocking { ChecksService(server.services()).cancel(investigation) }
        val receipt = (result as ApiResult.Success).value
        assertEquals(false, receipt.effective)
        val sent = server.take()
        assertEquals("/v1/jobs/00000000-0000-4000-8000-000000000102/cancel", sent.path)
        assertEquals(0L, sent.bodySize)
        assertEquals("/v1/investigations/${investigation.id}", server.take().path)
    }

    @Test
    fun theListStoresEveryInvestigationForTheInbox() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, """{"items":[$complete,$partial]}""")
        val checks = ChecksService(server.services())
        assertNull(runBlocking { checks.sync() })
        val stored = runBlocking { checks.stored() }
        assertEquals(2, stored.size)
        assertTrue(stored.all { it.investigation != null })
    }

    @Test
    fun aListReadWhileAShareWaitsImportsNothingNew() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val services = server.services()
        val listed = InvestigationCodec.parseInvestigation(partial)
        runBlocking {
            services.jobs.startShare(
                InvestigationRecord(
                    localId = "share-1",
                    state = LocalJobState.UPLOADING.wireName,
                    sourceKind = "upload",
                    idempotencyKey = "synthetic-share-key",
                    createdAt = 0,
                    updatedAt = 0
                )
            )
        }
        val body = InvestigationCodec.encodeInvestigation(listed)
        server.json(200, """{"items":[$body]}""")
        val checks = ChecksService(services)
        assertNull(runBlocking { checks.sync() })
        val rows = runBlocking { services.jobs.dao.all() }
        assertEquals(listOf("share-1"), rows.map { it.localId })
        val stored = runBlocking { services.jobs.accepted("share-1", listed) }
        assertEquals(listed.id, stored!!.serverId)
        assertEquals(1, runBlocking { services.jobs.dao.all() }.size)
    }

    @Test
    fun oneUnreadableItemFailsTheListInsteadOfDroppingIt() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(200, """{"items":[$complete,{"id":"broken"}]}""")
        val failure = runBlocking { ChecksService(server.services()).sync() }
        assertTrue(failure is ApiFailure.Incompatible)
    }

    @Test
    fun versionsAreListedAndAnEarlierVersionIsReadOnceThenCached() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.json(
            200,
            """{"investigation_id":"$completeId","items":[""" +
                """{"id":"rpt_synthetic_0001_v1","version":1,""" +
                """"created_at":"2026-10-04T12:05:00Z",""" +
                """"provisional":false,"change_summary":"First version.","supersedes":null,""" +
                """"fixture":false}]}"""
        )
        server.json(200, report(complete))
        val checks = ChecksService(server.services())
        val list = runBlocking { checks.versions(completeId) } as ApiResult.Success
        assertEquals(1, list.value.items.single().version)
        val first = runBlocking { checks.version(completeId, 2) }
        val second = runBlocking { checks.version(completeId, 2) }
        assertEquals((first as ApiResult.Success).value, (second as ApiResult.Success).value)
        assertEquals("/v1/investigations/$completeId/reports", server.take().path)
        assertEquals("/v1/investigations/$completeId/reports/2", server.take().path)
        assertEquals(2, server.server.requestCount)
    }

    @Test
    fun retryStartsANewCheckOfTheSameLinkWithANewKey() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val failed = ApiTestServer.result("failed").investigationPayload()
        val services = server.services()
        runBlocking { services.jobs.recordRead(InvestigationCodec.parseInvestigation(failed)) }
        server.json(202, ApiTestServer.intakeResponse("investigation-create-url"))
        val checks = ChecksService(services, keys = { "synthetic-new-key" })
        val localId = runBlocking { services.jobs.dao.all() }.single().localId
        val result = runBlocking { checks.retry(localId) }
        assertTrue(result is ApiResult.Success)
        val sent = server.take()
        assertEquals("/v1/investigations", sent.path)
        assertEquals("synthetic-new-key", sent.getHeader(OvrlyApi.IDEMPOTENCY_HEADER))
        val source = ContractFixtures.element(sent.body.readUtf8()).jsonObject.getValue("source")
        assertEquals(
            "\"https://video.example/synthetic/clip-0001\"",
            source.jsonObject.getValue("url").toString()
        )
        assertEquals(2, runBlocking { services.jobs.dao.all() }.size)
    }

    @Test
    fun aRetryReusesItsStoredKeyUntilTheNewCheckExistsAndThenStops() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val failed = InvestigationCodec.parseInvestigation(
            ApiTestServer.result("failed").investigationPayload()
        )
        val services = server.services()
        runBlocking { services.jobs.recordRead(failed) }
        var next = 0
        val checks = ChecksService(services, keys = { "synthetic-retry-${next++}" })
        server.error(500, "INTERNAL_ERROR", retryable = true, action = "retry")
        server.json(202, ApiTestServer.intakeResponse("investigation-create-url"))
        val lost = runBlocking { checks.retry(failed.id) }
        assertTrue(lost is ApiResult.Failure)
        val stored = runBlocking { services.jobs.dao.get(failed.id) }!!
        assertEquals("synthetic-retry-0", stored.retryKey)
        val created = runBlocking { checks.retry(failed.id) } as ApiResult.Success
        val keys = List(2) { server.take().getHeader(OvrlyApi.IDEMPOTENCY_HEADER) }
        assertEquals(listOf("synthetic-retry-0", "synthetic-retry-0"), keys)
        val old = runBlocking { services.jobs.dao.get(failed.id) }!!
        assertEquals(created.value.id, old.retriedAs)
        assertNull(runBlocking { checks.retry(failed.id) })
        assertEquals(2, server.server.requestCount)
    }

    @Test
    fun anExpansionNamesTheConfirmedFullVideoAndTheReceiptEchoesIt() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val video = "00000000-0000-4000-8000-000000000401"
        val receipt = receipt("expansion", null).dropLast(1) +
            ""","source_investigation_id":"$video"}"""
        server.json(202, receipt)
        val result = runBlocking {
            server.services().reports.reanalyze(
                completeId,
                ReanalysisRequest.Expansion(2, video),
                "synthetic-key-11"
            )
        }
        assertEquals(video, (result as ApiResult.Success).value.sourceInvestigationId)
        val body = ContractFixtures.element(server.take().body.readUtf8()).jsonObject
        assertEquals("\"$video\"", body.getValue("source_investigation_id").toString())
        assertEquals("true", body.getValue("match_confirmed").toString())
    }
}
