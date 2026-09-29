# Android

Kotlin/Jetpack Compose app for Android 10+ (API 29), with floating controls, local capture and share validation. No backend or research processing is connected.

## Setup

Install JDK 21, Android SDK platform 37, Build Tools 36.0.0 and Platform Tools, and accept the SDK licenses. The wrapper uses Gradle 9.8.0 and Android Gradle plugin 9.4.1 with built-in Kotlin. Lint treats warnings as errors.

Open this directory in Android Studio and select JDK 21 for Gradle. For terminal builds, set `JAVA_HOME` and `ANDROID_HOME`. Keep `local.properties` uncommitted and pointed at the same SDK.

From the repository root, build the debug APK and run unit tests, lint and quality gates:

### Windows

```powershell
.\scripts\android.ps1 -JdkPath $env:JAVA_HOME -SdkPath $env:ANDROID_HOME
```

### Linux / WSL

```bash
cd android
sh gradlew --no-daemon --console=plain :app:assembleDebug :app:testDebugUnitTest :app:lintDebug qualityCheck
```

Use a Linux JDK, SDK, checkout and Gradle cache in WSL.

APK: `app/build/outputs/apk/debug/app-debug.apk`, relative to this directory. The build needs no backend or provider credentials.

## Quality and dependencies

`qualityCheck` runs detekt 1.23.8 with Compose rules 0.4.23 and ktlint 1.8.0
through Gradle plugin 14.2.0. It checks application/test Kotlin and Gradle scripts.
Detekt runs source analysis at the root, independent of AGP's variant API; it is
not type-resolved analysis. Existing findings are recorded in `config/*baseline.xml`,
not silently fixed or excluded. New findings fail; baseline changes need review.
`app/lint.xml` documents the existing narrow lint annotations and disables no rules.
`settings.gradle.kts` pins patched transitive build-tool dependencies; those
overrides do not apply to the app's runtime dependencies.

Gradle verifies dependency bytes against `gradle/verification-metadata.xml`.
Never bypass a checksum failure. For an intentional dependency update, resolve
the affected build/check tasks with
`--refresh-dependencies --write-verification-metadata sha256`. Review the new
coordinates and hashes against their publishers, then rerun without those flags.
Include parent POMs/BOMs and Linux CI artifacts; a warm-cache pass alone does not
prove complete metadata coverage. CI never regenerates baselines or verification metadata.

Repository pre-commit checks also run these gates and audit dependencies; they
require JDK 21, the SDK, uv and network access. See [shared checks](../WORKFLOW.md#shared-quality-gates).

## Gallery and demo

Open `app/src/main/java/app/ovrly/ui/GalleryPreviews.kt` in Studio's Design or Split view. `GlassOverlay.kt` contains the live-control previews. Gallery selection does not change the live overlay.

Your space, Explore and the larger overlay demo use labeled sample reports. Sample saves survive navigation and restored activity state, not a fresh session. Settings contains the actual capture, permissions, storage, share and voice controls.

Enable the larger demo from Settings or the gallery with display-over-other-apps permission. If capture or voice is active, confirm stopping it first. The demo never records or contacts a provider; closing it never restarts a session.

## Appearance

Liquid Chrome is the default. Saved Light/Dark choices are preserved independently of Android's theme. Lexend is used for interface text; Instrument Serif is reserved for editorial styles. Both retain their [SIL Open Font licenses](app/src/main/assets/licenses/).

Native overlay blur requires a supported Android 12+ device. Unsupported or disabled blur and higher-opacity mode use solid surfaces.

On Android 12, the splash icon appears only for home/system-originated launches. It was visible from Samsung One UI Home but absent from Niagara Launcher and `adb shell am start` on the tested Samsung SM-A217F. Android 13+ supports `icon_preferred`; an Android 15 emulator showed the icon from every launch source. There is no supported per-app override on Android 12.

See [UI maintenance](../docs/android-ui.md) for artwork, splash behavior and asset regeneration, and [architecture](../docs/architecture.md#overlay-material-and-demo-boundaries) for overlay lifecycle and device checks.

## Optional voice experiment

Voxide is disabled by default. Native authorization and compatibility remain unresolved. After provider approval, configure `voxide.local.properties` from the example using only a publishable key. Values are embedded in the APK; never use a secret key.

Starting voice sends microphone audio to Voxide for gallery navigation only. It stops when the companion leaves the foreground or capture starts.
