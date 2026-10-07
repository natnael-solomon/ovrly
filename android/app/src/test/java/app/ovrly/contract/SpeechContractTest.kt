package app.ovrly.contract

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class SpeechContractTest {
    private val base = ContractFixtures.load(ContractFixtures.RESULTS).first()
        .investigation!!.jsonObject
    private val digest = "a".repeat(64)
    private val speech = """
        {
          "status":"completed", "reason":null, "provider":"groq", "model":"synthetic-model",
          "processing_version":1, "source_sha256":"$digest", "audio_sha256":"$digest",
          "settings_sha256":"$digest",
          "segments":[{"text":"Invented speech","interval":{"start_ms":100,"end_ms":900,"timebase":"media"}}]
        }
    """.trimIndent()

    private fun parse(value: String): Investigation = InvestigationCodec.parseInvestigation(
        JsonObject(base + ("speech" to ContractFixtures.element(value))).toString()
    )

    @Test
    fun timedSpeechRoundTripsSeparatelyFromReportAndCoverage() {
        val result = parse(speech)
        assertEquals(SpeechStatus.COMPLETED, result.speech?.status)
        assertEquals(Timebase.MEDIA, result.speech?.segments?.single()?.interval?.timebase)
        assertEquals(
            result,
            InvestigationCodec.parseInvestigation(InvestigationCodec.encodeInvestigation(result))
        )
        assertEquals(
            InvestigationCodec.parseInvestigation(base.toString()).coverage,
            result.coverage
        )
        assertEquals(
            SpeechStatus.UNKNOWN,
            parse(speech.replace("completed", "future")).speech?.status
        )
    }

    @Test
    fun everyUnavailableReasonAndNoSpeechAreTypedAndFutureReasonsAreUnknown() {
        for (reason in SpeechReason.entries.filter { it != SpeechReason.UNKNOWN }) {
            val value = speech.replace("\"reason\":null", "\"reason\":\"${reason.wireName}\"")
            assertEquals(reason, parse(value).speech?.reason)
        }
        assertEquals(
            SpeechReason.UNKNOWN,
            parse(speech.replace("\"reason\":null", "\"reason\":\"future\"")).speech?.reason
        )
    }

    @Test
    fun invalidSpeechIsRejectedRatherThanDroppedAsAnAdditiveField() {
        for (invalid in listOf(
            speech.replace("\"processing_version\":1", "\"processing_version\":1.5"),
            speech.replace(digest, "not-a-hash"),
            speech.replace("Invented speech", ""),
            speech.replace("\"end_ms\":900", "\"end_ms\":50"),
            speech.replace("\"reason\":null,", "")
        )) {
            assertThrows(ContractParseException::class.java) { parse(invalid) }
        }
    }
}
