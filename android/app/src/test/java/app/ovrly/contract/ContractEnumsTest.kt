package app.ovrly.contract

import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-checks the Kotlin enums and models against the committed schemas read from the test
 * classpath, so a schema change in `packages/contracts` fails the Android build until the
 * models are reviewed, and proves every enum maps an undefined value to `UNKNOWN` without
 * ever treating it as a known success state.
 */
class ContractEnumsTest {
    /** One entry per `$def` in `enums.schema.json`, in schema order. */
    private class Registered<E : Enum<E>>(
        val def: String,
        entries: List<E>,
        wireName: (E) -> String,
        private val parse: (String) -> E
    ) {
        val wireNames: List<String> = entries.map(wireName)
        val unknown: Enum<*> = entries.last()

        fun fromWire(name: String): Enum<*> = parse(name)
    }

    private val enums: List<Registered<*>> = listOf(
        Registered("job_state", JobState.entries, { it.wireName }) { JobState.fromWire(it) },
        Registered("retry_class", RetryClass.entries, { it.wireName }) { RetryClass.fromWire(it) },
        Registered("investigation_state", InvestigationState.entries, { it.wireName }) {
            InvestigationState.fromWire(it)
        },
        Registered("stage", Stage.entries, { it.wireName }) { Stage.fromWire(it) },
        Registered("processing_status", ProcessingStatus.entries, { it.wireName }) {
            ProcessingStatus.fromWire(it)
        },
        Registered("coverage_status", CoverageStatus.entries, { it.wireName }) {
            CoverageStatus.fromWire(it)
        },
        Registered("upload_state", UploadState.entries, { it.wireName }) {
            UploadState.fromWire(it)
        },
        Registered("source_kind", SourceKind.entries, { it.wireName }) { SourceKind.fromWire(it) },
        Registered("timebase", Timebase.entries, { it.wireName }) { Timebase.fromWire(it) },
        Registered("modality", Modality.entries, { it.wireName }) { Modality.fromWire(it) },
        Registered("relation", Relation.entries, { it.wireName }) { Relation.fromWire(it) },
        Registered("overall_assessment", OverallAssessment.entries, { it.wireName }) {
            OverallAssessment.fromWire(it)
        },
        Registered("source_inspection_level", SourceInspectionLevel.entries, { it.wireName }) {
            SourceInspectionLevel.fromWire(it)
        },
        Registered("source_type", SourceType.entries, { it.wireName }) { SourceType.fromWire(it) },
        Registered("retrieval_relevance", RetrievalRelevance.entries, { it.wireName }) {
            RetrievalRelevance.fromWire(it)
        },
        Registered("retraction_status", RetractionStatus.entries, { it.wireName }) {
            RetractionStatus.fromWire(it)
        },
        Registered("correction_attribution", CorrectionAttribution.entries, { it.wireName }) {
            CorrectionAttribution.fromWire(it)
        },
        Registered("capture_session_state", CaptureSessionState.entries, { it.wireName }) {
            CaptureSessionState.fromWire(it)
        },
        Registered("chunk_disposition", ChunkDisposition.entries, { it.wireName }) {
            ChunkDisposition.fromWire(it)
        }
    )

    /** A Kotlin model and the schema object it mirrors; [extra] names schema-only members. */
    private class Mirror(
        val descriptor: SerialDescriptor,
        val file: String,
        val pointer: String = "",
        val extra: Set<String> = emptySet()
    )

    private val mirrors = listOf(
        Mirror(Investigation.serializer().descriptor, INVESTIGATION),
        Mirror(Coverage.serializer().descriptor, INVESTIGATION, "coverage"),
        Mirror(InvestigationError.serializer().descriptor, INVESTIGATION, "safe_error"),
        Mirror(InvestigationSource.Url.serializer().descriptor, INVESTIGATION, "url_source", KIND),
        Mirror(
            InvestigationSource.Upload.serializer().descriptor,
            INVESTIGATION,
            "upload_source",
            KIND
        ),
        Mirror(InvestigationCreateRequest.serializer().descriptor, CREATE_REQUEST),
        Mirror(InvestigationSource.Url.serializer().descriptor, CREATE_REQUEST, "url_source", KIND),
        Mirror(
            InvestigationSource.Upload.serializer().descriptor,
            CREATE_REQUEST,
            "upload_source",
            KIND
        ),
        Mirror(Job.serializer().descriptor, "job.schema.json"),
        Mirror(ReportVersion.serializer().descriptor, "report-version.schema.json"),
        Mirror(Claim.serializer().descriptor, "claim.schema.json"),
        Mirror(ClaimCorrection.serializer().descriptor, "claim.schema.json", "correction"),
        Mirror(Evidence.serializer().descriptor, "evidence.schema.json"),
        Mirror(EvidenceSource.serializer().descriptor, "evidence.schema.json", "source"),
        Mirror(Assessment.serializer().descriptor, "assessment.schema.json"),
        Mirror(
            EvidenceRelation.serializer().descriptor,
            "assessment.schema.json",
            "evidence_relation"
        ),
        Mirror(Interval.serializer().descriptor, "common.schema.json", "interval"),
        Mirror(SeqRange.serializer().descriptor, "common.schema.json", "seq_range"),
        Mirror(Upload.serializer().descriptor, "upload.schema.json"),
        Mirror(UploadDeclareRequest.serializer().descriptor, "upload-declare-request.schema.json"),
        Mirror(
            UploadCompleteRequest.serializer().descriptor,
            "upload-complete-request.schema.json"
        ),
        Mirror(CaptureSession.serializer().descriptor, "capture-session.schema.json"),
        Mirror(CaptureChunkRequest.serializer().descriptor, "capture-chunk-request.schema.json"),
        Mirror(CaptureChunk.serializer().descriptor, "capture-chunk.schema.json"),
        Mirror(CaptureCreateRequest.serializer().descriptor, "capture-create-request.schema.json"),
        Mirror(CaptureCloseRequest.serializer().descriptor, "capture-close-request.schema.json"),
        Mirror(CaptureMetadata.serializer().descriptor, "capture-metadata.schema.json"),
        Mirror(CaptureStatus.serializer().descriptor, "capture-status.schema.json"),
        Mirror(CaptureManifest.serializer().descriptor, "capture-status.schema.json", "manifest"),
        Mirror(
            CaptureModalityCoverage.serializer().descriptor,
            "capture-status.schema.json",
            "modality_coverage"
        ),
        Mirror(CaptureWork.serializer().descriptor, "capture-status.schema.json", "work"),
        Mirror(CaptureClaimState.serializer().descriptor, "capture-status.schema.json", "claim_state"),
        Mirror(
            InvestigationSource.Capture.serializer().descriptor,
            INVESTIGATION,
            "capture_source",
            KIND
        ),
        Mirror(ContractError.serializer().descriptor, "error.schema.json")
    )

    @Test
    fun everyEnumDefHasOneKotlinEnumWithTheSameWireValues() {
        val defs = ContractFixtures.schema("enums.schema.json").getValue("\$defs").jsonObject
        assertEquals(defs.keys.toList(), enums.map { it.def })
        for (registered in enums) {
            val def = defs.getValue(registered.def).jsonObject
            val label = registered.def
            assertEquals(label, "string", def.getValue("type").jsonPrimitive.content)
            val expected = def.getValue("enum").jsonArray.map { it.jsonPrimitive.content }
            assertEquals(label, expected, registered.wireNames.dropLast(1))
            assertEquals(label, "", registered.wireNames.last())
            assertEquals(label, "UNKNOWN", registered.unknown.name)
            assertEquals(label, registered.wireNames.size, registered.wireNames.toSet().size)
            for (name in expected) {
                val entry = registered.fromWire(name)
                assertNotEquals("$label $name", registered.unknown, entry)
                assertEquals("$label $name", name, registered.wireNames[entry.ordinal])
            }
        }
    }

    @Test
    fun everyEnumMapsAFutureValueToUnknownAndNothingElse() {
        for (registered in enums) {
            val label = registered.def
            for (name in listOf(FUTURE, "", "UNKNOWN", "Unknown", " ", "null")) {
                assertEquals("$label '$name'", registered.unknown, registered.fromWire(name))
            }
            val first = registered.wireNames.first()
            val variants = listOf(first.uppercase(), " $first", "$first ", first.replace("_", "-"))
            for (variant in variants.filter { it != first }) {
                assertEquals("$label '$variant'", registered.unknown, registered.fromWire(variant))
            }
        }
        assertEquals(SourceInspectionLevel.UNDETERMINED, SourceInspectionLevel.fromWire("unknown"))
        assertEquals(RetractionStatus.UNDETERMINED, RetractionStatus.fromWire("unknown"))
        assertNotEquals(SourceInspectionLevel.UNKNOWN, SourceInspectionLevel.fromWire("unknown"))
        assertNotEquals(RetractionStatus.UNKNOWN, RetractionStatus.fromWire("unknown"))
    }

    @Test
    fun futureEnumValuesInAnInvestigationAreKeptButNeverSuccess() {
        val results = ContractFixtures.load(ContractFixtures.RESULTS)
        val complete = results.single { it.name == "complete" }
        val paths = listOf(
            listOf("state"),
            listOf("stage"),
            listOf("processing_status"),
            listOf("coverage", "status"),
            listOf("job", "state"),
            listOf("job", "stage"),
            listOf("job", "retry_class"),
            listOf("report", "claims", "0", "interval", "timebase"),
            listOf("report", "claims", "0", "modality"),
            listOf("report", "claims", "0", "correction", "attributed_to"),
            listOf("report", "evidence", "0", "source_type"),
            listOf("report", "evidence", "0", "inspection_level"),
            listOf("report", "evidence", "0", "retrieval_relevance"),
            listOf("report", "evidence", "0", "retraction_status"),
            listOf("report", "assessments", "0", "relations", "0", "relation"),
            listOf("report", "assessments", "0", "overall")
        )
        val payload = paths.fold(checkNotNull(complete.investigation)) { element, path ->
            ContractFixtures.replace(element, path, JsonPrimitive(FUTURE))
        }
        val investigation = InvestigationCodec.parseInvestigation(payload.toString())
        assertEquals(InvestigationState.UNKNOWN, investigation.state)
        assertEquals(Stage.UNKNOWN, investigation.stage)
        assertEquals(ProcessingStatus.UNKNOWN, investigation.processingStatus)
        assertEquals(CoverageStatus.UNKNOWN, investigation.coverage.status)
        assertFalse(investigation.isComplete)
        assertFalse(investigation.isFailed)
        val job = checkNotNull(investigation.job)
        assertEquals(JobState.UNKNOWN, job.state)
        assertEquals(Stage.UNKNOWN, job.stage)
        assertEquals(RetryClass.UNKNOWN, job.retryClass)
        assertFalse(job.isPublished)
        val report = checkNotNull(investigation.report)
        assertEquals(2, report.claims.size)
        assertEquals(Timebase.UNKNOWN, report.claims[0].interval.timebase)
        assertEquals(Modality.UNKNOWN, report.claims[0].modality)
        assertEquals(CorrectionAttribution.UNKNOWN, report.claims[0].correction?.attributedTo)
        assertEquals(Modality.TEXT, report.claims[1].modality)
        val evidence = report.evidence[0]
        assertEquals(SourceType.UNKNOWN, evidence.sourceType)
        assertEquals(SourceInspectionLevel.UNKNOWN, evidence.inspectionLevel)
        assertEquals(RetrievalRelevance.UNKNOWN, evidence.retrievalRelevance)
        assertEquals(RetractionStatus.UNKNOWN, evidence.retractionStatus)
        val assessment = report.assessments[0]
        assertEquals(Relation.UNKNOWN, assessment.relations[0].relation)
        assertEquals(Relation.SUPPORT, assessment.relations[1].relation)
        assertEquals(OverallAssessment.UNKNOWN, assessment.overall)
        assertEquals(OverallAssessment.QUALIFIED, report.assessments[1].overall)
        assertThrows(IllegalArgumentException::class.java) {
            InvestigationCodec.encodeInvestigation(investigation)
        }
    }

    @Test
    fun futureEnumValuesInTheOtherReadModelsAreNeverSuccess() {
        val intake = ContractFixtures.load(ContractFixtures.INTAKE).associateBy { it.name }
        fun response(name: String, vararg path: String): String {
            val element = checkNotNull(intake.getValue(name).response)
            val future = JsonPrimitive(FUTURE)
            return ContractFixtures.replace(element, path.toList(), future).toString()
        }

        val upload = UploadCodec.parseUpload(response("upload-complete", "state"))
        assertEquals(UploadState.UNKNOWN, upload.state)
        assertFalse(upload.isCompleted)
        assertThrows(IllegalArgumentException::class.java) { UploadCodec.encodeUpload(upload) }

        val chunk = CaptureCodec.parseChunk(response("capture-chunk-duplicate", "disposition"))
        assertEquals(ChunkDisposition.UNKNOWN, chunk.disposition)
        assertFalse(chunk.isStored)
        assertThrows(IllegalArgumentException::class.java) { CaptureCodec.encodeChunk(chunk) }

        val session = CaptureCodec.parseSession(response("capture-session-open", "state"))
        assertEquals(CaptureSessionState.UNKNOWN, session.state)
        assertFalse(session.isOpen)
        assertThrows(IllegalArgumentException::class.java) { CaptureCodec.encodeSession(session) }

        val investigation = InvestigationCodec.parseInvestigation(
            response("investigation-create-url", "source", "kind")
        )
        assertEquals(InvestigationSource.Unknown(FUTURE), investigation.source)
        assertEquals(SourceKind.UNKNOWN, investigation.source.kind)
        assertThrows(IllegalArgumentException::class.java) {
            InvestigationCodec.encodeInvestigation(investigation)
        }
    }

    @Test
    fun kotlinModelsMatchTheCommittedSchemaProperties() {
        for (mirror in mirrors) {
            val node = schemaNode(mirror.file, mirror.pointer)
            val label = "${mirror.file}#${mirror.pointer}"
            val additional = node.getValue("additionalProperties").jsonPrimitive.content
            assertEquals(label, "false", additional)
            val properties = node.getValue("properties").jsonObject.keys
            val required = node["required"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()
            val descriptor = mirror.descriptor
            val indices = 0 until descriptor.elementsCount
            val elements = indices.map { descriptor.getElementName(it) }
            val mandatory = indices.filterNot { descriptor.isElementOptional(it) }
                .map { descriptor.getElementName(it) }
            assertEquals(label, properties, elements.toSet() + mirror.extra)
            assertEquals(label, required.toSet(), mandatory.toSet() + mirror.extra)
        }
        val mapped = mirrors.map { it.file }.toSet() + UNMIRRORED_SCHEMAS
        assertEquals(ContractFixtures.schemaFiles(), mapped)
    }

    @Test
    fun constantsAndOneOfBranchesMatchTheCommittedSchemas() {
        val session = properties(schemaNode("capture-session.schema.json", ""))
        assertEquals(Timebase.CAPTURE.wireName, const(session, "timebase"))
        assertEquals(CaptureSession.DUPLICATE_HANDLING, const(session, "duplicate_handling"))
        assertEquals(CaptureSession.OUT_OF_ORDER_HANDLING, const(session, "out_of_order_handling"))
        for (file in listOf("capture-chunk-request.schema.json", "capture-chunk.schema.json")) {
            val interval = properties(schemaNode(file, "")).getValue("interval").jsonObject
            val restriction = properties(interval.getValue("allOf").jsonArray[1])
            assertEquals(file, Timebase.CAPTURE.wireName, const(restriction, "timebase"))
        }
        for (file in listOf(INVESTIGATION, CREATE_REQUEST)) {
            assertEquals(file, "url", const(properties(schemaNode(file, "url_source")), KIND_KEY))
            val upload = properties(schemaNode(file, "upload_source"))
            assertEquals(file, "upload", const(upload, KIND_KEY))
        }
        val branches = ContractFixtures.schema(INVESTIGATION).getValue("oneOf").jsonArray
            .map { properties(it) }
        assertEquals(3, branches.size)
        assertEquals(setOf("waiting", "checking", "cancelled"), status(branches[0]))
        assertEquals(setOf("queued", "running", "cancelled"), enumValues(branches[0], "state"))
        assertEquals("null", type(branches[0], "report"))
        assertEquals("null", type(branches[0], "error"))
        assertEquals(setOf("partial", "complete", "cancelled"), status(branches[1]))
        assertEquals(setOf("running", "completed", "cancelled"), enumValues(branches[1], "state"))
        assertEquals("object", type(branches[1], "report"))
        assertEquals("null", type(branches[1], "error"))
        assertEquals(setOf("failed"), status(branches[2]))
        assertEquals(setOf("failed"), enumValues(branches[2], "state"))
        assertEquals("null", type(branches[2], "report"))
        assertEquals("object", type(branches[2], "error"))
    }

    @Test
    fun versionPinMatchesTheContractsPackage() {
        val version = ContractFixtures.resourceText("VERSION").trim()
        assertEquals(version, ContractJson.CONTRACT_VERSION)
        assertEquals(version, VoiceActionCodec.CONTRACT_VERSION)
        assertTrue(version, Regex("0\\.\\d+\\.\\d+-draft").matches(version))
    }

    private fun schemaNode(file: String, pointer: String): JsonObject {
        val schema = ContractFixtures.schema(file)
        if (pointer.isEmpty()) return schema
        return schema.getValue("\$defs").jsonObject.getValue(pointer).jsonObject
    }

    private fun properties(node: JsonElement): JsonObject =
        node.jsonObject.getValue("properties").jsonObject

    private fun const(properties: JsonObject, key: String): String =
        properties.getValue(key).jsonObject.getValue("const").jsonPrimitive.content

    private fun type(properties: JsonObject, key: String): String =
        properties.getValue(key).jsonObject.getValue("type").jsonPrimitive.content

    private fun status(properties: JsonObject): Set<String> =
        enumValues(properties, "processing_status")

    private fun enumValues(properties: JsonObject, key: String): Set<String> =
        properties.getValue(key).jsonObject.getValue("enum").jsonArray
            .map { it.jsonPrimitive.content }
            .toSet()

    private companion object {
        const val FUTURE = "__future_value__"
        const val KIND_KEY = "kind"
        const val INVESTIGATION = "investigation.schema.json"
        const val CREATE_REQUEST = "investigation-create-request.schema.json"
        val KIND = setOf(KIND_KEY)

        /**
         * Schemas with no model of their own: definitions, `$ref` wrappers, the voice slice and
         * the BE-04 part 3 job action receipts (#75), whose Android models are a #62 follow-up.
         */
        val UNMIRRORED_SCHEMAS = setOf(
            "enums.schema.json",
            "upload-declare-response.schema.json",
            "upload-complete-response.schema.json",
            "voice-action-request.schema.json",
            "voice-action-response.schema.json",
            "job-action-request.schema.json",
            "job-cancel-response.schema.json",
            "job-delete-response.schema.json"
        )
    }
}
