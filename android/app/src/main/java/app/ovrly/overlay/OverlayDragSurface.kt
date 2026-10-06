package app.ovrly.overlay

import android.annotation.SuppressLint
import android.content.Context
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.widget.FrameLayout
import kotlin.math.abs
import kotlin.math.roundToInt

/** What the overlay window does with touches on its surface. */
internal interface DragCallbacks {
    /** True when a touch starting at window [y] may move the window. */
    fun canDrag(y: Float): Boolean

    /** True when holding still starts a long-press drag (the idle bubble's dismiss drag). */
    fun longPressDrag(): Boolean = false

    fun currentPosition(): Pair<Int, Int>

    fun onMove(x: Int, y: Int)

    /** Every touch down, so idle timers restart and an automatic expand stays open. */
    fun onTouch() = Unit

    fun onLongPress() = Unit

    /** A drag ended; [longPressed] when it started with a long press. */
    fun onRelease(longPressed: Boolean) = Unit
}

/**
 * Moves the overlay window. A drag starts once the touch moves past the touch slop, so taps
 * still reach the Compose children; with [DragCallbacks.longPressDrag], holding still for the
 * long-press timeout starts a drag too and the child never sees the tap.
 */
// Constructed only by the service. Compose children own taps and accessibility actions.
@SuppressLint("ViewConstructor", "ClickableViewAccessibility")
internal class OverlayDragSurface(context: Context, private val callbacks: DragCallbacks) :
    FrameLayout(context) {
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private val longPressMs = ViewConfiguration.getLongPressTimeout().toLong()
    private var downX = 0f
    private var downY = 0f
    private var origin = 0 to 0
    private var dragging = false
    private var dragAllowed = false
    private var longPressed = false
    private val longPress = Runnable {
        longPressed = true
        dragging = true
        callbacks.onLongPress()
    }

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        var intercept = false
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.rawX
                downY = event.rawY
                origin = callbacks.currentPosition()
                dragging = false
                longPressed = false
                dragAllowed = callbacks.canDrag(event.y)
                callbacks.onTouch()
                if (dragAllowed && callbacks.longPressDrag()) postDelayed(longPress, longPressMs)
            }

            MotionEvent.ACTION_MOVE -> if (dragAllowed) {
                if (moved(event)) removeCallbacks(longPress)
                dragging = dragging || moved(event)
                intercept = dragging
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                removeCallbacks(longPress)
                // After a long press the child must not see the release as a tap.
                if (longPressed) {
                    callbacks.onRelease(longPressed = true)
                    intercept = true
                }
                longPressed = false
                dragging = false
            }
        }
        return intercept
    }
    override fun onTouchEvent(event: MotionEvent): Boolean {
        // Non-clickable header space must own DOWN to receive the rest of a drag.
        if (!dragAllowed) return super.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> callbacks.onTouch()

            MotionEvent.ACTION_MOVE -> {
                if (moved(event)) removeCallbacks(longPress)
                dragging = dragging || moved(event)
                if (dragging) {
                    callbacks.onMove(
                        origin.first + (event.rawX - downX).roundToInt(),
                        origin.second + (event.rawY - downY).roundToInt()
                    )
                }
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                removeCallbacks(longPress)
                if (dragging) callbacks.onRelease(longPressed)
                dragging = false
                dragAllowed = false
                longPressed = false
            }
        }
        return true
    }

    private fun moved(event: MotionEvent): Boolean =
        abs(event.rawX - downX) > slop || abs(event.rawY - downY) > slop
}
