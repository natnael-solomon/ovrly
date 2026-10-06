package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/** Extraction observations only; COMPLETE never means a research report was published. */
@Serializable
internal data class MediaAnalysis(
    val status: AnalysisStatus,
    @SerialName("text_deadline") val textDeadline: String?,
    @SerialName("text_expired") val textExpired: Boolean,
    @SerialName("analyzed_modalities") val analyzedModalities: List<Modality>,
    @SerialName("pending_modalities") val pendingModalities: List<Modality>,
    @SerialName("unavailable_modalities") val unavailableModalities: List<Modality>,
    val gaps: List<AnalysisGap>,
    val text: JsonObject?,
    val captions: List<SpeechSegment>
)

@Serializable
internal data class AnalysisGap(
    val modality: Modality,
    val reason: String,
    val interval: Interval?
)
