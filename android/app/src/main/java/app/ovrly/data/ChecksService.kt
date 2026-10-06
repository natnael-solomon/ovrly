package app.ovrly.data

import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCreateRequest
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.ReportVersion
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** One stored check and its last parsed server read, if any. */
internal data class StoredCheck(val record: InvestigationRecord, val investigation: Investigation?)

/**
 * The actions of the Inbox and Report screens (#34): list, cancel, retry, continue, correct,
 * expand and read report versions. Every server read goes through [LocalJobs] so the screens
 * are rebuilt from the store, never from ViewModel memory.
 */
internal class ChecksService(
    private val services: ApiServices,
    private val keys: () -> String = { UUID.randomUUID().toString() }
) {
    private val jobs get() = services.jobs
    private val reports get() = services.reports
    private val earlier = ConcurrentHashMap<Pair<String, Int>, ReportVersion>()

    /** Every stored check, oldest first, without a network call. */
    suspend fun stored(): List<StoredCheck> = jobs.dao.all().map { record ->
        StoredCheck(record, record.serverId?.let { jobs.cachedInvestigation(it) })
    }

    /**
     * Reads the caller's investigations and stores each. Returns the failure, or null. An
     * unreachable server marks provisional reports that were not confirmed lately as stale.
     */
    suspend fun sync(): ApiFailure? = when (val result = reports.listInvestigations()) {
        is ApiResult.Success -> {
            // While a share waits for its create answer, an unknown id may be that share; it
            // is imported on a later sync, after the share's own row holds the id.
            val import = !jobs.hasUnacceptedShares()
            result.value.forEach { jobs.recordListed(it, import) }
            null
        }

        is ApiResult.Failure -> result.failure.also {
            if (it is ApiFailure.Network) jobs.markUnconfirmed()
        }
    }

    /** Requests cancellation of the investigation's job, then reads it again. */
    suspend fun cancel(investigation: Investigation): ApiResult<CancelReceipt>? {
        val job = investigation.job ?: return null
        return reports.cancelJob(job.id).also { services.investigations.refresh(investigation.id) }
    }

    /**
     * Before acceptance, resumes the stored share now (same staged copy and key). After a
     * retryable failure or a cancel without results, starts one new check of the same source.
     * Its `Idempotency-Key` is stored on the old record before the request and reused until
     * the server answers, so a lost answer replays the same new check; once it exists, the
     * old record points at it and is not retried again. The old check stays as it was.
     */
    suspend fun retry(localId: String): ApiResult<Investigation>? {
        val record = jobs.dao.get(localId)
        val previous = record?.serverId?.let { jobs.cachedInvestigation(it) }
        val source = previous?.source?.takeIf { it.canBeResent }
        return when {
            record == null || record.retriedAs != null -> null

            !record.jobState.accepted -> {
                services.reconciler?.reconcile()
                null
            }

            source == null -> null

            else -> recreate(record, source)
        }
    }

    private suspend fun recreate(
        record: InvestigationRecord,
        source: InvestigationSource
    ): ApiResult<Investigation> {
        val key = record.retryKey ?: keys().also { jobs.dao.upsert(record.copy(retryKey = it)) }
        val result = services.api.createInvestigation(InvestigationCreateRequest(source), key)
        if (result is ApiResult.Success) {
            jobs.recordRead(result.value)
            jobs.dao.get(record.localId)?.let {
                jobs.dao.upsert(it.copy(retriedAs = result.value.id))
            }
        }
        return result
    }

    private val InvestigationSource.canBeResent: Boolean
        get() = this is InvestigationSource.Url || this is InvestigationSource.Upload

    /** Reanalysis from the latest version; the same [key] and request replay the answer. */
    suspend fun reanalyze(
        investigation: Investigation,
        request: ReanalysisRequest,
        key: String
    ): ApiResult<ReanalysisReceipt> = reports.reanalyze(investigation.id, request, key).also {
        if (it is ApiResult.Success) services.investigations.refresh(investigation.id)
    }

    /** Every published version of [id], oldest first. */
    suspend fun versions(id: String): ApiResult<ReportVersionList> = reports.versions(id)

    /**
     * One immutable version, from memory when it was read before in this process, otherwise
     * from the server. Versions never change, so a read one is never refetched; the latest
     * version is cached in Room through every investigation read.
     */
    suspend fun version(id: String, version: Int): ApiResult<ReportVersion> =
        earlier[id to version]?.let { ApiResult.Success(it, "") }
            ?: reports.version(id, version).also {
                if (it is ApiResult.Success) earlier[id to version] = it.value
            }

    /** A fresh idempotency key for one user action. */
    fun newKey(): String = keys()
}

/**
 * `Idempotency-Key`s of reanalysis requests (#34), one per investigation and exact request
 * (reason, base version, claim and meaning, or chosen full video). Repeating a request after
 * a lost answer reuses its key, so the server replays the first answer; a different request,
 * such as picking another full video, gets its own key.
 */
internal class ReanalysisKeys(private val newKey: () -> String) {
    private val keys = HashMap<String, String>()

    fun keyFor(investigationId: String, request: ReanalysisRequest): String =
        keys.getOrPut(name(investigationId, request), newKey)

    /** The server answered: the next identical request is a new action with a new key. */
    fun answered(investigationId: String, request: ReanalysisRequest) {
        keys.remove(name(investigationId, request))
    }

    private fun name(investigationId: String, request: ReanalysisRequest) =
        "$investigationId/${request.encode()}"
}
