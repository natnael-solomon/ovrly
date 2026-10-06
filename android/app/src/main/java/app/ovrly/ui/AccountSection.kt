package app.ovrly.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import app.ovrly.data.ApiErrorCode
import app.ovrly.data.ApiFailure
import app.ovrly.data.LinkOutcome
import app.ovrly.data.SIGN_IN_UNAVAILABLE

/*
 * Guest identity and the optional account (AN-10, #36; BC-D07). Checking never asks for
 * sign-in. The copy says what an account keeps and what it does not, and, per decision 0003,
 * that a saved report cannot yet be restored on another device.
 */

/** What the account section shows. */
internal data class AccountUiState(
    /** False when this build has no way to get a Google ID token. */
    val available: Boolean = false,
    val linked: Boolean = false,
    val busy: Boolean = false,
    val notice: String? = null
)

@Composable
internal fun AccountSection(
    state: AccountUiState,
    onCommand: (CheckCommand) -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(SMALL_GAP)) {
        Text(
            "Account",
            Modifier.semantics { heading() },
            style = MaterialTheme.typography.titleMedium
        )
        Text(
            if (state.linked) {
                "Linked to a Google account. Reports you save are kept for that account."
            } else {
                "Checking works without signing in. This device uses a private guest " +
                    "identity, kept encrypted on this device and never backed up."
            },
            style = MaterialTheme.typography.bodyMedium
        )
        if (!state.linked) {
            OutlinedButton(
                { onCommand(CheckCommand.LinkAccount) },
                enabled = state.available && !state.busy
            ) { Text("Sign in with Google") }
            if (!state.available) Muted(SIGN_IN_UNAVAILABLE)
        }
        state.notice?.let {
            Text(
                it,
                Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                style = MaterialTheme.typography.bodyMedium
            )
        }
        Muted(
            "An account keeps only the reports you save. Check history, captures, shared " +
                "files and checks still running stay with this device and are not recovered."
        )
        Muted(RECOVERY_DISCLOSURE)
    }
}

/** Plain-language result of a link attempt; null when there is nothing to say. */
internal fun linkNotice(outcome: LinkOutcome): String? = when (outcome) {
    LinkOutcome.Cancelled -> null

    is LinkOutcome.Unavailable -> outcome.reason

    is LinkOutcome.Linked -> buildString {
        if (outcome.switched) {
            val moved = outcome.merged
            append("Signed in to your existing account. ")
            append(if (moved == 1) "1 saved report" else "$moved saved reports")
            append(" from this device moved to it. This device's check history is not merged.")
        } else {
            append("Linked. Reports you save are kept for your Google account.")
        }
        if (!outcome.stored) {
            append(" The account could not be stored on this device; sign in again later.")
        }
    }

    is LinkOutcome.Failed -> linkFailureText(outcome.failure)
}

private fun linkFailureText(failure: ApiFailure): String {
    val code = (failure as? ApiFailure.Server)?.code
    return when (code) {
        ApiErrorCode.ACCOUNT_ALREADY_LINKED ->
            "This device is already linked to a different Google account."

        ApiErrorCode.INVALID_ID_TOKEN -> "Google sign-in could not be verified. Try again."

        ApiErrorCode.ACCOUNT_LINK_UNAVAILABLE ->
            "Accounts are not set up on the ovrly service. Checking still works."

        ApiErrorCode.INVALID_CREDENTIAL, ApiErrorCode.AUTHENTICATION_REQUIRED ->
            "This device's guest identity was refused. Nothing was linked; try again later."

        else -> failureText(failure)
    }
}
