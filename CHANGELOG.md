# Changelog

## [Unreleased]

### Added

- Native Kotlin/Jetpack Compose Android companion and manually activated floating controls.
- Explicit-consent playback-audio and sampled-screen capture, bounded to three minutes and 32 MiB of app-private media.
- Temporary-capture retention and deletion controls.
- Share entry point for supported video content URIs and web URL references, without automatic downloading or analysis.
- Design gallery with 15 glass treatments, seven states per design, synthetic backdrops and higher-opacity comparisons.
- Experimental, configuration-gated Voxide companion adapter with an action to open the design gallery.
- Saved Light/Dark appearance selection: Mock 1 paper styling and Liquid Chrome, with bundled Lexend and Instrument Serif fonts.
- An explicitly enabled, separately labeled larger demo overlay with simulated claim/evidence interactions and session-stop confirmation.
- Your space and Explore previews with original chrome artwork, sample reports, search, topic filters and session-local sample saves.
- Native Android launch splash with a metallic split ring and Instrument Serif wordmark on Chrome black, with Android 10+ compatibility and no artificial startup delay.

### Changed

- Copied the Android foundation into a standalone `android` project within the new `ovrly` monorepo.
- Updated the Windows build helper for the new paths and separated root documentation from Android instructions.
- Reserved a documented `backend` boundary without starting backend services or adding unused dependencies.
- Restyled companion controls, dialogs, compact overlays and the gallery while retaining all 15 material studies.
- Unified the split-ring logo across floating controls, gallery, app icon and notifications, with balanced gaps and a lighter rounded stroke.
- Added a public-window background-blur path for supported Android 12+ devices, with runtime-aware opaque fallbacks.
- Made Liquid Chrome the fresh-install default while preserving saved appearance choices.
- Moved working companion controls into Settings, shortened labels and moved technical explanations into disclosures without removing capture consent.
- Sized the demo overlay below half the usable screen with 16 dp side/bottom margins, a draggable header and independently scrolling content.

### Known limitations

- The launch splash showed only its background during Android 12 device checks; native ring/wordmark presentation remains unresolved despite successful standalone vector rendering.
- Research, transcription, OCR and evidence retrieval are not connected.
- Gallery timers, claims and results are explicitly labeled fixtures; design selection does not change the live overlay.
- Shared media is not processed or queued.
- Real cross-window blur depends on Android version, device support and system state; unsupported devices use solid theme surfaces. No backdrop refraction is implemented.
- Physical-device smoke testing is partial; cross-app capture, the full lifecycle and live voice compatibility remain unverified.
- Voxide native authorization remains unresolved.
