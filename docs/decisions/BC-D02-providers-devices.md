# BC-D02: Supported providers and devices

**Question:** Which provider and device combinations may Ovrly advertise as
supported for live capture?

**Status:** Accepted. Decided by natnael-solomon on 5 October 2026.

**Owner and participants:** natnael-solomon decided, on the evidence in
[the compatibility matrix](../compatibility.md) gathered for AN-01 (#9).

## Chosen option

Advertise live capture for **YouTube Shorts, TikTok and Instagram Reels on
Android 12 and later**. Facebook Reels is not advertised until it is tested on
a device with the full Facebook app.

## Evidence

On a Samsung SM-A217F with Android 12, on 5 October 2026:

- YouTube, TikTok and Instagram: the overlay rendered over the provider and
  its Stop control worked; playback audio was captured
  (`playbackSignalDetected: true`); sampled frames were readable, including
  on-screen text and burned-in captions; no provider marked its window secure.
- Instagram with a 3.5 mm wired headset as the active output: audio was
  captured.
- Lifecycle: consent denial, notification Stop, rotation, screen lock,
  scrolling to the next video, removal from recents and the 3-minute limit all
  behaved as specified.

## Limits of the evidence

- One device and one Android version were tested. "Any Android 12+ phone" is
  an owner decision that extends that evidence; it is not a measured result on
  other manufacturers or versions.
- Bluetooth output, a known-silent control clip, a competing screen recording,
  and wired output on YouTube and TikTok were not tested.
- Providers can change their capture policy in an update. A capture with no
  playback signal on an advertised provider must be reported as a limitation,
  not a finding.

## User-visible consequences

Store text, onboarding and the submission name only the three providers and
Android 12 or later. Other providers may work but are not promised.

## What would reverse this decision

A failed capture (no overlay, no audio or black frames) on an advertised
provider or on another Android 12+ device; a provider update that blocks
playback capture; or Facebook evidence that adds it.

## Links

AN-01 (#9); AC01; BC-D01 to BC-D09 in the build contract; RFC-D04, RFC-D05,
RFC-D14; [compatibility matrix](../compatibility.md).
