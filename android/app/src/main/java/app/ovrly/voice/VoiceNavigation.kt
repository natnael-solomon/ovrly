package app.ovrly.voice

/** Tabs voice may open. Settings is excluded on purpose. */
enum class VoiceTab(val wireName: String, val label: String) {
    SPACE("space", "Your space"),
    EXPLORE("explore", "Explore");

    internal companion object {
        /** Accepts exactly `{"tab": "<wireName>"}`; anything else is null. */
        fun from(arguments: String?): VoiceTab? {
            val args = try {
                arguments?.let(VoiceProtocol::objectFrom)
            } catch (_: VoiceProtocolException) {
                null
            }
            val tab = args?.opt("tab")
            return if (args?.length() == 1 && tab is String) {
                entries.singleOrNull { it.wireName == tab }
            } else {
                null
            }
        }
    }
}

/** Called on the main thread. Returns false when [tab] is already showing. */
fun interface VoiceNavigator {
    fun openTab(tab: VoiceTab): Boolean
}
