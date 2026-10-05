# Device and provider compatibility

What the current `CaptureService` and `OverlayService` can do on real devices
with real providers. This table decides which combinations the app may
advertise (BC-D02, AC01). Every cell is filled from a recorded capture; see
"Method" for how a cell is scored. Only device model and Android version are
recorded.

Status: speaker, wired and failure cases recorded on 5 October 2026 (AN-01, #9). Bluetooth and Facebook are not available on the test device. Cells marked `untested` have no evidence yet.

## Devices

| Device | Android | Launcher | Notes |
| --- | --- | --- | --- |
| Samsung SM-A217F (Galaxy A21s) | 12 | Niagara Launcher, One UI Home for splash checks | Owner's phone; only device so far |

## Capability matrix

One row per provider on each device. Columns:

- **Overlay**: the floating control renders above the provider and receives touch.
- **Audio**: `AudioPlaybackCapture` yields non-silent samples (`playbackSignalDetected: true` in `capture.json`).
- **Frames**: sampled screen frames are readable (not black, not blank), checked by eye on the pulled JPEG.
- **Wired**: audio column result with a 3.5 mm headset connected.
- **Bluetooth**: audio column result with a Bluetooth output connected.
- **Secure**: whether the provider marks its window secure (frames come back black while audio may still flow).

| Provider | Overlay | Audio | Frames | Wired | Bluetooth | Secure | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| YouTube Shorts | yes | yes | yes | untested | not available | no | 2026-10-05 speaker cell: 83 s, 17 frames, signal true, frame readable |
| TikTok | yes | yes | yes | untested | not available | no | 2026-10-05 speaker cell: 76 s, 16 frames, signal true, frame readable incl. on-screen text and captions |
| Instagram Reels | yes | yes | yes | yes | not available | no | 2026-10-05 speaker cell: 83 s, 17 frames, signal true, frame readable incl. burned-in captions; wired cell: 51 s, 11 frames, signal true with the 3.5 mm headset as active output |
| Facebook Reels | not available | not available | not available | not available | not available | not available | Only the carrier stub is installed on this device; retest when the full app is available |
| Control: known-silent clip | n/a | expect false | untested | n/a | n/a | n/a | |

Wired was verified on Instagram only. `AudioPlaybackCapture` taps the playback mix before routing, so the result is expected to hold for the other providers; YouTube and TikTok wired cells are not separately recorded.

## Failure and interruption cases

Run on one provider (TikTok or YouTube); result must be the same on the
others for the overlay and capture lifecycle, which does not depend on the
provider.

| Case | Expected | Observed | Evidence |
| --- | --- | --- | --- |
| Capture permission denied | Capture does not start; clear message; no resources held | Pass. Cancel on the Android consent dialog: no service started; app shows "Screen capture consent canceled. No media access was started." | 2026-10-05 |
| System stops the projection (notification "Stop") | Capture ends with a reason; overlay stays usable | Pass. One UI 4 shows no system stop control for this projection; the app notification "Stop capture" action ends it (30 s, reason "Capture stopped."). Revoking the PROJECT_MEDIA app-op does not end an already granted projection. | 2026-10-05 |
| Another app starts a projection (built-in screen recorder) | Our capture ends or is refused with a reason; no crash | Not tested: no screen recorder is available on this device. | |
| Rotation during capture | Capture continues; frames may be letterboxed | Pass. Capture continued through landscape and back to portrait (38 s, 8 frames). | 2026-10-05 |
| Screen lock during capture | Capture ends with a reason; no audio recorded while locked | Pass. Stopped within the lock with reason "Capture stopped when the screen locked." (8 s). | 2026-10-05 |
| Scrolling to the next video | Capture continues; no automatic restart | Pass. Two scrolls on TikTok; capture continued, audio signal true (62 s, 13 frames), stopped from the overlay. | 2026-10-05 |
| Process death (swipe from recents) | Capture ends; private files finalised or cleaned | Pass. Capture and overlay ended at once; reason "Capture service stopped. Media access has been released."; capture.json finalised (17 s, 4 frames). | 2026-10-05 |
| Three-minute limit reached | Capture stops itself at 180 s with a reason; files finalised | Stopped at exactly 180 000 ms, 36 frames, signal true, reason "Stopped at the 3-minute capture limit" | 2026-10-05, TikTok |

## Diagnosis rules

Silence, blocked and routing are different findings and are recorded differently:

- **Silence**: `playbackSignalDetected: false` and the control clip (known silent) also reads false, while a known-loud clip on the same provider reads true. Record as silence, not blocking.
- **Blocked by provider policy**: a known-loud clip reads false on this provider while the same clip read true on another provider in the same session. Record as "no evidence of capture" and name the provider; do not assert `ALLOW_CAPTURE_BY_NONE` without inspecting the app's manifest.
- **Routing**: the result changes when the output device changes (speaker vs wired vs Bluetooth). Record per output.

## Method

1. Install the current `main` debug build (`app.ovrly`). Grant overlay and microphone permissions once.
2. Open the provider, start a clip with clear speech, start capture from the overlay, let it run 30 to 60 seconds, stop it from the overlay.
3. Run `.\scripts\device_matrix_pull.ps1 -Provider <name> -Condition <speaker|wired|bluetooth|case-name>`. It pulls `capture.json` and one mid-run frame into `.local/device-matrix/` and appends a row to `matrix.csv`.
4. Score the cell from the pulled files: audio from `playbackSignalDetected`, frames by opening the JPEG, overlay by whether Stop responded.
5. For the control row use a clip you know is muted.

Evidence folders stay local (`.local` is Git-ignored). This file records results only.

## Decision

Recorded as [BC-D02](decisions/BC-D02-providers-devices.md): YouTube Shorts, TikTok and Instagram Reels are advertised on Android 12 and later; Facebook is not advertised until tested.
