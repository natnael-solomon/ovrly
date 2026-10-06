package app.ovrly.ui

import android.content.Intent
import app.ovrly.contract.SourceInspectionLevel
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/*
 * Export of the loaded report (AN-11, #39; decision 0003). Built on the device from the
 * report it already holds and handed to the Android share sheet as plain text. It carries
 * the claim summary, source links, limitations, the provisional label, the report version
 * and the retrieval date. It never carries media, screen images, the transcript or the
 * claims' original wording, which is quoted from the transcript.
 */

/** The text handed to the share sheet. */
internal data class ReportExport(val subject: String, val text: String)

/** The export of [report], or null when it has no published version to export. */
internal fun reportExport(
    report: OpenReport,
    zone: ZoneId = ZoneId.systemDefault()
): ReportExport? {
    val view = report.view
    val version = view.version ?: return null
    val subject = "ovrly report: ${view.title} (version $version)"
    val text = buildString {
        appendLine(subject)
        appendLine(versionLine(view, version, zone))
        report.retrievedAt?.let {
            appendLine("Retrieved by this device: ${date(Instant.ofEpochMilli(it), zone)}")
        }
        status(view).forEach(::appendLine)
        appendLine()
        claims(view)
        appendLine()
        appendLine("Limitations")
        limitations(view).forEach { appendLine("- $it") }
    }
    return ReportExport(subject, text.trimEnd())
}

/** A share-sheet chooser for this export: plain text only, no attachment or stream. */
internal fun ReportExport.chooser(): Intent = Intent.createChooser(
    Intent(Intent.ACTION_SEND)
        .setType("text/plain")
        .putExtra(Intent.EXTRA_SUBJECT, subject)
        .putExtra(Intent.EXTRA_TEXT, text),
    "Share report"
)

private fun versionLine(view: ReportView, version: Int, zone: ZoneId): String {
    val published = view.publishedAt
        ?.let { runCatching { OffsetDateTime.parse(it).toInstant() }.getOrNull() }
        ?.let { ", published ${date(it, zone)}" }
        .orEmpty()
    val earlier = when (version) {
        view.latestVersion -> ""
        else -> " (an earlier version, kept unchanged)"
    }
    return "Version $version of ${view.latestVersion}$earlier$published"
}

private fun status(view: ReportView): List<String> = buildList {
    val coverage = view.coverage
    if (coverage?.provisional == true) add("PROVISIONAL: results may change.")
    if (coverage?.fixture == true) add("DEVELOPMENT FIXTURE: not a check of this media.")
    if (coverage?.stale == true) {
        add("May be out of date: this version could not be confirmed with ovrly recently.")
    }
    add("Check: " + listOfNotNull(view.work.status, view.work.stage).joinToString(" / "))
    view.work.message?.let(::add)
    coverage?.let {
        add("Coverage: " + listOfNotNull(it.coverage.text, it.amount).joinToString(", "))
    }
    view.changeSummary?.let { add("Changes in this version: $it") }
}

private fun StringBuilder.claims(view: ReportView) {
    appendLine("Claims (${view.claims.size})")
    view.empty?.let(::appendLine)
    view.claims.forEachIndexed { index, claim ->
        appendLine()
        appendLine("${index + 1}. ${claim.proposition}")
        appendLine("   Assessment: ${claim.assessment.text}")
        claim.summary?.let { appendLine("   $it") }
        claim.correction?.let { appendLine("   $it") }
        appendLine("   When: ${claim.interval}")
        appendLine("   Where it appeared: ${claim.modality}")
        if (claim.evidence.isEmpty()) appendLine("   Sources: none yet")
        claim.evidence.forEach { source(it) }
    }
}

private fun StringBuilder.source(evidence: EvidenceView) {
    appendLine("   - ${evidence.relation.text}: ${evidence.title} (${evidence.publisher})")
    appendLine("     ${evidence.sourceType}; ${evidence.access}")
    evidence.retraction?.let { appendLine("     ${it.text}") }
    evidence.url?.let { appendLine("     $it") }
}

private fun limitations(view: ReportView): List<String> = buildList {
    add("ovrly gives no verdict on the whole video. Each claim is assessed on its own.")
    add(
        "Claims are shown as ovrly understood them, not in their original wording. " +
            "This export holds no video, audio, screen images or transcript."
    )
    if (view.coverage?.coverage?.tone == Tone.WARNING || view.coverage?.provisional == true) {
        add("Only part of the media was checked, or checking was still running.")
    }
    if (view.captured) {
        add(
            "Times after capture started are on the capture timeline, not times in the " +
                "original video. A capture covers only what was on screen."
        )
    }
    if (view.claims.any { claim -> claim.evidence.any { it.access != FULL_TEXT } }) {
        add("Some sources were read only in part (abstract or title and metadata).")
    }
    add("Sources can change after retrieval; open the links to read them in full.")
}

private val FULL_TEXT = SourceInspectionLevel.FULL_TEXT.label

private val DATE = DateTimeFormatter.ofPattern("d MMMM yyyy, HH:mm 'UTC'xxx", Locale.ENGLISH)

private fun date(instant: Instant, zone: ZoneId): String = DATE.format(instant.atZone(zone))
