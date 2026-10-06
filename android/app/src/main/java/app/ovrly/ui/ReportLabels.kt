package app.ovrly.ui

import app.ovrly.contract.Assessment
import app.ovrly.contract.Claim
import app.ovrly.contract.CorrectionAttribution
import app.ovrly.contract.Coverage
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Evidence
import app.ovrly.contract.Interval
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.Modality
import app.ovrly.contract.OverallAssessment
import app.ovrly.contract.Relation
import app.ovrly.contract.RetractionStatus
import app.ovrly.contract.RetrievalRelevance
import app.ovrly.contract.SourceInspectionLevel
import app.ovrly.contract.SourceType
import app.ovrly.contract.Stage
import app.ovrly.contract.Timebase
import app.ovrly.data.CheckStatus
import java.net.URI
import java.time.OffsetDateTime
import java.util.Locale
import java.util.concurrent.TimeUnit

/*
 * Plain-language labels for contract values on the Inbox and Report screens (#34). Every
 * enum has an UNKNOWN branch that reads neutrally ("not recognised by this app version") and
 * never as a verdict, a success or a finding.
 */

/** Visual tone of a label. [NEUTRAL] is used for anything this version does not recognise. */
internal enum class Tone { NEUTRAL, SUPPORT, CHALLENGE, QUALIFY, MIXED, INSUFFICIENT, WARNING }

internal data class Label(val text: String, val tone: Tone = Tone.NEUTRAL)

internal const val NOT_RECOGNISED = "not recognised by this app version"

internal val Stage.label: String
    get() = when (this) {
        Stage.INTAKE -> "Receiving the video"
        Stage.MEDIA_VALIDATION -> "Checking the media"
        Stage.ASR -> "Transcribing speech"
        Stage.DEVICE_TEXT -> "Reading on-screen text"
        Stage.CLAIM_EXTRACTION -> "Finding claims"
        Stage.RETRIEVAL -> "Finding sources"
        Stage.ASSESSMENT -> "Weighing evidence"
        Stage.RECONCILIATION -> "Reconciling results"
        Stage.PUBLICATION -> "Publishing results"
        Stage.UNKNOWN -> "Stage $NOT_RECOGNISED"
    }

internal val Modality.label: String
    get() = when (this) {
        Modality.SPEECH -> "Spoken"
        Modality.TEXT -> "On-screen text"
        Modality.BOTH -> "Spoken and on screen"
        Modality.UNKNOWN -> "Where it appeared is $NOT_RECOGNISED"
    }

internal val OverallAssessment.label: Label
    get() = when (this) {
        OverallAssessment.SUPPORTED -> Label("Sources support this claim", Tone.SUPPORT)

        OverallAssessment.CHALLENGED -> Label("Sources challenge this claim", Tone.CHALLENGE)

        OverallAssessment.QUALIFIED -> Label("Sources qualify this claim", Tone.QUALIFY)

        OverallAssessment.MIXED -> Label("Sources disagree", Tone.MIXED)

        OverallAssessment.INSUFFICIENT_EVIDENCE ->
            Label("Not enough sound evidence", Tone.INSUFFICIENT)

        OverallAssessment.UNKNOWN -> Label("Assessment $NOT_RECOGNISED")
    }

internal val Relation.label: Label
    get() = when (this) {
        Relation.SUPPORT -> Label("Supports the claim", Tone.SUPPORT)
        Relation.CHALLENGE -> Label("Contradicts the claim", Tone.CHALLENGE)
        Relation.QUALIFY -> Label("Qualifies the claim", Tone.QUALIFY)
        Relation.MIXED -> Label("Mixed", Tone.MIXED)
        Relation.INSUFFICIENT -> Label("Not enough to decide", Tone.INSUFFICIENT)
        Relation.UNKNOWN -> Label("Relation $NOT_RECOGNISED")
    }

/** Contradicting evidence first, so it is never below the fold of supporting sources. */
internal val Relation?.order: Int
    get() = RELATION_ORDER.indexOf(this).takeIf { it >= 0 } ?: RELATION_ORDER.size

private val RELATION_ORDER = listOf(
    Relation.CHALLENGE,
    Relation.MIXED,
    Relation.QUALIFY,
    Relation.SUPPORT,
    Relation.INSUFFICIENT
)

internal val SourceInspectionLevel.label: String
    get() = when (this) {
        SourceInspectionLevel.FULL_TEXT -> "Full text read"
        SourceInspectionLevel.ABSTRACT_ONLY -> "Abstract only"
        SourceInspectionLevel.METADATA_ONLY -> "Title and metadata only"
        SourceInspectionLevel.UNDETERMINED -> "How much was read was not recorded"
        SourceInspectionLevel.UNKNOWN -> "Access level $NOT_RECOGNISED"
    }

internal val SourceType.label: String
    get() = when (this) {
        SourceType.PEER_REVIEWED -> "Peer-reviewed study"
        SourceType.PREPRINT -> "Preprint, not peer reviewed"
        SourceType.GOVERNMENT -> "Government source"
        SourceType.NEWS -> "News"
        SourceType.REFERENCE_WORK -> "Reference work"
        SourceType.PRIMARY_DOCUMENT -> "Primary document"
        SourceType.ORGANIZATION -> "Organization"
        SourceType.OTHER -> "Other source"
        SourceType.UNKNOWN -> "Source type $NOT_RECOGNISED"
    }

/** A warning about the source itself, or null when nothing needs saying. */
internal val RetractionStatus.warning: Label?
    get() = when (this) {
        RetractionStatus.NONE -> null

        RetractionStatus.CORRECTED -> Label("Corrected by its publisher", Tone.WARNING)

        RetractionStatus.RETRACTED ->
            Label("Retracted: do not rely on this source", Tone.WARNING)

        RetractionStatus.WITHDRAWN ->
            Label("Withdrawn: do not rely on this source", Tone.WARNING)

        RetractionStatus.UNDETERMINED -> Label("Retraction status could not be checked")

        RetractionStatus.UNKNOWN -> Label("Retraction status $NOT_RECOGNISED")
    }

internal val RetrievalRelevance.label: String
    get() = when (this) {
        RetrievalRelevance.HIGH -> "High relevance"
        RetrievalRelevance.MEDIUM -> "Medium relevance"
        RetrievalRelevance.LOW -> "Low relevance"
        RetrievalRelevance.UNKNOWN -> "Relevance $NOT_RECOGNISED"
    }

/** Who changed a claim's meaning, as shown next to it. */
internal val CorrectionAttribution.label: String
    get() = when (this) {
        CorrectionAttribution.USER -> "Corrected by you"
        CorrectionAttribution.PIPELINE -> "Corrected during checking"
        CorrectionAttribution.UNKNOWN -> "Corrected; who corrected it is $NOT_RECOGNISED"
    }

/** `m:ss` for a millisecond offset. */
internal fun clock(ms: Long): String {
    val seconds = ms / MILLIS_PER_SECOND
    return String.format(
        Locale.ROOT,
        "%d:%02d",
        seconds / SECONDS_PER_MINUTE,
        seconds % SECONDS_PER_MINUTE
    )
}

/**
 * Where a claim was observed. Capture offsets stay on the capture timeline: they are never
 * presented as a time in the original video, which ovrly does not know.
 */
internal fun Interval.label(): String {
    val range = "${clock(startMs)} to ${clock(endMs)}"
    return when (timebase) {
        Timebase.MEDIA -> "$range in the video"
        Timebase.CAPTURE -> "$range after capture started (not a time in the original video)"
        Timebase.UNKNOWN -> "$range, timeline $NOT_RECOGNISED"
    }
}

internal fun Coverage.label(): Label = when (status) {
    CoverageStatus.COMPLETE -> Label("All of the media was checked")
    CoverageStatus.PARTIAL -> Label("Part of the media has been checked so far", Tone.WARNING)
    CoverageStatus.NOT_STARTED -> Label("Checking has not reached the media yet")
    CoverageStatus.UNKNOWN -> Label("Coverage $NOT_RECOGNISED")
}

/** "1:00 of 3:05 checked", "1:00 checked", or null when nothing was measured. */
internal fun Coverage.amount(): String? {
    val covered = coveredMs ?: return null
    val total = totalMs
    return if (total != null) {
        "${clock(covered)} of ${clock(total)} checked"
    } else {
        "${clock(covered)} checked"
    }
}

/** One evidence item as the card shows it. */
internal data class EvidenceView(
    val id: String,
    val title: String,
    val publisher: String,
    val url: String?,
    val sourceType: String,
    val access: String,
    val relevance: String,
    val retraction: Label?,
    val relation: Label,
    /** One-line rationale for the relation, from the assessment. */
    val rationale: String?,
    /** The passage the pipeline read, when one was quoted. */
    val passage: String?
)

/** A claim with its assessment and evidence in one report version. */
internal data class ClaimView(
    val id: String,
    val proposition: String,
    val originalText: String,
    val interval: String,
    val modality: String,
    val assessment: Label,
    val summary: String?,
    val correction: String?,
    val supersededProposition: String?,
    val evidence: List<EvidenceView>
) {
    val contradicting: Int get() = evidence.count { it.relation.tone == Tone.CHALLENGE }
}

internal fun claimView(claim: Claim, assessment: Assessment?, evidence: List<Evidence>): ClaimView {
    val relations = assessment?.relations.orEmpty().associateBy { it.evidenceId }
    val items = evidence.sortedBy { relations[it.id]?.relation.order }.map { item ->
        val relation = relations[item.id]
        EvidenceView(
            id = item.id,
            title = item.source.title,
            publisher = item.source.publisher,
            url = item.source.url,
            sourceType = item.sourceType.label,
            access = item.inspectionLevel.label,
            relevance = item.retrievalRelevance.label,
            retraction = item.retractionStatus.warning,
            relation = relation?.relation?.label ?: Label("Not yet related to the claim"),
            rationale = relation?.note,
            passage = item.excerpt
        )
    }
    val assessed = assessment?.overall?.label ?: when {
        claim.correction != null -> Label("Being rechecked after the correction")
        else -> Label("Not assessed yet")
    }
    return ClaimView(
        id = claim.id,
        proposition = claim.proposition,
        originalText = claim.originalText,
        interval = claim.interval.label(),
        modality = claim.modality.label,
        assessment = if (assessment?.provisional == true) {
            assessed.copy(text = "${assessed.text} (provisional)")
        } else {
            assessed
        },
        summary = assessment?.summary,
        correction = claim.correction?.attributedTo?.label,
        supersededProposition = claim.correction?.supersededProposition,
        evidence = items
    )
}

private const val MILLIS_PER_SECOND = 1000L
private const val SECONDS_PER_MINUTE = 60L

internal val CheckStatus.tone: Tone
    get() = if (this == CheckStatus.FAILED) Tone.WARNING else Tone.NEUTRAL

internal fun sourceTitle(source: InvestigationSource?, kind: String, url: String?): String =
    when (source) {
        is InvestigationSource.Url -> "Link from ${host(source.url)}"

        is InvestigationSource.Upload -> "Shared video file"

        is InvestigationSource.Capture -> "Captured clip"

        is InvestigationSource.Unknown -> "Check from a source $NOT_RECOGNISED"

        null -> when (kind) {
            "url" -> url?.let { "Link from ${host(it)}" } ?: "Shared link"
            "upload" -> "Shared video file"
            "capture" -> "Captured clip"
            else -> "Check from a source $NOT_RECOGNISED"
        }
    }

private fun host(url: String): String =
    runCatching { URI(url).host }.getOrNull()?.removePrefix("www.") ?: "a shared link"

/** Relative age such as "just now", "4 min ago", "3 h ago" or "2 days ago". */
internal fun age(then: Long, now: Long): String {
    val minutes = TimeUnit.MILLISECONDS.toMinutes((now - then).coerceAtLeast(0))
    val hours = TimeUnit.MINUTES.toHours(minutes)
    val days = TimeUnit.HOURS.toDays(hours)
    return when {
        minutes < 1 -> "just now"
        hours < 1 -> "$minutes min ago"
        days < 1 -> "$hours h ago"
        days == 1L -> "1 day ago"
        else -> "$days days ago"
    }
}

internal fun epochMillis(timestamp: String): Long? =
    runCatching { OffsetDateTime.parse(timestamp).toInstant().toEpochMilli() }.getOrNull()
