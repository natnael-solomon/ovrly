package app.ovrly.ui

import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.ComposeContentTestRule
import androidx.compose.ui.test.onNodeWithText
import app.ovrly.contract.ContractJson
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LocalJobState
import app.ovrly.testing.Device
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * The shared result fixtures (`packages/contracts/fixtures/results`), which the debug build
 * ships as assets for its previews, parsed with the production codec for the #34 screens.
 */
internal object ChecksFixtures {
    const val NOW = 1_791_200_000_000L

    private fun root(name: String): JsonObject {
        val text = Device.context.assets.open("$name.json").bufferedReader().use { it.readText() }
        return ContractJson.tolerant.decodeFromString(JsonObject.serializer(), text)
    }

    fun investigation(
        name: String,
        vararg changes: Pair<List<String>, JsonElement>
    ): Investigation {
        val payload = changes.fold(root(name).getValue("investigation")) { json, (path, value) ->
            replace(json, path, value)
        }
        return InvestigationCodec.parseInvestigation(payload.toString())
    }

    fun record(investigation: Investigation, retriedAs: String? = null) = InvestigationRecord(
        localId = "local-${investigation.id}",
        serverId = investigation.id,
        state = LocalJobState.ACCEPTED.wireName,
        sourceKind = investigation.source.kind.wireName,
        idempotencyKey = "synthetic-key",
        createdAt = NOW,
        updatedAt = NOW,
        retriedAs = retriedAs
    )

    fun item(investigation: Investigation, retriedAs: String? = null): InboxItem =
        inboxItem(record(investigation, retriedAs), investigation, NOW)

    private fun replace(element: JsonElement, path: List<String>, value: JsonElement): JsonElement {
        if (path.isEmpty()) return value
        val head = path.first()
        val rest = path.drop(1)
        return when (element) {
            is JsonObject -> JsonObject(
                element + (head to replace(element.getValue(head), rest, value))
            )

            is JsonArray -> JsonArray(
                element.mapIndexed { index, item ->
                    if (index == head.toInt()) replace(item, rest, value) else item
                }
            )

            else -> error("cannot descend into $element with $head")
        }
    }
}

/**
 * Waits until [text] is on screen. A modal bottom sheet is composed before it has slid in,
 * so asserting at once can see it present but not yet displayed.
 */
internal fun ComposeContentTestRule.awaitDisplayed(text: String, timeoutMillis: Long = 5_000) {
    waitUntil(timeoutMillis) {
        try {
            onNodeWithText(text).assertIsDisplayed()
            true
        } catch (_: AssertionError) {
            false
        }
    }
}

/** Ids of the nodes that show [text] now, to tell an opened sheet's copy from the screen's. */
internal fun ComposeContentTestRule.idsWithText(text: String): Set<Int> =
    onAllNodes(hasText(text)).fetchSemanticsNodes().map(SemanticsNode::id).toSet()

/** The one node with [text] that was not there when [before] was taken. */
internal fun ComposeContentTestRule.newNodeWithText(
    text: String,
    before: Set<Int>
): SemanticsNodeInteraction {
    val added = idsWithText(text) - before
    check(added.size == 1) { "expected one new \"$text\", found ${added.size}" }
    val id = added.single()
    return onNode(hasText(text) and SemanticsMatcher("node $id") { it.id == id })
}
