package app.ovrly.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp

internal data class DemoClaim(val time: String, val title: String, val label: String, val explanation: String)
internal val DemoClaims = listOf(
    DemoClaim("00:12", "Time outside can help us unwind.", "Supported / sample",
        "This illustrates how a supporting source summary would read. No source was retrieved and no video was analyzed."),
    DemoClaim("00:28", "Twenty minutes works for everyone.", "Mixed / sample",
        "This illustrates a qualification: populations, methods and context can differ. This is not a research finding."),
    DemoClaim("00:46", "A walk is the best part of my day.", "Opinion / sample",
        "This illustrates a personal preference rather than a factual verdict. All claims in this panel are fixed examples."),
)

@Composable
internal fun DemoOverlayPanel(
    blurLabel: String,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
    panelWidth: Dp = 340.dp,
    maxHeight: Dp = 360.dp,
    onHeaderHeight: (Int) -> Unit = {},
) {
    val p = LocalOvrlyPalette.current
    var selected by remember { mutableIntStateOf(0) }
    var details by remember { mutableStateOf(false) }
    var checking by remember { mutableStateOf(false) }
    OverlayPanelScaffold(
        header = {
            Column(Modifier.weight(1f)) {
                Text("Demo", style = MaterialTheme.typography.titleSmall)
                Text("DEMO / SIMULATED", style = MaterialTheme.typography.labelMedium,
                    color = p.error)
            }
            IconButton(onClick = onClose, modifier = Modifier.size(48.dp).semantics { contentDescription = "Close demo overlay" }) {
                OverlayGlyph(Glyph.Close, Modifier.size(20.dp))
            }
        },
        footer = { OverlayPanelFooter("Drag header to move / no recording") },
        modifier = modifier,
        spec = OverlayPanelSpec(panelWidth, maxHeight, onHeaderHeight = onHeaderHeight)
    ) {
            Text("A moment outside / sample", style = MaterialTheme.typography.bodySmall, color = p.muted)
            if (checking) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Text("Simulated checking", modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                }
                Text("Preview only. No recording or network request is running.", style = MaterialTheme.typography.bodyMedium)
            } else if (details) {
                val claim = DemoClaims[selected]
                Text(claim.label, color = p.muted, style = MaterialTheme.typography.labelLarge)
                Text(claim.title, style = MaterialTheme.typography.titleLarge)
                Text(claim.explanation, style = MaterialTheme.typography.bodyMedium)
                Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text("Illustrative evidence card\nNo retrieved citation or verified assessment.",
                        Modifier.padding(16.dp), style = MaterialTheme.typography.bodySmall)
                }
                TextButton(onClick = { details = false }) { Text("Back to sample claims") }
            } else {
                DemoClaims.forEachIndexed { index, claim ->
                    Surface(
                        onClick = { selected = index },
                        shape = RoundedCornerShape(12.dp),
                        color = if (selected == index) MaterialTheme.colorScheme.secondaryContainer else p.surface.copy(alpha = 0.2f),
                        border = BorderStroke(1.dp, if (selected == index) p.muted else p.rule),
                    ) {
                        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text("${claim.time} / ${claim.label}", style = MaterialTheme.typography.labelSmall, color = p.muted)
                            Text(claim.title, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                Button(onClick = { details = true }, modifier = Modifier.fillMaxWidth()) {
                    Text("Read sample evidence")
                }
            }
            TextButton(onClick = { checking = !checking; details = false }) {
                Text(if (checking) "Show sample results" else "Preview checking state")
            }
            Text(blurLabel, style = MaterialTheme.typography.labelSmall, color = p.muted)
    }
}

/**
 * The large overlay panel shared by the demo and the expanded live results, so the two never
 * drift apart: glass, 12 dp padding, a header row (the only drag area; its height plus the top
 * padding goes to [OverlayPanelSpec.onHeaderHeight]), dividers, a scrolling body and a footer
 * line, sized by [spec].
 */
@Composable
internal fun OverlayPanelScaffold(
    header: @Composable RowScope.() -> Unit,
    footer: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    spec: OverlayPanelSpec = OverlayPanelSpec(),
    body: @Composable ColumnScope.() -> Unit
) {
    val p = LocalOvrlyPalette.current
    val headerPadding = with(LocalDensity.current) { 12.dp.roundToPx() }
    val fixedHeight = spec.fixedHeight
    val size = if (fixedHeight) Modifier.height(spec.height) else Modifier.heightIn(max = spec.height)
    CompositionLocalProvider(LocalContentColor provides p.ink) {
        Column(
            modifier.width(spec.width).then(size).mockGlass(spec.higherOpacity).padding(12.dp)
        ) {
            Row(
                Modifier.fillMaxWidth().onSizeChanged { spec.onHeaderHeight(it.height + headerPadding) },
                verticalAlignment = Alignment.CenterVertically,
                content = header
            )
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Column(
                Modifier.weight(1f, fill = fixedHeight).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                content = body
            )
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            footer()
        }
    }
}

/**
 * The panel's size and glass. With [fixedHeight] it is always [height] tall (the demo);
 * otherwise it grows with its content up to [height].
 */
@Immutable
internal data class OverlayPanelSpec(
    val width: Dp = 340.dp,
    val height: Dp = 360.dp,
    val fixedHeight: Boolean = true,
    val higherOpacity: Boolean = false,
    /** Height of the header plus the top padding: the window's drag area. */
    val onHeaderHeight: (Int) -> Unit = {}
)

/** The panel's footer line, announced politely when it changes. */
@Composable
internal fun OverlayPanelFooter(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier.semantics { liveRegion = LiveRegionMode.Polite },
        style = MaterialTheme.typography.labelSmall,
        color = LocalOvrlyPalette.current.muted
    )
}

@Preview(name = "Mock 1 / demo panel", widthDp = 380, heightDp = 740)
@Composable
private fun LightDemoPreview() {
    OvrlyTheme { DemoOverlayPanel("Solid glass / preview", {}, Modifier.padding(16.dp)) }
}

@Preview(name = "Chrome / demo panel", widthDp = 380, heightDp = 740)
@Composable
private fun DarkDemoPreview() {
    OvrlyTheme(dark = true) {
        Column(Modifier.background(ChromePalette.paper)) {
            DemoOverlayPanel("Solid glass / preview", {}, Modifier.padding(16.dp))
        }
    }
}
