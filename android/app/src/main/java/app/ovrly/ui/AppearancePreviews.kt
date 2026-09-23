package app.ovrly.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import app.ovrly.capture.CaptureState

@Preview(name = "Mock 1 / companion", widthDp = 390, heightDp = 844)
@Composable
private fun PaperCompanionPreview() = CompanionPreview(dark = false)

@Preview(name = "Chrome / companion", widthDp = 390, heightDp = 844)
@Composable
private fun ChromeCompanionPreview() = CompanionPreview(dark = true)

@Preview(name = "Narrow / large text", widthDp = 320, heightDp = 740, fontScale = 2f)
@Composable
private fun LargeTextCompanionPreview() = CompanionPreview(dark = false)

@Preview(name = "Chrome / gallery", widthDp = 390, heightDp = 844)
@Composable
private fun ChromeGalleryPreview() {
    OvrlyTheme(dark = true) { GalleryScreen(onBack = {}) }
}

@Preview(name = "Chrome / Your space", widthDp = 390, heightDp = 844)
@Composable
private fun ChromeSpacePreview() {
    OvrlyTheme(dark = true) {
        AppShell(AppDestination.SPACE, {}, activeSession = null, settings = {})
    }
}

@Preview(name = "Chrome / Explore", widthDp = 390, heightDp = 844)
@Composable
private fun ChromeExplorePreview() {
    OvrlyTheme(dark = true) {
        AppShell(AppDestination.EXPLORE, {}, activeSession = null, settings = {})
    }
}

@Composable
private fun CompanionPreview(dark: Boolean) {
    OvrlyTheme(dark) {
        CompanionScreen(
            capture = CaptureState(), share = null, storageBusy = false,
            overlayAllowed = false, overlayVisible = false, notificationsAllowed = false,
            higherOpacity = false, voiceActive = false, voiceMessage = "Voice is not configured.",
            voiceConfigured = false, showSetup = false,
            onDismissSetup = {}, onConfirmSetup = {}, onStart = {}, onStop = {},
            onOverlayPermission = {}, onShowOverlay = {}, onHideOverlay = {}, onResetOverlay = {},
            onOpacity = {}, onDeleteCapture = {}, onClearShare = {}, onGallery = {},
            onNotificationPermission = {}, onVoiceStart = {}, onVoiceStop = {},
            dark = dark, onDark = {}, overlayStatus = "Solid glass / preview",
            demoActive = false, onDemo = {},
        )
    }
}
