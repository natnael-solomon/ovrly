import com.android.build.api.artifact.SingleArtifact
import java.util.Properties
import org.gradle.api.tasks.util.PatternFilterable

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")
    id("org.jlleitschuh.gradle.ktlint")
}

// Room exports each schema version here; commit it so later migrations can be checked.
ksp {
    arg("room.schemaLocation", file("schemas").path)
    arg("room.generateKotlin", "true")
}

ktlint {
    version.set("1.8.0")
    baseline.set(rootProject.file("config/ktlint-app-baseline.xml"))
}

val voiceConfig = Properties().apply {
    val config = rootProject.file("voxide.local.properties")
    if (config.exists()) config.inputStream().use { load(it) }
}
val liveVoice = providers.environmentVariable("VOXIDE_LIVE").orNull.let {
    require(it == null || it == "0" || it == "1") { "VOXIDE_LIVE must be 0 or 1." }
    it == "1"
}
check(!liveVoice || providers.environmentVariable("CI").orNull != "true") {
    "Live voice is forbidden in CI."
}
check(!liveVoice || providers.environmentVariable("GITHUB_ACTIONS").orNull != "true") {
    "Live voice is forbidden in GitHub Actions."
}
check(!liveVoice || voiceConfig.getProperty("enabled") == "true") {
    "Live voice also requires enabled=true in the ignored local configuration."
}
fun quoted(value: String): String = "\"" + value.replace("\\", "\\\\")
    .replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + "\""

// API base URL and upload cap for share intake (AN-03, #18). The ignored
// api.local.properties overrides them; the default is the emulator's host loopback.
// Never commit a hosted address.
val apiConfig = Properties().apply {
    val config = rootProject.file("api.local.properties")
    if (config.exists()) config.inputStream().use { load(it) }
}
val apiBaseUrl: String = apiConfig.getProperty("baseUrl", "http://10.0.2.2:8000/").trim()
check(apiBaseUrl.startsWith("http://") || apiBaseUrl.startsWith("https://")) {
    "api.local.properties baseUrl must be an http or https URL."
}
// Mirrors the backend OVRLY_UPLOAD_MAX_BYTES default (256 MiB) until BC-D06 fixes the budget.
val uploadMaxBytes: Long = apiConfig.getProperty("uploadMaxBytes", "268435456").trim()
    .toLongOrNull()?.takeIf { it > 0 }
    ?: error("api.local.properties uploadMaxBytes must be a positive integer.")

// CI passes -Povrly.versionCode=<code> from the release ledger; local builds keep 1.
// Google Play accepts 1..2100000000 inclusive, so anything else is a configuration error.
fun releaseVersionCode(): Int {
    val requested = findProperty("ovrly.versionCode")?.toString() ?: return 1
    return requested.toIntOrNull()?.takeIf { it in 1..2_100_000_000 }
        ?: error("ovrly.versionCode must be an integer in 1..2100000000, got '$requested'")
}

android {
    namespace = "app.ovrly"
    compileSdk = 37
    defaultConfig {
        applicationId = "app.ovrly"
        minSdk = 29
        targetSdk = 37
        versionCode = releaseVersionCode()
        versionName = "0.1.0"
        buildConfigField("boolean", "VOXIDE_ENABLED", liveVoice.toString())
        buildConfigField("boolean", "VOXIDE_LIVE", liveVoice.toString())
        buildConfigField(
            "String",
            "VOXIDE_BASE_URL",
            quoted(voiceConfig.getProperty("baseUrl", "https://voxide.onrender.com"))
        )
        buildConfigField(
            "String",
            "VOXIDE_PUBLISHABLE_KEY",
            quoted(if (liveVoice) voiceConfig.getProperty("publishableKey", "") else "")
        )
        buildConfigField("String", "OVRLY_API_BASE_URL", quoted(apiBaseUrl))
        buildConfigField("long", "OVRLY_UPLOAD_MAX_BYTES", "${uploadMaxBytes}L")
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            // ML Kit ships native libraries per ABI (#100). Release keeps phone ABIs only;
            // debug keeps every ABI for x86_64 emulator CI. ChromeOS (x86_64) is not a supported
            // device (BC-D02), hence the narrow lint suppression.
            //noinspection ChromeOsAbiSupport
            ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        abortOnError = true
        warningsAsErrors = true
        lintConfig = file("lint.xml")
    }
    testOptions { unitTests.isReturnDefaultValues = true }
    sourceSets {
        // Shared contract schemas and fixtures are read in place from the repository root.
        // Unit tests load them as classpath resources; nothing is copied into the module.
        getByName("test").resources.directories.add(rootProject.file("../packages/contracts").path)
    }
}

// Keep only VERSION, schemas/ and fixtures/ from the contracts package on the test classpath.
tasks.matching { it.name.endsWith("UnitTestJavaRes") }.configureEach {
    (this as PatternFilterable).exclude("README.md", "ruff.toml", "validate.py", "tests/**")
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.core:core-splashscreen:1.2.0")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-service:2.11.0")
    implementation("androidx.savedstate:savedstate-ktx:1.5.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("com.squareup.okhttp3:okhttp:5.5.0")
    implementation("androidx.room:room-runtime:2.8.5")
    implementation("androidx.room:room-ktx:2.8.5")
    ksp("androidx.room:room-compiler:2.8.5")
    implementation("androidx.work:work-runtime-ktx:2.12.0")
    // Bundled Latin model: recognition runs on the device without a model download (#100).
    implementation("com.google.mlkit:text-recognition:16.0.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
    testImplementation("com.squareup.okhttp3:mockwebserver:5.5.0")
}

/**
 * Fails when the merged manifest still declares a Google datatransport or Firebase component.
 * ML Kit (#100) brings datatransport, which would upload SDK usage metrics; the app manifest
 * removes those components, and this check keeps them removed in every variant.
 */
abstract class VerifyNoTelemetryComponents : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val mergedManifest: RegularFileProperty

    @get:OutputFile
    abstract val report: RegularFileProperty

    @TaskAction
    fun verify() {
        val manifest = mergedManifest.get().asFile.readText()
        val banned = "com\\.google\\.android\\.datatransport\\.|com\\.google\\.firebase\\."
        val component = Regex(
            "<(service|receiver|provider|activity)\\b[^>]*android:name=\"((?:$banned)[^\"]+)\""
        )
        val found = component.findAll(manifest)
            .map { "${it.groupValues[1]} ${it.groupValues[2]}" }
            .toList()
        check(found.isEmpty()) {
            "Telemetry components in the merged manifest: ${found.joinToString()}"
        }
        report.get().asFile.writeText("No datatransport or Firebase components.\n")
    }
}

androidComponents {
    onVariants { variant ->
        val name = variant.name.replaceFirstChar { it.uppercase() }
        val verify = tasks.register<VerifyNoTelemetryComponents>("verify${name}NoTelemetry") {
            mergedManifest.set(variant.artifacts.get(SingleArtifact.MERGED_MANIFEST))
            report.set(layout.buildDirectory.file("reports/telemetry/${variant.name}.txt"))
        }
        tasks.matching { it.name == "assemble$name" }.configureEach { dependsOn(verify) }
        tasks.matching { it.name == "check" }.configureEach { dependsOn(verify) }
    }
}
