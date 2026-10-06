# Android UI maintenance

Setup and user-facing limitations are in the [Android README](../android/README.md). Overlay lifecycle and device checks are in [architecture](architecture.md#overlay-material-and-demo-boundaries).

## Appearance and text

Liquid Chrome is the fresh-install default; saved Light/Dark choices are independent of the system theme. Light mode follows Mock 1. The appearance applies to screens, dialogs, overlays and the gallery.

Lexend is the default for interface text, including dialog and demo-overlay claim headings. Instrument Serif is reserved for featured, Explore and report-detail titles, gallery headings and the text-based wordmark. This distinction does not change type sizes. Both fonts' SIL Open Font licenses ship in APK assets.

## Live results panel

The live overlay's forms (the examining pill, the expanded panel, the idle bubble and the "Saved to Inbox" pill) and the Stop choice (`ui/LiveCompactOverlay.kt`, `ui/LiveResultsPanel.kt`, copy in `ui/LiveResultsCopy.kt`, previews in `ui/LiveResultsPreviews.kt`) use the overlay glass, palette and Lexend type, in both Light and Dark. The expanded panel shares `OverlayPanelScaffold` with the demo panel (`ui/DemoOverlayPanel.kt`): glass, 12 dp padding, a header row with 48 dp controls, dividers, a scrolling body and a footer line. Every control has a 48 dp target and a TalkBack label; claim rows announce time, wording, assessment and state, and the phase line and update notice are polite live regions. Claim state is always written out ("Waiting", "Checking evidence", "Provisional", "Updated", "Assessed", "Incomplete: ...", "Unknown state"), never shown by color alone. Previews cover every form in Light, Dark and 200% text with the labelled fixture. Behavior is in the [Android README](../android/README.md#live-overlay-results).

## App icon

The adaptive launcher icon is `mipmap-anydpi/ic_launcher.xml`:

| Layer | Resource and dimensions |
| --- | --- |
| Foreground | `mipmap-*/ic_launcher_foreground.webp`: rendered chrome ring on a 108 dp canvas at five densities, filling 92% of the 66 dp safe zone. |
| Background | `drawable/ic_launcher_background.xml`: navy radial gradient with a faint lavender bloom. |
| Monochrome | `drawable/ic_launcher_monochrome.xml`: the ovrly mark (below) at 60 dp inside the 66 dp safe zone, for Android 13+ themed icons. |

Notifications and the overlay (idle setup ring, examining pill, bubble and panel header) use `drawable/ic_ovrly.xml`, the ovrly mark: the landing page's two-segment ring (`landing/public/art/ring.webp`) traced as one filled monochrome shape with the same gap, thickness and rounded ends, drawn on a 920-unit square at a 22 dp diameter. The generator `scripts/splash_icon.py` produces launcher foregrounds with the splash asset.

## Header wordmark

Your space and Explore use `drawable-*/wordmark_ovrly.webp`, a 44 dp rendered wordmark at five densities. `Modifier.chromeGlare` sweeps a diagonal glare across it about every five seconds; the docked voice orb repeats a fainter pass just after it. The animation is masked to the bitmap, runs in the draw phase without recomposition, pauses off screen and follows the system animator scale.

Light mode draws a blurred ink shadow, blurred sheen halo and crisp ink edge beneath the chrome for contrast with paper. Dark mode omits those layers and uses a stronger glare.

## Launch splash

Cold and warm launches use Android's SplashScreen API through AndroidX Core SplashScreen, including Android 10/11 compatibility resources. The static chrome ring sits on Chrome black (`#080910`). There is no separate Activity, timer, spinner, network request or keep-on-screen condition.

After the first app frame, a roughly 900 ms handoff scales the ring to 1.3x and fades it with the splash surface. App content fades in and rises 24 dp on the same clock, using Material emphasized-decelerate easing. Durations follow the system animator scale; reduced motion makes the handoff immediate. Recreated Activities skip it, hot resumes do not replay it, and a 2.5 s timeout reveals content if the platform never reports splash exit. This is branding, not a startup-speed improvement.

On Android 12+, a saved Light choice registers `Theme.Ovrly.Starting.Light` through `SplashScreen.setSplashScreenTheme`. The next cold launch uses paper (`#F0EFE5`) and light system bars. Dark clears the override to the manifest theme. Android 10/11 always show the Chrome-black compatibility splash before a saved Light first frame.

`MainActivity` installs the splash, loads the saved appearance, registers the matching splash theme and applies the app theme before `super.onCreate`. Restored state and incoming-intent routing are preserved.

### Artwork constraints

Android 12+ insets a plain drawable into a centered 192 dp circle within the 288 dp canvas and masks it there. The `drawable-*/splash_ovrly.webp` assets use a 288 dp canvas at each density, lossy WebP with exact alpha, and 15 to 79 KB per file. The ring fills 97% of the mask circle.

Keep the wordmark in the app header. Putting it below the ring inside the splash circle roughly halves the ring's size. The notification icon remains `ic_ovrly.xml`.

The [Android README](../android/README.md#appearance) records the Android 12 launch-source limitation.

## Regenerate assets

From the repository root, using rendered PNGs with transparent backgrounds:

```text
python scripts/splash_icon.py <ring.png> --wordmark <wordmark.png>
```

The script needs Pillow and numpy and is not part of Gradle or CI. It removes red anti-aliasing fringe, centers the ring in the mask, and writes splash, launcher foreground and header wordmark assets at every density. It rejects splash output that would be clipped, too small or color-fringed. `scripts/splash_icon.py --wordmark` selects wordmark generation.

`LaunchSplashResourcesTest` checks shipped canvas dimensions, alpha flags, sizes and wiring.
