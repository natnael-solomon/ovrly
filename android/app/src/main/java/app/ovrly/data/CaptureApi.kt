package app.ovrly.data

import app.ovrly.contract.CaptureApiCodec
import app.ovrly.contract.CaptureChunk
import app.ovrly.contract.CaptureCloseRequest
import app.ovrly.contract.CaptureCodec
import app.ovrly.contract.CaptureCreateRequest
import app.ovrly.contract.CaptureMetadata
import app.ovrly.contract.CaptureSession
import app.ovrly.contract.CaptureStatus
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * The live-capture endpoints (BE-06, `services/api/routes/captures.py`) on the shared
 * [ApiClient] through [OvrlyApi]: the same guest credential, `X-Request-Id`, cold-start
 * handling and typed [ApiFailure]s as the share journey. Every call is safe to repeat:
 * create replays by `Idempotency-Key`, a chunk by `(session_id, seq)` and close by its body.
 */
internal class CaptureApi(private val api: OvrlyApi) {
    /** `POST /v1/captures` 201; the same [idempotencyKey] and body replay the first session. */
    suspend fun open(
        idempotencyKey: String,
        request: CaptureCreateRequest
    ): ApiResult<CaptureSession> = api.sendAuthenticated(
        { token ->
            ApiCall(
                "captures.create",
                "POST",
                "v1/captures",
                CaptureApiCodec.encodeCreateRequest(request).toRequestBody(JSON),
                token,
                mapOf(OvrlyApi.IDEMPOTENCY_HEADER to idempotencyKey)
            )
        },
        CaptureCodec::parseSession
    )

    /**
     * `PUT /v1/captures/{id}/chunks/{seq}` as multipart with exactly a `metadata` text part
     * (`{"chunk": ..., "modality": ...}`) and a `content` file part. The server checks the
     * declared size and SHA-256 against the bytes.
     */
    suspend fun putChunk(
        sessionId: String,
        seq: Int,
        metadata: CaptureMetadata,
        content: ByteArray
    ): ApiResult<CaptureChunk> {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(METADATA_PART, CaptureApiCodec.encodeMetadata(metadata))
            .addFormDataPart(
                CONTENT_PART,
                "chunk-$seq",
                content.toRequestBody(metadata.chunk.contentType.toMediaType())
            )
            .build()
        return api.sendAuthenticated(
            { token ->
                ApiCall("captures.chunk", "PUT", "v1/captures/$sessionId/chunks/$seq", body, token)
            },
            CaptureCodec::parseChunk
        )
    }

    /** `POST /v1/captures/{id}/close` with the user's `continue_research` choice. */
    suspend fun close(sessionId: String, request: CaptureCloseRequest): ApiResult<CaptureSession> =
        api.sendAuthenticated(
            { token ->
                ApiCall(
                    "captures.close",
                    "POST",
                    "v1/captures/$sessionId/close",
                    CaptureApiCodec.encodeCloseRequest(request).toRequestBody(JSON),
                    token
                )
            },
            CaptureCodec::parseSession
        )

    /** `GET /v1/captures/{id}`: session, manifest, per-chunk work and claim progress. */
    suspend fun status(sessionId: String): ApiResult<CaptureStatus> = api.sendAuthenticated(
        { token -> ApiCall("captures.status", "GET", "v1/captures/$sessionId", token = token) },
        CaptureApiCodec::parseStatus
    )

    private companion object {
        const val METADATA_PART = "metadata"
        const val CONTENT_PART = "content"
        val JSON = "application/json".toMediaType()
    }
}
