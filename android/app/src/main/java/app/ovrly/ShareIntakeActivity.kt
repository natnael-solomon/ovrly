package app.ovrly

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import app.ovrly.data.ApiServices
import app.ovrly.share.IntakeAction
import app.ovrly.share.IntakeDependencies
import app.ovrly.share.IntakeState
import app.ovrly.share.ShareInputReader
import app.ovrly.share.ShareIntakeController
import app.ovrly.share.ShareLimits
import app.ovrly.share.ShareRead
import app.ovrly.share.ShareStaging
import app.ovrly.share.ShareSummary
import app.ovrly.ui.AppearanceStore
import app.ovrly.ui.OvrlyTheme
import app.ovrly.ui.ShareIntakeSheet
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * The share target. A translucent activity that shows the intake sheet over the app the
 * user shared from, instead of opening Settings; closing the sheet returns to that app.
 */
class ShareIntakeActivity : ComponentActivity() {
    private val model: ShareIntakeViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        AppearanceStore.load(this)
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null) model.accept(intent)
        setContent {
            val dark by AppearanceStore.dark.collectAsStateWithLifecycle()
            val state by model.state.collectAsStateWithLifecycle()
            val waking by model.waking.collectAsStateWithLifecycle()
            OvrlyTheme(dark) {
                ShareIntakeSheet(
                    state = state,
                    waking = waking,
                    onAction = { action ->
                        model.onAction(action)
                        if (action == IntakeAction.DISMISS) finish()
                    },
                    onPickFile = model::acceptPicked
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        model.accept(intent)
    }
}

/** Holds one intake across configuration changes; the summary is published for Settings. */
class ShareIntakeViewModel(application: Application) : AndroidViewModel(application) {
    private val limits = ShareLimits(maxBytes = ApiServices.uploadMaxBytes)
    private val reader = ShareInputReader(application, limits)
    private val controller = ShareIntakeController(
        viewModelScope,
        IntakeDependencies(
            service = ApiServices.get(application),
            staging = ShareStaging.forSession(application),
            limits = limits
        )
    )

    internal val state: StateFlow<IntakeState> get() = controller.state
    internal val waking: StateFlow<Boolean> get() = controller.waking

    fun accept(intent: Intent) = start { reader.read(intent) }

    /** A file chosen as the permitted alternative after a rejected share. */
    fun acceptPicked(uri: Uri) = start { reader.readVideo(uri) }

    internal fun onAction(action: IntakeAction) = controller.onAction(action)

    /** The sheet is gone for good: stop the intake and delete its private copy. */
    override fun onCleared() {
        controller.close()
    }

    private fun start(read: () -> ShareRead) {
        viewModelScope.launch { ShareSummary.latest.value = controller.accept(read) }
    }
}
