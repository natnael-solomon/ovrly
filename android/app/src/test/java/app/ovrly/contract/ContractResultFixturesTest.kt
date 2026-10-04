package app.ovrly.contract

import app.ovrly.contract.ContractFixtures.Fixture
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Drives the production investigation parser with the six committed result fixtures in
 * `packages/contracts/fixtures/results`, asserting the typed values each fixture's `expect`
 * block and the contract README promise rather than merely that parsing does not throw.
 */
class ContractResultFixturesTest {
    private val fixtures = ContractFixtures.load(ContractFixtures.RESULTS)
    private val byName = fixtures.associateBy { it.name }

    @Test
    fun everyCommittedResultFixtureIsClassifiedByTheseTests() {
        assertEquals(RESULT_FIXTURES, byName.keys)
        for (fixture in fixtures) {
            assertTrue("${fixture.name} must be synthetic", fixture.synthetic)
            assertEquals(fixture.name, "valid", fixture.expectPayload)
            assertNotNull(fixture.name, fixture.investigation)
        }
        assertEquals(ContractJson.CONTRACT_VERSION, ContractFixtures.resourceText("VERSION").trim())
    }

    @Test
    fun everyFixtureMatchesItsOwnExpectBlock() {
        for (fixture in fixtures) {
            val investigation = parse(fixture)
            val name = fixture.name
            val status = investigation.processingStatus.wireName
            assertEquals(name, fixture.expectedProcessingStatus, status)
            assertEquals(name, fixture.expectedState, investigation.state.wireName)
            assertEquals(name, fixture.expectedClaimCount, investigation.report?.claims?.size ?: 0)
            val assessments = investigation.report?.assessments?.size ?: 0
            assertEquals(name, fixture.expectedAssessmentCount, assessments)
            assertEquals(name, fixture.expectedErrorCode, investigation.error?.code)
            assertEquals(name, investigation.error != null, investigation.isFailed)
            investigation.report?.let { assertEquals(name, investigation.version, it.version) }
            assertTrue(name, investigation.id.startsWith(SYNTHETIC_UUID_PREFIX))
        }
    }

    @Test
    fun completeFixtureCarriesTheCorrectedSecondVersion() {
        val investigation = parse(fixture("complete"))
        assertTrue(investigation.isComplete)
        assertFalse(investigation.isFailed)
        assertEquals(ProcessingStatus.COMPLETE, investigation.processingStatus)
        assertEquals(InvestigationState.COMPLETED, investigation.state)
        assertEquals(Stage.PUBLICATION, investigation.stage)
        assertEquals(Coverage(CoverageStatus.COMPLETE, 185_000, 185_000), investigation.coverage)
        assertEquals(2, investigation.version)
        assertNull(investigation.error)
        assertEquals(
            InvestigationSource.Url("https://video.example/synthetic/clip-0001", 185_000),
            investigation.source
        )
        val job = checkNotNull(investigation.job)
        assertEquals(JobState.PUBLISHED, job.state)
        assertTrue(job.isPublished)
        assertEquals(Stage.PUBLICATION, job.stage)
        assertFalse(job.cancelRequested)
        assertEquals(1, job.attempts)
        assertNull(job.retryClass)
        val report = checkNotNull(investigation.report)
        assertEquals("rpt_synthetic_0001_v2", report.id)
        assertEquals(investigation.id, report.investigationId)
        assertEquals(2, report.version)
        assertFalse(report.provisional)
        assertEquals("rpt_synthetic_0001_v1", report.supersedes)
        assertTrue(report.assessesEveryClaim)
        val claimIds = report.claims.map { it.id }
        assertEquals(listOf("clm_synthetic_0001", "clm_synthetic_0002"), claimIds)
        val corrected = report.claims[0]
        assertEquals(Interval(12_400, 18_900, Timebase.MEDIA), corrected.interval)
        assertEquals(Modality.SPEECH, corrected.modality)
        assertEquals(
            "a recent survey found that nearly two thirds of city buses are electric now",
            corrected.originalText
        )
        assertEquals(
            "Nearly two thirds of the city's buses are electric, per a recent survey.",
            corrected.proposition
        )
        val correction = checkNotNull(corrected.correction)
        assertEquals(CorrectionAttribution.USER, correction.attributedTo)
        assertEquals("2026-10-04T12:10:00Z", correction.correctedAt)
        assertEquals(
            "Two thirds of the country's buses are electric.",
            correction.supersededProposition
        )
        assertEquals(Modality.TEXT, report.claims[1].modality)
        assertNull(report.claims[1].correction)
    }

    @Test
    fun completeFixtureAssessmentsAndEvidenceAreTyped() {
        val report = checkNotNull(parse(fixture("complete")).report)
        assertEquals(3, report.evidence.size)
        assertEquals(2, report.evidenceFor("clm_synthetic_0001").size)
        val supported = checkNotNull(report.assessmentFor("clm_synthetic_0001"))
        assertEquals(OverallAssessment.SUPPORTED, supported.overall)
        val relations = supported.relations.map { it.relation }
        assertEquals(listOf(Relation.SUPPORT, Relation.SUPPORT), relations)
        assertEquals(
            listOf("evd_synthetic_0001", "evd_synthetic_0002"),
            supported.relations.map { it.evidenceId }
        )
        assertNull(supported.relations[1].note)
        assertFalse(supported.provisional)
        val qualified = checkNotNull(report.assessmentFor("clm_synthetic_0002"))
        assertEquals(OverallAssessment.QUALIFIED, qualified.overall)
        assertEquals(listOf(Relation.QUALIFY), qualified.relations.map { it.relation })
        val primary = report.evidence.single { it.id == "evd_synthetic_0003" }
        assertEquals(SourceType.PRIMARY_DOCUMENT, primary.sourceType)
        assertEquals(SourceInspectionLevel.FULL_TEXT, primary.inspectionLevel)
        assertEquals(RetrievalRelevance.HIGH, primary.retrievalRelevance)
        assertEquals(RetractionStatus.NONE, primary.retractionStatus)
        assertEquals("https://sources.example/synthetic/0003", primary.source.url)
        assertEquals("2026-09-04T12:00:00Z", primary.source.publishedAt)
    }

    @Test
    fun partialFixtureIsProvisionalOnTheCaptureTimebaseWithOneClaimUnassessed() {
        val investigation = parse(fixture("partial"))
        assertFalse(investigation.isComplete)
        assertEquals(ProcessingStatus.PARTIAL, investigation.processingStatus)
        assertEquals(InvestigationState.RUNNING, investigation.state)
        assertEquals(Stage.RETRIEVAL, investigation.stage)
        assertEquals(Coverage(CoverageStatus.PARTIAL, 60_000, null), investigation.coverage)
        assertEquals(
            InvestigationSource.Upload("00000000-0000-4000-8000-000000000202"),
            investigation.source
        )
        assertNull(investigation.source.durationMs)
        val job = checkNotNull(investigation.job)
        assertEquals(JobState.RUNNING, job.state)
        assertFalse(job.isPublished)
        assertEquals(2, job.attempts)
        assertEquals(RetryClass.TRANSIENT, job.retryClass)
        val report = checkNotNull(investigation.report)
        assertTrue(report.provisional)
        assertNull(report.supersedes)
        assertEquals(1, report.version)
        assertFalse(report.assessesEveryClaim)
        assertEquals(2, report.claims.size)
        assertTrue(report.claims.all { it.interval.timebase == Timebase.CAPTURE })
        assertEquals(Interval(4_200, 9_800, Timebase.CAPTURE), report.claims[0].interval)
        val challenged = checkNotNull(report.assessmentFor("clm_synthetic_0003"))
        assertEquals(OverallAssessment.CHALLENGED, challenged.overall)
        assertTrue(challenged.provisional)
        assertEquals(listOf(Relation.CHALLENGE), challenged.relations.map { it.relation })
        assertNull(report.assessmentFor("clm_synthetic_0004"))
        assertTrue(report.evidenceFor("clm_synthetic_0004").isEmpty())
    }

    @Test
    fun failedFixtureCarriesTheErrorAndNoFindings() {
        val investigation = parse(fixture("failed"))
        assertTrue(investigation.isFailed)
        assertFalse(investigation.isComplete)
        assertEquals(ProcessingStatus.FAILED, investigation.processingStatus)
        assertEquals(InvestigationState.FAILED, investigation.state)
        assertEquals(Stage.MEDIA_VALIDATION, investigation.stage)
        assertEquals(Coverage(CoverageStatus.NOT_STARTED, null, null), investigation.coverage)
        val error = checkNotNull(investigation.error)
        assertEquals("MEDIA_UNSUPPORTED", error.code)
        assertEquals("Processing failed", error.message)
        assertFalse(error.retryable)
        assertNull(investigation.report)
        assertNull(investigation.source.durationMs)
        val job = checkNotNull(investigation.job)
        assertEquals(JobState.FAILED, job.state)
        assertEquals(RetryClass.NON_RETRIABLE_INPUT, job.retryClass)
        assertFalse(job.cancelRequested)
    }

    @Test
    fun cancelledFixtureRecordsTheRequestWithNoReportAndNoError() {
        val investigation = parse(fixture("cancelled"))
        assertFalse(investigation.isComplete)
        assertFalse(investigation.isFailed)
        assertEquals(ProcessingStatus.CANCELLED, investigation.processingStatus)
        assertEquals(InvestigationState.CANCELLED, investigation.state)
        assertEquals(Stage.ASR, investigation.stage)
        assertEquals(Coverage(CoverageStatus.PARTIAL, 30_000, null), investigation.coverage)
        assertNull(investigation.report)
        assertNull(investigation.error)
        val job = checkNotNull(investigation.job)
        assertEquals(JobState.CANCELLED, job.state)
        assertTrue(job.cancelRequested)
        assertNull(job.retryClass)
    }

    @Test
    fun insufficientEvidenceFixtureHasNoSupportedAssessment() {
        val investigation = parse(fixture("insufficient-evidence"))
        assertTrue(investigation.isComplete)
        assertFalse(investigation.isFailed)
        assertNull(investigation.error)
        val report = checkNotNull(investigation.report)
        assertFalse(report.provisional)
        assertEquals(1, report.claims.size)
        assertEquals(Modality.BOTH, report.claims.single().modality)
        val assessment = report.assessments.single()
        assertEquals(OverallAssessment.INSUFFICIENT_EVIDENCE, assessment.overall)
        assertEquals(setOf(Relation.INSUFFICIENT), assessment.relations.map { it.relation }.toSet())
        assertTrue(report.assessments.none { it.overall == OverallAssessment.SUPPORTED })
        val relations = report.assessments.flatMap { it.relations }
        assertTrue(relations.none { it.relation == Relation.SUPPORT })
        val retracted = report.evidence.single { it.id == "evd_synthetic_0005" }
        assertEquals(RetractionStatus.RETRACTED, retracted.retractionStatus)
        assertEquals(SourceInspectionLevel.ABSTRACT_ONLY, retracted.inspectionLevel)
        assertEquals(SourceType.PEER_REVIEWED, retracted.sourceType)
        assertEquals(RetrievalRelevance.MEDIUM, retracted.retrievalRelevance)
        assertNull(retracted.excerpt)
        val metadataOnly = report.evidence.single { it.id == "evd_synthetic_0006" }
        assertEquals(SourceInspectionLevel.METADATA_ONLY, metadataOnly.inspectionLevel)
        assertEquals(RetrievalRelevance.LOW, metadataOnly.retrievalRelevance)
        assertEquals(RetractionStatus.NONE, metadataOnly.retractionStatus)
        assertNull(metadataOnly.source.publishedAt)
        assertNull(metadataOnly.excerpt)
    }

    @Test
    fun noClaimsFixtureIsCompleteWithEmptyFindingsAndNoVerdict() {
        val investigation = parse(fixture("no-claims"))
        assertTrue(investigation.isComplete)
        assertFalse(investigation.isFailed)
        assertNull(investigation.error)
        assertEquals(Coverage(CoverageStatus.COMPLETE, 58_000, 58_000), investigation.coverage)
        val report = checkNotNull(investigation.report)
        assertFalse(report.provisional)
        assertTrue(report.claims.isEmpty())
        assertTrue(report.evidence.isEmpty())
        assertTrue(report.assessments.isEmpty())
        assertTrue(report.assessesEveryClaim)
        assertTrue(report.changeSummary.contains("not a statement that the content is accurate"))
        assertTrue(checkNotNull(investigation.job).isPublished)
    }

    @Test
    fun everyFixtureRoundTripsThroughTheEncoder() {
        for (fixture in fixtures) {
            val investigation = parse(fixture)
            val encoded = InvestigationCodec.encodeInvestigation(investigation)
            val expected = checkNotNull(fixture.investigation)
            assertEquals(
                fixture.name,
                ContractFixtures.withoutOptionalNulls(expected, OPTIONAL_KEYS),
                ContractFixtures.element(encoded)
            )
            val reparsed = InvestigationCodec.parseInvestigation(encoded)
            assertEquals(fixture.name, investigation, reparsed)
            val report = investigation.report ?: continue
            val encodedReport = InvestigationCodec.encodeReportVersion(report)
            assertEquals(fixture.name, report, InvestigationCodec.parseReportVersion(encodedReport))
        }
    }

    private fun parse(fixture: Fixture): Investigation =
        InvestigationCodec.parseInvestigation(fixture.investigationPayload())

    private fun fixture(name: String): Fixture =
        checkNotNull(byName[name]) { "$name fixture is missing" }

    private companion object {
        val RESULT_FIXTURES = setOf(
            "complete",
            "partial",
            "failed",
            "cancelled",
            "insufficient-evidence",
            "no-claims"
        )
        val OPTIONAL_KEYS = setOf("covered_ms", "total_ms", "duration_ms")
        const val SYNTHETIC_UUID_PREFIX = "00000000-0000-4000-8000-"
    }
}
