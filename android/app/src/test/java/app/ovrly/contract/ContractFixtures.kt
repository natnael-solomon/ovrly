package app.ovrly.contract

import java.io.File
import java.net.URL
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
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
    const val RESULTS = "results"
    const val INTAKE = "intake"
    const val CONTRACT_INCOMPATIBLE = "contract-incompatible"

    private val json = Json

    class Fixture(val name: String, root: JsonObject) {
        val synthetic: Boolean = root["synthetic"]?.jsonPrimitive?.content == "true"
        val expect: JsonObject = root.getValue("expect").jsonObject
        val request: JsonElement? = root["request"]
        val response: JsonElement? = root["response"]

        /** The investigation read payload of a `results` fixture. */
        val investigation: JsonElement? = root["investigation"]

        /** The single payload of an Android-only contract fixture. */
        val payload: JsonElement? = root["payload"]

        /** Schema file names an intake fixture declares for its request and response. */
        val schemas: Map<String, String> =
            root["schemas"]?.jsonObject?.mapValues { it.value.jsonPrimitive.content }.orEmpty()

        /** Schema file name an Android-only contract fixture declares for its payload. */
        val schema: String? = root["schema"]?.jsonPrimitive?.content

        val expectRequest: String? get() = expectation("request")
        val expectResponse: String? get() = expectation("response")
        val expectPayload: String? get() = expectation("payload")
        val expectedErrorCode: String? get() = expectation("error_code")
        val expectedProcessingStatus: String? get() = expectation("processing_status")
        val expectedState: String? get() = expectation("state")
        val expectedClaimCount: Int? get() = expectation("claim_count")?.toInt()
        val expectedAssessmentCount: Int? get() = expectation("assessment_count")?.toInt()
        val reason: String? get() = expectation("reason")

        fun requestPayload(): String = checkNotNull(request) { "$name has no request" }.toString()

        fun responsePayload(): String =
            checkNotNull(response) { "$name has no response" }.toString()

        fun investigationPayload(): String =
            checkNotNull(investigation) { "$name has no investigation" }.toString()

        fun payloadText(): String = checkNotNull(payload) { "$name has no payload" }.toString()

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

    fun schema(name: String): JsonObject = element(resourceText("schemas/$name")).jsonObject

    /** File names of every committed schema, so an unmapped new schema is noticed. */
    fun schemaFiles(): Set<String> {
        val url = checkNotNull(resource("schemas")) { "schemas is not on the test classpath" }
        return File(url.toURI()).listFiles { file -> file.extension == "json" }
            .orEmpty()
            .map { it.name }
            .toSet()
    }

    fun element(payload: String): JsonElement = json.parseToJsonElement(payload)

    /** [element] with the member at [path] replaced by [value]; every step must exist. */
    fun replace(element: JsonElement, path: List<String>, value: JsonElement): JsonElement {
        if (path.isEmpty()) return value
        val head = path.first()
        val rest = path.drop(1)
        return when (element) {
            is JsonObject -> JsonObject(
                element + (head to replace(element.getValue(head), rest, value))
            )

            is JsonArray -> JsonArray(
                element.mapIndexed { index, item ->
                    if (index == head.toInt()) replace(item, rest, value) else item
                }
            )

            else -> error("cannot descend into $element with $head")
        }
    }

    /**
     * [element] with the named members removed wherever their value is JSON null. The encoders
     * omit an optional member whose value is null (`encodeDefaults = false`), which the schema
     * treats as the same thing; every required nullable member is still compared literally.
     */
    fun withoutOptionalNulls(element: JsonElement, optional: Set<String>): JsonElement =
        when (element) {
            is JsonObject -> JsonObject(
                element.filterNot { (key, value) -> key in optional && value is JsonNull }
                    .mapValues { withoutOptionalNulls(it.value, optional) }
            )

            is JsonArray -> JsonArray(element.map { withoutOptionalNulls(it, optional) })

            else -> element
        }

    private fun resource(path: String): URL? = ContractFixtures::class.java.getResource("/$path")
}
