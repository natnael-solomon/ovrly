package app.ovrly.contract

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Proves the non-voice codecs reject incompatible payloads explicitly: the Android-only
 * fixtures in `src/test/resources/fixtures/contract-incompatible/` (a missing required field,
 * wrong types, a failed investigation with a report, an unknown source kind, extra request
 * fields, broken cross-references), malformed JSON, and models built in code that violate
 * the schema. Nothing is replaced by a success-shaped default.
 */
class ContractCompatibilityTest {
    private val incompatible = ContractFixtures.load(ContractFixtures.CONTRACT_INCOMPATIBLE)

    @Test
    fun everyIncompatibleFixtureDeclaresASchemaAndOneOutcome() {
        assertEquals(INCOMPATIBLE_FIXTURES, incompatible.map { it.name }.toSet())
        for (fixture in incompatible) {
            assertTrue(fixture.name, fixture.synthetic)
            assertNotNull(fixture.name, fixture.payload)
            assertTrue(fixture.name, fixture.schema in PARSERS.keys)
            assertTrue(fixture.name, fixture.expectPayload in setOf("invalid", "valid"))
            assertEquals(fixture.name, fixture.expectPayload == "invalid", fixture.reason != null)
        }
    }

    @Test
    fun invalidFixturesAreRejectedForTheStatedReason() {
        val invalid = incompatible.filter { it.expectPayload == "invalid" }
        assertEquals(INCOMPATIBLE_FIXTURES.size - 1, invalid.size)
        for (fixture in invalid) {
            val parse = PARSERS.getValue(checkNotNull(fixture.schema))
            val failure = assertThrows(fixture.name, ContractParseException::class.java) {
                parse(fixture.payloadText())
            }
            val message = failure.message.orEmpty()
            assertTrue(
                "${fixture.name}: expected '${fixture.reason}' in '$message'",
                message.contains(checkNotNull(fixture.reason))
            )
        }
    }

    @Test
    fun additiveFieldsOnReadModelsAreToleratedButRequestsStayStrict() {
        val fixture = incompatible.single { it.name == "investigation-additive-field-tolerated" }
        assertEquals("valid", fixture.expectPayload)
        val investigation = InvestigationCodec.parseInvestigation(fixture.payloadText())
        assertEquals(ProcessingStatus.CHECKING, investigation.processingStatus)
        assertEquals(InvestigationState.RUNNING, investigation.state)
        assertEquals(Stage.ASR, investigation.stage)
        assertEquals(JobState.RUNNING, investigation.job?.state)
        assertNull(investigation.report)
        assertNull(investigation.error)
        assertFalse(investigation.isComplete)
        val strict = incompatible.single { it.name == "investigation-create-extra-field" }
        assertThrows(ContractParseException::class.java) {
            InvestigationCodec.parseCreateRequest(strict.payloadText())
        }
    }

    @Test
    fun malformedPayloadsFailEveryParser() {
        val malformed = listOf(
            "", " ", "null", "true", "7", "\"text\"", "[]", "{}", "{", "{\"id\": }",
            "{\"id\": \"x\"} trailing", "{\"id\": \"x\"}{}"
        )
        for ((schema, parse) in PARSERS) {
            for (payload in malformed) {
                if (schema == "upload-complete-request.schema.json" && payload == "{}") continue
                assertThrows("$schema <$payload>", ContractParseException::class.java) {
                    parse(payload)
                }
            }
        }
    }

    @Test
    fun nullIsNeverAcceptedForARequiredNonNullField() {
        val intake = ContractFixtures.load(ContractFixtures.INTAKE).associateBy { it.name }
        val upload = checkNotNull(intake.getValue("upload-declare").response)
        for (key in listOf("id", "state", "target", "max_bytes", "declared_sha256", "created_at")) {
            val payload = ContractFixtures.replace(upload, listOf(key), NULL).toString()
            assertThrows(key, ContractParseException::class.java) {
                UploadCodec.parseUpload(payload)
            }
        }
        val investigation = checkNotNull(intake.getValue("investigation-create-url").response)
        val required =
            listOf("id", "state", "stage", "coverage", "version", "source", "processing_status")
        for (key in required) {
            val payload = ContractFixtures.replace(investigation, listOf(key), NULL).toString()
            assertThrows(key, ContractParseException::class.java) {
                InvestigationCodec.parseInvestigation(payload)
            }
        }
        for (key in listOf("job", "report", "error")) {
            val payload = ContractFixtures.replace(investigation, listOf(key), NULL).toString()
            assertNotNull(key, InvestigationCodec.parseInvestigation(payload))
        }
    }

    @Test
    fun quotedNumbersAndBooleansAreNeverCoerced() {
        val intake = ContractFixtures.load(ContractFixtures.INTAKE).associateBy { it.name }
        val chunk = checkNotNull(intake.getValue("capture-chunk-duplicate").response)
        for (path in listOf(listOf("seq"), listOf("size_bytes"), listOf("interval", "end_ms"))) {
            val quoted = ContractFixtures.element("\"7\"")
            val payload = ContractFixtures.replace(chunk, path, quoted).toString()
            val failure = assertThrows(path.toString(), ContractParseException::class.java) {
                CaptureCodec.parseChunk(payload)
            }
            assertTrue(failure.message, failure.message.orEmpty().contains("integer"))
            val fraction = ContractFixtures.element("7.5")
            assertThrows(path.toString(), ContractParseException::class.java) {
                CaptureCodec.parseChunk(ContractFixtures.replace(chunk, path, fraction).toString())
            }
        }
        val results = ContractFixtures.load(ContractFixtures.RESULTS).associateBy { it.name }
        val complete = checkNotNull(results.getValue("complete").investigation)
        for (path in listOf(listOf("job", "cancel_requested"), listOf("report", "provisional"))) {
            val quoted = ContractFixtures.element("\"false\"")
            val payload = ContractFixtures.replace(complete, path, quoted).toString()
            val failure = assertThrows(path.toString(), ContractParseException::class.java) {
                InvestigationCodec.parseInvestigation(payload)
            }
            assertTrue(failure.message, failure.message.orEmpty().contains("boolean"))
        }
    }

    @Test
    fun scalarsAndSmallModelsEnforceTheContractWhenBuiltInCode() {
        rejected { Interval(-1, 1000, Timebase.MEDIA) }
        rejected { Interval(1000, 1000, Timebase.MEDIA) }
        rejected { Interval(1001, 1000, Timebase.MEDIA) }
        rejected { SeqRange(3, 2) }
        rejected { SeqRange(-1, 2) }
        rejected { InvestigationSource.Url("ftp://video.example/clip") }
        rejected { InvestigationSource.Url("https://video.example/with space") }
        rejected { InvestigationSource.Url("https://") }
        rejected { InvestigationSource.Upload("upl_synthetic_0001") }
        rejected { InvestigationSource.Upload("00000000-0000-4000-8000-00000000000A") }
        rejected { InvestigationCreateRequest(InvestigationSource.Unknown("stream")) }
        rejected { InvestigationCreateRequest(InvestigationSource.Url(URL, 0)) }
        rejected { InvestigationCreateRequest(InvestigationSource.Url(URL, -5)) }
        assertNotNull(InvestigationCreateRequest(InvestigationSource.Url(URL, null)))
        rejected { UploadDeclareRequest(0, SHA) }
        rejected { UploadDeclareRequest(1, SHA.dropLast(1)) }
        rejected { UploadDeclareRequest(1, SHA, "xy") }
        rejected { InvestigationError("lower", "x", false) }
        rejected { InvestigationError("CODE", "", false) }
        rejected { claim(id = "bad id") }
        rejected { claim(originalText = "") }
        rejected { claim(proposition = "p".repeat(Claim.MAX_TEXT_LENGTH + 1)) }
        rejected { ClaimCorrection(CorrectionAttribution.USER, "2026-10-04", "x") }
        rejected { ClaimCorrection(CorrectionAttribution.USER, TS, "") }
        rejected { assessment(relations = emptyList(), overall = OverallAssessment.SUPPORTED) }
        rejected { assessment().copy(summary = "") }
        rejected { EvidenceRelation("evd_synthetic_0001", Relation.SUPPORT, "") }
        rejected { EvidenceSource("src_synthetic_0001", "", "P", null, null) }
        rejected { EvidenceSource("src_synthetic_0001", "T", "P", "mailto:x@example", null) }
        rejected { EvidenceSource("src_synthetic_0001", "T", "P", null, "yesterday") }
    }

    @Test
    fun reportsEnforceTheirCrossReferencesWhenBuiltInCode() {
        val oneClaim = listOf(claim())
        val oneEvidence = listOf(evidence())
        rejected { report(claims = listOf(claim(), claim())) }
        rejected { report(evidence = listOf(evidence(claimId = "clm_synthetic_0404"))) }
        rejected { report(assessments = listOf(assessment(claimId = "clm_synthetic_0404"))) }
        rejected { report(oneClaim, oneEvidence, listOf(assessment(), assessment())) }
        rejected { report(oneClaim, oneEvidence, listOf(assessment(version = 2))) }
        val missing = listOf(assessment(relations = listOf(relation("evd_synthetic_0404"))))
        rejected { report(oneClaim, oneEvidence, missing) }
        rejected { report(oneClaim, assessments = listOf(assessment())) }
        rejected { report().copy(version = 0) }
        rejected { report().copy(changeSummary = "") }
        rejected { report().copy(supersedes = "") }
        rejected { report().copy(investigationId = "inv_synthetic_0001") }
        val published = report(oneClaim, oneEvidence, listOf(assessment()))
        assertEquals(oneEvidence, published.evidenceFor("clm_synthetic_0001"))
        assertNotNull(published.assessmentFor("clm_synthetic_0001"))
        assertNull(published.assessmentFor("clm_synthetic_0404"))
    }

    @Test
    fun investigationsEnforceTheOneOfBranchesWhenBuiltInCode() {
        val published = report(listOf(claim()), listOf(evidence()), listOf(assessment()))
        val failed = ProcessingStatus.FAILED to InvestigationState.FAILED
        val complete = ProcessingStatus.COMPLETE to InvestigationState.COMPLETED
        val waiting = ProcessingStatus.WAITING to InvestigationState.QUEUED
        val cancelled = ProcessingStatus.CANCELLED to InvestigationState.CANCELLED
        rejected { investigation(report = published, error = error()) }
        rejected { investigation(failed) }
        rejected { investigation(failed, report = published) }
        rejected { investigation(complete) }
        rejected { investigation(waiting, report = published) }
        val running = ProcessingStatus.COMPLETE to InvestigationState.RUNNING
        rejected { investigation(running, error = error()) }
        rejected { investigation(report = published.copy(investigationId = OTHER_UUID)) }
        rejected { investigation(report = published.copy(version = 2)) }
        rejected { investigation(complete, report = published) }
        val assessed = listOf(assessment(provisional = false))
        val final = published.copy(provisional = false, assessments = assessed)
        rejected { investigation(complete, report = final.copy(assessments = emptyList())) }
        val done = investigation(complete, report = final)
        assertTrue(done.isComplete)
        assertFalse(done.isFailed)
        val stopped = investigation(failed, error = error())
        assertTrue(stopped.isFailed)
        assertFalse(stopped.isComplete)
        assertNull(stopped.report)
        assertNotNull(investigation(waiting))
        assertNotNull(investigation(cancelled))
        assertNotNull(investigation(cancelled, report = published))
    }

    @Test
    fun captureModelsEnforceTheCaptureTimebaseWhenBuiltInCode() {
        val media = Interval(0, 1000, Timebase.MEDIA)
        val capture = Interval(0, 10, Timebase.CAPTURE)
        val stored = ChunkDisposition.STORED
        rejected { CaptureChunkRequest(UUID, -1, capture, 1, SHA, "video/mp4") }
        rejected { CaptureChunkRequest(UUID, 0, media, 1, SHA, "video/mp4") }
        rejected { CaptureChunkRequest(UUID, 0, capture, 1, SHA, "v") }
        rejected { CaptureChunk(UUID, 0, media, 1, SHA, stored, TS, emptyList()) }
        rejected { CaptureChunk(UUID, 0, capture, 1, SHA, stored, TS, listOf(SeqRange(1, 0))) }
        rejected { captureSession(timebase = Timebase.MEDIA) }
        rejected { captureSession(duplicateHandling = "store_twice") }
        rejected { captureSession(outOfOrderHandling = "reject") }
        rejected { captureSession(startedAt = "soon") }
        assertTrue(captureSession().isOpen)
    }

    @Test
    fun unknownEnumsCannotBeEncodedFromCode() {
        val interval = Interval(0, 10, Timebase.CAPTURE)
        val unknown = ChunkDisposition.UNKNOWN
        val chunk = CaptureChunk(UUID, 0, interval, 1, SHA, unknown, TS, emptyList())
        assertFalse(chunk.isStored)
        rejected { CaptureCodec.encodeChunk(chunk) }
        val job = Job(UUID, JobState.UNKNOWN, Stage.INTAKE, false, 1, null, TS, TS)
        assertFalse(job.isPublished)
        val checking = ProcessingStatus.CHECKING to InvestigationState.RUNNING
        rejected { InvestigationCodec.encodeInvestigation(investigation(checking, job = job)) }
        val target = "/v1/uploads/x/content"
        val upload = Upload(UUID, UploadState.UNKNOWN, target, 1, 1, SHA, "video/mp4", TS, TS, null)
        assertFalse(upload.isCompleted)
        rejected { UploadCodec.encodeUpload(upload) }
    }

    private fun rejected(build: () -> Any) {
        assertThrows(IllegalArgumentException::class.java) { build() }
    }

    private fun claim(
        id: String = "clm_synthetic_0001",
        originalText: String = "spoken words",
        proposition: String = "A proposition."
    ) = Claim(
        id,
        "occ_synthetic_0001",
        Interval(0, 1000, Timebase.MEDIA),
        Modality.SPEECH,
        originalText,
        proposition,
        null
    )

    private fun evidence(claimId: String = "clm_synthetic_0001") = Evidence(
        "evd_synthetic_0001",
        claimId,
        EvidenceSource("src_synthetic_0001", "Title", "Publisher", null, null),
        SourceType.NEWS,
        SourceInspectionLevel.METADATA_ONLY,
        RetrievalRelevance.LOW,
        RetractionStatus.UNDETERMINED,
        null,
        TS
    )

    private fun relation(evidenceId: String = "evd_synthetic_0001") =
        EvidenceRelation(evidenceId, Relation.INSUFFICIENT, null)

    private fun assessment(
        claimId: String = "clm_synthetic_0001",
        version: Int = 1,
        relations: List<EvidenceRelation> = listOf(relation()),
        overall: OverallAssessment = OverallAssessment.INSUFFICIENT_EVIDENCE,
        provisional: Boolean = true
    ) = Assessment(
        "asm_synthetic_0001",
        claimId,
        version,
        relations,
        overall,
        provisional,
        "No sound evidence was found."
    )

    private fun report(
        claims: List<Claim> = emptyList(),
        evidence: List<Evidence> = emptyList(),
        assessments: List<Assessment> = emptyList()
    ) = ReportVersion(
        "rpt_synthetic_0001_v1",
        UUID,
        1,
        TS,
        true,
        "First results.",
        null,
        claims,
        evidence,
        assessments
    )

    private fun error() = InvestigationError("MEDIA_UNSUPPORTED", "Processing failed", false)

    private fun investigation(
        progress: Pair<ProcessingStatus, InvestigationState> =
            ProcessingStatus.PARTIAL to InvestigationState.RUNNING,
        error: InvestigationError? = null,
        job: Job? = null,
        report: ReportVersion? = null
    ) = Investigation(
        UUID,
        progress.second,
        Stage.RETRIEVAL,
        Coverage(CoverageStatus.PARTIAL),
        1,
        error,
        InvestigationSource.Url(URL),
        TS,
        TS,
        progress.first,
        job,
        report
    )

    private fun captureSession(
        timebase: Timebase = Timebase.CAPTURE,
        startedAt: String = TS,
        duplicateHandling: String = CaptureSession.DUPLICATE_HANDLING,
        outOfOrderHandling: String = CaptureSession.OUT_OF_ORDER_HANDLING
    ) = CaptureSession(
        UUID,
        OTHER_UUID,
        CaptureSessionState.OPEN,
        timebase,
        startedAt,
        null,
        180_000,
        10_000,
        0,
        null,
        0,
        emptyList(),
        duplicateHandling,
        outOfOrderHandling
    )

    private companion object {
        const val UUID = "00000000-0000-4000-8000-000000000001"
        const val OTHER_UUID = "00000000-0000-4000-8000-000000000002"
        const val URL = "https://video.example/synthetic/clip-0001"
        const val TS = "2026-10-04T12:00:00Z"
        val SHA = "0123456789abcdef".repeat(4)
        val NULL = ContractFixtures.element("null")

        val PARSERS: Map<String, (String) -> Any> = mapOf(
            "investigation.schema.json" to InvestigationCodec::parseInvestigation,
            "report-version.schema.json" to InvestigationCodec::parseReportVersion,
            "investigation-create-request.schema.json" to InvestigationCodec::parseCreateRequest,
            "upload.schema.json" to UploadCodec::parseUpload,
            "upload-declare-request.schema.json" to UploadCodec::parseDeclareRequest,
            "upload-complete-request.schema.json" to UploadCodec::parseCompleteRequest,
            "capture-session.schema.json" to CaptureCodec::parseSession,
            "capture-chunk.schema.json" to CaptureCodec::parseChunk,
            "capture-chunk-request.schema.json" to CaptureCodec::parseChunkRequest
        )

        val INCOMPATIBLE_FIXTURES = setOf(
            "capture-chunk-media-timebase",
            "capture-chunk-request-malformed-session-id",
            "capture-session-unknown-handling",
            "investigation-additive-field-tolerated",
            "investigation-complete-but-provisional",
            "investigation-complete-with-error",
            "investigation-create-extra-field",
            "investigation-create-unknown-source-kind",
            "investigation-failed-with-report",
            "investigation-missing-processing-status",
            "investigation-report-version-mismatch",
            "investigation-report-wrong-type",
            "job-cancel-requested-string",
            "report-assessment-without-relations-supported",
            "report-claim-interval-reversed",
            "report-claim-start-ms-quoted",
            "report-evidence-for-unknown-claim",
            "report-missing-claims",
            "upload-complete-with-field",
            "upload-declare-size-zero",
            "upload-declared-sha256-uppercase"
        )
    }
}
