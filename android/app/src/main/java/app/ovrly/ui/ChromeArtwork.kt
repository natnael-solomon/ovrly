package app.ovrly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.semantics.clearAndSetSemantics
import kotlin.math.PI
import kotlin.math.sin

@Composable
internal fun ChromeArtwork(variant: Int, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    val backdrop = if (p.dark) Color(0xFF10121D) else Color(0xFFE0E3D6)
    Box(modifier.clearAndSetSemantics {}.background(backdrop).drawWithCache {
        val w = size.width
        val h = size.height
        val unit = minOf(w, h)
        val steel = Brush.linearGradient(
            0f to Color(0xFF35384F),
            0.16f to Color(0xFFD3C9ED),
            0.25f to Color(0xFF727D99),
            0.38f to Color(0xFFE2EEE9),
            0.47f to Color(0xFF525F69),
            0.62f to Color(0xFFBFC1E1),
            0.73f to Color(0xFFF1E6F8),
            0.82f to Color(0xFF69648C),
            1f to Color(0xFFACE0CE),
            start = Offset(w * 0.2f, 0f), end = Offset(w * 0.85f, h),
        )
        val folds = (0..5).map { index ->
            Path().apply {
                val y = h * 0.25f + index * h * 0.085f
                moveTo(-w * 0.1f, y + h * 0.4f)
                cubicTo(w * 0.22f, y - h * 0.65f, w * 0.55f, y + h * 0.7f, w * 1.1f, y - h * 0.25f)
            }
        }
        onDrawBehind {
            drawRect(Brush.radialGradient(
                listOf(p.accent.copy(alpha = if (p.dark) 0.16f else 0.12f), Color.Transparent),
                center = Offset(w * 0.68f, h * 0.32f), radius = w * 0.7f,
            ))
            for (i in 1..5) {
                val x = w * i / 6
                drawLine(p.muted.copy(alpha = 0.08f), Offset(x, 0f), Offset(x, h), 1f)
            }
            when (variant) {
                0 -> {
                    rotate(-28f) {
                        repeat(3) { i ->
                            val x = w * 0.18f + unit * i * 0.14f
                            val y = h * 0.16f + unit * i * 0.055f
                            val ellipse = Size(unit * 0.65f, unit * 0.65f)
                            drawOval(Color(0xFF060914).copy(alpha = 0.55f),
                                Offset(x + 3f, y + 8f), ellipse, style = Stroke(unit * 0.11f))
                            drawOval(steel, Offset(x, y), ellipse, style = Stroke(unit * 0.105f))
                            drawOval(Color(0xFFEBF8F1).copy(alpha = 0.7f),
                                Offset(x - unit * 0.045f, y - unit * 0.045f),
                                Size(ellipse.width + unit * 0.09f, ellipse.height + unit * 0.09f),
                                style = Stroke(1.2f))
                        }
                    }
                }
                1 -> {
                    rotate(-18f) {
                        repeat(7) { i ->
                            val x = w * 0.19f + w * 0.09f * i
                            val y = h * 0.27f - sin(i * PI / 7).toFloat() * h * 0.09f
                            drawRoundRect(steel, Offset(x, y), Size(w * 0.065f, h * 0.6f),
                                cornerRadius = androidx.compose.ui.geometry.CornerRadius(w * 0.025f))
                            drawLine(Color(0xFFF1E6F8).copy(alpha = 0.7f),
                                Offset(x + 1f, y + h * 0.04f), Offset(x + 1f, y + h * 0.55f), 1f)
                        }
                    }
                }
                2 -> folds.forEach { path ->
                    drawPath(path, steel, style = Stroke(unit * 0.06f, cap = StrokeCap.Round))
                    drawPath(path, p.sheen.copy(alpha = 0.36f), style = Stroke(1f))
                }
                else -> {
                    repeat(21) { i ->
                        val x = w * (0.12f + i * 0.038f)
                        val bar = (0.15f + 0.55f * sin(i * PI / 20).toFloat()) * h
                        drawLine(steel, Offset(x, (h - bar) / 2), Offset(x, (h + bar) / 2),
                            w * 0.02f, cap = StrokeCap.Round)
                    }
                }
            }
            val tick = unit * 0.02f
            val corner = Offset(w * 0.09f, h * 0.12f)
            drawLine(p.muted.copy(alpha = 0.65f), corner - Offset(tick, 0f), corner + Offset(tick, 0f), 1f)
            drawLine(p.muted.copy(alpha = 0.65f), corner - Offset(0f, tick), corner + Offset(0f, tick), 1f)
        }
    })
}
