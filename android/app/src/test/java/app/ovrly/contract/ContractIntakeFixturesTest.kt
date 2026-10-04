package app.ovrly.contract

import app.ovrly.contract.ContractFixtures.Fixture
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Drives the production request and read-model codecs with the committed intake fixtures in
 * `packages/contracts/fixtures/intake`: every request parses and encodes back to the fixture
 * through the schema it names, every response parses to the expected typed values, and the
 * mixed-source negative fails.
 */
class ContractIntakeFixturesTest {
    private val fixtures = ContractFixtures.load(ContractFixtures.INTAKE)
    private val byName = fixtures.associateBy { it.name }

    @Test
    fun everyCommittedIntakeFixtureIsClassifiedByTheseTests() {
        assertEquals(INTAKE_FIXTURES, byName.keys)
        for (fixture in fixtures) {
            assertTrue("${fixture.name} must be synthetic", fixture.synthetic)
            assertEquals(fixture.name, fixture.request != null, fixture.expectRequest != null)
            assertEquals(fixture.name, fixture.response != null, fixture.expectResponse != null)
            assertEquals(fixture.name, fixture.request != null, "request" in fixture.schemas)
            assertEquals(fixture.name, fixture.response != null, "response" in fixture.schemas)
        }
    }

    @Test
    fun validRequestsParseThroughTheSchemaTheyNameAndEncodeBack() {
        val valid = fixtures.filter { it.expectRequest == "valid" }
        assertEquals(VALID_REQUESTS, valid.size)
        for (fixture in valid) {
            val schema = fixture.schemas.getValue("request")
            val request = parseRequest(schema, fixture.requestPayload())
            val encoded = encodeRequest(schema, request)
            val expected = ContractFixtures.withoutOptionalNulls(
                checkNotNull(fixture.request),
                OPTIONAL_KEYS
            )
            assertEquals(fixture.name, expected, ContractFixtures.element(encoded))
            assertEquals(fixture.name, request, parseRequest(schema, encoded))
        }
    }

    @Test
    fun validResponsesParseThroughTheSchemaTheyNameAndEncodeBack() {
        val valid = fixtures.filter { it.expectResponse == "valid" }
        assertEquals(VALID_RESPONSES, valid.size)
        for (fixture in valid) {
            val schema = fixture.schemas.getValue("response")
            val response = parseResponse(schema, fixture.responsePayload())
            val encoded = encodeResponse(schema, response)
            val expected = ContractFixtures.withoutOptionalNulls(
                checkNotNull(fixture.response),
                OPTIONAL_KEYS
            )
            assertEquals(fixture.name, expected, ContractFixtures.element(encoded))
            assertEquals(fixture.name, response, parseResponse(schema, encoded))
        }
    }

    @Test
    fun mixedSourceRequestFailsBecauseTheUrlBranchHasNoUploadId() {
        val fixture = fixture("investigation-create-mixed-source")
        assertEquals("invalid", fixture.expectRequest)
        assertNull(fixture.response)
        val failure = assertThrows(ContractParseException::class.java) {
            InvestigationCodec.parseCreateRequest(fixture.requestPayload())
        }
        assertTrue(failure.message, failure.message.orEmpty().contains("upload_id"))
    }

    @Test
    fun uploadDeclareFixtureParsesToAPendingUpload() {
        val fixture = fixture("upload-declare")
        val request = UploadCodec.parseDeclareRequest(fixture.requestPayload())
        assertEquals(UploadDeclareRequest(7_340_032, SHA256, "video/mp4"), request)
        val upload = UploadCodec.parseUpload(fixture.responsePayload())
        assertEquals(UPLOAD_ID, upload.id)
        assertEquals(UploadState.PENDING, upload.state)
        assertFalse(upload.isCompleted)
        assertEquals("/v1/uploads/$UPLOAD_ID/content", upload.target)
        assertEquals(268_435_456L, upload.maxBytes)
        assertEquals(request.sizeBytes, upload.declaredSizeBytes)
        assertEquals(request.sha256, upload.declaredSha256)
        assertEquals(request.contentType, upload.contentType)
        assertEquals("2026-10-04T12:15:00Z", upload.expiresAt)
        assertNull(upload.completedAt)
    }

    @Test
    fun uploadCompleteFixtureParsesAnEmptyBodyAndACompletedUpload() {
        val fixture = fixture("upload-complete")
        val request = UploadCodec.parseCompleteRequest(fixture.requestPayload())
        assertEquals(UploadCompleteRequest, request)
        assertEquals("{}", UploadCodec.encodeCompleteRequest(UploadCompleteRequest))
        val upload = UploadCodec.parseUpload(fixture.responsePayload())
        assertEquals(UploadState.COMPLETED, upload.state)
        assertTrue(upload.isCompleted)
        assertEquals("2026-10-04T12:03:20Z", upload.completedAt)
        assertEquals(UPLOAD_ID, upload.id)
    }

    @Test
    fun investigationCreateFixturesParseToTheIntakeReadModel() {
        val byUrl = fixture("investigation-create-url")
        val urlRequest = InvestigationCodec.parseCreateRequest(byUrl.requestPayload())
        assertEquals(
            InvestigationCreateRequest(InvestigationSource.Url(VIDEO_URL, 185_000)),
            urlRequest
        )
        val urlInvestigation = InvestigationCodec.parseInvestigation(byUrl.responsePayload())
        assertIntake(urlInvestigation, "00000000-0000-4000-8000-000000000401")
        assertEquals(urlRequest.source, urlInvestigation.source)
        assertEquals(185_000L, urlInvestigation.source.durationMs)

        val byUpload = fixture("investigation-create-upload")
        val uploadRequest = InvestigationCodec.parseCreateRequest(byUpload.requestPayload())
        assertEquals(
            InvestigationCreateRequest(InvestigationSource.Upload(UPLOAD_ID, null)),
            uploadRequest
        )
        val uploadInvestigation = InvestigationCodec.parseInvestigation(byUpload.responsePayload())
        assertIntake(uploadInvestigation, "00000000-0000-4000-8000-000000000402")
        assertEquals(InvestigationSource.Upload(UPLOAD_ID), uploadInvestigation.source)
        assertNull(uploadInvestigation.source.durationMs)
    }

    @Test
    fun captureSessionFixtureStatesTheGapAndTheHandlingRulesAsData() {
        val session = CaptureCodec.parseSession(fixture("capture-session-open").responsePayload())
        assertEquals(SESSION_ID, session.id)
        assertEquals("00000000-0000-4000-8000-000000000002", session.investigationId)
        assertEquals(CaptureSessionState.OPEN, session.state)
        assertTrue(session.isOpen)
        assertEquals(Timebase.CAPTURE, session.timebase)
        assertNull(session.closedAt)
        assertEquals(180_000L, session.maxDurationMs)
        assertEquals(10_000L, session.chunkDurationMs)
        assertEquals(5, session.chunksReceived)
        assertEquals(5, session.highestSeq)
        assertEquals(50_000L, session.receivedMs)
        assertEquals(listOf(SeqRange(3, 3)), session.gaps)
        assertEquals(CaptureSession.DUPLICATE_HANDLING, session.duplicateHandling)
        assertEquals(CaptureSession.OUT_OF_ORDER_HANDLING, session.outOfOrderHandling)
    }

    @Test
    fun captureChunkFixturesCarryTheExpectedDisposition() {
        val outOfOrder = fixture("capture-chunk-out-of-order")
        val request = CaptureCodec.parseChunkRequest(outOfOrder.requestPayload())
        assertEquals(SESSION_ID, request.sessionId)
        assertEquals(5, request.seq)
        assertEquals(Interval(50_000, 60_000, Timebase.CAPTURE), request.interval)
        assertEquals(393_216L, request.sizeBytes)
        assertEquals("video/mp4", request.contentType)
        val stored = CaptureCodec.parseChunk(outOfOrder.responsePayload())
        assertEquals(ChunkDisposition.OUT_OF_ORDER, stored.disposition)
        assertTrue(stored.isStored)
        assertEquals(request.interval, stored.interval)
        assertEquals(request.sha256, stored.sha256)
        assertEquals(listOf(SeqRange(3, 3)), stored.gaps)
        assertEquals("2026-10-04T12:01:01Z", stored.receivedAt)

        val replayed = fixture("capture-chunk-duplicate").responsePayload()
        val duplicate = CaptureCodec.parseChunk(replayed)
        assertEquals(ChunkDisposition.DUPLICATE, duplicate.disposition)
        assertTrue(duplicate.isStored)
        assertEquals(2, duplicate.seq)
        assertTrue(duplicate.gaps.isEmpty())
        assertEquals("2026-10-04T12:00:31Z", duplicate.receivedAt)
    }

    private fun assertIntake(investigation: Investigation, id: String) {
        assertEquals(id, investigation.id)
        assertEquals(InvestigationState.QUEUED, investigation.state)
        assertEquals(Stage.INTAKE, investigation.stage)
        assertEquals(ProcessingStatus.WAITING, investigation.processingStatus)
        assertEquals(Coverage(CoverageStatus.NOT_STARTED), investigation.coverage)
        assertEquals(1, investigation.version)
        assertNull(investigation.error)
        assertNull(investigation.job)
        assertNull(investigation.report)
        assertFalse(investigation.isComplete)
        assertFalse(investigation.isFailed)
    }

    private fun parseRequest(schema: String, payload: String): Any =
        checkNotNull(REQUEST_PARSERS[schema]) { "no request codec is mapped to $schema" }(payload)

    private fun encodeRequest(schema: String, request: Any): String = when (schema) {
        DECLARE_REQUEST -> UploadCodec.encodeDeclareRequest(request as UploadDeclareRequest)
        COMPLETE_REQUEST -> UploadCodec.encodeCompleteRequest(request as UploadCompleteRequest)
        CREATE_REQUEST -> encodeCreate(request as InvestigationCreateRequest)
        CHUNK_REQUEST -> CaptureCodec.encodeChunkRequest(request as CaptureChunkRequest)
        else -> error("no request codec is mapped to $schema")
    }

    private fun encodeCreate(request: InvestigationCreateRequest): String =
        InvestigationCodec.encodeCreateRequest(request)

    private fun parseResponse(schema: String, payload: String): Any =
        checkNotNull(RESPONSE_PARSERS[schema]) { "no read-model codec for $schema" }(payload)

    private fun encodeResponse(schema: String, response: Any): String = when (schema) {
        DECLARE_RESPONSE, COMPLETE_RESPONSE -> UploadCodec.encodeUpload(response as Upload)
        INVESTIGATION -> InvestigationCodec.encodeInvestigation(response as Investigation)
        SESSION -> CaptureCodec.encodeSession(response as CaptureSession)
        CHUNK -> CaptureCodec.encodeChunk(response as CaptureChunk)
        else -> error("no read-model codec is mapped to $schema")
    }

    private fun fixture(name: String): Fixture =
        checkNotNull(byName[name]) { "$name fixture is missing" }

    private companion object {
        const val DECLARE_REQUEST = "upload-declare-request.schema.json"
        const val COMPLETE_REQUEST = "upload-complete-request.schema.json"
        const val CREATE_REQUEST = "investigation-create-request.schema.json"
        const val CHUNK_REQUEST = "capture-chunk-request.schema.json"
        const val DECLARE_RESPONSE = "upload-declare-response.schema.json"
        const val COMPLETE_RESPONSE = "upload-complete-response.schema.json"
        const val INVESTIGATION = "investigation.schema.json"
        const val SESSION = "capture-session.schema.json"
        const val CHUNK = "capture-chunk.schema.json"

        val REQUEST_PARSERS: Map<String, (String) -> Any> = mapOf(
            DECLARE_REQUEST to UploadCodec::parseDeclareRequest,
            COMPLETE_REQUEST to UploadCodec::parseCompleteRequest,
            CREATE_REQUEST to InvestigationCodec::parseCreateRequest,
            CHUNK_REQUEST to CaptureCodec::parseChunkRequest
        )

        val RESPONSE_PARSERS: Map<String, (String) -> Any> = mapOf(
            DECLARE_RESPONSE to UploadCodec::parseUpload,
            COMPLETE_RESPONSE to UploadCodec::parseUpload,
            INVESTIGATION to InvestigationCodec::parseInvestigation,
            SESSION to CaptureCodec::parseSession,
            CHUNK to CaptureCodec::parseChunk
        )

        val INTAKE_FIXTURES = setOf(
            "capture-chunk-duplicate",
            "capture-chunk-out-of-order",
            "capture-session-open",
            "investigation-create-mixed-source",
            "investigation-create-upload",
            "investigation-create-url",
            "upload-complete",
            "upload-declare"
        )
        const val VALID_REQUESTS = 6
        const val VALID_RESPONSES = 7
        val OPTIONAL_KEYS = setOf("duration_ms", "covered_ms", "total_ms", "content_type")
        const val UPLOAD_ID = "00000000-0000-4000-8000-000000000301"
        const val SESSION_ID = "00000000-0000-4000-8000-000000000501"
        const val VIDEO_URL = "https://video.example/synthetic/clip-0001"
        const val SHA256 = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
    }
}
