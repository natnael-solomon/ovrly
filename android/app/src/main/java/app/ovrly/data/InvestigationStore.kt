package app.ovrly.data

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Upsert
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/*
 * Room store for AN-03 (#18). Each schema version is exported to `app/schemas`; any change to
 * these entities bumps the version and adds a migration tested against that export. The
 * database lives in app-private storage, which the data-extraction rules exclude from
 * backup and device transfer.
 */

/** One share on this device, from local pending to its last server read. */
@Entity(
    tableName = "investigations",
    indices = [Index(value = ["server_id"], unique = true), Index(value = ["share_key"])]
)
internal data class InvestigationRecord(
    @PrimaryKey
    @ColumnInfo(name = "local_id")
    val localId: String,
    @ColumnInfo(name = "server_id")
    val serverId: String? = null,
    /** [LocalJobState.wireName]. */
    val state: String,
    /** `url` or `upload`, or the wire kind of an investigation read from the server. */
    @ColumnInfo(name = "source_kind")
    val sourceKind: String,
    @ColumnInfo(name = "source_url")
    val sourceUrl: String? = null,
    @ColumnInfo(name = "idempotency_key")
    val idempotencyKey: String,
    /** Content hash or link hash used to offer the existing check on a repeated share. */
    @ColumnInfo(name = "share_key")
    val shareKey: String? = null,
    @ColumnInfo(name = "staged_path")
    val stagedPath: String? = null,
    @ColumnInfo(name = "content_type")
    val contentType: String? = null,
    @ColumnInfo(name = "duration_ms")
    val durationMs: Long? = null,
    @ColumnInfo(name = "size_bytes")
    val sizeBytes: Long? = null,
    val sha256: String? = null,
    /** The declared upload read model, so a retry completes it instead of a new one. */
    @ColumnInfo(name = "declared_upload")
    val declaredUpload: String? = null,
    @ColumnInfo(name = "upload_id")
    val uploadId: String? = null,
    @ColumnInfo(name = "processing_status")
    val processingStatus: String? = null,
    /** The last investigation read, as the contract encodes it; null if it had UNKNOWN values. */
    @ColumnInfo(name = "investigation_json")
    val investigationJson: String? = null,
    @ColumnInfo(name = "error_code")
    val errorCode: String? = null,
    @ColumnInfo(name = "created_at")
    val createdAt: Long,
    @ColumnInfo(name = "updated_at")
    val updatedAt: Long,
    @ColumnInfo(name = "synced_at")
    val syncedAt: Long? = null,
    /**
     * `Idempotency-Key` of "Try again" on this failed or cancelled check (#34), stored before
     * the request so a retry after a lost answer replays the same new check.
     */
    @ColumnInfo(name = "retry_key")
    val retryKey: String? = null,
    /** Server id of the new check "Try again" created; the old check offers it instead. */
    @ColumnInfo(name = "retried_as")
    val retriedAs: String? = null
)

/** The stored state; a value this version does not know reads as [LocalJobState.FAILED]. */
internal val InvestigationRecord.jobState: LocalJobState
    get() = LocalJobState.fromWire(state) ?: LocalJobState.FAILED

/**
 * A cached, immutable report version. [stale] means a newer version is known to exist, or a
 * provisional version could not be confirmed against the server recently.
 */
@Entity(tableName = "report_versions", primaryKeys = ["investigation_id", "version"])
internal data class ReportCacheEntry(
    @ColumnInfo(name = "investigation_id")
    val investigationId: String,
    val version: Int,
    @ColumnInfo(name = "report_id")
    val reportId: String,
    val json: String,
    val provisional: Boolean,
    @ColumnInfo(name = "fetched_at")
    val fetchedAt: Long,
    val stale: Boolean = false
)

/**
 * A captured chunk waiting to be sent to its capture session; `(session_id, seq)` is the
 * contract's idempotency key. Uploading them is AN-07 (#26).
 */
@Entity(tableName = "pending_chunks", primaryKeys = ["session_id", "seq"])
internal data class PendingChunk(
    @ColumnInfo(name = "session_id")
    val sessionId: String,
    val seq: Long,
    val path: String,
    @ColumnInfo(name = "size_bytes")
    val sizeBytes: Long,
    val sha256: String,
    @ColumnInfo(name = "start_ms")
    val startMs: Long,
    @ColumnInfo(name = "end_ms")
    val endMs: Long,
    /** `pending`, `uploading` or `stored`. */
    val state: String = PENDING,
    val attempts: Int = 0,
    @ColumnInfo(name = "created_at")
    val createdAt: Long
) {
    companion object {
        const val PENDING = "pending"
        const val STORED = "stored"
    }
}

/** Every query the data layer runs. The unit tests' `MemoryInvestigationDao` mirrors it. */
@Dao
internal interface InvestigationDao {
    @Query("SELECT * FROM investigations WHERE local_id = :localId")
    suspend fun get(localId: String): InvestigationRecord?

    @Query("SELECT * FROM investigations WHERE server_id = :serverId")
    suspend fun byServerId(serverId: String): InvestigationRecord?

    @Query(
        "SELECT * FROM investigations WHERE share_key = :shareKey AND server_id IS NOT NULL " +
            "ORDER BY updated_at DESC LIMIT 1"
    )
    suspend fun acceptedByShareKey(shareKey: String): InvestigationRecord?

    @Query("SELECT * FROM investigations ORDER BY created_at")
    suspend fun all(): List<InvestigationRecord>

    @Upsert
    suspend fun upsert(record: InvestigationRecord)

    @Query("DELETE FROM investigations WHERE local_id = :localId")
    suspend fun delete(localId: String)

    @Upsert
    suspend fun putReport(entry: ReportCacheEntry)

    @Query(
        "SELECT * FROM report_versions WHERE investigation_id = :investigationId " +
            "ORDER BY version DESC LIMIT 1"
    )
    suspend fun latestReport(investigationId: String): ReportCacheEntry?

    @Query(
        "UPDATE report_versions SET stale = 1 " +
            "WHERE investigation_id = :investigationId AND version < :version"
    )
    suspend fun markOlderStale(investigationId: String, version: Int)

    @Query("UPDATE report_versions SET stale = 1 WHERE provisional = 1 AND fetched_at < :cutoff")
    suspend fun markProvisionalStale(cutoff: Long)
}

/** Captured chunks waiting for their capture session; the uploader is AN-07 (#26). */
@Dao
internal interface PendingChunkDao {
    @Upsert
    suspend fun putChunk(chunk: PendingChunk)

    @Query(
        "SELECT * FROM pending_chunks WHERE session_id = :sessionId AND state != 'stored' " +
            "ORDER BY seq"
    )
    suspend fun unsentChunks(sessionId: String): List<PendingChunk>

    @Query("DELETE FROM pending_chunks WHERE session_id = :sessionId AND seq = :seq")
    suspend fun deleteChunk(sessionId: String, seq: Long)
}

@Database(
    entities = [
        InvestigationRecord::class,
        ReportCacheEntry::class,
        PendingChunk::class,
        SavedReportEntry::class
    ],
    version = 3,
    exportSchema = true
)
internal abstract class OvrlyDatabase : RoomDatabase() {
    abstract fun investigations(): InvestigationDao

    abstract fun pendingChunks(): PendingChunkDao

    abstract fun savedReports(): SavedReportDao

    companion object {
        /** No destructive fallback: a missing migration fails loudly instead of losing data. */
        fun open(context: Context): OvrlyDatabase =
            Room.databaseBuilder(context, OvrlyDatabase::class.java, "ovrly.db")
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3)
                .build()

        /** Version 2 (#34): the retry key and the check a retry created. */
        val MIGRATION_1_2_SQL = listOf(
            "ALTER TABLE investigations ADD COLUMN retry_key TEXT",
            "ALTER TABLE investigations ADD COLUMN retried_as TEXT"
        )

        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                MIGRATION_1_2_SQL.forEach(db::execSQL)
            }
        }

        /** Version 3 (#36): the device's copy of the owner's explicitly saved reports. */
        val MIGRATION_2_3_SQL = listOf(
            "CREATE TABLE IF NOT EXISTS `saved_reports` (`report_id` TEXT NOT NULL, " +
                "`investigation_id` TEXT NOT NULL, `version` INTEGER NOT NULL, " +
                "`saved_at` TEXT NOT NULL, `json` TEXT NOT NULL, `stored_at` INTEGER NOT NULL, " +
                "PRIMARY KEY(`report_id`))"
        )

        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                MIGRATION_2_3_SQL.forEach(db::execSQL)
            }
        }
    }
}
