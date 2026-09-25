import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

val voiceConfig = Properties().apply {
    val config = rootProject.file("voxide.local.properties")
    if (config.exists()) config.inputStream().use { load(it) }
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
        buildConfigField("boolean", "VOXIDE_ENABLED", (voiceConfig.getProperty("enabled") == "true").toString())
        buildConfigField("String", "VOXIDE_BASE_URL", quoted(voiceConfig.getProperty("baseUrl", "https://voxide.onrender.com")))
        buildConfigField("String", "VOXIDE_PUBLISHABLE_KEY", quoted(voiceConfig.getProperty("publishableKey", "")))
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        abortOnError = true
        warningsAsErrors = true
    }
    testOptions { unitTests.isReturnDefaultValues = true }
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
    implementation("com.squareup.okhttp3:okhttp:5.5.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
    testImplementation("com.squareup.okhttp3:mockwebserver:5.5.0")
}
