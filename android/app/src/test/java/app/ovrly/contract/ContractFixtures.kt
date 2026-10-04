package app.ovrly.contract

import java.io.File
import java.net.URL
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Reads contract fixtures from the unit-test classpath. The shared ones come from
 * `packages/contracts/fixtures` at the repository root (a Gradle test-resource directory,
 * not a copy); the Android-only incompatible ones live in `src/test/resources/fixtures`.
 */
internal object ContractFixtures {
    const val SHARED = "voice-actions"
    const val INCOMPATIBLE = "voice-actions-incompatible"

    private val json = Json

    class Fixture(val name: String, root: JsonObject) {
        val synthetic: Boolean = root["synthetic"]?.jsonPrimitive?.content == "true"
        val expect: JsonObject = root.getValue("expect").jsonObject
        val request: JsonElement? = root["request"]
        val response: JsonElement? = root["response"]

        val expectRequest: String? get() = expectation("request")
        val expectResponse: String? get() = expectation("response")
        val expectedErrorCode: String? get() = expectation("error_code")
        val reason: String? get() = expectation("reason")

        fun requestPayload(): String = checkNotNull(request) { "$name has no request" }.toString()

        fun responsePayload(): String =
            checkNotNull(response) { "$name has no response" }.toString()

        private fun expectation(key: String): String? = expect[key]?.jsonPrimitive?.content
    }

    fun directory(name: String): File {
        val url = checkNotNull(resource("fixtures/$name")) {
            "fixtures/$name is not on the test classpath; check the test resources srcDir"
        }
        return File(url.toURI()).also { check(it.isDirectory) { "$it is not a directory" } }
    }

    fun load(name: String): List<Fixture> = directory(name)
        .listFiles { file -> file.extension == "json" }
        .orEmpty()
        .sortedBy { it.name }
        .map { Fixture(it.nameWithoutExtension, json.parseToJsonElement(it.readText()).jsonObject) }

    fun resourceText(path: String): String =
        checkNotNull(resource(path)) { "$path is not on the test classpath" }.readText()

    fun element(payload: String): JsonElement = json.parseToJsonElement(payload)

    private fun resource(path: String): URL? = ContractFixtures::class.java.getResource("/$path")
}
