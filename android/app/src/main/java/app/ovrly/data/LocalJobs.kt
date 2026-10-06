package app.ovrly.data

import android.os.Process
import android.os.SystemClock
import app.ovrly.contract.ContractParseException
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.ReportVersion
import java.util.concurrent.TimeUnit

/**
 * Wall-clock time this process started, from the platform's own record, so it does not
 * depend on when this object is first read. Earlier staging belongs to a dead process.
 */
internal object ProcessStart {
    val millis: Long = processStartMillis(
        System.currentTimeMillis(),
        SystemClock.elapsedRealtime(),
        Process.getStartElapsedRealtime()
    )
}

/** Converts the process start on the elapsed-realtime clock to wall-clock milliseconds. */
internal fun processStartMillis(nowMillis: Long, elapsedMillis: Long, startElapsedMillis: Long) =
    nowMillis - (elapsedMillis - startElapsedMillis).coerceAtLeast(0)

/** A provisional report not confirmed against the server for [maxAgeMillis] is stale. */
internal data class StalenessPolicy(
    val maxAgeMillis: Long = TimeUnit.MINUTES.toMillis(DEFAULT_MAX_AGE_MINUTES)
)

private const val DEFAULT_MAX_AGE_MINUTES = 10L

/** A cached report version and whether it may be out of date. */
internal data class CachedReport(val report: ReportVersion, val fetchedAt: Long, val stale: Boolean)

/**
 * Every durable state change of a share goes through here: transitions follow
 * [LocalJobState.next], server reads win for accepted items, report versions are cached and
 * marked stale when a newer version is known or a provisional one could not be confirmed.
 */
internal class LocalJobs(
    val dao: InvestigationDao,
    private val clock: () -> Long = System::currentTimeMillis,
    private val staleness: StalenessPolicy = StalenessPolicy()
) {
    /** Records a share that passed the device checks, before anything is sent. */
    suspend fun startShare(record: InvestigationRecord) {
        val now = clock()
        dao.upsert(
            record.copy(
                state = LocalJobState.LOCAL_PENDING.wireName,
                createdAt = now,
                updatedAt = now
            )
        )
    }

    /**
     * Applies [event] and [change] to the record; returns the stored record, or null when the
     * record is missing or the transition is not allowed (the record is then left as it was).
     */
    suspend fun apply(
        localId: String,
        event: JobEvent,
        change: (InvestigationRecord) -> InvestigationRecord = { it }
    ): InvestigationRecord? {
        val record = dao.get(localId)
        val next = record?.jobState?.next(event)
        return if (record == null || next == null) {
            null
        } else {
            change(record).copy(state = next.wireName, updatedAt = clock()).also { dao.upsert(it) }
        }
    }

    /** `POST /v1/investigations` answered for the local share [localId]. */
    suspend fun accepted(localId: String, investigation: Investigation): InvestigationRecord? {
        val event = JobEvent.Accepted(investigation.processingStatus)
        val stored = apply(localId, event) {
            it.withServer(investigation, clock()).copy(stagedPath = null, declaredUpload = null)
        }
        // A share abandoned meanwhile has no row; do not leave an orphan report behind.
        if (stored != null) cacheReport(investigation)
        return stored
    }

    /** A server read of an investigation; creates a record if this device had none. */
    suspend fun recordRead(investigation: Investigation): InvestigationRecord? {
        val existing = dao.byServerId(investigation.id) ?: importRecord(investigation)
        val event = JobEvent.Server(investigation.processingStatus)
        return apply(existing.localId, event) { it.withServer(investigation, clock()) }
            .also { cacheReport(investigation) }
    }

    /** The server no longer has [serverId]; a repeated share no longer offers it. */
    suspend fun gone(serverId: String): InvestigationRecord? {
        val record = dao.byServerId(serverId) ?: return null
        return apply(record.localId, JobEvent.Gone) {
            it.copy(shareKey = null, errorCode = GONE)
        }
    }

    /** Drops a share the user abandoned before the server accepted it. */
    suspend fun abandon(localId: String) {
        val record = dao.get(localId) ?: return
        if (record.serverId == null) dao.delete(localId)
    }

    /** The server id of an accepted investigation created from the same item, if any. */
    suspend fun findAccepted(shareKey: String): String? = dao.acceptedByShareKey(shareKey)?.serverId

    /** The server could not be reached: provisional reports not confirmed lately go stale. */
    suspend fun markUnconfirmed() {
        dao.markProvisionalStale(clock() - staleness.maxAgeMillis)
    }

    private suspend fun importRecord(investigation: Investigation): InvestigationRecord {
        val now = clock()
        return InvestigationRecord(
            localId = investigation.id,
            serverId = investigation.id,
            state = LocalJobState.ACCEPTED.wireName,
            sourceKind = investigation.source.kind.wireName.ifEmpty { UNKNOWN_KIND },
            idempotencyKey = "",
            createdAt = now,
            updatedAt = now
        ).also { dao.upsert(it) }
    }

    private suspend fun cacheReport(investigation: Investigation) {
        dao.markOlderStale(investigation.id, investigation.version)
        val report = investigation.report ?: return
        val json = encode { InvestigationCodec.encodeReportVersion(report) } ?: return
        dao.putReport(
            ReportCacheEntry(
                investigationId = investigation.id,
                version = report.version,
                reportId = report.id,
                json = json,
                provisional = report.provisional,
                fetchedAt = clock()
            )
        )
    }

    private companion object {
        const val GONE = "NOT_FOUND"
        const val UNKNOWN_KIND = "unknown"
    }
}

/** The last stored read of [serverId], or null if none was stored or it no longer parses. */
internal suspend fun LocalJobs.cachedInvestigation(serverId: String): Investigation? =
    dao.byServerId(serverId)?.investigationJson?.let { json ->
        decode { InvestigationCodec.parseInvestigation(json) }
    }

/** The newest cached report version of [serverId] with its staleness flag. */
internal suspend fun LocalJobs.cachedReport(serverId: String): CachedReport? {
    val entry = dao.latestReport(serverId)
    val report = entry?.let { decode { InvestigationCodec.parseReportVersion(it.json) } }
    return if (entry != null && report != null) {
        CachedReport(report, entry.fetchedAt, entry.stale)
    } else {
        null
    }
}

private fun InvestigationRecord.withServer(investigation: Investigation, now: Long) = copy(
    serverId = investigation.id,
    processingStatus = investigation.processingStatus
        .takeIf { it != ProcessingStatus.UNKNOWN }?.wireName,
    investigationJson = encode { InvestigationCodec.encodeInvestigation(investigation) },
    errorCode = investigation.error?.code,
    syncedAt = now
)

/** A model holding UNKNOWN values has no wire form; it is not cached as JSON. */
private inline fun encode(write: () -> String): String? = try {
    write()
} catch (_: IllegalArgumentException) {
    null
}

private inline fun <T> decode(read: () -> T): T? = try {
    read()
} catch (_: ContractParseException) {
    null
}
