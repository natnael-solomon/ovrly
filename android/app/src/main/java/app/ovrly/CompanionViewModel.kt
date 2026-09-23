package app.ovrly

import android.app.Application
import android.content.Intent
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.core.content.edit
import app.ovrly.capture.CaptureFiles
import app.ovrly.capture.CapturePhase
import app.ovrly.capture.CaptureState
import app.ovrly.capture.CaptureStore
import app.ovrly.overlay.OverlayStore
import app.ovrly.share.ShareInputReader
import app.ovrly.share.SharedInput
import app.ovrly.ui.OverlayAppearance
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException
import java.io.IOException

class CompanionViewModel(application: Application) : AndroidViewModel(application) {
    val capture = CaptureStore.state
    private val mutableShare = MutableStateFlow<SharedInput?>(null)
    val sharedInput = mutableShare.asStateFlow()
    private val mutableStorageBusy = MutableStateFlow(true)
    val storageBusy = mutableStorageBusy.asStateFlow()
    private val preferences = application.getSharedPreferences("companion", 0)
    private var shareGeneration = 0

    init {
        OverlayStore.higherOpacity.value =
            preferences.getBoolean("higherOpacity", OverlayAppearance.DEFAULT_HIGHER_OPACITY)
        viewModelScope.launch {
            if (!capture.value.busy) {
                try {
                    val restored = withContext(Dispatchers.IO) { CaptureFiles(application).restoreOrExpire(System.currentTimeMillis()) }
                    if (restored != null && !capture.value.busy) CaptureStore.set(restored)
                } catch (error: IOException) {
                    storageError(error)
                } catch (error: JSONException) {
                    storageError(error)
                }
            }
            mutableStorageBusy.value = false
        }
    }

    fun higherOpacity(value: Boolean) {
        OverlayStore.higherOpacity.value = value
        preferences.edit { putBoolean("higherOpacity", value) }
    }

    fun acceptShare(intent: Intent) {
        val generation = ++shareGeneration
        mutableShare.value = SharedInput("Inspecting shared reference", "Only local metadata is being inspected. No media upload.", false)
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { ShareInputReader.read(getApplication(), intent) }
            if (generation == shareGeneration) mutableShare.value = result
        }
    }

    fun clearShare() {
        shareGeneration++
        mutableShare.value = null
    }

    fun deleteCapture() {
        if (capture.value.busy || mutableStorageBusy.value) return
        mutableStorageBusy.value = true
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { CaptureFiles(getApplication()).delete() }
                CaptureStore.set(CaptureState(message = "Local capture deleted. Research is not connected."))
            } catch (error: IOException) {
                storageError(error)
            } finally {
                mutableStorageBusy.value = false
            }
        }

    }

    fun checkRetention() {
        if (capture.value.busy || mutableStorageBusy.value) return
        mutableStorageBusy.value = true
        viewModelScope.launch {
            try {
                val deleted = withContext(Dispatchers.IO) {
                    CaptureFiles(getApplication()).expireIfNeeded(System.currentTimeMillis())
                }
                if (deleted) CaptureStore.set(CaptureState(message = "Expired or interrupted local capture was deleted. Research is not connected."))
            } catch (error: IOException) {
                storageError(error)
            } finally {
                mutableStorageBusy.value = false
            }
        }
    }

    private fun storageError(error: Exception) {
        Log.e("OvrlyStorage", "Private capture storage issue", error)
        CaptureStore.set(CaptureState(phase = CapturePhase.ERROR, hasLocalCapture = true,
            message = "Local capture could not be restored or deleted. Use Delete local capture before retrying."))
    }
}
