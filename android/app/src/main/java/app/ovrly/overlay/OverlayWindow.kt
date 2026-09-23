package app.ovrly.overlay

import android.app.Dialog
import android.content.Context
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.View
import android.view.Window
import android.view.WindowManager
import androidx.annotation.RequiresApi
import androidx.compose.ui.graphics.toArgb
import app.ovrly.R
import app.ovrly.ui.paletteFor
import java.util.function.Consumer

internal enum class BlurMode(val label: String) {
    NATIVE("Native background blur"),
    FALLBACK("Solid glass / blur unavailable"),
    OPAQUE("Solid glass / higher opacity"),
}

internal fun blurMode(sdk: Int, systemEnabled: Boolean, higherOpacity: Boolean): BlurMode = when {
    higherOpacity -> BlurMode.OPAQUE
    sdk >= 31 && systemEnabled -> BlurMode.NATIVE
    else -> BlurMode.FALLBACK
}

/**
 * A public Window/DecorView is necessary for localized cross-window background blur.
 * A raw WindowManager.addView or Compose RenderEffect cannot provide this effect.
 */
internal class OverlayWindow(
    private val context: Context,
    private val manager: WindowManager,
    private val params: WindowManager.LayoutParams,
    private val onBlurMode: (BlurMode) -> Unit,
) {
    private val dialog = Dialog(context, R.style.Theme_Ovrly_Overlay)
    private val window: Window = requireNotNull(dialog.window)
    private var listener: Consumer<Boolean>? = null
    private var dark = false
    private var higherOpacity = false
    private var systemBlur = false

    fun show(content: View) {
        window.attributes = params
        dialog.setCancelable(false)
        dialog.setCanceledOnTouchOutside(false)
        dialog.setContentView(content)
        updateMaterial()
        dialog.show()
        window.setLayout(params.width, params.height)
        window.decorView.setPadding(0, 0, 0, 0)
        if (Build.VERSION.SDK_INT >= 31) observeBlur()
    }

    @RequiresApi(31)
    private fun observeBlur() {
        systemBlur = manager.isCrossWindowBlurEnabled
        val observer = Consumer<Boolean> {
            systemBlur = it
            updateMaterial()
        }
        listener = observer
        manager.addCrossWindowBlurEnabledListener(context.mainExecutor, observer)
        updateMaterial()
    }

    fun appearance(dark: Boolean, higherOpacity: Boolean) {
        this.dark = dark
        this.higherOpacity = higherOpacity
        updateMaterial()
    }

    private fun updateMaterial() {
        val mode = blurMode(Build.VERSION.SDK_INT, systemBlur, higherOpacity)
        val p = paletteFor(dark)
        window.setBackgroundDrawable(GradientDrawable().apply {
            cornerRadius = 28f * context.resources.displayMetrics.density
            setColor(p.surface.copy(alpha = if (mode == BlurMode.NATIVE) 0.08f else 1f).toArgb())
        })
        if (Build.VERSION.SDK_INT >= 31) {
            // Deliberately no FLAG_BLUR_BEHIND: the video outside the panel stays sharp.
            window.setBackgroundBlurRadius(
                if (mode == BlurMode.NATIVE) (24 * context.resources.displayMetrics.density).toInt().coerceAtMost(100) else 0,
            )
        }
        onBlurMode(mode)
    }

    fun layout(width: Int, height: Int, x: Int, y: Int) {
        val current = window.attributes
        if (current.width == width && current.height == height && current.x == x && current.y == y) return
        window.attributes = current.apply {
            this.width = width
            this.height = height
            this.x = x
            this.y = y
        }
    }

    fun close() {
        if (Build.VERSION.SDK_INT >= 31) listener?.let(manager::removeCrossWindowBlurEnabledListener)
        listener = null
        dialog.dismiss()
    }
}
