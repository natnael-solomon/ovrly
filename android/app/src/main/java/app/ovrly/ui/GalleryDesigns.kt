package app.ovrly.ui

import androidx.compose.ui.graphics.Color

internal enum class StudyFamily(val title: String, val description: String) {
    QUIET("Quiet", "Less surface. More of the app."),
    SCULPTED("Sculpted", "Explore curved rims, facets and glass depth."),
    SOFT("Soft", "Gentler edges and more supported reading surfaces.");

    val designs: List<StudyDesign>
        get() = when (this) {
            QUIET -> listOf(StudyDesign.CLEAR_FLOAT, StudyDesign.HAIRLINE_HALO, StudyDesign.RIMLESS_AIR,
                StudyDesign.FLOATING_RAIL, StudyDesign.SPLIT_PEBBLE)
            SCULPTED -> listOf(StudyDesign.OPTICAL_RIM, StudyDesign.LIQUID_LENS, StudyDesign.DOMED_GLASS,
                StudyDesign.PRISM_EDGE, StudyDesign.CUSHION_GLASS)
            SOFT -> listOf(StudyDesign.FROSTED_VEIL, StudyDesign.SATIN_CAPSULE, StudyDesign.SMOKED_GLASS,
                StudyDesign.MILK_GLASS, StudyDesign.SOFT_SQUIRCLE)
        }
}

internal enum class StudyDesign(val title: String, val subtitle: String, val sourceNote: String) {
    CLEAR_FLOAT(
        "Clear float", "A hairline boundary. The app stays in view.",
        "Quiet alternative: low-density tint, no bevel or drop shadow.",
    ),
    OPTICAL_RIM(
        "Optical rim", "Double-wall glass, kept as the reference.",
        "Original optical-glass reference, retained as an isolated material study.",
    ),
    LIQUID_LENS(
        "Liquid lens", "A curved edge with a clearer central surface.",
        "QmDeve / AndroidLiquidGlassView-inspired edge-depth study. Static highlights, not refraction.",
    ),
    FROSTED_VEIL(
        "Frosted veil", "Soft corners. A diffuse, unoutlined surface.",
        "jmadaminov / glassmorphic-composables-inspired surface layering. Tint only, not backdrop blur.",
    ),
    SPLIT_PEBBLE(
        "Split pebble", "Small islands instead of one continuous bar.",
        "Footprint alternative: the readout and stop/cancel control have separate surfaces.",
    ),
    HAIRLINE_HALO(
        "Hairline halo", "Two fine traces, with air between them.",
        "A reduced double-wall study: thin contours without a bright bevel.",
    ),
    RIMLESS_AIR(
        "Rimless air", "Only a light wash behind the information.",
        "An unframed transparency study. No outline, shadow or simulated lens.",
    ),
    FLOATING_RAIL(
        "Floating rail", "A shallow pane resting on a single lower edge.",
        "A flatter 14 dp corner profile with one supporting rail rather than a full glass rim.",
    ),
    DOMED_GLASS(
        "Domed glass", "A rounded lens with a curved upper glint.",
        "Inspired by the visible domed-glass reference: a bright curved lip and a close contact shadow.",
    ),
    PRISM_EDGE(
        "Prism edge", "Clipped corners and crisp, angled facets.",
        "A geometric alternative to the liquid-glass rim. Static facets, not sampled refraction.",
    ),
    CUSHION_GLASS(
        "Cushion glass", "A broad, rounded rim with a softer reflection.",
        "A padded-glass edge study: a wide bevel and recessed inner face, without a hot highlight.",
    ),
    SATIN_CAPSULE(
        "Satin capsule", "A matte capsule with a quiet diagonal sheen.",
        "A restrained layered-surface alternative to glassmorphic-composables. No image capture or blur.",
    ),
    SMOKED_GLASS(
        "Smoked glass", "A stable charcoal lens, even over light apps.",
        "A fixed dark material with light labels. The mock does not sample or classify the app behind it.",
    ),
    MILK_GLASS(
        "Milk glass", "A warm, denser pane for uninterrupted reading.",
        "A fixed light material that prioritizes legibility over see-through area. No actual frosting.",
    ),
    SOFT_SQUIRCLE(
        "Soft squircle", "A compact rounded rectangle with lifted corners.",
        "A 16 dp corner-profile study, with a small corner glint instead of a capsule-wide rim.",
    );

    val family: StudyFamily
        get() = when (this) {
            CLEAR_FLOAT, HAIRLINE_HALO, RIMLESS_AIR, FLOATING_RAIL, SPLIT_PEBBLE -> StudyFamily.QUIET
            OPTICAL_RIM, LIQUID_LENS, DOMED_GLASS, PRISM_EDGE, CUSHION_GLASS -> StudyFamily.SCULPTED
            FROSTED_VEIL, SATIN_CAPSULE, SMOKED_GLASS, MILK_GLASS, SOFT_SQUIRCLE -> StudyFamily.SOFT
        }

    val radiusDp: Float
        get() = when (this) {
            FLOATING_RAIL -> 14f
            SOFT_SQUIRCLE -> 16f
            FROSTED_VEIL, MILK_GLASS -> 20f
            else -> 26f
        }

    val normalOpacity: Float
        get() = when (this) {
            CLEAR_FLOAT -> 0.34f
            HAIRLINE_HALO -> 0.36f
            RIMLESS_AIR -> 0.38f
            FLOATING_RAIL -> 0.42f
            OPTICAL_RIM -> 0.66f
            LIQUID_LENS -> 0.40f
            DOMED_GLASS, SPLIT_PEBBLE -> 0.56f
            PRISM_EDGE -> 0.48f
            CUSHION_GLASS -> 0.52f
            FROSTED_VEIL -> 0.70f
            SATIN_CAPSULE -> 0.64f
            SMOKED_GLASS -> 0.76f
            MILK_GLASS -> 0.84f
            SOFT_SQUIRCLE -> 0.58f
        }
}

internal data class StudyPalette(
    val fill: Color,
    val ink: Color,
    val secondary: Color,
    val accent: Color,
    val dark: Boolean,
)

internal fun StudyDesign.palette(dark: Boolean, higherOpacity: Boolean): StudyPalette {
    val darkMaterial = when (this) {
        StudyDesign.SMOKED_GLASS -> true
        StudyDesign.MILK_GLASS, StudyDesign.OPTICAL_RIM -> false
        else -> dark
    }
    val tint = when {
        this == StudyDesign.MILK_GLASS -> Color(0xFFF6F4EE)
        this == StudyDesign.SMOKED_GLASS -> Color(0xFF182427)
        darkMaterial -> ChromePalette.surface
        else -> PaperPalette.surface
    }
    return StudyPalette(
        fill = tint.copy(alpha = if (higherOpacity) 0.96f else normalOpacity),
        ink = if (darkMaterial) ChromePalette.ink else PaperPalette.ink,
        secondary = if (darkMaterial) Color(0xFFD0D3E0) else PaperPalette.muted,
        accent = if (darkMaterial) ChromePalette.sheen else Color(0xFF466123),
        dark = darkMaterial,
    )
}
