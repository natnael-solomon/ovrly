@file:UseSerializers(
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

@Serializable
internal data class MediaCoverage(
    @SerialName("has_audio") val hasAudio: Boolean,
    @SerialName("has_video") val hasVideo: Boolean,
    @SerialName("speech_status") val speechStatus: MediaSpeechStatus,
    @SerialName("text_status") val textStatus: MediaTextStatus,
    @SerialName("speech_unavailable_reason") val speechUnavailableReason: SpeechUnavailableReason?
)

@Serializable
internal data class SpeechSegment(val text: String, val interval: Interval) {
    init {
        require(text.isNotEmpty()) { "speech text must not be empty" }
    }
}

/** Hosted speech remains separate from media eligibility, screen text and report findings. */
@Serializable
internal data class SpeechResult(
    val status: SpeechStatus,
    val reason: SpeechReason?,
    val provider: ASRProvider?,
    val model: String?,
    @SerialName("processing_version") val processingVersion: Int?,
    @SerialName("source_sha256") val sourceSha256: String?,
    @SerialName("audio_sha256") val audioSha256: String?,
    @SerialName("settings_sha256") val settingsSha256: String?,
    val segments: List<SpeechSegment>
) {
    init {
        sourceSha256?.let { ContractSyntax.sha256("source_sha256", it) }
        audioSha256?.let { ContractSyntax.sha256("audio_sha256", it) }
        settingsSha256?.let { ContractSyntax.sha256("settings_sha256", it) }
    }
}
