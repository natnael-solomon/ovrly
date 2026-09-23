package app.ovrly.ui

import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ClipOp
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.unit.dp

internal fun Modifier.studySurface(
    design: StudyDesign,
    palette: StudyPalette,
    circular: Boolean = false,
): Modifier = drawWithCache {
    val radius = minOf(size.minDimension / 2f, (if (circular) 26f else design.radiusDp).dp.toPx())
    val faceted = design == StudyDesign.PRISM_EDGE && !circular
    fun contour(inset: Float): Path = Path().apply {
        val right = size.width - inset
        val bottom = size.height - inset
        if (faceted) {
            val cut = 12.dp.toPx()
            moveTo(inset + cut, inset)
            lineTo(right - cut, inset)
            lineTo(right, inset + cut)
            lineTo(right, bottom - cut)
            lineTo(right - cut, bottom)
            lineTo(inset + cut, bottom)
            lineTo(inset, bottom - cut)
            lineTo(inset, inset + cut)
            close()
        } else {
            addRoundRect(RoundRect(Rect(inset, inset, right, bottom),
                CornerRadius((radius - inset).coerceAtLeast(0f))))
        }
    }
    val outline = contour(0f)
    val edge = contour(0.5.dp.toPx())
    val bevel = contour(1.6.dp.toPx())
    val insetEdge = contour(3.2.dp.toPx())
    val innerFace = contour(4.8.dp.toPx())
    val softShadows = listOf(3f, 2f, 1f).map { spread ->
        contour(-spread.dp.toPx()).apply { translate(Offset(0f, 0.75.dp.toPx())) }
    }
    val topLip = Path().apply {
        moveTo(radius * 0.5f, radius * 0.5f)
        cubicTo(radius * 0.9f, 3.5.dp.toPx(),
            size.width - radius * 0.9f, 3.5.dp.toPx(),
            size.width - radius * 0.5f, radius * 0.5f)
    }
    val lowerLip = Path().apply {
        moveTo(radius * 0.55f, size.height - radius * 0.42f)
        cubicTo(radius, size.height - 3.dp.toPx(),
            size.width - radius, size.height - 3.dp.toPx(),
            size.width - radius * 0.55f, size.height - radius * 0.42f)
    }
    val cornerGlint = Path().apply {
        moveTo(4.dp.toPx(), radius + 4.dp.toPx())
        quadraticTo(4.dp.toPx(), 4.dp.toPx(), radius + 4.dp.toPx(), 4.dp.toPx())
    }
    val base = Brush.verticalGradient(listOf(
        palette.fill,
        palette.fill.copy(alpha = (palette.fill.alpha + 0.035f).coerceAtMost(1f)),
    ))
    val hairline = Brush.linearGradient(
        listOf(palette.ink.copy(alpha = 0.32f), palette.ink.copy(alpha = 0.05f)),
        end = Offset(size.width * 0.55f, size.height),
    )
    val lensRim = Brush.linearGradient(
        0f to Color.White.copy(alpha = 0.65f),
        0.30f to Color.White.copy(alpha = 0.18f),
        0.60f to Color.Transparent,
        1f to palette.ink.copy(alpha = 0.20f),
        end = Offset(size.width * 0.7f, size.height),
    )
    val lensInner = Brush.verticalGradient(
        listOf(Color.Black.copy(alpha = 0.12f), Color.Transparent, Color.White.copy(alpha = 0.20f)),
    )
    val veilSheen = Brush.verticalGradient(
        listOf(Color.White.copy(alpha = 0.06f), Color.Transparent),
    )
    val satinSheen = Brush.linearGradient(
        0f to Color.Transparent,
        0.4f to Color.White.copy(alpha = 0.035f),
        0.75f to Color.Transparent,
        end = Offset(size.width * 0.7f, size.height),
    )
    val diffuseBevel = Brush.verticalGradient(
        listOf(Color.White.copy(alpha = 0.36f), Color.Transparent, Color.Black.copy(alpha = 0.11f)),
    )
    onDrawBehind {
        if (design == StudyDesign.DOMED_GLASS || design == StudyDesign.CUSHION_GLASS) {
            // The outermost shadow reaches only 3.75 dp into the shared 4 dp allowance.
            clipPath(outline, clipOp = ClipOp.Difference) {
                softShadows.forEach { path -> drawPath(path, Color.Black.copy(alpha = 0.035f)) }
            }
        }
        drawPath(outline, base)
        when (design) {
            StudyDesign.CLEAR_FLOAT -> drawPath(edge, hairline, style = Stroke(0.6.dp.toPx()))
            StudyDesign.HAIRLINE_HALO -> {
                drawPath(edge, hairline, style = Stroke(0.5.dp.toPx()))
                drawPath(insetEdge, palette.ink.copy(alpha = 0.12f), style = Stroke(0.45.dp.toPx()))
            }
            StudyDesign.RIMLESS_AIR -> drawPath(outline, satinSheen)
            StudyDesign.FLOATING_RAIL -> if (circular) {
                drawPath(lowerLip, palette.ink.copy(alpha = 0.30f),
                    style = Stroke(1.4.dp.toPx(), cap = StrokeCap.Round))
            } else {
                drawLine(
                    palette.ink.copy(alpha = 0.30f),
                    Offset(radius, size.height - 2.dp.toPx()),
                    Offset(size.width - radius, size.height - 2.dp.toPx()),
                    1.4.dp.toPx(), StrokeCap.Round,
                )
            }
            StudyDesign.LIQUID_LENS -> {
                drawPath(bevel, lensRim, style = Stroke(2.1.dp.toPx()))
                drawPath(insetEdge, lensInner, style = Stroke(0.65.dp.toPx()))
            }
            StudyDesign.DOMED_GLASS -> {
                drawPath(edge, hairline, style = Stroke(0.7.dp.toPx()))
                drawPath(topLip, Color.White.copy(alpha = 0.13f),
                    style = Stroke(4.dp.toPx(), cap = StrokeCap.Round))
                drawPath(topLip, Color.White.copy(alpha = 0.78f),
                    style = Stroke(1.7.dp.toPx(), cap = StrokeCap.Round))
                drawPath(lowerLip, Color.White.copy(alpha = 0.32f),
                    style = Stroke(0.8.dp.toPx(), cap = StrokeCap.Round))
            }
            StudyDesign.PRISM_EDGE -> {
                drawPath(edge, lensRim, style = Stroke(1.25.dp.toPx()))
                drawPath(insetEdge, hairline, style = Stroke(0.45.dp.toPx()))
            }
            StudyDesign.CUSHION_GLASS -> {
                drawPath(bevel, diffuseBevel, style = Stroke(3.dp.toPx()))
                drawPath(innerFace, lensInner, style = Stroke(0.8.dp.toPx()))
            }
            StudyDesign.FROSTED_VEIL -> drawPath(outline, veilSheen)
            StudyDesign.SATIN_CAPSULE -> {
                drawPath(outline, satinSheen)
                drawPath(lowerLip, palette.ink.copy(alpha = 0.14f),
                    style = Stroke(0.6.dp.toPx(), cap = StrokeCap.Round))
            }
            StudyDesign.SMOKED_GLASS -> {
                drawPath(edge, lensInner, style = Stroke(0.7.dp.toPx()))
                drawPath(topLip, Color.White.copy(alpha = 0.23f),
                    style = Stroke(0.65.dp.toPx(), cap = StrokeCap.Round))
            }
            StudyDesign.MILK_GLASS -> {
                drawPath(outline, veilSheen)
                drawPath(lowerLip, Color(0xFF7E8175).copy(alpha = 0.22f),
                    style = Stroke(0.65.dp.toPx(), cap = StrokeCap.Round))
            }
            StudyDesign.SOFT_SQUIRCLE -> {
                drawPath(edge, hairline, style = Stroke(0.55.dp.toPx()))
                drawPath(cornerGlint, Color.White.copy(alpha = 0.36f),
                    style = Stroke(1.1.dp.toPx(), cap = StrokeCap.Round))
            }
            StudyDesign.SPLIT_PEBBLE -> drawPath(edge, hairline, style = Stroke(0.5.dp.toPx()))
            StudyDesign.OPTICAL_RIM -> Unit
        }
    }
}
