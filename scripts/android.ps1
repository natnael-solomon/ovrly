param(
    [string]$JdkPath = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$SdkPath = "$env:LOCALAPPDATA\Android\Sdk",
    [string[]]$Tasks = @(':app:assembleDebug', ':app:testDebugUnitTest', ':app:lintDebug')
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path $PSScriptRoot -Parent
$project = Join-Path $repository 'android'
if (!(Test-Path (Join-Path $project 'settings.gradle.kts'))) { throw "Android project not found at $project" }
if (!(Test-Path (Join-Path $JdkPath 'bin\java.exe'))) { throw "JDK not found at $JdkPath" }
if (!(Test-Path (Join-Path $SdkPath 'platforms\android-35\android.jar'))) {
    throw "Android SDK platform 35 is required at $SdkPath"
}
$oldJava = $env:JAVA_HOME
$oldSdk = $env:ANDROID_HOME
$oldCache = $env:GRADLE_USER_HOME
Push-Location $project
try {
    $env:JAVA_HOME = $JdkPath
    $env:ANDROID_HOME = $SdkPath
    $env:GRADLE_USER_HOME = Join-Path $repository '.gradle-home'
    & (Join-Path $project 'gradlew.bat') --no-daemon --console=plain @Tasks
    if ($LASTEXITCODE -ne 0) { throw "Android build failed with exit code $LASTEXITCODE" }
} finally {
    $env:JAVA_HOME = $oldJava
    $env:ANDROID_HOME = $oldSdk
    $env:GRADLE_USER_HOME = $oldCache
    Pop-Location
}
