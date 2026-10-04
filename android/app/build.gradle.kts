import java.util.Properties
import org.gradle.api.tasks.util.PatternFilterable

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("org.jlleitschuh.gradle.ktlint")
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
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
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
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
    testImplementation("com.squareup.okhttp3:mockwebserver:5.5.0")
}
