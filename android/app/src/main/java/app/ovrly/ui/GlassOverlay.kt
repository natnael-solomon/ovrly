package app.ovrly.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ovrly.R

sealed interface OverlayVisual {
    data object Idle : OverlayVisual
    data object Checking : OverlayVisual
    data object NoClaims : OverlayVisual
    data object NoAudio : OverlayVisual
    data object Captured : OverlayVisual
    data class Recording(val seconds: Int, val provisionalClaims: Int = 0) : OverlayVisual
    data class Results(val claims: Int) : OverlayVisual
}

private val Graphite: Color @Composable get() = LocalOvrlyPalette.current.ink
private val PaleLime: Color @Composable get() = LocalOvrlyPalette.current.accent
private val Coral: Color @Composable get() = LocalOvrlyPalette.current.error
private val RecordingInk: Color @Composable get() = LocalOvrlyPalette.current.error
private val MutedInk: Color @Composable get() = LocalOvrlyPalette.current.muted
private val Capsule = RoundedCornerShape(percent = 50)
private val OverlayText: TextStyle @Composable get() = TextStyle(
    color = Graphite,
    fontSize = 15.sp,
    lineHeight = 20.sp,
    fontWeight = FontWeight.Medium,
    fontFamily = OvrlySans,
)

/**
 * Wrap-content overlay with a 4 dp shadow allowance and independent touch targets.
 * The host owns capture, research, permission handling, and window dragging.
 */
@Composable
@OptIn(ExperimentalLayoutApi::class)
fun GlassOverlay(
    state: OverlayVisual,
    higherOpacity: Boolean,
    onSetup: () -> Unit,
    onStopCapture: () -> Unit,
    onCancelResearch: () -> Unit,
    onDetails: () -> Unit,
    modifier: Modifier = Modifier,
    referenceMaterial: Boolean = false,
) {
    Box(
        modifier = modifier
            .then(if (referenceMaterial) Modifier.padding(4.dp).opticalGlass(higherOpacity) else Modifier.mockGlass(higherOpacity))
            .defaultMinSize(minHeight = 52.dp),
        contentAlignment = Alignment.Center,
    ) {
        when (state) {
            OverlayVisual.Idle -> Box(
                modifier = Modifier
                    .size(52.dp)
                    .padding(2.dp)
                    .clip(CircleShape)
                    .clickable(
                        role = Role.Button,
                        onClickLabel = "Open capture setup",
                        onClick = onSetup,
                    )
                    .semantics { contentDescription = "Idle. Open capture setup" },
                contentAlignment = Alignment.Center,
            ) {
                InterruptedRing(Modifier.size(24.dp))
            }

            is OverlayVisual.Recording -> Row(
                modifier = Modifier.padding(start = 16.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Measure the fixed stop target before assigning remaining width to the readout.
                FlowRow(
                    modifier = Modifier.weight(1f, fill = false),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.Center,
                ) {
                    RecordingLabel(state.seconds)
                    if (state.provisionalClaims > 0) {
                        ProvisionalClaims(state.provisionalClaims, onDetails)
                    }
                }
                Spacer(Modifier.width(8.dp))
                ControlDivider()
                ControlButton(
                    glyph = Glyph.Stop,
                    label = "Stop capture",
                    onClick = onStopCapture,
                )
            }

            OverlayVisual.Checking -> Row(
                modifier = Modifier.padding(start = 16.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    modifier = Modifier
                        .weight(1f, fill = false)
                        .padding(vertical = 8.dp)
                        .clearAndSetSemantics {
                            contentDescription = "Checking captured claims. Research in progress"
                            progressBarRangeInfo = ProgressBarRangeInfo.Indeterminate
                        },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        color = MutedInk,
                        trackColor = Graphite.copy(alpha = 0.10f),
                        strokeWidth = 2.dp,
                    )
                    Spacer(Modifier.width(12.dp))
                    Text(
                        "Checking",
                        modifier = Modifier.weight(1f, fill = false),
                        style = OverlayText,
                    )
                }
                Spacer(Modifier.width(8.dp))
                ControlDivider()
                ControlButton(
                    glyph = Glyph.Close,
                    label = "Cancel research",
                    onClick = onCancelResearch,
                )
            }

            is OverlayVisual.Results -> DetailsRow(
                label = claimLabel(state.claims.coerceAtLeast(0)),
                description = "${claimLabel(state.claims.coerceAtLeast(0))} available",
                actionLabel = "Open claim results",
                onClick = onDetails,
                leading = { ClaimRing(Modifier.size(24.dp), provisional = false) },
            )

            OverlayVisual.NoClaims -> Row(
                modifier = Modifier
                    .padding(horizontal = 17.dp, vertical = 10.dp)
                    .clearAndSetSemantics {
                        contentDescription = "No checkable claims were found"
                    },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OverlayGlyph(Glyph.Neutral, Modifier.size(20.dp), MutedInk)
                Spacer(Modifier.width(12.dp))
                Text("No claims", style = OverlayText)
            }

            OverlayVisual.NoAudio -> DetailsRow(
                label = "No audio",
                description = "Playback audio unavailable",
                actionLabel = "Open audio availability details",
                onClick = onDetails,
                leading = { OverlayGlyph(Glyph.UnavailableSpeaker, Modifier.size(22.dp), MutedInk) },
            )

            OverlayVisual.Captured -> DetailsRow(
                label = "Research offline",
                description = "Research offline. No research results are available",
                actionLabel = "Open capture details",
                onClick = onDetails,
                leading = { OverlayGlyph(Glyph.Capture, Modifier.size(22.dp), MutedInk) },
            )
        }
    }
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
private fun RecordingLabel(seconds: Int) {
    val elapsed = seconds.coerceAtLeast(0)
    val coral = Coral
    FlowRow(
        modifier = Modifier
            .padding(vertical = 8.dp)
            .clearAndSetSemantics {
                contentDescription =
                    "Recording. ${elapsed / 60} minutes, ${elapsed % 60} seconds elapsed"
            },
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(Modifier.heightIn(min = 22.dp), verticalAlignment = Alignment.CenterVertically) {
            Canvas(Modifier.size(12.dp)) {
                drawCircle(coral.copy(alpha = 0.14f))
                drawCircle(coral, radius = 3.5.dp.toPx())
            }
            Spacer(Modifier.width(4.dp))
            Text("REC", style = OverlayText.copy(
                color = RecordingInk, fontSize = 11.sp, letterSpacing = 0.8.sp,
                fontWeight = FontWeight.SemiBold,
            ))
        }
        Text(
            text = "${elapsed / 60}:${(elapsed % 60).toString().padStart(2, '0')}",
            style = OverlayText.copy(
                fontSize = 16.sp,
                lineHeight = 22.sp,
                fontWeight = FontWeight.SemiBold,
                fontFeatureSettings = "tnum",
            ),
            softWrap = false,
        )
    }
}

@Composable
private fun ProvisionalClaims(claims: Int, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .sizeIn(minWidth = 96.dp, minHeight = 48.dp)
            .clip(Capsule)
            .clickable(
                role = Role.Button,
                onClickLabel = "Open preliminary claims",
                onClick = onClick,
            )
            .semantics {
                contentDescription = "${claimLabel(claims)} identified"
                stateDescription = "Preliminary, not verified"
            },
        contentAlignment = Alignment.Center,
    ) {
        Row(
            modifier = Modifier
                .padding(vertical = 6.dp)
                .background(PaleLime.copy(alpha = 0.34f), Capsule)
                .padding(horizontal = 10.dp, vertical = 8.dp)
                .clearAndSetSemantics { },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ClaimRing(Modifier.size(16.dp), provisional = true)
            Text(
                text = claimLabel(claims),
                style = OverlayText.copy(fontSize = 13.sp, fontWeight = FontWeight.SemiBold),
            )
        }
    }
}

@Composable
private fun DetailsRow(
    label: String,
    description: String,
    actionLabel: String,
    onClick: () -> Unit,
    leading: @Composable () -> Unit,
) {
    Row(
        modifier = Modifier
            .padding(2.dp)
            .sizeIn(minHeight = 48.dp)
            .clip(Capsule)
            .clickable(role = Role.Button, onClickLabel = actionLabel, onClick = onClick)
            .semantics { contentDescription = description }
            .padding(start = 14.dp, end = 10.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        leading()
        Spacer(Modifier.width(12.dp))
        Text(
            text = label,
            modifier = Modifier
                .weight(1f, fill = false)
                .clearAndSetSemantics { },
            style = OverlayText,
        )
        Spacer(Modifier.width(16.dp))
        Box(
            Modifier.size(28.dp).background(Graphite.copy(alpha = 0.045f), CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            OverlayGlyph(Glyph.Chevron, Modifier.size(14.dp), MutedInk)
        }
    }
}

@Composable
private fun ControlButton(glyph: Glyph, label: String, onClick: () -> Unit) {
    val ink = Graphite
    val p = LocalOvrlyPalette.current
    Box(
        modifier = Modifier
            .size(48.dp)
            .clip(CircleShape)
            .drawWithCache {
                val inset = 8.dp.toPx()
                val controlSize = Size(size.width - inset * 2f, size.height - inset * 2f)
                val corner = CornerRadius(controlSize.minDimension / 2f)
                val fill = Brush.verticalGradient(
                    listOf(p.accent.copy(alpha = 0.24f), p.sheen.copy(alpha = 0.10f)),
                )
                onDrawBehind {
                    drawRoundRect(fill, Offset(inset, inset), controlSize, corner)
                    drawRoundRect(
                        ink.copy(alpha = 0.16f),
                        Offset(inset, inset),
                        controlSize,
                        corner,
                        style = Stroke(0.7.dp.toPx()),
                    )
                }
            }
            .clickable(role = Role.Button, onClickLabel = label, onClick = onClick)
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) {
        OverlayGlyph(glyph, Modifier.size(18.dp))
    }
}

@Composable
private fun ControlDivider() {
    Spacer(Modifier.width(1.dp).height(20.dp).background(Graphite.copy(alpha = 0.12f)))
}

@Composable
internal fun ClaimRing(
    modifier: Modifier,
    provisional: Boolean,
    ink: Color = Graphite,
    accent: Color = if (provisional) Color(0xFF829353) else PaleLime,
) {
    Canvas(modifier) {
        val stroke = 2.5.dp.toPx()
        val inset = stroke / 2f + 1.dp.toPx()
        val origin = Offset(inset, inset)
        val ringSize = Size(size.width - inset * 2, size.height - inset * 2)
        drawArc(ink.copy(alpha = 0.12f), 0f, 360f, false, origin, ringSize, style = Stroke(stroke))
        drawArc(
            color = accent,
            startAngle = -90f,
            sweepAngle = if (provisional) 240f else 320f,
            useCenter = false,
            topLeft = origin,
            size = ringSize,
            style = Stroke(stroke, cap = StrokeCap.Round),
        )
    }
}

@Composable
internal fun InterruptedRing(modifier: Modifier = Modifier, tint: Color = Graphite) {
    Icon(
        painter = painterResource(R.drawable.ic_ovrly),
        contentDescription = null,
        modifier = modifier,
        tint = tint,
    )
}

internal enum class Glyph { Stop, Close, Chevron, Neutral, UnavailableSpeaker, Capture }

@Composable
internal fun OverlayGlyph(glyph: Glyph, modifier: Modifier = Modifier, tint: Color = Graphite) {
    Canvas(modifier) {
        fun point(x: Float, y: Float) = Offset(size.width * x / 24f, size.height * y / 24f)
        val strokeWidth = 1.6.dp.toPx()
        fun line(x1: Float, y1: Float, x2: Float, y2: Float) {
            drawLine(tint, point(x1, y1), point(x2, y2), strokeWidth, StrokeCap.Round)
        }
        when (glyph) {
            Glyph.Stop -> drawRoundRect(
                color = tint,
                topLeft = point(4f, 4f),
                size = Size(size.width * 16f / 24f, size.height * 16f / 24f),
                cornerRadius = CornerRadius(2.dp.toPx()),
            )

            Glyph.Close -> {
                line(5f, 5f, 19f, 19f)
                line(19f, 5f, 5f, 19f)
            }

            Glyph.Chevron -> {
                line(9f, 5f, 16f, 12f)
                line(16f, 12f, 9f, 19f)
            }

            Glyph.Neutral -> {
                drawCircle(tint, size.minDimension / 2f - strokeWidth, style = Stroke(strokeWidth))
                line(7f, 12f, 17f, 12f)
            }

            Glyph.UnavailableSpeaker -> {
                val speaker = Path().apply {
                    moveTo(size.width * 4f / 24f, size.height * 9f / 24f)
                    lineTo(size.width * 8f / 24f, size.height * 9f / 24f)
                    lineTo(size.width * 13f / 24f, size.height * 5f / 24f)
                    lineTo(size.width * 13f / 24f, size.height * 19f / 24f)
                    lineTo(size.width * 8f / 24f, size.height * 15f / 24f)
                    lineTo(size.width * 4f / 24f, size.height * 15f / 24f)
                    close()
                }
                drawPath(speaker, tint, style = Stroke(strokeWidth))
                line(3f, 3f, 21f, 21f)
            }

            Glyph.Capture -> {
                drawRoundRect(
                    color = tint,
                    topLeft = point(5f, 3f),
                    size = Size(size.width * 14f / 24f, size.height * 18f / 24f),
                    cornerRadius = CornerRadius(2.dp.toPx()),
                    style = Stroke(strokeWidth),
                )
                line(9f, 9f, 15f, 9f)
                line(9f, 13f, 15f, 13f)
                line(9f, 17f, 12f, 17f)
            }
        }
    }
}

internal fun claimLabel(count: Int): String = "$count ${if (count == 1) "claim" else "claims"}"

@Preview(name = "Overlay controls / seven fixtures", widthDp = 400, heightDp = 860)
@Composable
private fun OverlayControlsPreview() {
    MaterialTheme {
        Column(
            Modifier.fillMaxWidth().background(Color(0xFFE7EBE2)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text("OVERLAY CONTROLS", style = OverlayText.copy(fontSize = 11.sp, letterSpacing = 1.sp))
            Text("Design fixtures / not live results", style = OverlayText.copy(color = MutedInk, fontSize = 12.sp))
            listOf(
                "01  Idle" to OverlayVisual.Idle,
                "02  Recording" to OverlayVisual.Recording(seconds = 42),
                "03  Preliminary claims" to OverlayVisual.Recording(seconds = 72, provisionalClaims = 2),
                "04  Checking" to OverlayVisual.Checking,
                "05  Available claims" to OverlayVisual.Results(claims = 4),
                "06  No claims" to OverlayVisual.NoClaims,
                "07  Playback unavailable" to OverlayVisual.NoAudio,
            ).forEach { (label, state) ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(label, style = OverlayText.copy(color = MutedInk, fontSize = 11.sp))
                    GlassOverlay(
                        state, higherOpacity = false,
                        onSetup = {}, onStopCapture = {}, onCancelResearch = {}, onDetails = {},
                    )
                }
            }
        }
    }
}

@Preview(name = "Bounded 320 dp window, 200% text", widthDp = 320, heightDp = 300, fontScale = 2f)
@Composable
private fun NarrowWindowPreview() {
    Column(
        modifier = Modifier.width(320.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        listOf(
            OverlayVisual.Recording(seconds = 42),
            OverlayVisual.Recording(seconds = Int.MAX_VALUE),
            OverlayVisual.Captured,
        ).forEach { state ->
            GlassOverlay(
                state = state,
                higherOpacity = true,
                onSetup = {},
                onStopCapture = {},
                onCancelResearch = {},
                onDetails = {},
            )
        }
    }

}

@Preview(name = "Glass shell / sample backdrops", widthDp = 400, heightDp = 420)
@Composable
private fun GlassTranslucencyPreview() {
    MaterialTheme {
        Column(
            Modifier.fillMaxWidth().background(Color(0xFFF7F8F4)).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("SAMPLE BACKDROPS / NOT LIVE VIDEO", style = OverlayText.copy(fontSize = 11.sp))
            listOf(
                Triple("Translucent / light", Color(0xFFD4DED4), false),
                Triple("Translucent / dark", Color(0xFF303B34), false),
                Triple("Higher-opacity fallback / dark", Color(0xFF303B34), true),
            ).forEach { (label, backing, higherOpacity) ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(label, style = OverlayText.copy(fontSize = 12.sp))
                    Box(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                            .background(backing)
                            .drawWithCache {
                                onDrawBehind {
                                    drawRect(
                                        Color(0xFFBD9B86).copy(alpha = 0.65f),
                                        topLeft = Offset(size.width * 0.22f, 0f),
                                        size = Size(size.width * 0.18f, size.height),
                                    )
                                    drawRect(
                                        Color(0xFF819C9D).copy(alpha = 0.65f),
                                        topLeft = Offset(size.width * 0.58f, 0f),
                                        size = Size(size.width * 0.18f, size.height),
                                    )
                                }
                            }
                            .padding(8.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            GlassOverlay(
                                OverlayVisual.Idle, higherOpacity,
                                onSetup = {}, onStopCapture = {}, onCancelResearch = {}, onDetails = {},
                            )
                            GlassOverlay(
                                OverlayVisual.Recording(seconds = 42), higherOpacity,
                                onSetup = {}, onStopCapture = {}, onCancelResearch = {}, onDetails = {},
                            )
                        }
                    }
                }
            }
        }
    }
}
