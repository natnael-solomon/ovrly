package app.ovrly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Preview(
    name = "Interactive gallery · 15 designs",
    group = "Lab · Phone",
    showBackground = true,
    widthDp = 412,
    heightDp = 860,
)
@Composable
private fun GalleryScreenPreview() {
    OvrlyTheme {
        GalleryScreen(onBack = {})
    }
}

@Preview(
    name = "Clear float · Seven states",
    group = "States · Quiet · Clear float",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun ClearFloatStatesPreview() {
    GalleryStatesSheet(StudyDesign.CLEAR_FLOAT)
}

@Preview(
    name = "Hairline halo · Seven states",
    group = "States · Quiet · Hairline halo",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun HairlineHaloStatesPreview() {
    GalleryStatesSheet(StudyDesign.HAIRLINE_HALO)
}

@Preview(
    name = "Rimless air · Seven states",
    group = "States · Quiet · Rimless air",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun RimlessAirStatesPreview() {
    GalleryStatesSheet(StudyDesign.RIMLESS_AIR)
}

@Preview(
    name = "Floating rail · Seven states",
    group = "States · Quiet · Floating rail",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun FloatingRailStatesPreview() {
    GalleryStatesSheet(StudyDesign.FLOATING_RAIL)
}

@Preview(
    name = "Split pebble · Seven states",
    group = "States · Quiet · Split pebble",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun SplitPebbleStatesPreview() {
    GalleryStatesSheet(StudyDesign.SPLIT_PEBBLE)
}

@Preview(
    name = "Optical rim · Seven states",
    group = "States · Sculpted · Optical rim",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun OpticalRimStatesPreview() {
    GalleryStatesSheet(StudyDesign.OPTICAL_RIM)
}

@Preview(
    name = "Liquid lens · Seven states",
    group = "States · Sculpted · Liquid lens",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun LiquidLensStatesPreview() {
    GalleryStatesSheet(StudyDesign.LIQUID_LENS)
}

@Preview(
    name = "Domed glass · Seven states",
    group = "States · Sculpted · Domed glass",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun DomedGlassStatesPreview() {
    GalleryStatesSheet(StudyDesign.DOMED_GLASS)
}

@Preview(
    name = "Prism edge · Seven states",
    group = "States · Sculpted · Prism edge",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun PrismEdgeStatesPreview() {
    GalleryStatesSheet(StudyDesign.PRISM_EDGE)
}

@Preview(
    name = "Cushion glass · Seven states",
    group = "States · Sculpted · Cushion glass",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun CushionGlassStatesPreview() {
    GalleryStatesSheet(StudyDesign.CUSHION_GLASS)
}

@Preview(
    name = "Frosted veil · Seven states",
    group = "States · Soft · Frosted veil",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun FrostedVeilStatesPreview() {
    GalleryStatesSheet(StudyDesign.FROSTED_VEIL)
}

@Preview(
    name = "Satin capsule · Seven states",
    group = "States · Soft · Satin capsule",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun SatinCapsuleStatesPreview() {
    GalleryStatesSheet(StudyDesign.SATIN_CAPSULE)
}

@Preview(
    name = "Smoked glass · Seven states",
    group = "States · Soft · Smoked glass",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun SmokedGlassStatesPreview() {
    GalleryStatesSheet(StudyDesign.SMOKED_GLASS)
}

@Preview(
    name = "Milk glass · Seven states",
    group = "States · Soft · Milk glass",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun MilkGlassStatesPreview() {
    GalleryStatesSheet(StudyDesign.MILK_GLASS)
}

@Preview(
    name = "Soft squircle · Seven states",
    group = "States · Soft · Soft squircle",
    showBackground = true,
    widthDp = 412,
    heightDp = 1100,
)
@Composable
private fun SoftSquircleStatesPreview() {
    GalleryStatesSheet(StudyDesign.SOFT_SQUIRCLE)
}

@Preview(
    name = "Quiet · Five designs · Provisional recording",
    group = "Lab · Comparison",
    showBackground = true,
    widthDp = 412,
    heightDp = 900,
)
@Composable
private fun QuietFamilyComparisonPreview() {
    GalleryFamilySheet(StudyFamily.QUIET)
}

@Preview(
    name = "Sculpted · Five designs · Provisional recording",
    group = "Compare · Sculpted",
    showBackground = true,
    widthDp = 412,
    heightDp = 900,
)
@Composable
private fun SculptedFamilyComparisonPreview() {
    GalleryFamilySheet(StudyFamily.SCULPTED)
}

@Preview(
    name = "Soft · Five designs · Provisional recording",
    group = "Compare · Soft",
    showBackground = true,
    widthDp = 412,
    heightDp = 900,
)
@Composable
private fun SoftFamilyComparisonPreview() {
    GalleryFamilySheet(StudyFamily.SOFT)
}

@Preview(
    name = "Quiet · 320dp · 200% text · Provisional recording",
    group = "Stress · Quiet",
    showBackground = true,
    widthDp = 320,
    heightDp = 2000,
    fontScale = 2f,
)
@Composable
private fun QuietFamilyLargeTextPreview() {
    GalleryFamilySheet(StudyFamily.QUIET, StudyBackdrop.READING, higherOpacity = true, stress = true)
}

@Preview(
    name = "Sculpted · 320dp · 200% text · Provisional recording",
    group = "Stress · Sculpted",
    showBackground = true,
    widthDp = 320,
    heightDp = 2000,
    fontScale = 2f,
)
@Composable
private fun SculptedFamilyLargeTextPreview() {
    GalleryFamilySheet(StudyFamily.SCULPTED, StudyBackdrop.READING, higherOpacity = true, stress = true)
}

@Preview(
    name = "Soft · 320dp · 200% text · Provisional recording",
    group = "Stress · Soft",
    showBackground = true,
    widthDp = 320,
    heightDp = 2000,
    fontScale = 2f,
)
@Composable
private fun SoftFamilyLargeTextPreview() {
    GalleryFamilySheet(StudyFamily.SOFT, StudyBackdrop.READING, higherOpacity = true, stress = true)
}

@Composable
private fun GalleryStatesSheet(design: StudyDesign) {
    GalleryPreviewSheet {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            GallerySampleHeading("${design.title} / Seven states")
            GalleryBody("${design.family.title} · Synthetic dark video · Standard opacity", small = true)
            GalleryBody(design.sourceNote, small = true)
        }
        GalleryFixtures.forEachIndexed { index, fixture ->
            GalleryPreviewStrip(
                label = "${(index + 1).toString().padStart(2, '0')} / ${fixture.title}",
                fixture = fixture,
                design = design,
                backdrop = StudyBackdrop.VIDEO,
                higherOpacity = false,
            )
        }
        GalleryPreviewLimits()
    }
}

@Composable
private fun GalleryFamilySheet(
    family: StudyFamily,
    backdrop: StudyBackdrop = StudyBackdrop.VIDEO,
    higherOpacity: Boolean = false,
    stress: Boolean = false,
) {
    val fixture = GalleryFixtures[2]
    val designs = family.designs
    GalleryPreviewSheet {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            GallerySampleHeading("${family.title} / ${designs.size} designs")
            if (!stress) {
                GalleryBody(family.description, small = true)
            }
            GalleryBody("Same provisional recording fixture · Fixed 1:12 / 2 claims", small = true)
            GalleryBody(
                "Synthetic ${backdrop.title.lowercase()} · " +
                    if (higherOpacity) "Higher opacity" else "Standard opacity",
                small = true,
            )
        }
        designs.forEach { design ->
            GalleryPreviewStrip(design.title, fixture, design, backdrop, higherOpacity)
        }
        GalleryPreviewLimits()
    }
}

@Composable
private fun GalleryPreviewSheet(content: @Composable () -> Unit) {
    OvrlyTheme {
        Column(
            modifier = Modifier
                .widthIn(max = 600.dp)
                .fillMaxWidth()
                .background(GalleryBackground)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            content()
        }
    }
}

@Composable
private fun GalleryPreviewStrip(
    label: String,
    fixture: GalleryFixture,
    design: StudyDesign,
    backdrop: StudyBackdrop,
    higherOpacity: Boolean,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            text = label,
            color = GalleryInk,
            fontSize = 12.sp,
            lineHeight = 18.sp,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.semantics { heading() },
        )
        GalleryStudySample(
            fixture = fixture,
            design = design,
            backdrop = backdrop,
            higherOpacity = higherOpacity,
            minimalBackdrop = true,
        )
    }
}

@Composable
private fun GalleryPreviewLimits() {
    GalleryBody(
        "Fixed mocks only; no live changes, sampling, blur or refraction. Manual palettes: " +
            "Optical rim / Milk stay light, Smoked stays dark. “No claims” is not a truth verdict.",
        small = true,
    )
}
