# Changelog

## [Unreleased]

### Added

- Backend CI with PostgreSQL/migration tests, strict types, service coverage, main-baseline comparison, seven-day reports and grouped uv dependency updates. Android coverage and required-check activation remain open under #13.
- Backend foundation with FastAPI readiness, PostgreSQL/Alembic, standalone or embedded worker lifecycle, local tests and one-command Linux/WSL setup. Durable jobs remain separate work.
- Native Kotlin/Compose Android companion with manually activated floating controls, consented playback-audio and sampled-screen capture, and temporary-media retention/deletion. Capture is limited to three minutes and 32 MiB.
- Share validation for supported video content URIs and web URL references, without downloading or analysis.
- Design gallery with 15 glass treatments, seven states per design, synthetic backdrops and higher-opacity comparisons.
- Configuration-gated Voxide experiment that opens the design gallery.
- Saved Light/Dark appearance, labeled Your space and Explore sample reports, search, topic filters and session-local sample saves.
- Larger sample overlay with simulated evidence interactions and confirmation before stopping a real session.
- Android 10+ native launch splash with a chrome ring, a 900 ms handoff and no artificial delay. Android 12+ follows the saved Light/Dark appearance.
- Versioned evaluation-data schemas, blind annotation/adjudication workflow, synthetic JSONL examples and dependency-free validation with CI. The reviewed clip corpus is still pending.

### Changed

- Added manual Telegram APK distribution: an exact merged `main` commit builds without credentials, then signs and delivers after owner approval. An immutable ledger supplies version codes; redelivery resends the current issued bytes. Release builds use R8 and resource shrinking. See [release signing](docs/release-signing.md).
- Reserved Instrument Serif for editorial report/gallery headings and the text-based wordmark. Functional headings, including capture dialogs and demo claims, use Lexend without changing type sizes.
- Added rendered chrome splash, adaptive launcher and header-wordmark assets. The splash fills Android's icon mask; header glare and Light-mode contrast treatments remain local drawing effects. Notifications retain the monochrome split ring. See [UI maintenance](docs/android-ui.md).
- Corrected the "A moment outside" artwork to use circles.
- Updated to Gradle 9.8.0, Android Gradle plugin 9.4.1 with Kotlin 2.2.10, and compileSdk/targetSdk 37. Dependencies include core 1.19.1, splashscreen 1.2.0, activity 1.13.0, lifecycle 2.11.0, savedstate 1.5.0, Compose BOM 2026.09.00, coroutines 1.11.0 and OkHttp 5.5.0. Lint treats warnings as errors.
- Organized the Android foundation under `android`, updated the Windows helper and reserved the `backend` boundary.
- Made Liquid Chrome the fresh-install default while preserving saved appearance choices; restyled controls and retained all 15 gallery studies and font licenses.
- Moved working controls into Settings without removing capture consent. The demo overlay stays below half the usable screen, with 16 dp margins, a draggable header and separate body scrolling.
- Added public-window background blur on supported Android 12+ devices, with opaque fallbacks.

### Fixed

- Reject database-test URL and inherited routing overrides before connecting, keeping migration tests on their disposable database.
- Reject whitespace-suffixed evaluation IDs and SHA-256 values, including hashes used for cross-split isolation. Preserve Unicode separators inside JSON strings when reading physical JSONL lines.

### Known limitations

- Research, transcription, OCR and evidence retrieval are not connected; shared media is not processed or queued.
- Gallery and report content are labeled samples. Gallery selection does not change the live overlay.
- Native blur depends on device/system support; no backdrop refraction is implemented.
- Physical-device testing is partial. Full cross-app capture/lifecycle behavior and live Voxide authorization/compatibility remain unverified.
- Android 12 shows the splash icon only for home/system-originated launches. It was visible from Samsung One UI Home but absent from Niagara Launcher and `adb shell am start`; the Android 15 emulator showed it from every launch source. No supported per-app override exists below Android 13.
