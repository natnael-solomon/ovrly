plugins {
    id("com.android.application") version "9.4.1" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20" apply false
    id("io.gitlab.arturbosch.detekt") version "1.23.8"
    id("org.jlleitschuh.gradle.ktlint") version "14.2.0"
}

detekt {
    toolVersion = "1.23.8"
    source.setFrom(
        fileTree("app/src") {
            include("**/*.kt")
        },
        fileTree(".") {
            include("*.kts", "app/*.kts")
        }
    )
    config.setFrom(files("config/detekt.yml"))
    buildUponDefaultConfig = true
    baseline = file("config/detekt-baseline.xml")
    basePath = rootDir.absolutePath
}

dependencies {
    detektPlugins("io.nlopez.compose.rules:detekt:0.4.23")
}

ktlint {
    version.set("1.8.0")
    baseline.set(file("config/ktlint-root-baseline.xml"))
}

tasks.register("qualityCheck") {
    group = "verification"
    description = "Check all handwritten Kotlin and Gradle scripts against reviewed baselines."
    dependsOn("detekt", "ktlintCheck", ":app:ktlintCheck")
}
