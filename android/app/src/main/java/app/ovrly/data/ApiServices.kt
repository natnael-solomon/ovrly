package app.ovrly.data

import android.content.Context
import app.ovrly.BuildConfig
import app.ovrly.share.ShareStaging
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * What open intake sheets own: the local ids of their shares and their staging directories,
 * registered before any byte is staged. Reconciliation leaves both alone.
 */
internal class LiveShares {
    private val ids = ConcurrentHashMap.newKeySet<String>()
    private val directories = ConcurrentHashMap.newKeySet<File>()

    fun add(localId: String) {
        ids += localId
    }

    fun remove(localId: String) {
        ids -= localId
    }

    operator fun contains(localId: String): Boolean = localId in ids

    fun addDirectory(directory: File) {
        directories += directory
    }

    fun removeDirectory(directory: File) {
        directories -= directory
    }

    /** Staging directories of open sheets; never swept. */
    fun directories(): Set<File> = directories.toSet()
}

/**
 * The process-wide data layer: one [OvrlyApi] (one OkHttp client, one guest credential), the
 * Room-backed [LocalJobs] and [InvestigationRepository], and the [Reconciler]. Entry point
 * for share intake and for #31 and #34.
 */
internal class ApiServices(
    val api: OvrlyApi,
    val jobs: LocalJobs,
    val investigations: InvestigationRepository,
    val reconciler: Reconciler? = null,
    val live: LiveShares = LiveShares(),
    /** For store writes that must outlive a screen, such as forgetting an abandoned share. */
    val background: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    companion object {
        @Volatile
        private var shared: ApiServices? = null

        /** Null when the build's `OVRLY_API_BASE_URL` is not a usable http(s) base URL. */
        fun get(context: Context): ApiServices? = shared ?: synchronized(this) {
            shared ?: create(context.applicationContext)?.also { shared = it }
        }

        /** Upload cap checked on the device; mirrors the backend `OVRLY_UPLOAD_MAX_BYTES`. */
        val uploadMaxBytes: Long get() = BuildConfig.OVRLY_UPLOAD_MAX_BYTES

        private fun create(context: Context): ApiServices? {
            val url = baseUrl(BuildConfig.OVRLY_API_BASE_URL) ?: return null
            val api = OvrlyApi(ApiClient(url), KeystoreCredentialStore(context))
            val jobs = LocalJobs(OvrlyDatabase.open(context).investigations())
            val investigations = InvestigationRepository(api, jobs)
            val live = LiveShares()
            val reconciler = Reconciler(
                api,
                jobs,
                investigations,
                ShareStaging.root(context),
                uploadMaxBytes,
                live
            )
            return ApiServices(api, jobs, investigations, reconciler, live)
        }

        /** Parses a base URL; the path always ends in `/` so `v1/...` resolves under it. */
        fun baseUrl(value: String): HttpUrl? = value.toHttpUrlOrNull()
            ?.takeIf { it.query == null && it.fragment == null && it.username.isEmpty() }
            ?.let { url ->
                if (url.encodedPath.endsWith("/")) {
                    url
                } else {
                    url.newBuilder().encodedPath(url.encodedPath + "/").build()
                }
            }
    }
}
