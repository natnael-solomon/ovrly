@file:UseSerializers(
    StrictLongSerializer::class,
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.data

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import app.ovrly.contract.ContractBooleanSerializer
import app.ovrly.contract.ContractJson
import app.ovrly.contract.ContractParseException
import app.ovrly.contract.ContractSyntax
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ReportVersion
import app.ovrly.contract.StrictIntSerializer
import app.ovrly.contract.StrictLongSerializer
import java.time.Instant
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers
import okhttp3.RequestBody.Companion.toRequestBody

/*
 * Explicitly saved reports (AN-10, #36; AC08). A save is a server-side copy of one immutable
 * report version for its owner (BE-10, #33): nothing is saved without the user's action, and
 * the device keeps a copy of the server's list in Room so Saved reports read offline.
 */

/** `POST /v1/reports/{report_id}/save` and each item of `GET /v1/reports/saved`. */
@Serializable
internal data class SavedReport(
    @SerialName("report_id")
    val reportId: String,
    @SerialName("investigation_id")
    val investigationId: String,
    val version: Int,
    @SerialName("saved_at")
    val savedAt: String,
    /** The snapshot taken when the report was saved. */
    val report: ReportVersion
) {
    init {
        ContractSyntax.opaqueId("report_id", reportId)
        ContractSyntax.uuid("investigation_id", investigationId)
        require(version >= 1) { "version starts at 1" }
        ContractSyntax.timestamp("saved_at", savedAt)
        require(report.id == reportId && report.version == version) {
            "report must be the saved version"
        }
        require(report.investigationId == investigationId) {
            "report must belong to the saved investigation"
        }
    }
}

@Serializable
internal data class SavedReportList(val items: List<SavedReport>) {
    init {
        require(items.size <= MAX_ITEMS) { "items holds at most $MAX_ITEMS saves" }
    }

    companion object {
        const val MAX_ITEMS = 100
    }
}

internal object SavedReportCodec {
    fun parseSaved(payload: String): SavedReport = ContractJson.parse("saved report") {
        ContractJson.tolerant.decodeFromString(SavedReport.serializer(), payload)
    }

    /** One save this client cannot read fails the whole list instead of being dropped. */
    fun parseList(payload: String): SavedReportList = ContractJson.parse("saved reports") {
        ContractJson.tolerant.decodeFromString(SavedReportList.serializer(), payload)
    }
}

/** A saved report as the device last read it from the server. */
@Entity(tableName = "saved_reports")
internal data class SavedReportEntry(
    @PrimaryKey
    @ColumnInfo(name = "report_id")
    val reportId: String,
    @ColumnInfo(name = "investigation_id")
    val investigationId: String,
    val version: Int,
    @ColumnInfo(name = "saved_at")
    val savedAt: String,
    /** The saved report version, as the contract encodes it. */
    val json: String,
    @ColumnInfo(name = "stored_at")
    val storedAt: Long
)

/** Every saved-report query; the unit tests' `MemorySavedReportDao` mirrors it. */
@Dao
internal abstract class SavedReportDao {
    @Query("SELECT * FROM saved_reports")
    abstract suspend fun all(): List<SavedReportEntry>

    @Upsert
    abstract suspend fun upsert(entry: SavedReportEntry)

    @Query("DELETE FROM saved_reports WHERE report_id = :reportId")
    abstract suspend fun delete(reportId: String)

    @Query("DELETE FROM saved_reports")
    abstract suspend fun clear()

    /** The server's list replaces the stored one in one transaction. */
    @Transaction
    open suspend fun replaceAll(entries: List<SavedReportEntry>) {
        clear()
        entries.forEach { upsert(it) }
    }
}

/** A stored save, parsed; [report] is null when the stored copy can no longer be read. */
internal data class StoredSave(val entry: SavedReportEntry, val report: ReportVersion?)

/**
 * Saves, removes and lists the caller's saved reports. The server is the source of truth: a
 * save is stored on the device only after the server answered, and every list read replaces
 * the stored list. One lock orders a list read after a save or removal that started first, so
 * an older list never undoes a newer action.
 */
internal class SavedReports(
    private val api: OvrlyApi,
    private val dao: SavedReportDao,
    private val clock: () -> Long = System::currentTimeMillis
) {
    private val lock = Mutex()

    /** The stored saves, newest first, without a network call. */
    suspend fun stored(): List<StoredSave> = dao.all().sortedWith(NEWEST_FIRST).map { entry ->
        val report = try {
            InvestigationCodec.parseReportVersion(entry.json)
        } catch (_: ContractParseException) {
            null
        }
        StoredSave(entry, report)
    }

    /** Reads `GET /v1/reports/saved` and stores it; returns the failure, or null. */
    suspend fun sync(): ApiFailure? = lock.withLock {
        when (val result = list()) {
            is ApiResult.Success -> {
                dao.replaceAll(result.value.items.map(::entry))
                null
            }

            is ApiResult.Failure -> result.failure
        }
    }

    /** Explicitly saves [reportId] for its owner; repeating it is harmless. */
    suspend fun save(reportId: String): ApiResult<SavedReport> = lock.withLock {
        api.sendAuthenticated(
            { ApiCall("reports.save", "POST", "v1/reports/$reportId/save", EMPTY_BODY, it) },
            SavedReportCodec::parseSaved
        ).also { if (it is ApiResult.Success) dao.upsert(entry(it.value)) }
    }

    /**
     * Removes the caller's save of [reportId]; the report itself is not changed. A 404 means
     * the caller holds no save of it (already removed, for example on another device), so the
     * stored copy is dropped too.
     */
    suspend fun unsave(reportId: String): ApiResult<Unit> = lock.withLock {
        val result = api.sendAuthenticated(
            { ApiCall("reports.unsave", "DELETE", "v1/reports/$reportId/save", EMPTY_BODY, it) },
            { }
        )
        when {
            result is ApiResult.Success -> result.also { dao.delete(reportId) }

            result is ApiResult.Failure && result.isNotFound -> {
                dao.delete(reportId)
                ApiResult.Success(Unit, result.failure.requestId)
            }

            else -> result
        }
    }

    private suspend fun list(): ApiResult<SavedReportList> = api.sendAuthenticated(
        { ApiCall("reports.saved", "GET", "v1/reports/saved", token = it) },
        SavedReportCodec::parseList
    )

    private fun entry(saved: SavedReport) = SavedReportEntry(
        reportId = saved.reportId,
        investigationId = saved.investigationId,
        version = saved.version,
        savedAt = saved.savedAt,
        json = encoded(saved.report),
        storedAt = clock()
    )

    private val ApiResult.Failure.isNotFound: Boolean
        get() = (failure as? ApiFailure.Server)?.code == ApiErrorCode.NOT_FOUND

    private companion object {
        val EMPTY_BODY = ByteArray(0).toRequestBody()

        val NEWEST_FIRST: Comparator<SavedReportEntry> =
            compareByDescending<SavedReportEntry> { savedInstant(it.savedAt) }
                .thenBy { it.reportId }

        fun savedInstant(value: String): Instant = try {
            OffsetDateTime.parse(value).toInstant()
        } catch (_: DateTimeParseException) {
            Instant.EPOCH
        }

        /** A version with values this app does not know has no wire form; it reads as unknown. */
        fun encoded(report: ReportVersion): String = try {
            InvestigationCodec.encodeReportVersion(report)
        } catch (_: IllegalArgumentException) {
            ""
        }
    }
}
