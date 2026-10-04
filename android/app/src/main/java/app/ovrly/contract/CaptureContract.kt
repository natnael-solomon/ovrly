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
 * Typed models for `capture-session.schema.json`, `capture-chunk-request.schema.json` and
 * `capture-chunk.schema.json` (contract 0.1.0-draft). Hand-authored schemas: no endpoint
 * serves them yet. Every interval is on the `capture` timebase, and the duplicate and
 * out-of-order rules travel as data. Parse and encode with [CaptureCodec].
 */

/** `common.schema.json#/$defs/seq_range`: inclusive range of chunk sequence numbers. */
@Serializable
internal data class SeqRange(
    @SerialName("from_seq")
    val fromSeq: Int,
    @SerialName("to_seq")
    val toSeq: Int
) {
    init {
        require(fromSeq in 0..toSeq) { "seq_range must satisfy 0 <= from_seq <= to_seq" }
    }
}

/** Read model of one live capture session. [isOpen] is true only for `open`. */
@Serializable
internal data class CaptureSession(
    val id: String,
    @SerialName("investigation_id")
    val investigationId: String,
    val state: CaptureSessionState,
    val timebase: Timebase,
    @SerialName("started_at")
    val startedAt: String,
    @SerialName("closed_at")
    val closedAt: String?,
    @SerialName("max_duration_ms")
    val maxDurationMs: Long,
    @SerialName("chunk_duration_ms")
    val chunkDurationMs: Long,
    @SerialName("chunks_received")
    val chunksReceived: Int,
    @SerialName("highest_seq")
    val highestSeq: Int?,
    @SerialName("received_ms")
    val receivedMs: Long,
    val gaps: List<SeqRange>,
    @SerialName("duplicate_handling")
    val duplicateHandling: String,
    @SerialName("out_of_order_handling")
    val outOfOrderHandling: String
) {
    val isOpen: Boolean
        get() = state == CaptureSessionState.OPEN

    init {
        ContractSyntax.uuid("session.id", id)
        ContractSyntax.uuid("session.investigation_id", investigationId)
        require(timebase == Timebase.CAPTURE) { "session.timebase must be capture" }
        ContractSyntax.timestamp("session.started_at", startedAt)
        closedAt?.let { ContractSyntax.timestamp("session.closed_at", it) }
        require(duplicateHandling == DUPLICATE_HANDLING) {
            "session.duplicate_handling must be $DUPLICATE_HANDLING"
        }
        require(outOfOrderHandling == OUT_OF_ORDER_HANDLING) {
            "session.out_of_order_handling must be $OUT_OF_ORDER_HANDLING"
        }
    }

    companion object {
        const val DUPLICATE_HANDLING = "replay_acknowledgement"
        const val OUT_OF_ORDER_HANDLING = "accept_and_record_gaps"
    }
}

/** Declaration of one capture chunk; `(session_id, seq)` is the idempotency key. */
@Serializable
internal data class CaptureChunkRequest(
    @SerialName("session_id")
    val sessionId: String,
    val seq: Int,
    val interval: Interval,
    @SerialName("size_bytes")
    val sizeBytes: Long,
    val sha256: String,
    @SerialName("content_type")
    val contentType: String
) {
    init {
        ContractSyntax.uuid("session_id", sessionId)
        require(seq >= 0) { "seq must be zero or positive" }
        require(interval.timebase == Timebase.CAPTURE) { "interval.timebase must be capture" }
        ContractSyntax.sha256("sha256", sha256)
        ContractSyntax.text(
            "content_type",
            contentType,
            max = UploadDeclareRequest.MAX_CONTENT_TYPE_LENGTH,
            min = UploadDeclareRequest.MIN_CONTENT_TYPE_LENGTH
        )
    }
}

/**
 * Acknowledgement of one capture chunk. [isStored] is true when the server holds this
 * `(session, seq)` after the acknowledgement: `stored`, `out_of_order` (stored with a gap
 * below it) and `duplicate` (already held, nothing stored twice). An undefined disposition
 * is never stored; [gaps] says what to resend.
 */
@Serializable
internal data class CaptureChunk(
    @SerialName("session_id")
    val sessionId: String,
    val seq: Int,
    val interval: Interval,
    @SerialName("size_bytes")
    val sizeBytes: Long,
    val sha256: String,
    val disposition: ChunkDisposition,
    @SerialName("received_at")
    val receivedAt: String,
    val gaps: List<SeqRange>
) {
    val isStored: Boolean
        get() = disposition != ChunkDisposition.UNKNOWN

    init {
        ContractSyntax.uuid("chunk.session_id", sessionId)
        require(seq >= 0) { "chunk.seq must be zero or positive" }
        require(interval.timebase == Timebase.CAPTURE) { "chunk.interval.timebase must be capture" }
        ContractSyntax.sha256("chunk.sha256", sha256)
        ContractSyntax.timestamp("chunk.received_at", receivedAt)
    }
}
