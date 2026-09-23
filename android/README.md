# Android

Kotlin/Jetpack Compose app for Android 10+ (API 29), with floating controls, local capture and share validation. No backend or research processing is connected.

## Setup

Install JDK 21, Android SDK platform 35, Build Tools 35.0.0 and Platform Tools, and accept the SDK licenses.

Open this directory in Android Studio and select JDK 21 for Gradle. For terminal builds, set `JAVA_HOME` and `ANDROID_HOME`; any `local.properties` must point to the same SDK and stay uncommitted.

From the repository root, build the debug APK, run unit tests and lint:

### Windows

```powershell
.\scripts\android.ps1 -JdkPath $env:JAVA_HOME -SdkPath $env:ANDROID_HOME
```

### Linux / WSL

```bash
cd android
sh gradlew --no-daemon --console=plain :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

Use a Linux JDK and SDK in WSL, with the checkout and Gradle caches in the Linux filesystem.

APK: `app/build/outputs/apk/debug/app-debug.apk` (relative to this directory). No backend or provider credentials are required.

## Design gallery

Open `app/src/main/java/app/ovrly/ui/GalleryPreviews.kt` in Studio's Design or Split view. Gallery selections do not change the live overlay; `GlassOverlay.kt` contains the live-control previews.

## Launch splash

Cold and warm launches use Android's native SplashScreen API through AndroidX Core SplashScreen, including its Android 10/11 compatibility resources. The static launch artwork is a metallic, two-gap ring above the Instrument Serif `ovrly.` wordmark on Chrome black (`#080910`). It is local vector artwork, not a loading screen or a separate Activity: there is no timer, spinner, network request, keep-on-screen condition or custom exit animation. Android dismisses it when the app draws; hot resumes do not replay an intro. This branding does not make startup faster.

Android 12+ owns the icon mask and placement. The compact ring and wordmark share a 288 dp vector, entirely inside its centered 192 dp safe circle, so the wordmark sits directly below the ring rather than in Android's bottom branding-image slot. The ring preserves `ic_ovrly.xml`'s geometry; the monochrome launcher/notification icon is unchanged. The wordmark is outlined from the bundled, SIL-licensed `instrument_serif.ttf` at 40 dp with -1 dp tracking, centered at x=144 with baseline y=214. Keep both elements within the safe circle when editing `splash_ovrly.xml`.

Launch branding is always dark, regardless of the system theme. `MainActivity` installs the splash before loading saved appearance and applying the app theme, all before `super.onCreate`; a saved Light choice is still applied to the first app frame. Existing restored state and incoming-intent routing are unchanged.

**Known issue:** launcher-origin checks on a Samsung SM-A217F running Android 12 showed the Chrome background without the ring or wordmark. The vector resolves and renders in isolation on that device, but its native launch presentation remains unresolved. Other supported Android versions have not been visually verified.

## Appearance and overlay demo

Liquid Chrome is the fresh-install default; existing Light/Dark choices are preserved independently of Android's theme. Light mode follows Mock 1. Both apply to all screens, dialogs, overlays and the gallery. Lexend is bundled for interface text; Instrument Serif is retained for the wordmark and sample titles. Both retain their [SIL Open Font licenses](app/src/main/assets/licenses/).

The app opens on **Your space**, a clearly labeled sample collection. **Explore** offers topic filters, search and sample reports with original chrome artwork. Sample saves affect the preview collection only and survive navigation and restored activity state, not a fresh session. No account, saved research or backend is implied. **Settings** retains capture, permissions, overlay controls, appearance, share intake, voice, storage and gallery access, with technical details behind disclosures. Overlay setup, notifications and incoming shares route directly to Settings.

The larger overlay demo is explicitly enabled from Settings or the gallery and requires display-over-other-apps permission. All claims and evidence are labeled samples. If capture or voice is running, confirmation is required to stop it first. The demo replaces the compact controls, never records or contacts a provider, and has a close button and notification action. Closing it never restarts a session. It opens in the bottom half of the usable screen, spans its width with 16 dp side/bottom margins, and stays below half-height. Drag its header vertically to reposition it; scroll its body independently. System bars and cutouts remain outside the panel.

The service now hosts a public Android `Window` for localized background blur on supported Android 12+ devices. Unsupported devices, runtime blur disablement and the higher-opacity preference use solid, readable theme surfaces. No screen capture, hidden API or whole-screen blur is used for this effect. Native blur remains a device-validation requirement, not a guarantee for every phone. See [architecture](../docs/architecture.md#overlay-material-and-demo-boundaries) for the validation boundary.

## Optional voice experiment

Voxide is disabled by default; native authorization and compatibility remain unresolved. After provider approval, configure `voxide.local.properties` from the example file using only a publishable key, never a secret key: values are embedded in the APK.

Starting voice sends microphone audio to Voxide for gallery navigation only. It stops when the companion leaves the foreground or capture starts.
