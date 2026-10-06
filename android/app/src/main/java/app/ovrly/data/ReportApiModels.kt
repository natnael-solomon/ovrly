@file:UseSerializers(
    StrictLongSerializer::class,
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.data

import app.ovrly.contract.ContractBooleanSerializer
import app.ovrly.contract.ContractJson
import app.ovrly.contract.ContractParseException
import app.ovrly.contract.ContractSyntax
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.Job
import app.ovrly.contract.StrictIntSerializer
import app.ovrly.contract.StrictLongSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * BE-10 (#33) bodies that `packages/contracts/openapi.json` defines inline rather than as
 * schema files: the report version list, the reanalysis request and receipt, and the job
 * cancel receipt. Read models are parsed tolerantly; nothing here becomes success-shaped
 * when a required field is missing.
 */

/** One entry of `GET /v1/investigations/{id}/reports`: what changed, without claims. */
@Serializable
internal data class ReportVersionSummary(
    val id: String,
    val version: Int,
    @SerialName("created_at")
    val createdAt: String,
    val provisional: Boolean,
    @SerialName("change_summary")
    val changeSummary: String,
    val supersedes: String?,
    /** True only for a development stub (`OVRLY_STUB_REPORTS`); never a check of the media. */
    val fixture: Boolean
) {
    init {
        ContractSyntax.opaqueId("summary.id", id)
        require(version >= 1) { "summary.version starts at 1" }
        ContractSyntax.timestamp("summary.created_at", createdAt)
        supersedes?.let { ContractSyntax.opaqueId("summary.supersedes", it) }
    }
}

@Serializable
internal data class ReportVersionList(
    @SerialName("investigation_id")
    val investigationId: String,
    val items: List<ReportVersionSummary>
) {
    init {
        ContractSyntax.uuid("investigation_id", investigationId)
    }
}

/**
 * Body of `POST /v1/investigations/{id}/reanalyze`. Reanalysis always starts from the latest
 * version, named by [baseVersion]; earlier versions never change.
 */
internal sealed interface ReanalysisRequest {
    val baseVersion: Int
    val reason: String

    /** Replaces one claim's normalized meaning; the original wording is kept by the server. */
    data class Correction(
        override val baseVersion: Int,
        val claimId: String,
        val proposition: String
    ) : ReanalysisRequest {
        override val reason: String get() = "correction"

        init {
            ContractSyntax.opaqueId("claim_id", claimId)
            require(proposition.isNotBlank()) { "proposition must not be blank" }
            ContractSyntax.text("proposition", proposition, max = MAX_PROPOSITION_LENGTH)
        }
    }

    /**
     * Checks the full video a captured clip was matched to: [sourceInvestigationId] is the
     * shared check the user picked and confirmed as that video. Only built after that
     * confirmation on screen, so `match_confirmed` is always true on the wire.
     */
    data class Expansion(override val baseVersion: Int, val sourceInvestigationId: String) :
        ReanalysisRequest {
        override val reason: String get() = "expansion"

        init {
            ContractSyntax.uuid("source_investigation_id", sourceInvestigationId)
        }
    }

    /** Searches further for evidence on the same claims. */
    data class Deeper(override val baseVersion: Int) : ReanalysisRequest {
        override val reason: String get() = "deeper"
    }

    companion object {
        const val MAX_PROPOSITION_LENGTH = 2000
    }
}

internal fun ReanalysisRequest.encode(): String {
    require(baseVersion >= 1) { "base_version starts at 1" }
    return buildJsonObject {
        put("reason", reason)
        put("base_version", baseVersion)
        when (val request = this@encode) {
            is ReanalysisRequest.Correction -> {
                put("claim_id", request.claimId)
                put("proposition", request.proposition)
            }

            is ReanalysisRequest.Expansion -> {
                put("match_confirmed", true)
                put("source_investigation_id", request.sourceInvestigationId)
            }

            is ReanalysisRequest.Deeper -> Unit
        }
    }.toString()
}

/** 202 receipt of an accepted reanalysis; replayed unchanged for the same key and body. */
@Serializable
internal data class ReanalysisReceipt(
    val id: String,
    @SerialName("investigation_id")
    val investigationId: String,
    val reason: String,
    @SerialName("base_version")
    val baseVersion: Int,
    /** The version a correction published at once; null for expansion and deeper. */
    @SerialName("published_version")
    val publishedVersion: Int?,
    val job: Job,
    @SerialName("created_at")
    val createdAt: String,
    /** The confirmed full-video check of an expansion; null for other reasons. */
    @SerialName("source_investigation_id")
    val sourceInvestigationId: String? = null
) {
    init {
        ContractSyntax.uuid("id", id)
        ContractSyntax.uuid("investigation_id", investigationId)
        ContractSyntax.timestamp("created_at", createdAt)
        sourceInvestigationId?.let { ContractSyntax.uuid("source_investigation_id", it) }
    }
}

/** `POST /v1/jobs/{id}/cancel` receipt: `effective` (200) or `requested` (202). */
@Serializable
internal data class CancelReceipt(
    @SerialName("job_id")
    val jobId: String,
    val cancellation: String
) {
    val effective: Boolean get() = cancellation == "effective"
}

internal object ReportApiCodec {
    fun parseVersions(payload: String): ReportVersionList = ContractJson.parse("report list") {
        ContractJson.tolerant.decodeFromString(ReportVersionList.serializer(), payload)
    }

    fun parseReceipt(payload: String): ReanalysisReceipt = ContractJson.parse("reanalysis") {
        ContractJson.tolerant.decodeFromString(ReanalysisReceipt.serializer(), payload)
    }

    fun parseCancel(payload: String): CancelReceipt = ContractJson.parse("cancel receipt") {
        ContractJson.tolerant.decodeFromString(CancelReceipt.serializer(), payload)
    }

    /**
     * `GET /v1/investigations`. Every item goes through the contract parser; one item this
     * client cannot read fails the whole list instead of silently dropping it.
     */
    fun parseInvestigationList(payload: String): List<Investigation> {
        val root = ContractJson.parse("investigation list") {
            ContractJson.tolerant.decodeFromString(JsonObject.serializer(), payload)
        }
        val items = root["items"] as? JsonArray
            ?: throw ContractParseException("Invalid investigation list: items is required")
        return items.map { InvestigationCodec.parseInvestigation(it.toString()) }
    }
}
