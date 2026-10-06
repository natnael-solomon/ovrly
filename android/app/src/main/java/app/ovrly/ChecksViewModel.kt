package app.ovrly

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.ovrly.contract.Investigation
import app.ovrly.data.ApiFailure
import app.ovrly.data.ApiResult
import app.ovrly.data.ApiServices
import app.ovrly.data.ChecksService
import app.ovrly.data.ReanalysisKeys
import app.ovrly.data.ReanalysisRequest
import app.ovrly.data.StoredCheck
import app.ovrly.data.StoredSave
import app.ovrly.ui.CheckCommand
import app.ovrly.ui.ChecksUiState
import app.ovrly.ui.OpenReport
import app.ovrly.ui.OpenReportSession
import app.ovrly.ui.ReportLoader
import app.ovrly.ui.failureText
import app.ovrly.ui.fullVideoCandidates
import app.ovrly.ui.inboxItem
import app.ovrly.ui.linkNotice
import app.ovrly.ui.reportView
import app.ovrly.ui.saveState
import app.ovrly.ui.savedCopyView
import app.ovrly.ui.savedItems
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * State of the Inbox, Library and open report (#34). Everything shown is rebuilt from the
 * Room store after every server read, so nothing depends on this object surviving.
 */
internal class ChecksViewModel(application: Application) : AndroidViewModel(application) {
    private val services = ApiServices.get(application)
    private val checks = services?.let { ChecksService(it) }
    private val loader = services?.let { api ->
        checks?.let { ReportLoader(it, ReportLoader.staleFrom(api.jobs)) }
    }
    private val mutableState = MutableStateFlow(ChecksUiState(available = services != null))
    private val session = OpenReportSession { investigation, version, candidates ->
        loader?.load(investigation, version, candidates) ?: OpenReport(reportView(investigation))
    }
    private val refreshing = Mutex()
    private var stored: List<StoredCheck> = emptyList()
    private val saves get() = services?.saved
    private var storedSaves: List<StoredSave> = emptyList()

    /** Report id of a save or removal in flight; the open report's controls wait for it. */
    private var saving: String? = null
    private val keys = ReanalysisKeys { checks?.newKey().orEmpty() }

    val state: StateFlow<ChecksUiState> = mutableState.asStateFlow()
    val report: StateFlow<OpenReport?> = session.report

    /**
     * Reads the server while the app is visible: every [ACTIVE_POLL_MILLIS] while a check is
     * still running, otherwise every [IDLE_POLL_MILLIS]. Call from a lifecycle-bound scope.
     */
    suspend fun pollWhileVisible() {
        val service = checks ?: return
        coroutineScope {
            // The cold-start hint changes while a call is in flight, so it is observed, not read.
            launch {
                services?.api?.waking?.collect { waking ->
                    mutableState.update { it.copy(waking = waking) }
                }
            }
            while (true) {
                reload()
                val failure = service.sync()
                saves?.sync()
                mutableState.update {
                    it.copy(
                        offline = failure is ApiFailure.Network,
                        updateNeeded = failure is ApiFailure.Incompatible
                    )
                }
                reload()
                val active = mutableState.value.inbox.any { it.stage != null }
                delay(if (active) ACTIVE_POLL_MILLIS else IDLE_POLL_MILLIS)
            }
        }
    }

    fun on(command: CheckCommand) {
        when (command) {
            is CheckCommand.Open -> {
                session.open(command.serverId)
                loader?.invalidate()
                viewModelScope.launch { reload() }
            }

            CheckCommand.Close -> session.close()

            is CheckCommand.ShowVersion -> {
                session.show(command.version)
                viewModelScope.launch { reload() }
            }

            CheckCommand.DismissNotice -> {
                mutableState.update { it.copy(notice = null) }
                session.update { it.copy(notice = null) }
            }

            is CheckCommand.OpenSaved -> openSaved(command.reportId)

            is CheckCommand.Save -> changeSave(command.reportId, save = true)

            is CheckCommand.Unsave -> changeSave(command.reportId, save = false)

            CheckCommand.LinkAccount -> {
                val account = services?.account ?: return
                if (mutableState.value.account.busy) return
                mutableState.update { it.copy(account = it.account.copy(busy = true)) }
                viewModelScope.launch {
                    val notice = try {
                        linkNotice(account.link())
                    } finally {
                        mutableState.update { it.copy(account = it.account.copy(busy = false)) }
                    }
                    // A second-device link moves saves to the account; read them again.
                    saves?.sync()
                    reload()
                    mutableState.update { it.copy(account = it.account.copy(notice = notice)) }
                }
            }

            else -> act(command)
        }
    }

    private fun act(command: CheckCommand) {
        val service = checks
        val target = targetOf(command)
        val busyKey = target?.record?.localId ?: session.openId
        if (service == null || busyKey == null || busyKey in mutableState.value.busy) return
        mutableState.update { it.copy(busy = it.busy + busyKey) }
        session.update { it.copy(busy = true) }
        viewModelScope.launch {
            val notice = try {
                perform(service, command, target)
            } finally {
                mutableState.update { it.copy(busy = it.busy - busyKey) }
            }
            loader?.invalidate()
            reload()
            mutableState.update { it.copy(notice = notice) }
            session.update { it.copy(notice = notice) }
        }
    }

    /**
     * Opens a saved report: its check, at the saved version, when the check is on this device;
     * otherwise the saved copy, read-only.
     */
    private fun openSaved(reportId: String) {
        val save = storedSaves.firstOrNull { it.entry.reportId == reportId } ?: return
        val check = stored.firstOrNull { it.investigation?.id == save.entry.investigationId }
        val investigation = check?.investigation
        if (investigation != null) {
            session.open(investigation.id)
            if (save.entry.version != investigation.report?.version) {
                session.show(save.entry.version)
            }
            loader?.invalidate()
        } else {
            val view = savedCopyView(save) ?: return
            session.showCopy(OpenReport(view, save = saveState(view, storedSaves, copy = true)))
        }
        viewModelScope.launch { reload() }
    }

    /** Explicit save or removal of the shown version; never done without the user's tap. */
    private fun changeSave(reportId: String, save: Boolean) {
        val saved = saves ?: return
        if (saving != null) return
        saving = reportId
        session.update { it.copy(busy = true) }
        viewModelScope.launch {
            val result = try {
                if (save) saved.save(reportId) else saved.unsave(reportId)
            } finally {
                saving = null
            }
            val notice = when (result) {
                is ApiResult.Failure -> failureText(result.failure)
                is ApiResult.Success -> if (save) SAVED_NOTICE else UNSAVED_NOTICE
            }
            val copy = session.report.value?.save?.copy == true
            if (!save && copy && result is ApiResult.Success) {
                session.close()
                mutableState.update { it.copy(notice = notice) }
            }
            reload()
            session.update { it.copy(busy = false, notice = notice) }
        }
    }

    private fun targetOf(command: CheckCommand): StoredCheck? {
        val localId = when (command) {
            is CheckCommand.Cancel -> command.localId
            is CheckCommand.Retry -> command.localId
            is CheckCommand.Continue -> command.localId
            else -> null
        }
        return if (localId != null) {
            stored.firstOrNull { it.record.localId == localId }
        } else {
            stored.firstOrNull { it.investigation?.id == session.openId }
        }
    }

    /** Runs one action; returns a short notice for the user, or null when nothing to say. */
    private suspend fun perform(
        service: ChecksService,
        command: CheckCommand,
        target: StoredCheck?
    ): String? {
        val investigation = target?.investigation
        val result: ApiResult<*>? = when (command) {
            is CheckCommand.Cancel -> investigation?.let { service.cancel(it) }
            is CheckCommand.Retry -> service.retry(command.localId)
            else -> investigation?.let { reanalyze(service, it, command) }
        }
        return when (result) {
            is ApiResult.Failure -> failureText(result.failure)
            is ApiResult.Success -> successText(command)
            null -> if (command is CheckCommand.Retry) "Trying again." else null
        }
    }

    private suspend fun reanalyze(
        service: ChecksService,
        investigation: Investigation,
        command: CheckCommand
    ): ApiResult<*>? {
        val base = investigation.version
        val request = when (command) {
            is CheckCommand.Continue -> ReanalysisRequest.Deeper(base)

            is CheckCommand.Correct ->
                ReanalysisRequest.Correction(base, command.claimId, command.proposition.trim())

            is CheckCommand.Expand -> ReanalysisRequest.Expansion(base, command.fullVideoId)

            else -> null
        } ?: return null
        val key = keys.keyFor(investigation.id, request)
        return service.reanalyze(investigation, request, key).also {
            if (it is ApiResult.Success) keys.answered(investigation.id, request)
        }
    }

    private fun successText(command: CheckCommand): String? = when (command) {
        is CheckCommand.Cancel -> "Cancelling. Results published so far are kept."

        is CheckCommand.Retry -> "Started a new check of the same video."

        is CheckCommand.Continue -> "Continuing the check. A new version will follow."

        is CheckCommand.Correct ->
            "Correction saved as a new version. The earlier version is kept."

        is CheckCommand.Expand ->
            "Checking the full video. The current version stays; a new one will follow."

        else -> null
    }

    private suspend fun reload() = refreshing.withLock {
        val service = checks ?: return@withLock
        val now = System.currentTimeMillis()
        stored = service.stored()
        storedSaves = saves?.stored().orEmpty()
        val linked = services?.api?.let { api -> withContext(Dispatchers.IO) { api.isLinked() } }
        val items = stored.map { inboxItem(it.record, it.investigation, now) }
            .sortedByDescending { it.createdAt }
        mutableState.update {
            it.copy(
                loaded = true,
                inbox = items.filterNot { item -> item.library },
                library = items.filter { item -> item.library },
                saved = savedItems(storedSaves, stored, now),
                account = it.account.copy(
                    available = services?.account?.available == true,
                    linked = linked == true
                )
            )
        }
        val openId = session.openId
        val open = stored.firstOrNull { it.investigation?.id == openId }?.investigation
        val openItem = items.firstOrNull { it.serverId == openId }
        val candidates = if (openItem?.captured == true) {
            fullVideoCandidates(openItem, items)
        } else {
            emptyList()
        }
        val busy = openItem?.localId in mutableState.value.busy || saving != null
        session.refresh(open, candidates, busy = busy)
        session.update {
            it.copy(save = saveState(it.view, storedSaves, copy = it.save?.copy == true))
        }
    }

    private companion object {
        const val ACTIVE_POLL_MILLIS = 5_000L
        const val IDLE_POLL_MILLIS = 60_000L
        const val SAVED_NOTICE =
            "Saved. A copy of this version is kept for you on the ovrly service."
        const val UNSAVED_NOTICE = "Removed from saved reports. The check itself is unchanged."
    }
}
