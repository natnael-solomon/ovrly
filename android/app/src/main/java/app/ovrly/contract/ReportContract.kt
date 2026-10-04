@file:UseSerializers(
    StrictLongSerializer::class,
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

/*
 * Typed read models for `report-version.schema.json`, `claim.schema.json`,
 * `evidence.schema.json` and `assessment.schema.json` (contract 0.1.0-draft). Constructors
 * enforce the schema's lengths, patterns and the cross-references the schema descriptions
 * state, so an instance that exists is one the contract allows; a claim says nothing about
 * truth and a report has no verdict. Parse with [InvestigationCodec].
 */

/** `common.schema.json#/$defs/interval`: half-open `[startMs, endMs)` on [timebase]. */
@Serializable
internal data class Interval(
    @SerialName("start_ms")
    val startMs: Long,
    @SerialName("end_ms")
    val endMs: Long,
    val timebase: Timebase
) {
    init {
        require(startMs >= 0 && startMs < endMs) { "interval must satisfy 0 <= start_ms < end_ms" }
    }
}

/** `claim.schema.json#/$defs/correction`: who replaced the proposition and what it was. */
@Serializable
internal data class ClaimCorrection(
    @SerialName("attributed_to")
    val attributedTo: CorrectionAttribution,
    @SerialName("corrected_at")
    val correctedAt: String,
    @SerialName("superseded_proposition")
    val supersededProposition: String
) {
    init {
        ContractSyntax.timestamp("correction.corrected_at", correctedAt)
        ContractSyntax.text(
            "correction.superseded_proposition",
            supersededProposition,
            max = Claim.MAX_TEXT_LENGTH
        )
    }
}

/** One factual claim occurrence: where it was observed, the words, and the assessed meaning. */
@Serializable
internal data class Claim(
    val id: String,
    @SerialName("occurrence_id")
    val occurrenceId: String,
    val interval: Interval,
    val modality: Modality,
    @SerialName("original_text")
    val originalText: String,
    val proposition: String,
    val correction: ClaimCorrection?
) {
    init {
        ContractSyntax.opaqueId("claim.id", id)
        ContractSyntax.opaqueId("claim.occurrence_id", occurrenceId)
        ContractSyntax.text("claim.original_text", originalText, max = MAX_TEXT_LENGTH)
        ContractSyntax.text("claim.proposition", proposition, max = MAX_TEXT_LENGTH)
    }

    companion object {
        const val MAX_TEXT_LENGTH = 2000
    }
}

/** `evidence.schema.json#/$defs/source`: identity of a source independent of any claim. */
@Serializable
internal data class EvidenceSource(
    val id: String,
    val title: String,
    val publisher: String,
    val url: String?,
    @SerialName("published_at")
    val publishedAt: String?
) {
    init {
        ContractSyntax.opaqueId("source.id", id)
        ContractSyntax.text("source.title", title, max = MAX_TITLE_LENGTH)
        ContractSyntax.text("source.publisher", publisher, max = MAX_PUBLISHER_LENGTH)
        url?.let { ContractSyntax.httpUrl("source.url", it) }
        publishedAt?.let { ContractSyntax.timestamp("source.published_at", it) }
    }

    companion object {
        const val MAX_TITLE_LENGTH = 500
        const val MAX_PUBLISHER_LENGTH = 200
    }
}

/**
 * One retrieved source considered for one claim. Carries identity, kind, how much was read,
 * retrieval relevance and retraction status; the support relation lives in the assessment.
 */
@Serializable
internal data class Evidence(
    val id: String,
    @SerialName("claim_id")
    val claimId: String,
    val source: EvidenceSource,
    @SerialName("source_type")
    val sourceType: SourceType,
    @SerialName("inspection_level")
    val inspectionLevel: SourceInspectionLevel,
    @SerialName("retrieval_relevance")
    val retrievalRelevance: RetrievalRelevance,
    @SerialName("retraction_status")
    val retractionStatus: RetractionStatus,
    val excerpt: String?,
    @SerialName("retrieved_at")
    val retrievedAt: String
) {
    init {
        ContractSyntax.opaqueId("evidence.id", id)
        ContractSyntax.opaqueId("evidence.claim_id", claimId)
        excerpt?.let { ContractSyntax.text("evidence.excerpt", it, max = MAX_EXCERPT_LENGTH) }
        ContractSyntax.timestamp("evidence.retrieved_at", retrievedAt)
    }

    companion object {
        const val MAX_EXCERPT_LENGTH = 1000
    }
}

/** `assessment.schema.json#/$defs/evidence_relation`: one evidence item's relation to a claim. */
@Serializable
internal data class EvidenceRelation(
    @SerialName("evidence_id")
    val evidenceId: String,
    val relation: Relation,
    val note: String?
) {
    init {
        ContractSyntax.opaqueId("relation.evidence_id", evidenceId)
        note?.let { ContractSyntax.text("relation.note", it, max = Assessment.MAX_NOTE_LENGTH) }
    }
}

/**
 * The pipeline's reading of one claim against its evidence in one report version. An
 * assessment without relations can only be `insufficient_evidence` (or a value this version
 * does not define); `insufficient` describes the evidence, never a processing failure.
 */
@Serializable
internal data class Assessment(
    val id: String,
    @SerialName("claim_id")
    val claimId: String,
    val version: Int,
    val relations: List<EvidenceRelation>,
    val overall: OverallAssessment,
    val provisional: Boolean,
    val summary: String
) {
    init {
        ContractSyntax.opaqueId("assessment.id", id)
        ContractSyntax.opaqueId("assessment.claim_id", claimId)
        ContractSyntax.text("assessment.summary", summary, max = MAX_SUMMARY_LENGTH)
        require(relations.isNotEmpty() || overall.isInsufficientOrUnknown()) {
            "an assessment without relations can only be insufficient_evidence"
        }
    }

    private fun OverallAssessment.isInsufficientOrUnknown() =
        this == OverallAssessment.INSUFFICIENT_EVIDENCE || this == OverallAssessment.UNKNOWN

    companion object {
        const val MAX_SUMMARY_LENGTH = 600
        const val MAX_NOTE_LENGTH = 600
    }
}

/**
 * One immutable published report version. Evidence and assessments refer to claims of the
 * same version, relations refer to evidence of the same version, each claim is assessed at
 * most once and every assessment carries this version. Empty [claims] means no assessable
 * factual claim was found, not that the video is accurate.
 */
@Serializable
internal data class ReportVersion(
    val id: String,
    @SerialName("investigation_id")
    val investigationId: String,
    val version: Int,
    @SerialName("created_at")
    val createdAt: String,
    val provisional: Boolean,
    @SerialName("change_summary")
    val changeSummary: String,
    val supersedes: String?,
    val claims: List<Claim>,
    val evidence: List<Evidence>,
    val assessments: List<Assessment>
) {
    /** True when every claim of this version has an assessment; vacuously true with no claims. */
    val assessesEveryClaim: Boolean
        get() = assessments.size == claims.size

    fun assessmentFor(claimId: String): Assessment? =
        assessments.firstOrNull { it.claimId == claimId }

    fun evidenceFor(claimId: String): List<Evidence> = evidence.filter { it.claimId == claimId }

    init {
        ContractSyntax.opaqueId("report.id", id)
        ContractSyntax.uuid("report.investigation_id", investigationId)
        require(version >= 1) { "report.version starts at 1" }
        ContractSyntax.timestamp("report.created_at", createdAt)
        ContractSyntax.text("report.change_summary", changeSummary, max = MAX_SUMMARY_LENGTH)
        supersedes?.let { ContractSyntax.opaqueId("report.supersedes", it) }
        requireConsistentReferences()
    }

    private fun requireConsistentReferences() {
        val claimIds = claims.map { it.id }.toSet()
        val evidenceIds = evidence.map { it.id }.toSet()
        require(claimIds.size == claims.size) { "report.claims contains a duplicate claim id" }
        require(evidenceIds.size == evidence.size) { "report.evidence contains a duplicate id" }
        for (item in evidence) {
            require(item.claimId in claimIds) { "evidence ${item.id} refers to an unknown claim" }
        }
        val assessed = HashSet<String>()
        for (assessment in assessments) {
            require(assessment.claimId in claimIds) {
                "assessment ${assessment.id} refers to an unknown claim"
            }
            require(assessed.add(assessment.claimId)) {
                "claim ${assessment.claimId} is assessed twice in one report version"
            }
            require(assessment.version == version) {
                "assessment ${assessment.id} must carry report.version $version"
            }
            for (relation in assessment.relations) {
                require(relation.evidenceId in evidenceIds) {
                    "assessment ${assessment.id} refers to unknown evidence"
                }
            }
        }
    }

    companion object {
        const val MAX_SUMMARY_LENGTH = 600
    }
}
