package app.ovrly.overlay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import androidx.compose.ui.graphics.toArgb
import app.ovrly.R
import app.ovrly.ui.paletteFor
import kotlin.math.roundToInt

/**
 * The drop target shown while the idle bubble is long-press dragged: a round "X" near the
 * bottom of the screen. It never takes touches; the service asks [contains] on release.
 */
internal class DismissTarget(private val context: Context, private val manager: WindowManager) {
    private val density = context.resources.displayMetrics.density
    private val size = (TARGET_DP * density).roundToInt()
    private val bottom = (BOTTOM_DP * density).roundToInt()
    private var view: FrameLayout? = null

    // Same physical coordinates as the overlay window, independent of text direction.
    @SuppressLint("RtlHardcoded")
    private val params = WindowManager.LayoutParams(
        size,
        size,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
        PixelFormat.TRANSLUCENT
    ).apply {
        gravity = Gravity.TOP or Gravity.LEFT
        title = "ovrly dismiss target"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            setFitInsetsTypes(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
        }
    }

    fun show(dark: Boolean, availableWidth: Int, availableHeight: Int) {
        if (view != null) return
        val p = paletteFor(dark)
        params.x = (availableWidth - size) / 2
        params.y = availableHeight - bottom - size
        val frame = FrameLayout(context).apply {
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(p.surface.copy(alpha = 0.92f).toArgb())
                setStroke((1 * density).roundToInt(), p.rule.toArgb())
            }
            contentDescription = "Drop here to dismiss the overlay"
            addView(
                ImageView(context).apply {
                    setImageResource(R.drawable.ic_overlay_dismiss)
                    setColorFilter(p.ink.toArgb())
                },
                FrameLayout.LayoutParams(size / 2, size / 2, Gravity.CENTER)
            )
        }
        manager.addView(frame, params)
        view = frame
    }

    /** True when a bubble centred at window ([x], [y]) is over the target. */
    fun contains(x: Int, y: Int): Boolean = view != null &&
        overDismissTarget(x, y, params.x + size / 2, params.y + size / 2, size)

    fun highlight(active: Boolean) {
        view?.animate()?.scaleX(if (active) ACTIVE_SCALE else 1f)
            ?.scaleY(if (active) ACTIVE_SCALE else 1f)
            ?.setDuration(HIGHLIGHT_MS)
    }

    fun hide() {
        view?.let { runCatching { manager.removeView(it) } }
        view = null
    }

    private companion object {
        const val TARGET_DP = 64
        const val BOTTOM_DP = 48
        const val ACTIVE_SCALE = 1.2f
        const val HIGHLIGHT_MS = 120L
    }
}
