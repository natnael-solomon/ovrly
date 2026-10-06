package app.ovrly.capture

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import app.ovrly.contract.CaptureChunkRequest
import app.ovrly.contract.CaptureCloseRequest
import app.ovrly.contract.CaptureCreateRequest
import app.ovrly.contract.CaptureMetadata
import app.ovrly.contract.Interval
import app.ovrly.contract.Timebase
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import org.json.JSONException

/**
 * Sends the sealed chunks of the local capture and closes the server session once the
 * capture has finished and the user chose whether research continues. Every step is
 * idempotent: the session is opened with the local session id as `Idempotency-Key`, chunks
 * are keyed by `(session_id, seq)` and a sent chunk is recorded only after the server holds it.
 */
internal class CaptureUploader(
    private val api: CaptureSessionApi,
    private val root: File,
    private val wifiOnly: Boolean = false,
    /** Called with the server session id once it is known, on every run. */
    private val onSession: (String) -> Unit = {}
) {
    enum class Outcome {
        /** Nothing left to do now; a later chunk or the choice schedules another run. */
        DONE,

        /** A transport or retryable server error; try again with backoff. */
        RETRY,

        /** A permanent error was recorded; the chunks stay on the device. */
        STOPPED
    }

    private var ledger = CaptureLedger(root)

    suspend fun sync(sessionId: String, onProgress: (UploadProgress) -> Unit = {}): Outcome {
        ledger = CaptureLedger(root, sessionId)
        val manifest = CaptureFiles.manifestOrNull(root)?.takeIf { it.sessionId == sessionId }
        if (manifest == null || ledger.closed || ledger.failure != null) return Outcome.DONE
        val outcome = try {
            upload(manifest, onProgress)
            Outcome.DONE
        } catch (error: CaptureApiException) {
            if (error.retryable) Outcome.RETRY else stop(error)
        } catch (_: IOException) {
            Outcome.RETRY
        }
        if (root.isDirectory) onProgress(progress(manifest, sending = false))
        return outcome
    }

    private suspend fun upload(manifest: LocalManifest, onProgress: (UploadProgress) -> Unit) {
        val choice = ledger.choice
        if (choice == false) {
            if (manifest.finished) close(manifest, continueResearch = false)
            return
        }
        // No server session is opened for a capture that sealed no chunk.
        val remote = ledger.remoteSessionId ?: manifest.chunks.takeIf { it.isNotEmpty() }?.let {
            api.open(manifest.sessionId, CaptureCreateRequest(CaptureLimits.CHUNK_MS.toInt()))
                .id.also(ledger::saveRemoteSessionId)
        }
        remote?.let {
            onSession(it)
            send(manifest, it, onProgress)
        }
        if (manifest.finished && choice == true) close(manifest, continueResearch = true)
    }

    private suspend fun send(
        manifest: LocalManifest,
        remote: String,
        onProgress: (UploadProgress) -> Unit
    ) {
        for (chunk in manifest.chunks.filterNot { ledger.isSent(it.seq) }) {
            onProgress(progress(manifest, sending = true))
            val file = File(root, chunk.fileName)
            if (!file.exists()) {
                throw CaptureApiException(MISSING, "A chunk was deleted before it was sent.", false)
            }
            val acknowledgement = api.putChunk(
                remote,
                chunk.seq,
                metadata(remote, chunk),
                file.readBytes()
            )
            if (!acknowledgement.isStored || acknowledgement.seq != chunk.seq) {
                throw CaptureApiException(UNKNOWN, "The server did not store the chunk.", true)
            }
            ledger.markSent(chunk.seq)
        }
    }

    private suspend fun close(manifest: LocalManifest, continueResearch: Boolean) {
        val remote = ledger.remoteSessionId
        if (remote != null) {
            api.close(
                remote,
                CaptureCloseRequest(continueResearch, manifest.durationMs.toInt())
            )
        }
        ledger.markClosed(continueResearch)
    }

    private fun stop(error: CaptureApiException): Outcome = try {
        ledger.fail(error.code, error.message ?: error.code)
        Outcome.STOPPED
    } catch (_: IOException) {
        // The local capture was deleted while uploading; there is nothing left to retry.
        Outcome.DONE
    }

    private fun metadata(remote: String, chunk: SealedChunk) = CaptureMetadata(
        CaptureChunkRequest(
            sessionId = remote,
            seq = chunk.seq,
            interval = Interval(chunk.startMs, chunk.endMs, Timebase.CAPTURE),
            sizeBytes = chunk.sizeBytes,
            sha256 = chunk.sha256,
            contentType = SealedChunk.CONTENT_TYPE
        ),
        chunk.modality
    )

    private fun progress(manifest: LocalManifest, sending: Boolean) =
        UploadProgress.of(manifest, ledger, api.isTestServer, sending, wifiOnly)

    private companion object {
        const val MISSING = "CAPTURE_CHUNK_MISSING"
        const val UNKNOWN = "CAPTURE_CHUNK_NOT_STORED"
    }
}

/** Runs [CaptureUploader] for one capture session under WorkManager. */
class CaptureUploadWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val session = inputData.getString(CaptureUploads.KEY_SESSION) ?: return Result.failure()
        val uploader = CaptureUploader(
            CaptureApis.current(applicationContext),
            CaptureFiles.rootOf(applicationContext),
            CapturePreferences.wifiOnly(applicationContext)
        ) { CaptureLive.connect(applicationContext, it) }
        return when (uploader.sync(session, CaptureStore::upload)) {
            CaptureUploader.Outcome.RETRY -> Result.retry()
            CaptureUploader.Outcome.DONE, CaptureUploader.Outcome.STOPPED -> Result.success()
        }
    }
}

/**
 * One WorkManager chain per capture session, retried with exponential backoff. It needs any
 * connection, or an unmetered one when the user chose Wi-Fi only.
 */
internal object CaptureUploads {
    const val KEY_SESSION = "session"
    private const val INITIAL_BACKOFF_SECONDS = 10L

    fun schedule(context: Context, sessionId: String, replace: Boolean = false) {
        val network = if (CapturePreferences.wifiOnly(context)) {
            NetworkType.UNMETERED
        } else {
            NetworkType.CONNECTED
        }
        val request = OneTimeWorkRequestBuilder<CaptureUploadWorker>()
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(network).build()
            )
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                INITIAL_BACKOFF_SECONDS,
                TimeUnit.SECONDS
            )
            .setInputData(workDataOf(KEY_SESSION to sessionId))
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "capture-upload-$sessionId",
            if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE,
            request
        )
    }
}
