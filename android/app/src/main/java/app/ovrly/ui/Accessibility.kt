package app.ovrly.ui

import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import app.ovrly.capture.CaptureLimits
import app.ovrly.contract.Interval
import app.ovrly.contract.Timebase
import java.util.concurrent.TimeUnit

/*
 * Small TalkBack helpers for screens outside the overlay (AN-11, #39). Spoken text replaces
 * compact visual text such as "REC 0:12 / 3:00" that TalkBack would read as a time of day.
 */

/** Gives the node [description] for TalkBack, or leaves it unchanged when null. */
internal fun Modifier.spokenAs(description: String?): Modifier =
    if (description == null) this else semantics { contentDescription = description }

/** What TalkBack reads for the in-app recording timer. */
internal fun recordingDescription(seconds: Long): String =
    "Recording, ${spokenClock(TimeUnit.SECONDS.toMillis(seconds))} " +
        "of ${spokenClock(CaptureLimits.LIVE_MS)}"

/** A millisecond offset in words for TalkBack, such as "1 minute 5 seconds". */
internal fun spokenClock(ms: Long): String {
    val seconds = TimeUnit.MILLISECONDS.toSeconds(ms)
    val minutes = TimeUnit.SECONDS.toMinutes(seconds)
    val rest = seconds - TimeUnit.MINUTES.toSeconds(minutes)
    val secondsText = if (rest == 1L) "1 second" else "$rest seconds"
    val minutesText = if (minutes == 1L) "1 minute" else "$minutes minutes"
    return when {
        minutes == 0L -> secondsText
        rest == 0L -> minutesText
        else -> "$minutesText $secondsText"
    }
}

/** [label] in words, so TalkBack does not read "0:12" as a time of day. */
internal fun Interval.spokenLabel(): String {
    val range = "from ${spokenClock(startMs)} to ${spokenClock(endMs)}"
    return when (timebase) {
        Timebase.MEDIA -> "$range in the video"
        Timebase.CAPTURE -> "$range after capture started, not a time in the original video"
        Timebase.UNKNOWN -> "$range, timeline $NOT_RECOGNISED"
    }
}
