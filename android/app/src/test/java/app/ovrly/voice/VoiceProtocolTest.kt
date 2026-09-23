package app.ovrly.voice

import java.util.Base64
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceProtocolTest {
    @Test fun configurationIsDisabledUnlessExplicitlyEnabledWithKey() {
        assertEquals("Disabled", VoiceConfiguration(false, "invalid", "vox_sk_forbidden").unavailableState()?.status)
        assertEquals("Disabled", VoiceConfiguration(true, "https://example.invalid", "").unavailableState()?.status)
    }

    @Test fun configurationAcceptsOnlySecureCredentialFreeOriginsAndPublishableKeys() {
        for (url in listOf("https://example.invalid", "https://example.invalid/", "wss://example.invalid:8443")) {
            assertNull(VoiceConfiguration(true, url, "vox_pub_test").unavailableState())
        }
        for (url in listOf(
            "http://example.invalid", "ws://example.invalid", "file:///etc/test",
            "https://name:password@example.invalid", "https://example.invalid/path",
            "https://example.invalid?key=anything", "https://example.invalid#fragment",
            "https://example.invalid:0", "https://example.invalid:70000",
            "https://", "https://vox_sk_key.example.invalid", "https://example.invalid\\evil",
        )) {
            assertNotNull(url, VoiceConfiguration(true, url, "vox_pub_test").unavailableState())
        }
        for (key in listOf("vox_sk_secret", "other", "vox_pub_", "vox_pub_bad\nheader", "vox_pub_vox_sk_secret")) {
            assertNotNull(VoiceConfiguration(true, "https://example.invalid", key).unavailableState())
        }
        assertEquals("https://example.invalid:8443",
            VoiceConfiguration(true, "wss://example.invalid:8443/", "vox_pub_test").httpsOrigin)
    }

    @Test fun manifestAdvertisesOnlyRealGalleryActionAndNoUserState() {
        val manifest = JSONObject(VoiceProtocol.manifest())
        assertEquals("production", manifest.getString("environment"))
        assertEquals(0, manifest.getJSONArray("stateSchema").length())
        val actions = manifest.getJSONArray("actions")
        assertEquals(1, actions.length())
        val action = actions.getJSONObject(0)
        assertEquals("open_design_gallery", action.getString("name"))
        assertEquals(0, action.getJSONObject("params").length())
        assertFalse(action.getBoolean("dangerous"))
    }

    @Test fun matchesSdkInputAndOutputPcmEnvelopes() {
        val bytes = byteArrayOf(0, 0, -1, 127, 0, -128)
        val input = JSONObject(VoiceProtocol.input(bytes))
        assertEquals("audio_input", input.getString("type"))
        val audio = VoiceProtocol.parse("""{"type":"audio","data":"${input.getString("data")}"}""") as VoiceEvent.Audio
        assertArrayEquals(bytes, audio.pcm)
    }

    @Test fun inputHasStrictSizeAndSampleAlignmentLimits() {
        for (bytes in listOf(ByteArray(0), ByteArray(1), ByteArray(642))) {
            assertThrows(IllegalArgumentException::class.java) { VoiceProtocol.input(bytes) }
        }
        VoiceProtocol.input(ByteArray(640))
    }

    @Test fun rejectsInvalidAudioAndOversizedOutput() {
        for (base64 in listOf("", "AAA", "AA==", "%bad", "AA A=", "AB==",
            Base64.getEncoder().encodeToString(ByteArray(VoiceProtocol.MAX_AUDIO_BYTES + 2)))) {
            invalid("""{"type":"audio","data":"$base64"}""")
        }
        invalid("""{"type":"audio","data":123}""")
        val max = Base64.getEncoder().encodeToString(ByteArray(VoiceProtocol.MAX_AUDIO_BYTES))
        assertEquals(VoiceProtocol.MAX_AUDIO_BYTES,
            (VoiceProtocol.parse("""{"type":"audio","data":"$max"}""") as VoiceEvent.Audio).pcm.size)
    }

    @Test fun knownActionRequiresExplicitEmptyObjectArguments() {
        val valid = VoiceProtocol.parse(tool("{}")) as VoiceEvent.Tool
        assertTrue(valid.validArguments)
        for (args in listOf("null", "\"{}\"", "[]", """{"path":"/account"}""", "false", "0")) {
            assertFalse((VoiceProtocol.parse(tool(args)) as VoiceEvent.Tool).validArguments)
        }
        assertFalse((VoiceProtocol.parse("""{"type":"tool_call","id":"1","name":"open_design_gallery"}""")
            as VoiceEvent.Tool).validArguments)
        assertFalse((VoiceProtocol.parse("""{"type":"tool_call","id":"1","name":"open_design_gallery","args":{},"extra":1}""")
            as VoiceEvent.Tool).validArguments)
    }

    @Test fun rejectsInvalidToolIdentifiers() {
        for (id in listOf("", "a b", "x".repeat(129))) {
            invalid("""{"type":"tool_call","id":"$id","name":"open_design_gallery","args":{}}""")
        }
        invalid("""{"type":"tool_call","id":1,"name":"open_design_gallery","args":{}}""")
        invalid("""{"type":"tool_call","id":"1","name":"../other","args":{}}""")
    }

    @Test fun toolResultsPreserveCorrelationAndRealOutcome() {
        val call = VoiceProtocol.parse(tool("{}")) as VoiceEvent.Tool
        val success = JSONObject(VoiceProtocol.toolResult(call, true, "Design gallery opened."))
        assertEquals("tool_result", success.getString("type"))
        assertEquals("call-1", success.getString("id"))
        assertEquals("open_design_gallery", success.getString("name"))
        assertEquals(0, success.getJSONObject("state").length())
        assertEquals("success", success.getJSONObject("result").getString("status"))
        assertEquals("Design gallery opened.", success.getJSONObject("result").getString("result"))
        val failure = JSONObject(VoiceProtocol.toolResult(call, false, "Failed"))
        assertEquals("error", failure.getJSONObject("result").getString("status"))
        assertEquals("Failed", failure.getJSONObject("result").getString("message"))
    }

    @Test fun rejectsLenientJsonDuplicatesTrailingDataAndExcessiveNesting() {
        for (raw in listOf(
            "{type:'ready'}", """{"type":"ready",}""", """{"type":"ready"} trailing""",
            """{"type":"ready","type":"audio"}""", """{"type":"ready","\u0074ype":"audio"}""",
            """{"type":"ready","args":{"x":1,"x":2}}""",
            """{"type":"ready","x":NaN}""", """{"type":"ready","x":01}""",
            """{"type":"ready","x":1.}""", """{"type":"ready","x":+1}""",
            """{"type":"ready","x":[1,]}""", """{"type":"ready","x":undefined}""",
            """{"type":"ready","x":"\q"}""", "[{}]", """{"type":1}""",
            """{"type":"ready","x":""" + "[".repeat(15) + "0" + "]".repeat(15) + "}",
            """{"type":"ready","x":""" + "[" + List(600) { "0" }.joinToString(",") + "]}",
        )) invalid(raw)
        invalid("""{"type":"ready","x":"${"x".repeat(VoiceProtocol.MAX_MESSAGE_BYTES)}"}""")
        invalid("""{"type":"ready","x":"${"é".repeat(VoiceProtocol.MAX_MESSAGE_BYTES / 2)}"}""")
    }

    @Test fun parsesKnownEventsAndValidJsonScalarsWithoutCoercion() {
        assertEquals(VoiceEvent.Ready, VoiceProtocol.parse(
            """{"type":"ready","unused":[true,false,null,-0.25e+2,{"x":"escaped\"value"}]}"""))
        assertEquals(VoiceEvent.Interrupted, VoiceProtocol.parse("""{"type":"interrupted"}"""))
        assertEquals(VoiceEvent.TurnComplete, VoiceProtocol.parse("""{"type":"turn_complete"}"""))
        assertEquals(VoiceEvent.Text, VoiceProtocol.parse("""{"type":"text","text":"Hello","turnComplete":true}"""))
        assertEquals(VoiceEvent.Text, VoiceProtocol.parse("""{"type":"text_user","text":"Hi"}"""))
        assertEquals(VoiceEvent.Error(true), VoiceProtocol.parse("""{"type":"error","message":"usage_limit"}"""))
        assertEquals(VoiceEvent.Unknown, VoiceProtocol.parse("""{"type":"future_event"}"""))
        invalid("""{"type":"text","text":0}""")
        invalid("""{"type":"text","text":"x","turnComplete":"true"}""")
    }

    private fun tool(args: String) =
        """{"type":"tool_call","id":"call-1","name":"open_design_gallery","args":$args}"""

    private fun invalid(text: String) {
        assertThrows(VoiceProtocolException::class.java) { VoiceProtocol.parse(text) }
    }
}
