package app.ovrly.data

/** In-memory [InvestigationDao] with the same semantics, for unit tests. */
internal open class MemoryInvestigationDao : InvestigationDao {
    private val records = linkedMapOf<String, InvestigationRecord>()
    private val reports = linkedMapOf<Pair<String, Int>, ReportCacheEntry>()

    override suspend fun get(localId: String) = synchronized(this) { records[localId] }

    override suspend fun byServerId(serverId: String) =
        synchronized(this) { records.values.firstOrNull { it.serverId == serverId } }

    override suspend fun acceptedByShareKey(shareKey: String) = synchronized(this) {
        records.values.filter { it.shareKey == shareKey && it.serverId != null }
            .maxByOrNull { it.updatedAt }
    }

    override suspend fun all() = synchronized(this) { records.values.sortedBy { it.createdAt } }

    override suspend fun upsert(record: InvestigationRecord): Unit = synchronized(this) {
        val clash = records.values.any {
            it.localId != record.localId && it.serverId != null && it.serverId == record.serverId
        }
        check(!clash) { "server_id must be unique" }
        records[record.localId] = record
    }

    override suspend fun delete(localId: String): Unit = synchronized(this) {
        records.remove(localId)
    }

    override suspend fun putReport(entry: ReportCacheEntry): Unit = synchronized(this) {
        reports[entry.investigationId to entry.version] = entry
    }

    override suspend fun latestReport(investigationId: String) = synchronized(this) {
        reports.values.filter { it.investigationId == investigationId }.maxByOrNull { it.version }
    }

    override suspend fun markOlderStale(investigationId: String, version: Int): Unit =
        synchronized(this) {
            reports.replaceAll { _, entry ->
                val older = entry.investigationId == investigationId && entry.version < version
                if (older) entry.copy(stale = true) else entry
            }
        }

    override suspend fun markProvisionalStale(cutoff: Long): Unit = synchronized(this) {
        reports.replaceAll { _, entry ->
            if (entry.provisional && entry.fetchedAt < cutoff) entry.copy(stale = true) else entry
        }
    }
}
