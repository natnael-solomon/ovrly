package app.ovrly.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal enum class StudyAction { SETUP, STOP_CAPTURE, CANCEL_RESEARCH, DETAILS }

internal fun OverlayVisual.studyAction(): StudyAction? = when (this) {
    OverlayVisual.Idle -> StudyAction.SETUP
    is OverlayVisual.Recording -> StudyAction.STOP_CAPTURE
    OverlayVisual.Checking -> StudyAction.CANCEL_RESEARCH
    is OverlayVisual.Results, OverlayVisual.NoAudio, OverlayVisual.Captured -> StudyAction.DETAILS
    OverlayVisual.NoClaims -> null
}

@Composable
internal fun StudyOverlay(
    state: OverlayVisual,
    design: StudyDesign,
    darkBackdrop: Boolean,
    higherOpacity: Boolean,
    onAction: (StudyAction) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (design == StudyDesign.OPTICAL_RIM) {
        OvrlyTheme(dark = false) {
        GlassOverlay(
            state, higherOpacity,
            onSetup = { onAction(StudyAction.SETUP) },
            onStopCapture = { onAction(StudyAction.STOP_CAPTURE) },
            onCancelResearch = { onAction(StudyAction.CANCEL_RESEARCH) },
            onDetails = { onAction(StudyAction.DETAILS) },
            modifier = modifier,
            referenceMaterial = true,
        )
        }
        return
    }
    val palette = design.palette(darkBackdrop, higherOpacity)
    val action = state.studyAction()
    val surface = Modifier.studySurface(design, palette, circular = state == OverlayVisual.Idle)
    val fixtureModifier = modifier.padding(4.dp).semantics {
        stateDescription = "${design.title}. Design fixture, not live capture or results"
    }
    if (state == OverlayVisual.Idle) {
        Box(
            fixtureModifier.size(52.dp).then(surface).clip(CircleShape)
                .clickable(role = Role.Button, onClickLabel = "Open capture setup") {
                    onAction(StudyAction.SETUP)
                }
                .semantics { contentDescription = "Idle. Open capture setup" },
            contentAlignment = Alignment.Center,
        ) {
            InterruptedRing(Modifier.size(23.dp), palette.ink)
        }
    } else if (design == StudyDesign.SPLIT_PEBBLE &&
        (action == StudyAction.STOP_CAPTURE || action == StudyAction.CANCEL_RESEARCH)
    ) {
        Row(fixtureModifier, verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.weight(1f, fill = false).then(surface)
                    .heightIn(min = 52.dp).padding(horizontal = 12.dp, vertical = 2.dp),
                contentAlignment = Alignment.Center,
            ) {
                StudyReadout(state, palette, onAction)
            }
            Spacer(Modifier.width(6.dp))
            Box(
                Modifier.size(52.dp).studySurface(design, palette, circular = true),
                contentAlignment = Alignment.Center,
            ) {
                StudyControl(state, action, design, palette, onAction)
            }
        }
    } else {
        Row(
            fixtureModifier.then(surface).heightIn(min = 52.dp)
                .padding(start = 12.dp, end = if (action == null) 12.dp else 2.dp, top = 2.dp, bottom = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.weight(1f, fill = false)) { StudyReadout(state, palette, onAction) }
            if (action != null) {
                Spacer(Modifier.width(4.dp))
                StudyControl(state, action, design, palette, onAction)
            }
        }
    }
}

private fun studyText(palette: StudyPalette) = TextStyle(
    color = palette.ink,
    fontFamily = OvrlySans,
    fontSize = 14.sp,
    lineHeight = 20.sp,
    fontWeight = FontWeight.Medium,
    fontFeatureSettings = "tnum",
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StudyReadout(state: OverlayVisual, palette: StudyPalette, onAction: (StudyAction) -> Unit) {
    when (state) {
        is OverlayVisual.Recording -> FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            FlowRow(
                Modifier.padding(vertical = 9.dp).clearAndSetSemantics {
                    contentDescription = "Recording. ${state.seconds / 60} minutes, ${state.seconds % 60} seconds"
                },
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Row(Modifier.heightIn(min = 22.dp), verticalAlignment = Alignment.CenterVertically) {
                    Canvas(Modifier.size(7.dp)) { drawCircle(Color(0xFFF38E77)) }
                    Spacer(Modifier.width(5.dp))
                    Text("REC", style = studyText(palette).copy(fontSize = 10.sp, letterSpacing = 0.5.sp))
                }
                Text(
                    "${state.seconds / 60}:${(state.seconds % 60).toString().padStart(2, '0')}",
                    style = studyText(palette).copy(fontSize = 15.sp, fontWeight = FontWeight.SemiBold),
                    softWrap = false,
                )
            }
            if (state.provisionalClaims > 0) {
                Row(
                    Modifier.sizeIn(minHeight = 48.dp, minWidth = 88.dp)
                        .clip(RoundedCornerShape(24.dp))
                        .clickable(role = Role.Button, onClickLabel = "Open preliminary claims") {
                            onAction(StudyAction.DETAILS)
                        }
                        .semantics {
                            contentDescription = claimLabel(state.provisionalClaims)
                            stateDescription = "Preliminary, not verified"
                        }
                        .padding(horizontal = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    ClaimRing(Modifier.size(17.dp), true, palette.ink, palette.accent)
                    Text(claimLabel(state.provisionalClaims), style = studyText(palette).copy(fontSize = 13.sp))
                }
            }
        }
        OverlayVisual.Checking -> Row(
            Modifier.padding(vertical = 8.dp).clearAndSetSemantics {
                contentDescription = "Checking captured claims. Research in progress"
                progressBarRangeInfo = ProgressBarRangeInfo.Indeterminate
            },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Canvas(Modifier.size(18.dp)) {
                val stroke = 1.7.dp.toPx()
                val inset = stroke / 2f
                val diameter = Size(size.width - stroke, size.height - stroke)
                drawArc(palette.ink.copy(alpha = 0.16f), 0f, 360f, false,
                    Offset(inset, inset), diameter, style = Stroke(stroke))
                drawArc(palette.secondary, -90f, 230f, false,
                    Offset(inset, inset), diameter, style = Stroke(stroke, cap = StrokeCap.Round))
            }
            Text("Checking", style = studyText(palette))
        }
        is OverlayVisual.Results -> StudyStatus(palette, claimLabel(state.claims), "Claims available, not a verdict") {
            ClaimRing(Modifier.size(22.dp), false, palette.ink, palette.accent)
        }
        OverlayVisual.NoClaims -> StudyStatus(palette, "No claims", "No checkable claims were found") {
            OverlayGlyph(Glyph.Neutral, Modifier.size(20.dp), palette.secondary)
        }
        OverlayVisual.NoAudio -> StudyStatus(palette, "No audio", "Playback audio unavailable") {
            OverlayGlyph(Glyph.UnavailableSpeaker, Modifier.size(21.dp), palette.secondary)
        }
        OverlayVisual.Captured -> StudyStatus(palette, "Research offline", "Research is not connected") {
            OverlayGlyph(Glyph.Capture, Modifier.size(21.dp), palette.secondary)
        }
        OverlayVisual.Idle -> Unit
    }
}

@Composable
private fun StudyStatus(
    palette: StudyPalette,
    label: String,
    description: String,
    leading: @Composable () -> Unit,
) {
    Row(
        Modifier.padding(vertical = 8.dp).semantics { contentDescription = description },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        leading()
        Text(label, style = studyText(palette), modifier = Modifier.weight(1f, fill = false))
    }
}

@Composable
private fun StudyControl(
    state: OverlayVisual,
    action: StudyAction,
    design: StudyDesign,
    palette: StudyPalette,
    onAction: (StudyAction) -> Unit,
) {
    val glyph = when (action) {
        StudyAction.STOP_CAPTURE -> Glyph.Stop
        StudyAction.CANCEL_RESEARCH -> Glyph.Close
        else -> Glyph.Chevron
    }
    val label = when (action) {
        StudyAction.STOP_CAPTURE -> "Stop capture"
        StudyAction.CANCEL_RESEARCH -> "Cancel research"
        StudyAction.DETAILS -> if (state == OverlayVisual.NoAudio) "Open audio availability details" else "Open claim details"
        StudyAction.SETUP -> "Open capture setup"
    }
    Box(
        Modifier.size(48.dp).clip(CircleShape)
            .clickable(role = Role.Button, onClickLabel = label) { onAction(action) }
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) {
        val inset = if (design.family == StudyFamily.SCULPTED || design == StudyDesign.FROSTED_VEIL ||
            design == StudyDesign.MILK_GLASS
        ) {
            Modifier.background(palette.ink.copy(alpha = 0.07f),
                RoundedCornerShape(
                    when (design) {
                        StudyDesign.DOMED_GLASS, StudyDesign.PRISM_EDGE -> 7.dp
                        StudyDesign.FROSTED_VEIL, StudyDesign.MILK_GLASS -> 10.dp
                        else -> 18.dp
                    },
                ))
        } else Modifier
        Box(Modifier.size(32.dp).then(inset), contentAlignment = Alignment.Center) {
            OverlayGlyph(glyph, Modifier.size(if (glyph == Glyph.Chevron) 14.dp else 17.dp), palette.ink)
        }
    }
}
