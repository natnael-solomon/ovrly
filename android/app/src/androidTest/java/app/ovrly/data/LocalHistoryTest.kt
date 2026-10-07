package app.ovrly.data

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.testing.Device
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

/**
 * What a second-device link leaves in Room (AN-10, #36; BC-D07), against the real queries:
 * the revoked guest's accepted checks and cached reports go, and a share it had not sent yet
 * stays with its staged copy but loses the guest's upload, so it is uploaded again as the
 * account instead of naming an upload the account does not own.
 */
@RunWith(AndroidJUnit4::class)
class LocalHistoryTest {
    private val database = Room.inMemoryDatabaseBuilder(Device.context, OvrlyDatabase::class.java)
        .build()
    private val checks = database.investigations()

    @After
    fun close() = database.close()

    private fun record(localId: String, serverId: String?, state: LocalJobState) =
        InvestigationRecord(
            localId = localId,
            serverId = serverId,
            state = state.wireName,
            sourceKind = "upload",
            idempotencyKey = "key-$localId",
            stagedPath = "/staged/$localId.mp4",
            declaredUpload = """{"id":"guest-upload"}""",
            uploadId = "00000000-0000-4000-8000-000000000a01",
            createdAt = 1L,
            updatedAt = 1L
        )

    @Test
    fun aSwitchForgetsServerChecksAndTheGuestsUploadsButKeepsUnsentShares() = runBlocking {
        val accepted = "00000000-0000-4000-8000-000000000b01"
        checks.upsert(record("sent", accepted, LocalJobState.SUCCEEDED))
        checks.upsert(record("unsent", null, LocalJobState.UPLOADING))
        checks.putReport(
            ReportCacheEntry(accepted, 1, "rpt_guest_v1", "{}", provisional = false, fetchedAt = 1L)
        )

        database.localHistory().forgetServerHistory()

        assertNull(checks.get("sent"))
        assertNull(checks.latestReport(accepted))
        val unsent = checkNotNull(checks.get("unsent"))
        assertNull(unsent.uploadId)
        assertNull(unsent.declaredUpload)
        assertEquals("/staged/unsent.mp4", unsent.stagedPath)
        assertEquals("key-unsent", unsent.idempotencyKey)
        assertEquals(LocalJobState.UPLOADING.wireName, unsent.state)
        assertEquals(listOf("unsent"), checks.all().map { it.localId })
    }
}
