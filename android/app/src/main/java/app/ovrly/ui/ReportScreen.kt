package app.ovrly.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/**
 * Report of a real check (#34): work state, then evidence state (coverage, version), then
 * the claims. Claim detail holds the original wording, the normalized meaning, where and how
 * the claim appeared, what changed between versions, a correction and the evidence. The
 * header's share action hands [reportExport] of the shown version to [sharer] (#39).
 */
@Composable
internal fun CheckReportScreen(
    report: OpenReport,
    onCommand: (CheckCommand) -> Unit,
    modifier: Modifier = Modifier,
    sharer: ReportSharer = rememberReportSharer()
) {
    val view = report.view
    var correcting by rememberSaveable(view.investigationId) { mutableStateOf<String?>(null) }
    var expanding by rememberSaveable(view.investigationId) { mutableStateOf(false) }
    val export = remember(report) { reportExport(report) }
    Box(modifier.fillMaxSize()) {
        ReportList(
            report,
            ReportActions(
                onCommand = onCommand,
                onCorrect = { correcting = it },
                onFullVideo = { expanding = true },
                onShare = export?.let { shown -> { sharer.share(shown) } }
            )
        )
        view.claims.firstOrNull { it.id == correcting }?.let { claim ->
            CorrectionSheet(
                claim = claim,
                onSubmit = {
                    correcting = null
                    onCommand(CheckCommand.Correct(claim.id, it))
                },
                onDismiss = { correcting = null }
            )
        }
        if (expanding) {
            FullVideoSheet(
                candidates = report.candidates,
                onConfirm = {
                    expanding = false
                    onCommand(CheckCommand.Expand(it))
                },
                onDismiss = { expanding = false }
            )
        }
    }
}

/** Hands an export to the share sheet. */
internal fun interface ReportSharer {
    fun share(export: ReportExport)
}

/** Opens the Android share sheet with the export as plain text. */
@Composable
internal fun rememberReportSharer(): ReportSharer {
    val context = LocalContext.current
    return remember(context) { ReportSharer { context.startActivity(it.chooser()) } }
}

/** What the report list can do; [onShare] is null when there is no version to export. */
private class ReportActions(
    val onCommand: (CheckCommand) -> Unit,
    val onCorrect: (String) -> Unit,
    val onFullVideo: () -> Unit,
    val onShare: (() -> Unit)?
)

@Composable
private fun ReportList(report: OpenReport, actions: ReportActions, modifier: Modifier = Modifier) {
    val onCommand = actions.onCommand
    val view = report.view
    var expanded by rememberSaveable(view.investigationId) {
        mutableStateOf(view.claims.firstOrNull()?.id)
    }
    LazyColumn(
        modifier.fillMaxSize(),
        contentPadding = PaddingValues(SCREEN_PADDING),
        verticalArrangement = Arrangement.spacedBy(SCREEN_GAP)
    ) {
        item { ReportHeader(view.title, { onCommand(CheckCommand.Close) }, actions.onShare) }
        item { WorkCard(view.work) }
        report.notice?.let { notice ->
            item { Notice(notice, { onCommand(CheckCommand.DismissNotice) }) }
        }
        view.coverage?.let { coverage -> item { CoverageCard(coverage) } }
        if (view.version != null) {
            item { VersionSection(report, { onCommand(CheckCommand.ShowVersion(it)) }) }
        }
        if (view.captured && view.canCorrect && report.candidates.isNotEmpty()) {
            item { FullVideoCard(report.busy, actions.onFullVideo) }
        }
        view.empty?.let { empty ->
            item { Text(empty, style = MaterialTheme.typography.bodyLarge) }
        }
        items(view.claims, key = { it.id }) { claim ->
            ClaimCard(
                claim = claim,
                detail = ClaimDetail(
                    expanded = expanded == claim.id,
                    changes = report.changes[claim.id].orEmpty(),
                    comparedWith = report.comparedWith,
                    canCorrect = view.canCorrect && !report.busy
                ),
                onToggle = { expanded = if (expanded == claim.id) null else claim.id },
                onCorrect = { actions.onCorrect(claim.id) }
            )
        }
    }
}

@Composable
private fun ReportHeader(
    title: String,
    onBack: () -> Unit,
    onShare: (() -> Unit)?,
    modifier: Modifier = Modifier
) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(SMALL_GAP)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(
                    Icons.AutoMirrored.Outlined.ArrowBack,
                    "Back to Your space",
                    Modifier.size(ICON)
                )
            }
            Text(
                "REPORT",
                Modifier.weight(1f),
                style = MaterialTheme.typography.labelSmall,
                color = LocalOvrlyPalette.current.muted
            )
            if (onShare != null) {
                IconButton(onClick = onShare) {
                    Icon(Icons.Outlined.Share, "Share report", Modifier.size(ICON))
                }
            }
        }
        Text(title, Modifier.semantics { heading() }, style = OvrlyEditorialTypography.title)
    }
}

/** Work state: where the check is. Separate from what it found. */
@Composable
private fun WorkCard(work: WorkBanner, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    Panel(work.tone, modifier) {
        Text("CHECK", style = MaterialTheme.typography.labelSmall, color = p.muted)
        Text(
            listOfNotNull(work.status, work.stage).joinToString(" / "),
            style = MaterialTheme.typography.titleSmall,
            color = if (work.tone == Tone.WARNING) p.error else p.ink
        )
        work.message?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
    }
}

/** Evidence state: how much media the shown version covers and how final it is. */
@Composable
private fun CoverageCard(coverage: CoverageBanner, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    Panel(coverage.coverage.tone, modifier) {
        Text("COVERAGE", style = MaterialTheme.typography.labelSmall, color = p.muted)
        Text(coverage.coverage.text, style = MaterialTheme.typography.titleSmall)
        coverage.amount?.let { Muted(it) }
        if (coverage.provisional) {
            Text("Provisional: results may change.", style = MaterialTheme.typography.bodyMedium)
        }
        if (coverage.stale) {
            Text(
                "May be out of date: this version could not be confirmed with ovrly recently.",
                style = MaterialTheme.typography.bodyMedium,
                color = p.error
            )
        }
        if (coverage.fixture) {
            Text(
                "Development fixture: not a check of this media.",
                style = MaterialTheme.typography.bodyMedium,
                color = p.error
            )
        }
    }
}

@Composable
private fun VersionSection(
    report: OpenReport,
    onVersion: (Int) -> Unit,
    modifier: Modifier = Modifier
) {
    val view = report.view
    Column(modifier, verticalArrangement = Arrangement.spacedBy(SMALL_GAP)) {
        Text(
            "Version ${view.version} of ${view.latestVersion}",
            style = MaterialTheme.typography.titleSmall
        )
        view.changeSummary?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        if (view.version != view.latestVersion) {
            Muted("You are viewing an earlier version. It is kept unchanged.")
        }
        if (report.versions.size > 1) {
            Row(
                Modifier.horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(SMALL_GAP)
            ) {
                report.versions.forEach { choice ->
                    FilterChip(
                        selected = choice.version == view.version,
                        onClick = { onVersion(choice.version) },
                        label = { Text(choice.label) }
                    )
                }
            }
        }
        report.versionsNote?.let { Muted(it) }
    }
}

@Composable
private fun FullVideoCard(busy: Boolean, onStart: () -> Unit, modifier: Modifier = Modifier) {
    Panel(Tone.NEUTRAL, modifier) {
        Text("Shared the full video?", style = MaterialTheme.typography.titleSmall)
        Text(
            "This check covers the part you captured. If you later shared the full video, " +
                "ovrly can check all of it after you confirm it is the same video.",
            style = MaterialTheme.typography.bodyMedium
        )
        OutlinedButton(onStart, enabled = !busy) { Text("Check the full video") }
    }
}

/** A bordered card with a tone bar; the content always states the tone in words. */
@Composable
internal fun Panel(tone: Tone, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val p = LocalOvrlyPalette.current
    Surface(
        modifier.fillMaxWidth(),
        shape = RoundedCornerShape(CARD_RADIUS),
        color = p.surface,
        border = BorderStroke(1.dp, p.rule)
    ) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            ToneBar(tone)
            Column(
                Modifier.weight(1f).padding(CARD_PADDING),
                verticalArrangement = Arrangement.spacedBy(SMALL_GAP)
            ) { content() }
        }
    }
}

internal val SMALL_GAP = 8.dp
private val SCREEN_PADDING = 24.dp
private val SCREEN_GAP = 16.dp
private val ICON = 20.dp
