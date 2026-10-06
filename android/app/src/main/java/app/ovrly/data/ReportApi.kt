package app.ovrly.data

import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ReportVersion
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * The BE-10 (#33) report routes the Inbox and Report screens use (#34), sent through
 * [OvrlyApi.sendAuthenticated]: the same credential and OkHttp client, no second HTTP client.
 */
internal class ReportApi(private val api: OvrlyApi) {
    /** The caller's investigations, so the Inbox also shows checks this device did not share. */
    suspend fun listInvestigations(): ApiResult<List<Investigation>> = api.sendAuthenticated(
        { ApiCall("investigations.list", "GET", "v1/investigations", token = it) },
        ReportApiCodec::parseInvestigationList
    )

    /** Every published version of [id], oldest first, without claims or evidence. */
    suspend fun versions(id: String): ApiResult<ReportVersionList> = api.sendAuthenticated(
        { ApiCall("reports.list", "GET", "v1/investigations/$id/reports", token = it) },
        ReportApiCodec::parseVersions
    )

    /** One immutable report version. */
    suspend fun version(id: String, version: Int): ApiResult<ReportVersion> = api.sendAuthenticated(
        { ApiCall("reports.get", "GET", "v1/investigations/$id/reports/$version", token = it) },
        InvestigationCodec::parseReportVersion
    )

    /** Correction, confirmed expansion or deeper search; the same key and body replay. */
    suspend fun reanalyze(
        id: String,
        request: ReanalysisRequest,
        idempotencyKey: String
    ): ApiResult<ReanalysisReceipt> = api.sendAuthenticated(
        {
            ApiCall(
                "investigations.reanalyze",
                "POST",
                "v1/investigations/$id/reanalyze",
                request.encode().toRequestBody(JSON),
                it,
                mapOf(OvrlyApi.IDEMPOTENCY_HEADER to idempotencyKey)
            )
        },
        ReportApiCodec::parseReceipt
    )

    /** Requests cancellation of [jobId]; the route takes no body. */
    suspend fun cancelJob(jobId: String): ApiResult<CancelReceipt> = api.sendAuthenticated(
        { ApiCall("jobs.cancel", "POST", "v1/jobs/$jobId/cancel", EMPTY, it) },
        ReportApiCodec::parseCancel
    )

    private companion object {
        val JSON = "application/json".toMediaType()

        /** Cancel accepts no body; OkHttp still needs one for POST. */
        val EMPTY = ByteArray(0).toRequestBody()
    }
}
