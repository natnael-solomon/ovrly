# Android

Kotlin/Jetpack Compose app for Android 10+ (API 29), with floating controls, local capture and share validation. No backend or research processing is connected.

## Setup

Install JDK 21, Android SDK platform 37, Build Tools 36.0.0 and Platform Tools, and accept the SDK licenses. The wrapper uses Gradle 9.8.0 and Android Gradle plugin 9.4.1 with built-in Kotlin. Lint treats warnings as errors.

Open this directory in Android Studio and select JDK 21 for Gradle. For terminal builds, set `JAVA_HOME` and `ANDROID_HOME`. Keep `local.properties` uncommitted and pointed at the same SDK.

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

Use a Linux JDK, SDK, checkout and Gradle cache in WSL.

APK: `app/build/outputs/apk/debug/app-debug.apk`, relative to this directory. The build needs no backend or provider credentials.

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
