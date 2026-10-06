<#
.SYNOPSIS
Cross-compiles the RES-02 on-device ASR replay drivers for arm64-v8a.

.DESCRIPTION
Builds whisper_bench (statically linked whisper.cpp) and vosk_bench (linked
against the official vosk-android libvosk.so) with the Android NDK and the
SDK's CMake/Ninja. No Gradle. Inputs and outputs live outside the checkout.
Run under the heavy-work lock on shared machines.
#>
param(
    [Parameter(Mandatory)] [string] $WhisperSrc,
    [Parameter(Mandatory)] [string] $VoskDir,
    [Parameter(Mandatory)] [string] $BuildDir,
    [string] $Sdk = "$env:LOCALAPPDATA\Android\Sdk",
    [string] $NdkVersion = "29.0.13599879",
    [string] $CmakeVersion = "3.22.1",
    [string] $ArmArch = "armv8.2-a+fp16",
    [int] $Jobs = 2
)
$ErrorActionPreference = 'Stop'
$ndk = Join-Path $Sdk "ndk\$NdkVersion"
$cmakeBin = Join-Path $Sdk "cmake\$CmakeVersion\bin"
$source = $PSScriptRoot
& "$cmakeBin\cmake.exe" -S $source -B $BuildDir -G Ninja `
    "-DCMAKE_MAKE_PROGRAM=$cmakeBin\ninja.exe" `
    "-DCMAKE_TOOLCHAIN_FILE=$ndk\build\cmake\android.toolchain.cmake" `
    -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-29 -DANDROID_STL=c++_static `
    -DCMAKE_BUILD_TYPE=Release "-DGGML_CPU_ARM_ARCH=$ArmArch" `
    "-DWHISPER_SRC=$WhisperSrc" "-DVOSK_DIR=$VoskDir"
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
& "$cmakeBin\cmake.exe" --build $BuildDir --target whisper_bench vosk_bench -j $Jobs
if ($LASTEXITCODE -ne 0) { throw "build failed" }
Get-ChildItem (Join-Path $BuildDir 'whisper_bench'), (Join-Path $BuildDir 'vosk_bench') |
    ForEach-Object { '{0} {1}' -f $_.Name, (Get-FileHash $_.FullName).Hash.ToLower() }
