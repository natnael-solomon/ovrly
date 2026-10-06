import java.util.Properties
import org.gradle.api.tasks.util.PatternFilterable
import org.gradle.testing.jacoco.plugins.JacocoPluginExtension
import org.gradle.testing.jacoco.tasks.JacocoReport

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

// JaCoCo coverage for unit and instrumented tests (REPO-04 #13, REPO-05 #76). Off unless
// -Povrly.coverage=true, so ordinary debug builds and Android checks stay uninstrumented.
val coverageEnabled: Boolean = findProperty("ovrly.coverage")?.toString().let {
    require(it == null || it == "true" || it == "false") {
        "ovrly.coverage must be true or false."
    }
    it == "true"
}
val jacocoToolVersion = "0.8.14"

android {
    namespace = "app.ovrly"
    compileSdk = 37
    defaultConfig {
        applicationId = "app.ovrly"
        minSdk = 29
        targetSdk = 37
        versionCode = releaseVersionCode()
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
        debug {
            enableUnitTestCoverage = coverageEnabled
            enableAndroidTestCoverage = coverageEnabled
        }
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
    testOptions {
        unitTests.isReturnDefaultValues = true
        animationsDisabled = true
    }
    testCoverage { jacocoVersion = jacocoToolVersion }
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

// Reviewed coverage exclusions: generated code, Compose previews and gallery fixtures only.
// Previews declared inside production files (GlassOverlay.kt, DemoOverlayPanel.kt) share a
// class with real code and stay counted.
val coverageExclusions = listOf(
    "**/R.class",
    "**/R$*.class",
    "**/BuildConfig.class",
    "**/Manifest.class",
    "**/Manifest$*.class",
    "**/*_Impl.class",
    "**/*_Impl$*.class",
    "**/*\$\$serializer.class",
    "**/ComposableSingletons$*.class",
    "app/ovrly/ui/GalleryPreviewsKt*.class",
    "app/ovrly/ui/AppearancePreviewsKt*.class",
    "app/ovrly/ui/LiveResultsPreviewsKt*.class",
    "app/ovrly/ui/GalleryFixturesKt*.class",
    "app/ovrly/ui/GalleryFixture.class",
    "app/ovrly/ui/GalleryFixture$*.class"
)

if (coverageEnabled) {
    apply(plugin = "jacoco")
    extensions.configure<JacocoPluginExtension> { toolVersion = jacocoToolVersion }

    // One report from whatever execution data exists: unit tests, connected runs and the
    // retried attempts that android_instrumented.py moves under build/instrumented/.
    // It never runs tests itself, so it can follow an emulator run in a separate step.
    tasks.register<JacocoReport>("jacocoDebugReport") {
        group = "verification"
        description = "JaCoCo line coverage of the debug variant from existing execution data."
        mustRunAfter(
            tasks.matching {
                it.name == "testDebugUnitTest" || it.name == "connectedDebugAndroidTest"
            }
        )
        val classTrees = listOf("compileDebugKotlin", "compileDebugJavaWithJavac").map { name ->
            tasks.named(name).map { task ->
                task.outputs.files.asFileTree.matching {
                    include("**/*.class")
                    exclude(coverageExclusions)
                }
            }
        }
        classDirectories.setFrom(classTrees)
        sourceDirectories.setFrom(files("src/main/java", "src/main/kotlin"))
        executionData.setFrom(
            fileTree(layout.buildDirectory) {
                include(
                    "outputs/unit_test_code_coverage/debugUnitTest/*.exec",
                    "jacoco/testDebugUnitTest.exec",
                    "outputs/code_coverage/debugAndroidTest/connected/**/*.ec",
                    "instrumented/**/*.ec"
                )
            }
        )
        reports {
            xml.required.set(true)
            html.required.set(true)
            csv.required.set(false)
        }
    }
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
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
    testImplementation("com.squareup.okhttp3:mockwebserver:5.5.0")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    androidTestImplementation("androidx.test:rules:1.7.0")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
