package app.ovrly.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

internal enum class StudyBackdrop(val title: String, val dark: Boolean) {
    VIDEO("Dark video", true),
    READING("Light reading", false),
    CONTRAST("Mixed contrast", true),
}

@Composable
internal fun GalleryBackdrop(
    backdrop: StudyBackdrop,
    compact: Boolean,
    modifier: Modifier = Modifier,
    onMockBackgroundTap: (() -> Unit)? = null,
    minimal: Boolean = false,
    content: @Composable () -> Unit,
) {
    val sceneryInk = if (backdrop.dark) Color(0xFF92968C) else Color(0xFF6B6F62)
    val tapModifier = if (onMockBackgroundTap != null) {
        Modifier.clickable(
            onClickLabel = "Count a mock background tap",
            onClick = onMockBackgroundTap,
        )
    } else {
        Modifier
    }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = if (minimal) 76.dp else if (compact) 184.dp else 288.dp)
            .clip(RoundedCornerShape(if (minimal) 12.dp else 20.dp))
            .drawWithCache {
                val videoSky = Brush.verticalGradient(
                    listOf(Color(0xFF172B2D), Color(0xFF46605A), Color(0xFF213D39)),
                )
                val distantShore = Path().apply {
                    moveTo(0f, size.height * 0.52f)
                    cubicTo(
                        size.width * 0.23f, size.height * 0.3f,
                        size.width * 0.3f, size.height * 0.6f,
                        size.width * 0.61f, size.height * 0.47f,
                    )
                    cubicTo(
                        size.width * 0.85f, size.height * 0.32f,
                        size.width * 0.86f, size.height * 0.51f,
                        size.width, size.height * 0.38f,
                    )
                    lineTo(size.width, size.height)
                    lineTo(0f, size.height)
                    close()
                }
                val nearShore = Path().apply {
                    moveTo(0f, size.height * 0.75f)
                    cubicTo(
                        size.width * 0.34f, size.height * 0.54f,
                        size.width * 0.59f, size.height * 0.91f,
                        size.width, size.height * 0.64f,
                    )
                    lineTo(size.width, size.height)
                    lineTo(0f, size.height)
                    close()
                }
                onDrawBehind {
                    when (backdrop) {
                        StudyBackdrop.VIDEO -> {
                            drawRect(videoSky)
                            drawCircle(
                                color = Color(0xFF8A906C),
                                radius = size.width * 0.074f,
                                center = Offset(size.width * 0.76f, size.height * 0.25f),
                            )
                            drawPath(distantShore, Color(0xFF3B5147))
                            drawPath(nearShore, Color(0xFF213B34))
                            repeat(7) { index ->
                                val y = size.height * (0.62f + index * 0.024f)
                                drawLine(
                                    color = Color(0xFF5F7566),
                                    start = Offset(size.width * (0.62f + index * 0.015f), y),
                                    end = Offset(size.width * (0.88f - index * 0.017f), y),
                                    strokeWidth = 1.dp.toPx(),
                                )
                            }
                        }
                        StudyBackdrop.READING -> drawReadingPaper()
                        StudyBackdrop.CONTRAST -> drawContrastTiles()
                    }
                }
            }
            .then(tapModifier)
            .padding(
                horizontal = if (minimal) 8.dp else 12.dp,
                vertical = if (minimal) 8.dp else 16.dp,
            ),
        verticalArrangement = if (minimal) Arrangement.Center else Arrangement.SpaceBetween,
    ) {
        if (!minimal) {
            Text(
                text = when (backdrop) {
                    StudyBackdrop.VIDEO -> "SYNTHETIC VIDEO / STILL"
                    StudyBackdrop.READING -> "SYNTHETIC READING APP"
                    StudyBackdrop.CONTRAST -> "SYNTHETIC / CONTRAST STUDY"
                },
                color = sceneryInk,
                fontSize = 9.sp,
                lineHeight = 14.sp,
                letterSpacing = 1.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
        }
        // Nested overlay controls consume their own clicks; only unused scenery counts as a mock tap.
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = if (minimal) 0.dp else if (compact) 20.dp else 32.dp),
            contentAlignment = Alignment.Center,
        ) {
            content()
        }
        if (!minimal) {
            Column(
                modifier = Modifier.padding(horizontal = 4.dp).clearAndSetSemantics {},
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = when (backdrop) {
                        StudyBackdrop.VIDEO -> "A pause by the water"
                        StudyBackdrop.READING -> "Room for attention"
                        StudyBackdrop.CONTRAST -> "Light, shade, repeat."
                    },
                    color = sceneryInk,
                    fontFamily = if (backdrop == StudyBackdrop.READING) FontFamily.Serif else FontFamily.SansSerif,
                    fontSize = if (compact) 13.sp else 20.sp,
                    lineHeight = if (compact) 20.sp else 28.sp,
                    fontWeight = FontWeight.Medium,
                )
                if (!compact) {
                    Text(
                        text = when (backdrop) {
                            StudyBackdrop.VIDEO -> "An original landscape, drawn in Compose."
                            StudyBackdrop.READING -> "The page can wait. Notice the space between one thought and the next."
                            StudyBackdrop.CONTRAST -> "Original geometric tiles, not a screen capture."
                        },
                        color = sceneryInk,
                        fontSize = 11.sp,
                        lineHeight = 17.sp,
                    )
                }
            }
        }
    }
}

private fun DrawScope.drawReadingPaper() {
    drawRect(Color(0xFFEEECE3))
    drawRect(
        color = Color(0xFFE3E2D7),
        topLeft = Offset(size.width * 0.86f, 0f),
        size = Size(size.width * 0.14f, size.height),
    )
    drawLine(
        color = Color(0xFFC8CDBE),
        start = Offset(16.dp.toPx(), size.height * 0.3f),
        end = Offset(size.width * 0.8f, size.height * 0.3f),
        strokeWidth = 1.dp.toPx(),
    )
    repeat(5) { index ->
        val y = size.height * (0.4f + index * 0.058f)
        drawLine(
            color = Color(0xFFD6D8CB),
            start = Offset(16.dp.toPx(), y),
            end = Offset(size.width * if (index == 4) 0.57f else 0.79f, y),
            strokeWidth = 2.dp.toPx(),
        )
    }
}

private fun DrawScope.drawContrastTiles() {
    drawRect(Color(0xFF242B2C))
    val tileSize = size.width / 5f
    val colors = listOf(
        Color(0xFF34494A),
        Color(0xFF858E81),
        Color(0xFF3F4A41),
        Color(0xFF92968A),
        Color(0xFF1F3437),
    )
    val rows = (size.height / tileSize).toInt() + 1
    repeat(rows) { row ->
        repeat(5) { column ->
            drawRect(
                color = colors[(column + row * 2) % colors.size],
                topLeft = Offset(column * tileSize, row * tileSize),
                size = Size(tileSize, tileSize),
            )
        }
    }
    drawRect(Color(0xFF182524).copy(alpha = 0.48f))
}
