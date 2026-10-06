package app.ovrly.data

import app.ovrly.contract.ContractJson
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.InvestigationCreateRequest
import app.ovrly.contract.Upload
import app.ovrly.contract.UploadCodec
import app.ovrly.contract.UploadCompleteRequest
import app.ovrly.contract.UploadDeclareRequest
import java.io.File
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import okio.buffer
import okio.source

/**
 * The `/v1` endpoints the share journey uses, on top of [ApiClient]. Authenticated calls
 * use the stored guest credential, minting one with `POST /v1/principals/guest` on first
 * need. If the server refuses the stored credential (expired workspace, BC-D06), it is
 * dropped and the call is repeated once with a new guest identity.
 */
internal class OvrlyApi(private val client: ApiClient, private val credentials: CredentialStore) {
    private val minting = Mutex()

    @Volatile
    private var cached: String? = null

    val waking: StateFlow<Boolean> get() = client.waking

    suspend fun declareUpload(request: UploadDeclareRequest): ApiResult<Upload> = authenticated {
        ApiCall(
            "uploads.declare",
            "POST",
            "v1/uploads",
            jsonBody(UploadCodec.encodeDeclareRequest(request)),
            it
        )
    }.let { call -> call(UploadCodec::parseUpload) }

    /** Streams [file] to the upload's own target; [progress] receives bytes sent so far. */
    suspend fun putContent(upload: Upload, file: File, progress: (Long) -> Unit): ApiResult<Unit> {
        val body = FileBody(file, upload.contentType, progress)
        return authenticated { ApiCall("uploads.content", "PUT", upload.target, body, it) }
            .let { call -> call { } }
    }

    suspend fun completeUpload(uploadId: String): ApiResult<Upload> = authenticated {
        val body = jsonBody(UploadCodec.encodeCompleteRequest(UploadCompleteRequest))
        ApiCall("uploads.complete", "POST", "v1/uploads/$uploadId/complete", body, it)
    }.let { call -> call(UploadCodec::parseUpload) }

    /** Creates an investigation; the same [idempotencyKey] and body replay the first answer. */
    suspend fun createInvestigation(
        request: InvestigationCreateRequest,
        idempotencyKey: String
    ): ApiResult<Investigation> = authenticated {
        ApiCall(
            "investigations.create",
            "POST",
            "v1/investigations",
            jsonBody(InvestigationCodec.encodeCreateRequest(request)),
            it,
            mapOf(IDEMPOTENCY_HEADER to idempotencyKey)
        )
    }.let { call -> call(InvestigationCodec::parseInvestigation) }

    suspend fun getInvestigation(id: String): ApiResult<Investigation> = authenticated {
        ApiCall("investigations.get", "GET", "v1/investigations/$id", token = it)
    }.let { call -> call(InvestigationCodec::parseInvestigation) }

    /** Builds an authenticated call; invoking the result sends it with the re-mint rule. */
    private fun authenticated(build: (String) -> ApiCall) = AuthenticatedCall(build)

    private inner class AuthenticatedCall(private val build: (String) -> ApiCall) {
        suspend operator fun <T> invoke(parse: (String) -> T): ApiResult<T> {
            val first = sendWith(credential(), parse)
            val rejected = first is ApiResult.Failure && first.failure.isCredentialRejected
            if (rejected) forgetCredential()
            return if (rejected) sendWith(credential(), parse) else first
        }

        private suspend fun <T> sendWith(token: ApiResult<String>, parse: (String) -> T) =
            when (token) {
                is ApiResult.Failure -> token
                is ApiResult.Success -> client.send(build(token.value), parse)
            }
    }

    /** The stored credential, or a newly minted guest one. Concurrent callers mint once. */
    private suspend fun credential(): ApiResult<String> = minting.withLock {
        (cached ?: credentials.read())?.let {
            cached = it
            return@withLock ApiResult.Success(it, "")
        }
        val call = ApiCall("principals.guest", "POST", "v1/principals/guest", jsonBody("{}"))
        when (val minted = client.send(call, ::parseGuest)) {
            is ApiResult.Failure -> minted

            is ApiResult.Success -> {
                val token = minted.value.credential.token
                cached = token
                credentials.write(token)
                ApiResult.Success(token, minted.requestId)
            }
        }
    }

    private fun forgetCredential() {
        cached = null
        credentials.clear()
    }

    private class FileBody(
        private val file: File,
        contentType: String,
        private val progress: (Long) -> Unit
    ) : RequestBody() {
        private val type = contentType.toMediaTypeOrNull()

        override fun contentType() = type

        override fun contentLength() = file.length()

        override fun writeTo(sink: BufferedSink) {
            file.source().buffer().use { source ->
                var sent = 0L
                while (true) {
                    val read = source.read(sink.buffer, CHUNK_BYTES)
                    if (read < 0) break
                    sink.emitCompleteSegments()
                    sent += read
                    progress(sent)
                }
            }
        }
    }

    companion object {
        const val IDEMPOTENCY_HEADER = "Idempotency-Key"
        private const val CHUNK_BYTES = 64L * 1024
        private val JSON = "application/json".toMediaType()

        private fun jsonBody(json: String) = json.toRequestBody(JSON)

        /** `POST /v1/principals/guest` 201; not part of `packages/contracts` schemas yet. */
        fun parseGuest(payload: String): GuestPrincipal = ContractJson.parse("guest principal") {
            ContractJson.tolerant.decodeFromString(GuestPrincipal.serializer(), payload)
        }
    }
}

/** Guest principal response; the token is opaque and is never logged. */
@Serializable
internal data class GuestPrincipal(
    @SerialName("principal_id")
    val principalId: String,
    val kind: String,
    val credential: GuestCredential
) {
    init {
        require(kind == "guest") { "kind must be guest" }
    }
}

@Serializable
internal data class GuestCredential(
    val token: String,
    @SerialName("token_type")
    val tokenType: String
) {
    init {
        require(token.isNotEmpty() && token.none { it.isWhitespace() }) {
            "credential.token must be a non-empty opaque string"
        }
        require(tokenType == "bearer") { "credential.token_type must be bearer" }
    }

    override fun toString() = "GuestCredential(token=<redacted>, tokenType=$tokenType)"
}
