package app.ovrly.ui

import android.content.Context
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import app.ovrly.contract.ContractJson
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LocalJobState
import kotlinx.serialization.json.JsonObject

/*
 * Previews of the six shared result fixtures through the production parser (#34). The
 * debug build reads `packages/contracts/fixtures/results` in place as assets (see
 * `app/build.gradle.kts`); nothing is copied, and release builds do not contain them.
 */

private const val PREVIEW_NOW = 1_791_200_000_000L

private fun fixture(context: Context, name: String): Investigation {
    val text = context.assets.open("$name.json").bufferedReader().use { it.readText() }
    val root = ContractJson.tolerant.decodeFromString(JsonObject.serializer(), text)
    return InvestigationCodec.parseInvestigation(root.getValue("investigation").toString())
}

@Composable
private fun FixtureReport(name: String, dark: Boolean = false) {
    val context = LocalContext.current
    val investigation = remember(name) { fixture(context, name) }
    OvrlyTheme(dark) {
        Surface(color = LocalOvrlyPalette.current.paper) {
            CheckReportScreen(OpenReport(reportView(investigation)), {})
        }
    }
}

@Preview(name = "Report / complete (corrected v2)", widthDp = 390, heightDp = 1600)
@Composable
private fun CompleteReportPreview() = FixtureReport("complete")

@Preview(name = "Report / partial (capture timebase)", widthDp = 390, heightDp = 1400)
@Composable
private fun PartialReportPreview() = FixtureReport("partial")

@Preview(name = "Report / failed", widthDp = 390, heightDp = 700)
@Composable
private fun FailedReportPreview() = FixtureReport("failed")

@Preview(name = "Report / cancelled", widthDp = 390, heightDp = 700)
@Composable
private fun CancelledReportPreview() = FixtureReport("cancelled")

@Preview(name = "Report / insufficient evidence", widthDp = 390, heightDp = 1400)
@Composable
private fun InsufficientReportPreview() = FixtureReport("insufficient-evidence", dark = true)

@Preview(name = "Report / no claims", widthDp = 390, heightDp = 800)
@Composable
private fun NoClaimsReportPreview() = FixtureReport("no-claims")

@Preview(name = "Inbox and Library / six fixtures", widthDp = 390, heightDp = 1600)
@Composable
private fun InboxPreview() {
    val context = LocalContext.current
    val items = remember {
        listOf("partial", "failed", "cancelled", "complete", "insufficient-evidence", "no-claims")
            .map { name ->
                val investigation = fixture(context, name)
                val record = InvestigationRecord(
                    localId = investigation.id,
                    serverId = investigation.id,
                    state = LocalJobState.ACCEPTED.wireName,
                    sourceKind = investigation.source.kind.wireName,
                    idempotencyKey = "preview",
                    createdAt = PREVIEW_NOW,
                    updatedAt = PREVIEW_NOW
                )
                inboxItem(record, investigation, PREVIEW_NOW)
            }
    }
    OvrlyTheme {
        Surface(color = LocalOvrlyPalette.current.paper) {
            ChecksSections(
                ChecksUiState(
                    loaded = true,
                    inbox = items.filterNot { it.library },
                    library = items.filter { it.library }
                ),
                {},
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp)
            )
        }
    }
}
