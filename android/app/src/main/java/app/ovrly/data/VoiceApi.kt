package app.ovrly.data

import app.ovrly.contract.ContractParseException
import app.ovrly.contract.VoiceActionCodec
import app.ovrly.contract.VoiceActionRequest
import app.ovrly.contract.VoiceActionResponse
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * `POST /v1/voice/actions` (BE-10, #33) on the shared [OvrlyApi] credential and [ApiClient].
 *
 * The server answers an allowlisted, well-formed request with 200 and a voice response whose
 * `result` is `accepted` or `denied`; a schema failure is 422 `VALIDATION_FAILED` and a
 * `request_id` reused for a different action or target is 409 `IDEMPOTENCY_KEY_REUSED`, both
 * in the shared error shape. Repeating a request with the same `request_id` replays the first
 * response, so a retry never runs an action twice.
 */
internal class VoiceApi(private val api: OvrlyApi) {
    suspend fun perform(request: VoiceActionRequest): ApiResult<VoiceActionResponse> {
        val body = VoiceActionCodec.encodeRequest(request).toRequestBody(JSON)
        return api.sendAuthenticated(
            { token -> ApiCall("voice.actions", "POST", "v1/voice/actions", body, token) }
        ) { payload ->
            VoiceActionCodec.parseResponse(payload).also {
                // A response for another request would be someone else's outcome; never use it.
                if (it.requestId != request.requestId) {
                    throw ContractParseException(
                        "voice response request_id does not echo the request"
                    )
                }
            }
        }
    }

    private companion object {
        val JSON = "application/json".toMediaType()
    }
}
