package app.ovrly.ui

import app.ovrly.data.ApiFailure

/** Everything the Inbox and Library show. */
internal data class ChecksUiState(
    /** False when this build has no usable service address; the sections say so. */
    val available: Boolean = true,
    val loaded: Boolean = false,
    val inbox: List<InboxItem> = emptyList(),
    val library: List<InboxItem> = emptyList(),
    val offline: Boolean = false,
    val updateNeeded: Boolean = false,
    val waking: Boolean = false,
    val notice: String? = null,
    /** Local ids with an action in flight; their buttons are disabled. */
    val busy: Set<String> = emptySet(),
    /** The owner's explicitly saved reports, as last read from the service (#36). */
    val saved: List<SavedItem> = emptyList(),
    /** Guest identity and the optional account link (#36). */
    val account: AccountUiState = AccountUiState()
)

/** What the account section shows (#36). */
internal data class AccountUiState(
    /** False when this build has no way to get a Google ID token. */
    val available: Boolean = false,
    val linked: Boolean = false,
    val busy: Boolean = false,
    val notice: String? = null
)

/** One published version in the picker. */
internal data class VersionChoice(val version: Int, val label: String, val fixture: Boolean)

/** The open report of a real check. */
internal data class OpenReport(
    val view: ReportView,
    val versions: List<VersionChoice> = emptyList(),
    /** Why the version list is missing, for example while offline. */
    val versionsNote: String? = null,
    /** The version the shown one is compared with in claim detail, if loaded. */
    val comparedWith: Int? = null,
    /** Per claim id: what changed since [comparedWith]. */
    val changes: Map<String, List<String>> = emptyMap(),
    /** Later shared checks that may be this captured clip's full video. */
    val candidates: List<InboxItem> = emptyList(),
    val busy: Boolean = false,
    val notice: String? = null,
    /** When this device last read the shown version from ovrly (epoch millis), if known. */
    val retrievedAt: Long? = null,
    /** Whether the shown version is saved (#36); null when there is nothing to save. */
    val save: SaveState? = null
)

/** What Your space needs from the checks store; null in previews without a service. */
internal data class ChecksShell(
    val state: ChecksUiState,
    val report: OpenReport?,
    val onCommand: (CheckCommand) -> Unit
)

/** Every user action on the Inbox, Library and Report screens. */
internal sealed interface CheckCommand {
    data class Open(val serverId: String) : CheckCommand

    data object Close : CheckCommand

    data class Cancel(val localId: String) : CheckCommand

    data class Retry(val localId: String) : CheckCommand

    data class Continue(val localId: String) : CheckCommand

    data class ShowVersion(val version: Int) : CheckCommand

    data class Correct(val claimId: String, val proposition: String) : CheckCommand

    /** Sent only after the user confirmed that [fullVideoId] is the captured clip's video. */
    data class Expand(val fullVideoId: String) : CheckCommand

    data object DismissNotice : CheckCommand

    /** Explicitly saves the shown version [reportId] for its owner (#36). */
    data class Save(val reportId: String) : CheckCommand

    /** Removes the owner's save of [reportId]; the report itself does not change. */
    data class Unsave(val reportId: String) : CheckCommand

    /** Opens a saved report: its check when it is on this device, otherwise the saved copy. */
    data class OpenSaved(val reportId: String) : CheckCommand

    /** Starts the optional Google account link (BC-D07); never needed for checking. */
    data object LinkAccount : CheckCommand
}

/** Plain-language text for a failed call; never shown as a result. */
internal fun failureText(failure: ApiFailure): String = when (failure) {
    is ApiFailure.Network -> "You are offline or ovrly could not be reached. Try again later."
    is ApiFailure.Incompatible -> "Update needed: this app cannot read the service's answer."
    is ApiFailure.Server -> "${failure.error.message} (${failure.error.code})"
}
