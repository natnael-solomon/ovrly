package app.ovrly.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal val GalleryInk: Color @Composable get() = LocalOvrlyPalette.current.ink
internal val GalleryMuted: Color @Composable get() = LocalOvrlyPalette.current.muted
internal val GalleryBackground: Color @Composable get() = LocalOvrlyPalette.current.paper
private val GalleryAccent: Color @Composable get() = LocalOvrlyPalette.current.accent
private val GalleryRule: Color @Composable get() = LocalOvrlyPalette.current.rule

private enum class GalleryView(val title: String) {
    STATES("Seven states"),
    DESIGNS("Compare designs"),
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
fun GalleryScreen(onBack: () -> Unit, onDemo: (() -> Unit)? = null) {
    if (!LocalInspectionMode.current) {
        BackHandler(onBack = onBack)
    }
    var designName by rememberSaveable { mutableStateOf(StudyDesign.CLEAR_FLOAT.name) }
    var backdropName by rememberSaveable { mutableStateOf(StudyBackdrop.VIDEO.name) }
    var fixtureIndex by rememberSaveable { mutableStateOf(1) }
    var higherOpacity by rememberSaveable { mutableStateOf(false) }
    var viewName by rememberSaveable { mutableStateOf(GalleryView.STATES.name) }
    var compareAllFamilies by rememberSaveable { mutableStateOf(false) }
    val design = StudyDesign.valueOf(designName)
    val family = design.family
    val familyDesigns = family.designs
    val comparisonDesigns = if (compareAllFamilies) StudyFamily.entries.flatMap { it.designs } else familyDesigns
    val backdrop = StudyBackdrop.valueOf(backdropName)
    val fixture = GalleryFixtures[fixtureIndex]
    val view = GalleryView.valueOf(viewName)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(GalleryBackground)
            .safeDrawingPadding(),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(
            modifier = Modifier
                .widthIn(max = 600.dp)
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack, modifier = Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp)) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "Back to setup",
                    tint = GalleryInk,
                    modifier = Modifier.size(22.dp),
                )
            }
            Text(
                text = "OVRLY / MATERIAL LAB",
                color = GalleryMuted,
                fontSize = 11.sp,
                lineHeight = 16.sp,
                letterSpacing = 1.1.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
        }

        LazyColumn(
            modifier = Modifier.widthIn(max = 600.dp).fillMaxWidth().weight(1f),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            item(key = "intro") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        text = "Overlay studies",
                        color = GalleryInk,
                        style = MaterialTheme.typography.displaySmall,
                        modifier = Modifier.semantics { heading() },
                    )
                    GalleryBody(
                        "${StudyDesign.entries.size} designs · " +
                            "${StudyDesign.entries.size * GalleryFixtures.size} fixed fixtures · Local mocks only",
                    )
                    if (onDemo != null) {
                        TextButton(onClick = onDemo) { Text("Try the larger demo over other apps") }
                        GalleryBody("Explicit opt-in. Simulated evidence, with native blur where supported.", small = true)
                    }
                }
            }

            item(key = "design-chooser") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    FlowRow(
                        modifier = Modifier.fillMaxWidth().selectableGroup(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        StudyFamily.entries.forEach { option ->
                            GalleryChoice(
                                title = option.title,
                                selected = option == family,
                                onClick = {
                                    if (option != family) {
                                        designName = option.designs.first().name
                                    }
                                },
                            )
                        }
                    }
                    GallerySectionLabel("${family.title} / ${familyDesigns.size} surfaces")
                    FlowRow(
                        modifier = Modifier.fillMaxWidth().selectableGroup(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        familyDesigns.forEach { option ->
                            GalleryChoice(
                                title = option.title,
                                selected = option == design,
                                onClick = { designName = option.name },
                            )
                        }
                    }
                }
            }

            item(key = "in-context") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    GallerySectionLabel("${design.title} / ${fixture.title}")
                    GalleryStudySample(
                        fixture = fixture,
                        design = design,
                        backdrop = backdrop,
                        higherOpacity = higherOpacity,
                        compact = false,
                        countBackgroundTaps = true,
                    )
                    GalleryFixtureChooser(
                        selectedIndex = fixtureIndex,
                        onSelect = { fixtureIndex = it },
                    )
                    GalleryBody(fixture.explanation)
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        GallerySampleHeading(design.title)
                        GalleryBody(design.subtitle)
                        GalleryBody(design.sourceNote, small = true)
                        GalleryBody("${family.title}: ${family.description}", small = true)
                    }
                }
            }

            item(key = "backdrop-controls") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    GallerySectionLabel("Synthetic context")
                    FlowRow(
                        modifier = Modifier.fillMaxWidth().selectableGroup(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        StudyBackdrop.entries.forEach { option ->
                            GalleryChoice(
                                title = option.title,
                                selected = option == backdrop,
                                onClick = { backdropName = option.name },
                            )
                        }
                    }
                    Text(
                        text = "Manually matched mock palettes — not automatic app sampling. " +
                            "Optical rim and Milk glass stay light; Smoked glass stays dark.",
                        color = GalleryInk,
                        fontSize = 13.sp,
                        lineHeight = 20.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    GalleryOpacityToggle(
                        checked = higherOpacity,
                        onCheckedChange = { higherOpacity = it },
                    )
                }
            }

            item(key = "sheet-chooser") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    HorizontalDivider(color = GalleryRule)
                    FlowRow(
                        modifier = Modifier.fillMaxWidth().selectableGroup(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        GalleryView.entries.forEach { option ->
                            GalleryChoice(
                                title = option.title,
                                selected = option == view,
                                onClick = { viewName = option.name },
                                role = Role.Tab,
                            )
                        }
                    }
                    if (view == GalleryView.DESIGNS) {
                        FlowRow(
                            modifier = Modifier.fillMaxWidth().selectableGroup(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            GalleryChoice(
                                title = "${family.title} / ${familyDesigns.size}",
                                selected = !compareAllFamilies,
                                onClick = { compareAllFamilies = false },
                            )
                            GalleryChoice(
                                title = "All designs / ${StudyDesign.entries.size}",
                                selected = compareAllFamilies,
                                onClick = { compareAllFamilies = true },
                            )
                        }
                    }
                    GalleryBody(
                        if (view == GalleryView.STATES) {
                            "${design.title} across all seven fixtures. Every control below " +
                                "only explains its purpose."
                        } else {
                            "${if (compareAllFamilies) "All families" else family.title} / " +
                                "${comparisonDesigns.size} designs. Same “${fixture.title}” fixture, " +
                                "backdrop and opacity; change them above to compare fairly."
                        },
                    )
                }
            }

            if (view == GalleryView.STATES) {
                itemsIndexed(
                    items = GalleryFixtures,
                    key = { _, sample -> "state-${design.name}-${sample.title}" },
                ) { index, sample ->
                    GalleryStateSample(index, sample, design, backdrop, higherOpacity)
                }
            } else {
                items(
                    items = comparisonDesigns,
                    key = { sample -> "design-${sample.name}" },
                ) { sample ->
                    GalleryComparisonSample(sample, fixture, backdrop, higherOpacity)
                }
            }

            item(key = "study-limits") {
                GalleryStudyLimits()
            }
        }
    }
}

@Composable
private fun GalleryChoice(
    title: String,
    selected: Boolean,
    onClick: () -> Unit,
    role: Role = Role.RadioButton,
) {
    val shape = RoundedCornerShape(16.dp)
    Box(
        modifier = Modifier
            .sizeIn(minWidth = 48.dp, minHeight = 48.dp)
            .clip(shape)
            .background(if (selected) GalleryAccent else Color.Transparent)
            .border(1.dp, if (selected) GalleryInk.copy(alpha = 0.3f) else GalleryRule, shape)
            .selectable(selected = selected, role = role, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = title,
            color = if (selected) LocalOvrlyPalette.current.accentInk else GalleryInk,
            fontSize = 13.sp,
            lineHeight = 18.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
        )
    }
}

@Composable
private fun GalleryFixtureChooser(selectedIndex: Int, onSelect: (Int) -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth()) {
        OutlinedButton(
            onClick = { expanded = true },
            modifier = Modifier.fillMaxWidth().sizeIn(minHeight = 64.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = GalleryInk),
            border = BorderStroke(1.dp, GalleryRule),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = "FIXTURE ${(selectedIndex + 1).toString().padStart(2, '0')} / 07 · CHANGE",
                    color = GalleryMuted,
                    fontSize = 10.sp,
                    lineHeight = 16.sp,
                    letterSpacing = 0.7.sp,
                )
                Text(
                    text = GalleryFixtures[selectedIndex].title,
                    color = GalleryInk,
                    fontSize = 15.sp,
                    lineHeight = 22.sp,
                    fontWeight = FontWeight.Medium,
                )
            }
            Icon(
                imageVector = Icons.Default.ArrowDropDown,
                contentDescription = null,
                modifier = Modifier.padding(start = 8.dp).size(24.dp),
            )
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
            containerColor = GalleryBackground,
            modifier = Modifier.widthIn(max = 360.dp),
        ) {
            GalleryFixtures.forEachIndexed { index, fixture ->
                DropdownMenuItem(
                    text = {
                        Text(
                            text = "${(index + 1).toString().padStart(2, '0')} / ${fixture.title}" +
                                if (index == selectedIndex) " · Selected" else "",
                            color = GalleryInk,
                            fontSize = 14.sp,
                            lineHeight = 20.sp,
                            fontWeight = if (index == selectedIndex) FontWeight.SemiBold else FontWeight.Normal,
                        )
                    },
                    modifier = Modifier.sizeIn(minHeight = 48.dp),
                    onClick = {
                        onSelect(index)
                        expanded = false
                    },
                )
            }
        }
    }
}

@Composable
private fun GalleryOpacityToggle(checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .sizeIn(minHeight = 64.dp)
            .toggleable(value = checked, role = Role.Switch, onValueChange = onCheckedChange)
            .padding(vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                text = "Higher opacity",
                color = GalleryInk,
                fontSize = 15.sp,
                lineHeight = 22.sp,
                fontWeight = FontWeight.Medium,
            )
            GalleryBody("A denser fill, in this lab only.", small = true)
        }
        Switch(
            checked = checked,
            onCheckedChange = null,
            colors = SwitchDefaults.colors(
                checkedThumbColor = LocalOvrlyPalette.current.accentInk,
                checkedTrackColor = GalleryAccent,
                checkedBorderColor = GalleryAccent,
                uncheckedThumbColor = GalleryMuted,
                uncheckedTrackColor = MaterialTheme.colorScheme.surfaceVariant,
                uncheckedBorderColor = GalleryMuted,
            ),
        )
    }
}

@Composable
internal fun GalleryStateSample(
    index: Int,
    fixture: GalleryFixture,
    design: StudyDesign,
    backdrop: StudyBackdrop,
    higherOpacity: Boolean,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        GallerySampleHeading("${(index + 1).toString().padStart(2, '0')} / ${fixture.title}")
        GalleryBody(fixture.explanation, small = true)
        GalleryStudySample(fixture, design, backdrop, higherOpacity)
    }
}

@Composable
internal fun GalleryComparisonSample(
    design: StudyDesign,
    fixture: GalleryFixture,
    backdrop: StudyBackdrop,
    higherOpacity: Boolean,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        GallerySampleHeading(design.title)
        GalleryBody(design.subtitle, small = true)
        GalleryStudySample(fixture, design, backdrop, higherOpacity)
    }
}

@Composable
internal fun GalleryStudySample(
    fixture: GalleryFixture,
    design: StudyDesign,
    backdrop: StudyBackdrop,
    higherOpacity: Boolean,
    compact: Boolean = true,
    countBackgroundTaps: Boolean = false,
    minimalBackdrop: Boolean = false,
) {
    var feedback by rememberSaveable(design.name, fixture.title) { mutableStateOf<String?>(null) }
    var mockBackgroundTaps by rememberSaveable { mutableStateOf(0) }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        GalleryBackdrop(
            backdrop = backdrop,
            compact = compact,
            minimal = minimalBackdrop,
            onMockBackgroundTap = if (countBackgroundTaps) {
                { mockBackgroundTaps += 1 }
            } else {
                null
            },
        ) {
            StudyOverlay(
                state = fixture.state,
                design = design,
                darkBackdrop = backdrop.dark,
                higherOpacity = higherOpacity,
                onAction = { action -> feedback = fixture.actionExplanation(action) },
                modifier = Modifier.widthIn(max = 440.dp),
            )
        }
        if (countBackgroundTaps) {
            Text(
                text = "Mock background taps: $mockBackgroundTaps",
                color = GalleryInk,
                fontSize = 12.sp,
                lineHeight = 18.sp,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            )
            GalleryBody(
                "Tap the scenery to try this local mock. This is not proof of Android " +
                    "cross-window touch pass-through.",
                small = true,
            )
        }
        feedback?.let { message ->
            Text(
                text = message,
                color = GalleryInk,
                fontSize = 13.sp,
                lineHeight = 20.sp,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(12.dp))
                    .padding(12.dp)
                    .semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
    }
}

@Composable
internal fun GalleryStudyLimits() {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        HorizontalDivider(color = GalleryRule)
        GallerySampleHeading("A study, not a setting")
        GalleryBody(
            "No design applies to the live overlay. Timers and claim counts are fixed fixtures. " +
                "All scenery is synthetic. The edges and layered fills are drawn locally; " +
                "there is no actual blur or refraction. Permissions, capture and research are untouched.",
            small = true,
        )
        GalleryBody(
            "Counts are not verdicts. “No checkable claims” does not mean everything was true. " +
                "The lime accent marks available claims, never truth.",
            small = true,
        )
    }
}

@Composable
internal fun GallerySampleHeading(text: String) {
    Text(
        text = text,
        color = GalleryInk,
        style = MaterialTheme.typography.titleLarge,
        modifier = Modifier.semantics { heading() },
    )
}

@Composable
private fun GallerySectionLabel(text: String) {
    Text(
        text = text,
        color = GalleryMuted,
        fontSize = 12.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.5.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.semantics { heading() },
    )
}

@Composable
internal fun GalleryBody(text: String, small: Boolean = false) {
    Text(
        text = text,
        color = GalleryMuted,
        fontSize = if (small) 12.sp else 13.sp,
        lineHeight = if (small) 18.sp else 20.sp,
    )
}
