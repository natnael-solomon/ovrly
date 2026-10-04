@file:UseSerializers(
    StrictLongSerializer::class,
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

/*
 * Typed models for `upload-declare-request.schema.json`, `upload-complete-request.schema.json`
 * and `upload.schema.json` (contract 0.1.0-draft); the declare and complete responses are
 * both the [Upload] read model. Parse and encode with [UploadCodec].
 */

/** Request body for `POST /v1/uploads`: the bytes the client is about to send. */
@Serializable
internal data class UploadDeclareRequest(
    @SerialName("size_bytes")
    val sizeBytes: Long,
    val sha256: String,
    @SerialName("content_type")
    val contentType: String? = null
) {
    init {
        ContractSyntax.positive("size_bytes", sizeBytes)
        ContractSyntax.sha256("sha256", sha256)
        contentType?.let {
            ContractSyntax.text(
                "content_type",
                it,
                max = MAX_CONTENT_TYPE_LENGTH,
                min = MIN_CONTENT_TYPE_LENGTH
            )
        }
    }

    companion object {
        const val MIN_CONTENT_TYPE_LENGTH = 3
        const val MAX_CONTENT_TYPE_LENGTH = 128
    }
}

/** Request body for `POST /v1/uploads/{id}/complete`: empty today; any field is an error. */
@Serializable
internal data object UploadCompleteRequest

/** Read model of an upload. [isCompleted] is true only for `completed`, never for UNKNOWN. */
@Serializable
internal data class Upload(
    val id: String,
    val state: UploadState,
    val target: String,
    @SerialName("max_bytes")
    val maxBytes: Long,
    @SerialName("declared_size_bytes")
    val declaredSizeBytes: Long,
    @SerialName("declared_sha256")
    val declaredSha256: String,
    @SerialName("content_type")
    val contentType: String,
    @SerialName("expires_at")
    val expiresAt: String,
    @SerialName("created_at")
    val createdAt: String,
    @SerialName("completed_at")
    val completedAt: String?
) {
    val isCompleted: Boolean
        get() = state == UploadState.COMPLETED

    init {
        ContractSyntax.uuid("upload.id", id)
        ContractSyntax.text("upload.target", target, max = MAX_TARGET_LENGTH)
        ContractSyntax.sha256("upload.declared_sha256", declaredSha256)
        ContractSyntax.text(
            "upload.content_type",
            contentType,
            max = UploadDeclareRequest.MAX_CONTENT_TYPE_LENGTH,
            min = UploadDeclareRequest.MIN_CONTENT_TYPE_LENGTH
        )
        ContractSyntax.timestamp("upload.expires_at", expiresAt)
        ContractSyntax.timestamp("upload.created_at", createdAt)
        completedAt?.let { ContractSyntax.timestamp("upload.completed_at", it) }
    }

    companion object {
        const val MAX_TARGET_LENGTH = 512
    }
}
