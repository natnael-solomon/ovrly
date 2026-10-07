package app.ovrly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.ovrly.overlay.LiveClaim
import app.ovrly.overlay.LiveClaimState
import app.ovrly.overlay.LiveCoverage
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveSessionPhase

/** Size and look of the expanded live panel, set by the host from the demo panel geometry. */
internal data class LivePanelFrame(
    val width: Dp = 340.dp,
    val maxHeight: Dp = 360.dp,
    val higherOpacity: Boolean = false,
    /** False when the phone's animations are off: nothing pulses. */
    val animate: Boolean = true,
    /** Where the pill sits inside the open panel's window, so it stays where it was. */
    val pillOffset: Dp = 0.dp
)

/**
 * The claims panel shown under the pill, in the demo panel's frame and style
 * ([OverlayPanelScaffold]): it grows with its content up to [LivePanelFrame.maxHeight] and
 * scrolls inside. The pill above it carries the mark, timer, claim count and Stop (Dismiss
 * after Stop), so the panel's top row is only the session state and the fixture label. The
 * Stop choice opens in the body.
 */
@Composable
internal fun LiveExpandedPanel(
    model: LiveOverlayModel,
    actions: LivePanelActions,
    modifier: Modifier = Modifier,
    frame: LivePanelFrame = LivePanelFrame()
) {
    OverlayPanelScaffold(
        header = { LiveHeader(model) },
        footer = { OverlayPanelFooter("Drag the pill to move") },
        modifier = modifier,
        spec = OverlayPanelSpec(
            frame.width,
            frame.maxHeight,
            fixedHeight = false,
            higherOpacity = frame.higherOpacity
        )
    ) {
        if (model.panel.stopPrompt) {
            StopChoiceContent(actions.onStopChoice, actions.onCancelStop)
        } else {
            LiveResultsBody(model, actions)
        }
    }
}

/** The running capture, for the header and the pill. */
internal data class ExaminingState(val seconds: Int, val canStop: Boolean = true)

/** The panel's top row: the session state and, for fixtures, their label. */
@Composable
private fun RowScope.LiveHeader(model: LiveOverlayModel) {
    val p = LocalOvrlyPalette.current
    Column(Modifier.weight(1f)) {
        Text(
            liveStatusLabel(model.results),
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            style = MaterialTheme.typography.labelSmall,
            color = p.muted,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis
        )
        model.sourceLabel?.let {
            // Two lines, so "FIXTURE / NOT LIVE" keeps its disclosure at large font sizes.
            Text(
                it.uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = p.error,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
        }
    }
}

/** Coverage, the newest update notice, then the claims in spoken order or one claim's detail. */
@Composable
internal fun ColumnScope.LiveResultsBody(model: LiveOverlayModel, actions: LivePanelActions) {
    val p = LocalOvrlyPalette.current
    val results = model.results
    val detail = model.panel.detailClaimId?.let { id -> results.claims.firstOrNull { it.id == id } }
    results.coverage?.let { CapturedSegment(it) }
    model.panel.notices.lastOrNull()?.let { id ->
        results.claims.firstOrNull { it.id == id }?.let { UpdateNotice(it, actions) }
    }
    HorizontalDivider(color = p.rule)
    if (detail != null) {
        ClaimDetail(detail, results.phase, actions.onCloseClaim)
    } else {
        ClaimList(results, actions.onOpenClaim)
    }
}

@Composable
private fun CapturedSegment(coverage: LiveCoverage, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    // One line: what was analyzed and how much of the video that is.
    Text(
        buildAnnotatedString {
            withStyle(SpanStyle(color = p.ink, fontWeight = FontWeight.Medium)) {
                append(CAPTURED_SEGMENT_LABEL)
            }
            append(" · ")
            append(capturedCoverageLabel(coverage))
        },
        modifier = modifier,
        style = MaterialTheme.typography.labelMedium,
        color = p.muted
    )
}

@Composable
private fun UpdateNotice(
    claim: LiveClaim,
    actions: LivePanelActions,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    Row(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(p.accent.copy(alpha = if (p.dark) 0.18f else 0.36f))
            .semantics { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(
            Modifier
                .weight(1f)
                .sizeIn(minHeight = 48.dp)
                .clickable(
                    role = Role.Button,
                    onClickLabel = "Open claim detail",
                    onClick = { actions.onOpenClaim(claim.id) }
                )
                .padding(horizontal = 12.dp, vertical = 6.dp)
        ) {
            Text(UPDATE_NOTICE_LABEL, style = MaterialTheme.typography.labelMedium)
            Text(
                claim.text ?: PENDING_CLAIM_TEXT,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        PanelIconButton(
            glyph = Glyph.Close,
            label = "Dismiss update notice",
            onClick = { actions.onDismissNotice(claim.id) }
        )
    }
}

@Composable
private fun ClaimList(
    results: LiveResults,
    onOpen: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        if (results.claims.isEmpty()) {
            Text(
                emptyClaimsLabel(results.extraction, results.phase),
                modifier = Modifier.padding(vertical = 8.dp),
                style = MaterialTheme.typography.bodyMedium
            )
        }
        results.claims.forEach { claim ->
            ClaimRow(claim, results.phase, onOpen = { onOpen(claim.id) })
        }
    }
}

@Composable
private fun ClaimRow(
    claim: LiveClaim,
    phase: LiveSessionPhase,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    val state = liveStateLabel(claim, phase)
    Row(
        modifier
            .fillMaxWidth()
            .sizeIn(minHeight = 48.dp)
            .clip(RoundedCornerShape(14.dp))
            .clickable(role = Role.Button, onClickLabel = "Open claim detail", onClick = onOpen)
            .clearAndSetSemantics {
                contentDescription = claimDescription(claim, phase)
                stateDescription = state
            }
            .padding(horizontal = 4.dp, vertical = 6.dp),
        verticalAlignment = Alignment.Top
    ) {
        // The time sits on the state line, so the claim text gets the panel's full width.
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    claim.startMs?.let(::clockLabel) ?: "--:--",
                    softWrap = false,
                    style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum"),
                    color = p.muted
                )
                Spacer(Modifier.width(8.dp))
                StateChip(claim.state, state)
            }
            Text(
                claim.text ?: PENDING_CLAIM_TEXT,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis
            )
            claim.assessment?.let {
                Text(
                    assessmentLabel(it),
                    style = MaterialTheme.typography.labelMedium,
                    color = p.muted
                )
            }
        }
    }
}

@Composable
private fun StateChip(state: LiveClaimState, label: String, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    val strong = state == LiveClaimState.UPDATED || state == LiveClaimState.ASSESSED
    Text(
        label,
        modifier = modifier
            .clip(CircleShape)
            .background(if (strong) p.accent.copy(alpha = 0.5f) else p.rule.copy(alpha = 0.6f))
            .padding(horizontal = 8.dp, vertical = 2.dp),
        style = MaterialTheme.typography.labelSmall,
        color = if (strong && !p.dark) p.accentInk else p.ink
    )
}

@Composable
private fun ClaimDetail(
    claim: LiveClaim,
    phase: LiveSessionPhase,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            listOfNotNull(claim.startMs?.let(::clockLabel), liveStateLabel(claim, phase))
                .joinToString(" / "),
            style = MaterialTheme.typography.labelMedium,
            color = p.muted
        )
        Text(
            claim.text ?: PENDING_CLAIM_TEXT,
            modifier = Modifier.semantics { heading() },
            style = MaterialTheme.typography.titleMedium
        )
        val change = claim.change
        val assessment = claim.assessment
        if (change != null) {
            Text(UPDATE_NOTICE_LABEL, style = MaterialTheme.typography.labelLarge)
            Text("Before: ${assessmentLabel(change.before)}. ${change.before.summary}")
            Text("Now: ${assessmentLabel(change.after)}. ${change.after.summary}")
            if (change.reason.isNotBlank()) {
                Text(
                    "Why it changed: ${change.reason}",
                    style = MaterialTheme.typography.bodySmall,
                    color = p.muted
                )
            }
        } else if (assessment != null) {
            Text("${assessmentLabel(assessment)}. ${assessment.summary}")
        }
        if (!claim.complete) {
            Text(
                "Not a final assessment.",
                style = MaterialTheme.typography.bodySmall,
                color = p.muted
            )
        }
        TextButton(onClick = onBack, modifier = Modifier.sizeIn(minHeight = 48.dp)) {
            Text("Back to live results")
        }
    }
}

@Composable
internal fun PanelIconButton(
    glyph: Glyph,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier
            .size(48.dp)
            .clip(CircleShape)
            .clickable(role = Role.Button, onClickLabel = label, onClick = onClick)
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center
    ) {
        OverlayGlyph(glyph, Modifier.size(18.dp))
    }
}
