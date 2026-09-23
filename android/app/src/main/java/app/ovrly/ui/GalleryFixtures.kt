package app.ovrly.ui

internal data class GalleryFixture(
    val title: String,
    val explanation: String,
    val state: OverlayVisual,
    val detailsExplanation: String,
)

internal fun GalleryFixture.actionExplanation(action: StudyAction): String = when (action) {
    StudyAction.SETUP -> "Fixture only. Setup prepares a real capture before Android asks for " +
        "permission. No setup or permission request was opened."
    StudyAction.STOP_CAPTURE -> "Fixture only. Stop would end live capture. " +
        "This sample timer stays fixed; no capture was running."
    StudyAction.CANCEL_RESEARCH -> "Fixture only. Cancel would stop research, not audio capture. " +
        "This after-capture state has no recording timer. No research was running."
    StudyAction.DETAILS -> detailsExplanation
}

internal val GalleryFixtures = listOf(
    GalleryFixture(
        title = "Idle",
        explanation = "The interrupted ring is the setup action. Nothing is being captured.",
        state = OverlayVisual.Idle,
        detailsExplanation = "Fixture only. Open setup to prepare a real capture.",
    ),
    GalleryFixture(
        title = "Recording",
        explanation = "A coral dot and REC label identify capture. The square stops it.",
        state = OverlayVisual.Recording(seconds = 42),
        detailsExplanation = "Fixture only. This sample represents an active recording.",
    ),
    GalleryFixture(
        title = "Recording with preliminary claims",
        explanation = "The unfinished lime ring marks provisional claims. The recording dot stays " +
            "visible; claims and stop are separate touch targets.",
        state = OverlayVisual.Recording(seconds = 72, provisionalClaims = 2),
        detailsExplanation = "Fixture only. This opens two preliminary, unverified claims " +
            "without stopping capture.",
    ),
    GalleryFixture(
        title = "Researching after capture",
        explanation = "Checking has its own progress indicator and cancel action. " +
            "There is no recording timer.",
        state = OverlayVisual.Checking,
        detailsExplanation = "Fixture only. Research is still in progress.",
    ),
    GalleryFixture(
        title = "Results available",
        explanation = "A count and disclosure action open claim details. " +
            "The ring is not a truth checkmark.",
        state = OverlayVisual.Results(claims = 4),
        detailsExplanation = "Fixture only. This opens four claim details. " +
            "The count says nothing about their truth or supporting evidence.",
    ),
    GalleryFixture(
        title = "No checkable claims",
        explanation = "A neutral minus reports no claims to check, not that everything was true.",
        state = OverlayVisual.NoClaims,
        detailsExplanation = "Fixture only. No checkable claims were identified. " +
            "That does not establish the truth of the recording.",
    ),
    GalleryFixture(
        title = "Audio unavailable",
        explanation = "The crossed playback speaker means audio was unavailable. " +
            "Open details for capture limitations and a share alternative.",
        state = OverlayVisual.NoAudio,
        detailsExplanation = "Fixture only. The crossed speaker refers to playback audio, " +
            "not the microphone. The source app may block Android playback capture. " +
            "Where available, share a video or link from the source app instead. " +
            "This mock does not open sharing, capture audio, or submit anything for research.",
    ),
)
