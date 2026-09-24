package app.ovrly.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Favorite
import androidx.compose.material.icons.outlined.FavoriteBorder
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.BlurredEdgeTreatment
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.ovrly.R

@Composable
internal fun YourSpaceScreen(
    reports: List<SampleReport>,
    onOpen: (SampleReport) -> Unit,
    onExplore: () -> Unit,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var searching by rememberSaveable { mutableStateOf(false) }
    val visible = filterSamples(reports, query)
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(24.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        item { SampleHeader("Your space", searching, { searching = !searching; query = "" }) }
        if (searching) item { SampleSearch(query, { query = it }) }
        if (visible.isEmpty()) {
            item {
                EmptySamples(
                    title = if (query.isBlank()) "No samples saved" else "No matches",
                    action = if (query.isBlank()) "Explore samples" else "Clear search",
                    onAction = { if (query.isBlank()) onExplore() else query = "" },
                )
            }
        } else {
            item {
                FeatureSample(visible.first(), onClick = { onOpen(visible.first()) })
            }
            items(visible.drop(1), key = { it.id }) { report ->
                SampleRow(report, onClick = { onOpen(report) })
            }
        }
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Sample collection", style = MaterialTheme.typography.labelSmall,
                    color = LocalOvrlyPalette.current.muted)
                TextButton(onClick = onExplore) {
                    Text("Explore")
                    Spacer(Modifier.width(8.dp))
                    Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(16.dp))
                }
            }
        }
    }
}

@Composable
internal fun ExploreScreen(onOpen: (SampleReport) -> Unit) {
    var topic by rememberSaveable { mutableStateOf("All") }
    var query by rememberSaveable { mutableStateOf("") }
    var searching by rememberSaveable { mutableStateOf(false) }
    val ordered = SampleReports.drop(1) + SampleReports.first()
    val visible = filterSamples(ordered, query, topic)
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(24.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        item { SampleHeader("Explore", searching, { searching = !searching; query = "" }) }
        if (searching) item { SampleSearch(query, { query = it }) }
        item {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SampleTopics.forEach {
                    FilterChip(selected = topic == it, onClick = { topic = it }, label = { Text(it) })
                }
            }
        }
        if (visible.isEmpty()) item {
            EmptySamples("No matches", "Reset filters") { query = ""; topic = "All" }
        }
        items(visible, key = { it.id }) { report ->
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp))
                    .clickable(role = Role.Button, onClick = { onOpen(report) }),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                ChromeArtwork(report.artwork, Modifier.fillMaxWidth().aspectRatio(1.7f).clip(RoundedCornerShape(24.dp)))
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(report.title, style = MaterialTheme.typography.titleLarge)
                        Text("${report.topic} / ${report.claims.size} sample claims",
                            style = MaterialTheme.typography.labelSmall, color = LocalOvrlyPalette.current.muted)
                    }
                    Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(20.dp),
                        tint = LocalOvrlyPalette.current.muted)
                }
                Spacer(Modifier.height(4.dp))
            }
        }
    }
}

@Composable
private fun SampleHeader(title: String, searching: Boolean, onSearch: () -> Unit) {
    val p = LocalOvrlyPalette.current
    Column(verticalArrangement = Arrangement.spacedBy(20.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            val wordmark = painterResource(R.drawable.wordmark_ovrly)
            Box {
                if (!p.dark) {
                    // Paper is close in value to the chrome. Chrome needs both a highlight and a shadow to
                    // read: a soft ink drop-shadow below, a warm sheen halo around, and a crisp ink edge.
                    Image(
                        wordmark, contentDescription = null,
                        modifier = Modifier.height(40.dp).offset(y = 3.dp)
                            .blur(4.dp, BlurredEdgeTreatment.Unbounded),
                        contentScale = ContentScale.FillHeight,
                        colorFilter = ColorFilter.tint(p.ink.copy(alpha = 0.6f), BlendMode.SrcIn),
                    )
                    Image(
                        wordmark, contentDescription = null,
                        modifier = Modifier.height(40.dp).scale(1.06f)
                            .blur(2.dp, BlurredEdgeTreatment.Unbounded),
                        contentScale = ContentScale.FillHeight,
                        colorFilter = ColorFilter.tint(p.sheen.copy(alpha = 0.9f), BlendMode.SrcIn),
                    )
                    Image(
                        wordmark, contentDescription = null,
                        modifier = Modifier.height(40.dp).scale(1.03f),
                        contentScale = ContentScale.FillHeight,
                        colorFilter = ColorFilter.tint(p.ink.copy(alpha = 0.85f), BlendMode.SrcIn),
                    )
                }
                Image(
                    wordmark, contentDescription = "ovrly",
                    modifier = Modifier.height(40.dp).chromeGlare(strength = if (p.dark) 0.6f else 0.34f),
                    contentScale = ContentScale.FillHeight,
                )
            }
            Spacer(Modifier.weight(1f))
            Text("PREVIEW", style = MaterialTheme.typography.labelSmall, color = p.muted)
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(title, Modifier.weight(1f).semantics { heading() },
                style = MaterialTheme.typography.titleMedium)
            IconButton(onClick = onSearch) {
                Icon(if (searching) Icons.Outlined.Close else Icons.Outlined.Search,
                    if (searching) "Close search" else "Search samples", Modifier.size(20.dp))
            }
        }
    }
}

@Composable
private fun SampleSearch(query: String, onQuery: (String) -> Unit) {
    OutlinedTextField(
        value = query, onValueChange = onQuery, modifier = Modifier.fillMaxWidth(),
        label = { Text("Search samples") }, singleLine = true,
        shape = RoundedCornerShape(16.dp),
    )
}

@Composable
private fun FeatureSample(report: SampleReport, onClick: () -> Unit) {
    val p = LocalOvrlyPalette.current
    Surface(onClick = onClick, shape = RoundedCornerShape(28.dp),
        color = p.surface, border = BorderStroke(1.dp, p.rule.copy(alpha = 0.6f))) {
        Column {
            ChromeArtwork(report.artwork, Modifier.fillMaxWidth().aspectRatio(1.35f))
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("${report.topic.uppercase()} / SAMPLE", style = MaterialTheme.typography.labelSmall,
                    color = p.muted)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(report.title, Modifier.weight(1f),
                        style = MaterialTheme.typography.titleLarge)
                    Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(20.dp), tint = p.accent)
                }
                SampleTrace(report)
            }
        }
    }
}

@Composable
private fun SampleTrace(report: SampleReport) {
    val p = LocalOvrlyPalette.current
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            report.claims.forEachIndexed { index, _ ->
                Box(Modifier.weight(1f).height(3.dp)
                    .background(if (index % 2 == 0) p.accent else p.sheen, RoundedCornerShape(2.dp)))
            }
        }
        Text("${report.claims.size} sample claims", style = MaterialTheme.typography.labelSmall, color = p.muted)
    }
}

@Composable
private fun SampleRow(report: SampleReport, onClick: () -> Unit) {
    val p = LocalOvrlyPalette.current
    Column(verticalArrangement = Arrangement.spacedBy(20.dp)) {
        Row(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp))
                .clickable(role = Role.Button, onClick = onClick),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            ChromeArtwork(report.artwork, Modifier.size(80.dp).clip(RoundedCornerShape(16.dp)))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(report.title, style = MaterialTheme.typography.titleMedium)
                Text("${report.topic} / Sample", style = MaterialTheme.typography.labelSmall, color = p.muted)
            }
            Icon(Icons.AutoMirrored.Outlined.ArrowForward, null, Modifier.size(16.dp), tint = p.muted)
        }
        HorizontalDivider(color = p.rule.copy(alpha = 0.5f))
    }
}

@Composable
private fun EmptySamples(title: String, action: String, onAction: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp)) {
        InterruptedRing(Modifier.size(40.dp), LocalOvrlyPalette.current.muted)
        Text(title, style = MaterialTheme.typography.titleMedium)
        TextButton(onClick = onAction) { Text(action) }
    }
}

@Composable
internal fun SampleReportScreen(
    report: SampleReport,
    saved: Boolean,
    onBack: () -> Unit,
    onSave: () -> Unit,
) {
    val p = LocalOvrlyPalette.current
    var expanded by rememberSaveable(report.id) { mutableStateOf<Int?>(0) }
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(24.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back to samples", Modifier.size(20.dp))
                }
                Text("SAMPLE REPORT", Modifier.weight(1f), style = MaterialTheme.typography.labelSmall,
                    color = p.muted)
                IconToggleButton(checked = saved, onCheckedChange = { onSave() }) {
                    Icon(if (saved) Icons.Outlined.Favorite else Icons.Outlined.FavoriteBorder,
                        if (saved) "Remove sample from Your space" else "Save sample to Your space",
                        tint = if (saved) p.accent else p.muted, modifier = Modifier.size(20.dp))
                }
            }
        }
        item {
            ChromeArtwork(report.artwork, Modifier.fillMaxWidth().aspectRatio(1.8f).clip(RoundedCornerShape(24.dp)))
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(report.title, style = MaterialTheme.typography.titleLarge)
                Text("${report.topic} / ${report.claims.size} sample claims",
                    style = MaterialTheme.typography.labelSmall, color = p.muted)
                Text("Illustrative content. No video analyzed or sources retrieved.",
                    style = MaterialTheme.typography.bodySmall, color = p.muted)
            }
        }
        items(report.claims.size, key = { it }) { index ->
            val claim = report.claims[index]
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                        .clickable(role = Role.Button, onClickLabel = if (expanded == index) "Collapse claim" else "Expand claim",
                            onClick = { expanded = if (expanded == index) null else index })
                        .padding(vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("${claim.time} / ${claim.label}", style = MaterialTheme.typography.labelSmall, color = p.muted)
                    Text(claim.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
                    Text(if (expanded == index) "Less" else "Context", style = MaterialTheme.typography.labelSmall,
                        color = if (p.dark) p.accent else p.ink)
                }
                if (expanded == index) Text(claim.explanation, style = MaterialTheme.typography.bodyMedium, color = p.muted)
                HorizontalDivider(color = p.rule.copy(alpha = 0.5f))
            }
        }
        item {
            Text("Saves affect this preview collection only.", style = MaterialTheme.typography.bodySmall, color = p.muted)
        }
    }
}
