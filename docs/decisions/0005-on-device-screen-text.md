# 0005: On-device screen-text recognizer

**Decision ID and question:** RFC-D28 follow-up to [0004](0004-asr-ocr-benchmarks.md):
which recognizer reads on-screen text during live capture (AN-06, #100), and how is
SDK telemetry kept off the network?

**Status:** Accepted. Chosen by the product owner on 6 October 2026 and accepted
the same day after physical-device runs on a Samsung SM-A217F (Android 12; PR #105)
on Instagram reels and YouTube Shorts. ovrly sent nothing to Google's logging
endpoints (its process logs only that the `cct` transport is not registered); all
47 and 56 kept frames were read, with a median of 433 and 461 ms per frame. The
phone was charging and already hot during the runs, so temperature and battery
over three minutes were not measured.

**Owner and participants:** natnael-solomon (product owner) decided. The Android
implementation is #100. RES-02 (#14, Neb-iyu) owns the sampling values and the
recognizer comparison in 0004, which this record does not close.

**Options considered:** Google ML Kit Text Recognition Latin, bundled model;
ML Kit with the unbundled Play-services model; Tesseract on the device; no
on-device OCR (frames kept on the phone, text not read).

**Evidence and uncertainties:** Decision 0001 requires readable on-screen text on
the live path. 0004 names ML Kit as the intended on-device comparison; no phone
measurement existed. A first implementation (6 October 2026) showed that ML Kit
pulls in Google's `datatransport` libraries, whose upload components send SDK
usage metrics outside the project's stated chunk allowlist; the owner first
removed OCR (#97), then restored it with those components removed. Speed, battery,
heat and recognition quality on the test phone are measured in the #100 device run.

## Chosen option and rationale

ML Kit Text Recognition Latin `16.0.1` with the **bundled** model
(`com.google.mlkit:text-recognition`), so recognition never downloads a model or
calls Play services. The app manifest removes `datatransport`'s
`JobInfoSchedulerService`, `AlarmManagerSchedulerBroadcastReceiver` and
`TransportBackendDiscovery` with `tools:node="remove"`; without them the metrics
are never scheduled for upload and no upload backend is registered. The build task
`verify<Variant>NoTelemetry` fails `assemble` and `check` for every variant if any
`com.google.android.datatransport` or `com.google.firebase` component remains in the
merged manifest.

Frames stay in the app's private storage. Only the recognized lines, as
`{text, box, frame_pts}` observations with the sampling policy and recognizer
version, travel to the ovrly backend inside capture chunks.

**User-visible consequences:** Live capture reads on-screen text and the live
results can cover both speech and text. No setting is needed; no data goes to
Google for recognition.

**Technical, privacy, cost and evaluation consequences:** The APK grows by the
bundled model. Recognition costs CPU on the phone, bounded by the 1 Hz probe and
the 20-frames-per-minute cap. The SDK may still write its unsent metrics to a
local database in the app's private storage; they are deleted with the app data.

**Dependencies / capability gates:** #100 device evidence (time per frame,
observations on provider clips, temperature and battery over three minutes, no
network traffic to Google logging); #14 for the final sampling values.

**Rejected alternatives and why:** Unbundled model: needs Play services and a
model download. Tesseract on the device: not evaluated on the phone. On identical
bundle bytes (#103, 0004's 6 October revision), bundled ML Kit's selected-region
disagreement was 0.187 to 0.207 against Tesseract fast/best's 0.502/0.532. The
shipped sampler's per-minute cap dropped one brief card in that replay
([#110](https://github.com/natnael-solomon/ovrly/issues/110)).
No OCR: violates decision 0001's readable-text requirement. Leaving
`datatransport` active: sends data outside the allowlist.

**What evidence would reverse this decision:** Any observed network traffic from
ML Kit or `datatransport` on a device, a recognition time or heat cost that makes
live capture unusable on the test phone, or RES-02 results showing on-device
recognition is inadequate (0004).

**Linked contract, test, source and ideation record:** #100 (moved from #26/#30),
#97, decision 0001, 0004, BC-D05, RFC-D28/D71; `capture/CaptureText.kt`,
`CaptureTextTest`, `app/build.gradle.kts` (`VerifyNoTelemetryComponents`),
`app/src/main/AndroidManifest.xml`.
