package app.ovrly.contract

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

class AnalysisContractTest {
    @Test
    fun extractionStatusIsSeparateFromResearchProgressAndToleratesUnknownStatus() {
        val base = ContractFixtures.load(ContractFixtures.RESULTS).first().investigation!!.jsonObject
        val analysis = """
            {"status":"no_usable","text_deadline":"2026-10-06T12:01:00Z","text_expired":true,
             "analyzed_modalities":[],"pending_modalities":[],"unavailable_modalities":["speech","text"],
             "gaps":[],"text":null,"captions":[]}
        """.trimIndent()
        fun parse(value: String) = InvestigationCodec.parseInvestigation(
            JsonObject(base + ("analysis" to ContractFixtures.element(value))).toString()
        )
        val result = parse(analysis)
        assertEquals(AnalysisStatus.NO_USABLE, result.analysis?.status)
        assertEquals(InvestigationCodec.parseInvestigation(base.toString()).report, result.report)
        assertEquals(
            result,
            InvestigationCodec.parseInvestigation(InvestigationCodec.encodeInvestigation(result))
        )
        assertEquals(
            AnalysisStatus.UNKNOWN,
            parse(analysis.replace("no_usable", "future")).analysis?.status
        )
    }
}
