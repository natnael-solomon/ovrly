package app.ovrly.ui

import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import app.ovrly.capture.CaptureLimits
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
