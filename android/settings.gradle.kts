pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "ovrly"
include(":app")

// Build-tool transitive fixes only; application runtime dependencies are unchanged.
val toolingSecurityVersions = mapOf(
    "ch.qos.logback:logback-classic" to "1.5.34",
    "ch.qos.logback:logback-core" to "1.5.34",
    "com.fasterxml.jackson.core:jackson-core" to "2.22.3",
    "com.fasterxml.jackson.core:jackson-databind" to "2.22.3",
    "org.apache.commons:commons-lang3" to "3.18.0",
    "org.apache.httpcomponents:httpclient" to "4.5.14",
    "org.apache.httpcomponents:httpmime" to "4.5.14",
    "org.bitbucket.b_c:jose4j" to "0.9.6",
    "org.bouncycastle:bcpkix-jdk18on" to "1.85",
    "org.bouncycastle:bcprov-jdk18on" to "1.85",
    "org.bouncycastle:bcutil-jdk18on" to "1.85",
    "org.jdom:jdom2" to "2.0.6.1",
    "org.jetbrains.kotlin:kotlin-gradle-plugin" to "2.4.20"
)

gradle.beforeProject {
    fun org.gradle.api.artifacts.Configuration.pinToolingFixes() {
        resolutionStrategy.eachDependency {
            toolingSecurityVersions["${requested.group}:${requested.name}"]?.let {
                useVersion(it)
                because("Patched build tooling required by the repository vulnerability gate")
            }
        }
    }

    buildscript.configurations.configureEach {
        pinToolingFixes()
    }
    configurations.configureEach {
        if (name == "androidLintTool" || name.startsWith("detekt") || name.startsWith("ktlint")) {
            pinToolingFixes()
        }
    }
}
