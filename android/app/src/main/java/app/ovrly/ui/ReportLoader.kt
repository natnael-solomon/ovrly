package app.ovrly.ui

import app.ovrly.contract.Investigation
import app.ovrly.contract.ReportVersion
import app.ovrly.data.ApiResult
import app.ovrly.data.ChecksService
import app.ovrly.data.LocalJobs
import app.ovrly.data.ReportVersionSummary
import app.ovrly.data.cachedReport

/**
 * Builds the [OpenReport] for one investigation: the shown version (the latest by default,
 * or an earlier one from the picker), its staleness, the version list and the per-claim
 * changes against the version it superseded. Earlier versions are read, never edited.
 */
internal class ReportLoader(
    private val checks: ChecksService,
    /** Reads the cached staleness of the latest version of an investigation. */
    private val stale: suspend (String) -> Boolean
) {
    private var summaries: Pair<String, List<ReportVersionSummary>>? = null

    /** Forgets the version list so the next [load] reads it again (after a reanalysis). */
    fun invalidate() {
        summaries = null
    }

    suspend fun load(
        investigation: Investigation,
        shownVersion: Int?,
        candidates: List<InboxItem>
    ): OpenReport {
        val latest = investigation.report
        val wanted = shownVersion?.takeIf { latest != null && it != latest.version }
        val loaded = wanted?.let { checks.version(investigation.id, it) }
        val shown = (loaded as? ApiResult.Success)?.value ?: latest
        val list = versionList(investigation)
        val fixture = list?.firstOrNull { it.version == shown?.version }?.fixture == true
        val isStale = shown != null && shown == latest && this.stale(investigation.id)
        val previous = shown?.takeIf { it.version > 1 }
            ?.let { checks.version(investigation.id, it.version - 1) }
        val earlier = (previous as? ApiResult.Success)?.value
        return OpenReport(
            view = reportView(investigation, shown, ShownFlags(isStale, fixture)),
            versions = list.orEmpty().map { it.choice(investigation.version) },
            versionsNote = when {
                list == null && latest != null -> "Earlier versions need a connection."
                loaded is ApiResult.Failure -> "That version could not be loaded."
                else -> null
            },
            comparedWith = earlier?.version,
            changes = shown?.let { changes(it, earlier) }.orEmpty(),
            candidates = candidates
        )
    }

    private suspend fun versionList(investigation: Investigation): List<ReportVersionSummary>? {
        val cached = summaries?.takeIf { (id, items) ->
            id == investigation.id && (items.lastOrNull()?.version ?: 0) >= investigation.version
        }?.second
        return when {
            cached != null -> cached
            investigation.report == null -> emptyList()
            else -> fetchVersions(investigation.id)
        }
    }

    private suspend fun fetchVersions(id: String): List<ReportVersionSummary>? {
        val items = (checks.versions(id) as? ApiResult.Success)?.value?.items
        if (items != null) summaries = id to items
        return items
    }

    private fun changes(shown: ReportVersion, earlier: ReportVersion?): Map<String, List<String>> =
        if (earlier == null) {
            emptyMap()
        } else {
            shown.claims.associate { it.id to claimChanges(it.id, earlier, shown) }
        }

    private fun ReportVersionSummary.choice(latest: Int): VersionChoice {
        val tags = listOfNotNull(
            "latest".takeIf { version == latest },
            "provisional".takeIf { provisional },
            "development fixture".takeIf { fixture }
        )
        val suffix = if (tags.isEmpty()) "" else " (${tags.joinToString()})"
        return VersionChoice(version, "Version $version$suffix", fixture)
    }

    companion object {
        fun staleFrom(jobs: LocalJobs): suspend (String) -> Boolean =
            { id -> jobs.cachedReport(id)?.stale == true }
    }
}
