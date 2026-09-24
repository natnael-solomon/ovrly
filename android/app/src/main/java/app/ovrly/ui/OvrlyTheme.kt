package app.ovrly.ui

import android.content.Context
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Text
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.edit
import app.ovrly.R
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

object AppearanceStore {
    const val DEFAULT_DARK = true
    private val mutableDark = MutableStateFlow(DEFAULT_DARK)
    val dark = mutableDark.asStateFlow()

    fun load(context: Context) {
        mutableDark.value = context.getSharedPreferences("companion", 0).getBoolean("darkTheme", DEFAULT_DARK)
    }

    fun setDark(context: Context, dark: Boolean) {
        context.getSharedPreferences("companion", 0).edit { putBoolean("darkTheme", dark) }
        mutableDark.value = dark
    }
}

internal data class OvrlyPalette(
    val dark: Boolean,
    val paper: Color,
    val surface: Color,
    val ink: Color,
    val muted: Color,
    val rule: Color,
    val accent: Color,
    val accentInk: Color,
    val error: Color,
    val sheen: Color,
) {
    fun glassFill(blurred: Boolean, higherOpacity: Boolean): Color =
        surface.copy(alpha = if (blurred && !higherOpacity) 0.86f else 1f)
}

internal val PaperPalette = OvrlyPalette(
    false, Color(0xFFF0EFE5), Color(0xFFF8F7EF), Color(0xFF252826),
    Color(0xFF53594A), Color(0xFFD3D4C7), Color(0xFFD5EB97),
    Color(0xFF29371E), Color(0xFF943D35), Color(0xFFFFFFF8),
)
internal val ChromePalette = OvrlyPalette(
    true, Color(0xFF080910), Color(0xFF191C29), Color(0xFFEDEEF5),
    Color(0xFFBCC0D1), Color(0xFF414454), Color(0xFFC5B4FA),
    Color(0xFF231D35), Color(0xFFF0AFB6), Color(0xFFB0EFD2),
)
internal fun paletteFor(dark: Boolean) = if (dark) ChromePalette else PaperPalette
internal val LocalOvrlyPalette = staticCompositionLocalOf { PaperPalette }
internal val LocalWindowBlur = staticCompositionLocalOf { false }

internal val OvrlySans = FontFamily(Font(R.font.lexend))
internal val OvrlySerif = FontFamily(
    Font(R.font.instrument_serif),
    Font(R.font.instrument_serif_italic, style = FontStyle.Italic),
)

private fun sans(size: Int, height: Int, weight: FontWeight = FontWeight.Normal) =
    TextStyle(fontFamily = OvrlySans, fontSize = size.sp, lineHeight = height.sp, fontWeight = weight)
private fun serif(size: Int, height: Int) =
    TextStyle(fontFamily = OvrlySerif, fontSize = size.sp, lineHeight = height.sp)

private val OvrlyTypography = Typography(
    displayLarge = serif(56, 60), displayMedium = serif(48, 52), displaySmall = serif(40, 44),
    headlineLarge = serif(36, 40), headlineMedium = serif(32, 36), headlineSmall = serif(28, 32),
    titleLarge = serif(26, 32), titleMedium = sans(17, 24, FontWeight.Medium),
    titleSmall = sans(15, 22, FontWeight.Medium),
    bodyLarge = sans(16, 24), bodyMedium = sans(14, 22), bodySmall = sans(12, 18),
    labelLarge = sans(14, 20, FontWeight.Medium),
    labelMedium = sans(12, 18, FontWeight.Medium), labelSmall = sans(11, 16, FontWeight.Medium),
)

@Composable
fun OvrlyTheme(dark: Boolean = false, content: @Composable () -> Unit) {
    val p = paletteFor(dark)
    val base = if (dark) darkColorScheme() else lightColorScheme()
    CompositionLocalProvider(LocalOvrlyPalette provides p) {
        MaterialTheme(
            colorScheme = base.copy(
                primary = if (dark) p.accent else p.ink,
                onPrimary = if (dark) p.accentInk else p.surface,
                primaryContainer = p.accent, onPrimaryContainer = p.accentInk,
                secondary = if (dark) p.sheen else Color(0xFF466123),
                onSecondary = if (dark) p.accentInk else p.surface,
                secondaryContainer = if (dark) Color(0xFF303046) else Color(0xFFE1E6D2),
                onSecondaryContainer = p.ink,
                tertiary = p.sheen, onTertiary = p.accentInk,
                background = p.paper, onBackground = p.ink,
                surface = p.surface, onSurface = p.ink,
                surfaceVariant = if (dark) Color(0xFF242737) else Color(0xFFE5E7DA),
                onSurfaceVariant = p.muted,
                surfaceContainer = p.surface, surfaceContainerHigh = p.surface,
                surfaceContainerHighest = if (dark) Color(0xFF242737) else Color(0xFFE5E7DA),
                surfaceContainerLow = p.paper, surfaceContainerLowest = p.paper,
                surfaceBright = p.surface, surfaceDim = p.paper,
                outline = p.muted, outlineVariant = p.rule,
                error = p.error, onError = if (dark) Color(0xFF401920) else p.surface,
                errorContainer = if (dark) Color(0xFF44242B) else Color(0xFFF0D9D0),
                onErrorContainer = if (dark) Color(0xFFFFDADF) else Color(0xFF60271F),
                surfaceTint = Color.Transparent,
            ),
            typography = OvrlyTypography,
            shapes = Shapes(
                extraSmall = RoundedCornerShape(8.dp), small = RoundedCornerShape(12.dp),
                medium = RoundedCornerShape(16.dp), large = RoundedCornerShape(24.dp),
                extraLarge = RoundedCornerShape(28.dp),
            ),
            content = content,
        )
    }
}

@Composable
internal fun Wordmark(
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.headlineLarge,
) {
    val p = LocalOvrlyPalette.current
    Text(
        "ovrly.",
        modifier,
        style = style.copy(
            brush = Brush.linearGradient(
                if (p.dark) listOf(p.ink, p.accent, p.sheen, p.ink) else listOf(p.ink, p.ink),
            ),
            letterSpacing = (-1).sp,
        ),
    )
}

@Composable
internal fun ThemeChoice(dark: Boolean, onDark: (Boolean) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(selected = !dark, onClick = { onDark(false) }, label = { Text("Light") })
        FilterChip(selected = dark, onClick = { onDark(true) }, label = { Text("Dark") })
    }
}

/**
 * A soft diagonal highlight that sweeps across the content every few seconds, clipped to the
 * content's own pixels so only the chrome lights up. The sweep offset is read inside the draw
 * phase, so frames do not recompose; the animation pauses automatically when the composable is
 * off screen and collapses to a single static frame under the reduced-motion animator setting.
 */
internal fun Modifier.chromeGlare(strength: Float = 0.34f): Modifier = composed {
    val transition = rememberInfiniteTransition(label = "chromeGlare")
    val sweep by transition.animateFloat(
        initialValue = -0.6f, targetValue = 1.6f,
        animationSpec = infiniteRepeatable(
            animation = keyframes {
                durationMillis = 5200
                -0.6f at 0
                -0.6f at 3400 // rest between passes
                1.6f at 5200 using CubicBezierEasing(0.4f, 0f, 0.2f, 1f)
            },
        ),
        label = "sweep",
    )
    graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
        .drawWithCache {
            val band = size.width * 0.28f
            val glare = Brush.linearGradient(
                0f to Color.Transparent,
                0.5f to Color.White.copy(alpha = strength),
                1f to Color.Transparent,
                start = Offset(0f, size.height), end = Offset(band, 0f),
                tileMode = TileMode.Decal,
            )
            onDrawWithContent {
                drawContent()
                val x = sweep * size.width
                translate(left = x) {
                    drawRect(glare, topLeft = Offset(0f, 0f), size = Size(band, size.height), blendMode = BlendMode.SrcAtop)
                }
            }
        }
}
@Composable
internal fun Modifier.mockGlass(higherOpacity: Boolean = false): Modifier {
    val p = LocalOvrlyPalette.current
    val fill = p.glassFill(LocalWindowBlur.current, higherOpacity)
    val shape = RoundedCornerShape(28.dp)
    return clip(shape)
        .background(Brush.linearGradient(listOf(fill, p.paper.copy(alpha = fill.alpha), fill)))
        .border(
            BorderStroke(1.dp, Brush.linearGradient(listOf(
                p.sheen.copy(alpha = if (p.dark) 0.65f else 0.95f),
                p.rule, p.accent.copy(alpha = 0.65f),
            ))),
            shape,
        )
}
