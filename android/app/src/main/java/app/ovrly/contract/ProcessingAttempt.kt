@file:UseSerializers(StrictIntSerializer::class)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

@Serializable
internal data class ProcessingAttempt(
    val provider: String,
    val model: String?,
    @SerialName("decision_id") val decisionId: String?,
    val task: String,
    val outcome: String,
    @SerialName("prompt_tokens") val promptTokens: Int?,
    @SerialName("completion_tokens") val completionTokens: Int?,
    @SerialName("total_tokens") val totalTokens: Int?,
    @SerialName("thinking_leaked") val thinkingLeaked: Boolean,
    val fenced: Boolean,
    val repair: Boolean,
    val feedback: String?
) {
    init {
        require(provider.isNotBlank() && task.isNotBlank() && outcome.isNotBlank()) {
            "processing provenance needs nonblank provider, task and outcome"
        }
        require(listOf(promptTokens, completionTokens, totalTokens).all { it == null || it >= 0 }) {
            "token usage must be nonnegative"
        }
    }
}
