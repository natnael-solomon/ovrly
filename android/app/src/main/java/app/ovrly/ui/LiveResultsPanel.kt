package app.ovrly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.ovrly.overlay.LiveClaim
import app.ovrly.overlay.LiveClaimState
import app.ovrly.overlay.LiveCoverage
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveSessionPhase
import kotlin.math.roundToInt

/**
 * Compact live results: session state, the captured-segment label, update notices and the
 * claims in spoken order, or one claim's detail. [showStop] adds a Stop control for hosts
 * without the recording pill; Stop only opens the choice, it never stops capture directly.
 */
@Composable
internal fun LiveResultsPanel(
    model: LiveOverlayModel,
    actions: LivePanelActions,
    modifier: Modifier = Modifier,
    layout: LiveOverlayLayout = LiveOverlayLayout(),
    showStop: Boolean = false
) {
    val p = LocalOvrlyPalette.current
    val results = model.results
    val panel = model.panel
    val detail = panel.detailClaimId?.let { id -> results.claims.firstOrNull { it.id == id } }
    CompositionLocalProvider(LocalContentColor provides p.ink) {
        Column(
            modifier
                .widthIn(max = 360.dp)
                .heightIn(max = layout.maxHeight)
                .mockGlass(layout.higherOpacity)
                .padding(horizontal = 14.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            LiveHeader(results, model.sourceLabel, showStop, actions)
            // Everything below the header scrolls as one region, so large text never hides
            // the list behind the coverage line and notice. The host cap bounds it; without
            // one (previews) it falls back to a fixed height.
            Column(
                Modifier
                    .weight(1f, fill = false)
                    .heightIn(max = scrollCap(layout.maxHeight))
                    .onGloballyPositioned { actions.onListTop(it.positionInRoot().y.roundToInt()) }
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                results.coverage?.let { CapturedSegment(it) }
                panel.notices.lastOrNull()?.let { id ->
                    results.claims.firstOrNull { it.id == id }?.let { UpdateNotice(it, actions) }
                }
                HorizontalDivider(color = p.rule)
                if (detail != null) {
                    ClaimDetail(detail, results.phase, actions.onCloseClaim)
                } else {
                    ClaimList(results, actions.onOpenClaim)
                }
            }
        }
    }
}

@Composable
private fun LiveHeader(
    results: LiveResults,
    sourceLabel: String?,
    showStop: Boolean,
    actions: LivePanelActions,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(
                "Live results",
                modifier = Modifier.semantics { heading() },
                style = MaterialTheme.typography.titleSmall
            )
            Text(
                liveStatusLabel(results),
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                style = MaterialTheme.typography.labelMedium,
                color = p.muted
            )
            sourceLabel?.let {
                Text(it.uppercase(), style = MaterialTheme.typography.labelMedium, color = p.error)
            }
        }
        if (showStop) {
            PanelIconButton(Glyph.Stop, "Stop capture", actions.onRequestStop)
        }
        PanelIconButton(Glyph.Close, "Hide live results. Capture continues", actions.onHide)
    }
}

@Composable
private fun CapturedSegment(coverage: LiveCoverage, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    Column(modifier.semantics(mergeDescendants = true) {}) {
        Text(CAPTURED_SEGMENT_LABEL, style = MaterialTheme.typography.labelLarge)
        Text(
            capturedCoverageLabel(coverage),
            style = MaterialTheme.typography.bodySmall,
            color = p.muted
        )
    }
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
            .clip(RoundedCornerShape(16.dp))
            .background(p.accent.copy(alpha = if (p.dark) 0.22f else 0.42f))
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
                .padding(horizontal = 12.dp, vertical = 8.dp)
        ) {
            Text(UPDATE_NOTICE_LABEL, style = MaterialTheme.typography.labelLarge)
            Text(
                claim.text ?: PENDING_CLAIM_TEXT,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 2,
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
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
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
            .padding(horizontal = 6.dp, vertical = 8.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text(
            claim.startMs?.let(::clockLabel) ?: "--:--",
            modifier = Modifier.widthIn(min = 44.dp).padding(end = 4.dp),
            softWrap = false,
            style = MaterialTheme.typography.labelMedium,
            color = p.muted
        )
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            StateChip(claim.state, state)
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
private fun PanelIconButton(
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

private val MaxListHeight = 260.dp

private fun scrollCap(maxHeight: Dp): Dp =
    if (maxHeight == Dp.Unspecified) MaxListHeight else Dp.Unspecified
