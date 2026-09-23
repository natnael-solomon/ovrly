package app.ovrly.overlay

internal enum class DemoEntry { WAIT_FOR_STORAGE, CONFIRM_STOP, READY }

internal fun demoEntry(captureBusy: Boolean, voiceActive: Boolean, storageBusy: Boolean): DemoEntry = when {
    storageBusy -> DemoEntry.WAIT_FOR_STORAGE
    captureBusy || voiceActive -> DemoEntry.CONFIRM_STOP
    else -> DemoEntry.READY
}
