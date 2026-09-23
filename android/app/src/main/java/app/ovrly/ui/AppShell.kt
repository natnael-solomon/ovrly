package app.ovrly.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

enum class AppDestination(val label: String) {
    SPACE("Your space"), EXPLORE("Explore"), SETTINGS("Settings"),
}

@Composable
fun AppShell(
    destination: AppDestination,
    onDestination: (AppDestination) -> Unit,
    activeSession: String?,
    settings: @Composable () -> Unit,
) {
    val p = LocalOvrlyPalette.current
    val pages = rememberSaveableStateHolder()
    var selectedReport by rememberSaveable { mutableStateOf<String?>(null) }
    var savedIds by rememberSaveable { mutableStateOf(SampleReports.take(3).map { it.id }) }
    val report = SampleReports.firstOrNull { it.id == selectedReport }
    val showingReport = destination != AppDestination.SETTINGS && report != null
    val navigate: (AppDestination) -> Unit = {
        selectedReport = null
        onDestination(it)
    }
    BackHandler(enabled = showingReport || destination != AppDestination.SPACE) {
        if (showingReport) selectedReport = null else navigate(AppDestination.SPACE)
    }
    Scaffold(
        containerColor = p.paper,
        bottomBar = {
            Column(Modifier.background(p.paper).navigationBarsPadding()) {
                if (activeSession != null) {
                    TextButton(onClick = { navigate(AppDestination.SETTINGS) },
                        modifier = Modifier.fillMaxWidth()) {
                        Text("$activeSession / Open controls", color = p.error)
                    }
                }
                Surface(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    color = p.surface,
                    shape = RoundedCornerShape(24.dp),
                    border = BorderStroke(1.dp, p.rule.copy(alpha = 0.7f)),
                ) {
                    Row(Modifier.fillMaxWidth().selectableGroup().padding(4.dp)) {
                        AppDestination.entries.forEach { tab ->
                            val selected = destination == tab
                            val tint = if (selected) p.ink else p.muted
                            Column(
                                Modifier.weight(1f).clip(RoundedCornerShape(20.dp))
                                    .background(if (selected) p.accent.copy(alpha = 0.1f) else androidx.compose.ui.graphics.Color.Transparent)
                                    .selectable(selected, role = Role.Tab, onClick = { navigate(tab) })
                                    .heightIn(min = 72.dp).padding(horizontal = 4.dp, vertical = 8.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(4.dp, Alignment.CenterVertically),
                            ) {
                                Icon(
                                    when (tab) {
                                        AppDestination.SPACE -> Icons.Outlined.Home
                                        AppDestination.EXPLORE -> Icons.Outlined.Search
                                        AppDestination.SETTINGS -> Icons.Outlined.Settings
                                    },
                                    contentDescription = null, tint = tint, modifier = Modifier.size(20.dp),
                                )
                                Text(tab.label, style = MaterialTheme.typography.labelSmall,
                                    color = tint, textAlign = TextAlign.Center)
                                Box(Modifier.width(16.dp).height(2.dp).background(
                                    if (selected) p.accent else androidx.compose.ui.graphics.Color.Transparent,
                                    RoundedCornerShape(1.dp)))
                            }
                        }
                    }
                }
            }
        },
    ) { insets ->
        Box(Modifier.fillMaxSize().padding(insets).consumeWindowInsets(insets)) {
            pages.SaveableStateProvider(if (showingReport) "report-${report?.id}" else destination.name) {
                if (showingReport) {
                    SampleReportScreen(
                        report, saved = report.id in savedIds,
                        onBack = { selectedReport = null },
                        onSave = {
                            savedIds = if (report.id in savedIds) savedIds - report.id else savedIds + report.id
                        },
                    )
                } else when (destination) {
                    AppDestination.SPACE -> YourSpaceScreen(
                        reports = SampleReports.filter { it.id in savedIds },
                        onOpen = { selectedReport = it.id },
                        onExplore = { navigate(AppDestination.EXPLORE) },
                    )
                    AppDestination.EXPLORE -> ExploreScreen(onOpen = { selectedReport = it.id })
                    AppDestination.SETTINGS -> settings()
                }
            }
        }
    }
}
