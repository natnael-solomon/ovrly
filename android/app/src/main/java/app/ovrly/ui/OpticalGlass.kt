package app.ovrly.ui

import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ClipOp
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.unit.dp

internal fun Modifier.opticalGlass(higherOpacity: Boolean): Modifier = drawWithCache {
    val radius = minOf(size.width / 2f, size.height / 2f, 26.dp.toPx())
    val faceInset = 4.25.dp.toPx()
    fun contour(inset: Float) = RoundRect(
        Rect(inset, inset, size.width - inset, size.height - inset),
        CornerRadius((radius - inset).coerceAtLeast(0f)),
    )
    val outline = Path().apply { addRoundRect(contour(0f)) }
    val face = Path().apply { addRoundRect(contour(faceInset)) }
    val rim = Path().apply {
        fillType = PathFillType.EvenOdd
        addRoundRect(contour(0f))
        addRoundRect(contour(faceInset))
    }
    val bodyFill = Brush.verticalGradient(OverlayAppearance.fill(higherOpacity))
    val rimFill = if (higherOpacity) bodyFill else Brush.verticalGradient(OverlayAppearance.rimFill)
    val edgeInk = Color(0xFF52645F)
    val lightDirection = Offset(size.width * 0.65f, size.height)
    val outerContour = Brush.linearGradient(
        0f to edgeInk.copy(alpha = 0.36f),
        0.40f to edgeInk.copy(alpha = 0.13f),
        1f to edgeInk.copy(alpha = 0.44f),
        end = lightDirection,
    )
    val bevel = Brush.linearGradient(
        0f to Color.White.copy(alpha = 0.84f),
        0.28f to Color.White.copy(alpha = 0.44f),
        0.55f to Color.White.copy(alpha = 0.08f),
        0.80f to edgeInk.copy(alpha = 0.08f),
        1f to edgeInk.copy(alpha = 0.26f),
        end = lightDirection,
    )
    val innerContour = Brush.linearGradient(
        0f to edgeInk.copy(alpha = 0.28f),
        0.46f to edgeInk.copy(alpha = 0.09f),
        0.72f to Color.White.copy(alpha = 0.18f),
        1f to Color.White.copy(alpha = 0.62f),
        end = lightDirection,
    )
    val faceHighlight = Brush.verticalGradient(
        0f to Color.White.copy(alpha = 0.56f),
        0.32f to Color.White.copy(alpha = 0.10f),
        0.64f to Color.Transparent,
        1f to Color.White.copy(alpha = 0.16f),
    )
    val shadowSpreads = listOf(3.dp.toPx(), 2.2.dp.toPx(), 1.4.dp.toPx(), 0.6.dp.toPx())
    val shadowAlpha = listOf(0.025f, 0.035f, 0.04f, 0.04f)
    val shadowOffset = 0.75.dp.toPx()
    val outerInset = 0.45.dp.toPx()
    val bevelInset = 1.55.dp.toPx()
    val innerInset = 3.35.dp.toPx()
    val highlightInset = 4.45.dp.toPx()
    val outerStroke = Stroke(0.65.dp.toPx())
    val bevelStroke = Stroke(1.5.dp.toPx())
    val innerStroke = Stroke(0.65.dp.toPx())
    val highlightStroke = Stroke(0.65.dp.toPx())

    onDrawBehind {
        // Keep the contact shadow within the existing 4 dp allowance and out of the clear face.
        clipPath(outline, clipOp = ClipOp.Difference) {
            shadowSpreads.forEachIndexed { index, spread ->
                drawRoundRect(
                    color = edgeInk.copy(alpha = shadowAlpha[index]),
                    topLeft = Offset(-spread, shadowOffset - spread),
                    size = Size(size.width + spread * 2f, size.height + spread * 2f),
                    cornerRadius = CornerRadius(radius + spread),
                )
            }
        }
        drawPath(rim, rimFill)
        drawPath(face, bodyFill)
        fun wall(inset: Float, brush: Brush, stroke: Stroke) {
            drawRoundRect(
                brush = brush,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2f, size.height - inset * 2f),
                cornerRadius = CornerRadius((radius - inset).coerceAtLeast(0f)),
                style = stroke,
            )
        }
        wall(outerInset, outerContour, outerStroke)
        wall(bevelInset, bevel, bevelStroke)
        wall(innerInset, innerContour, innerStroke)
        wall(highlightInset, faceHighlight, highlightStroke)
    }
}
