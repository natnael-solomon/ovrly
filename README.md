# ovrly

Native Android companion with floating controls and local video-interval capture. Research and backend processing are not connected.

## Start here (team)

1. **Find your task** on the public [ovrly development board](https://github.com/users/natnael-solomon/projects/3). Filter by assignee; each issue is the spec (outcome, completion checklist, *Depends on*, contract links). Read the board README (ⓘ, top right) once: it explains the `AN-`/`BE-`/`RES-`/`REPO-` codes, the shared definition of done, the free-tier constraints the plan assumes, and the working rules (fixed backend start order, nobody idles on a blocker).
2. **Set up the toolchain** below and build once before changing anything.
3. **Work the way [WORKFLOW.md](WORKFLOW.md) describes**: one issue per branch, Conventional Commit PR titles, link the issue, one approval from another developer, squash merge. Capture, permission, overlay and voice changes also need physical-device evidence in the PR.

Deadlines and checkpoints (CP0–CP3) are the board milestones. Decisions are recorded in `docs/decisions/`, introduced by [`REPO-01`](https://github.com/natnael-solomon/ovrly/issues/5).

## Get started

Requires JDK 21, Android SDK platform 37 and Build Tools 36.0.0. The project builds with Gradle 9.7.1 (wrapper) and Android Gradle plugin 9.4.1; lint runs with warnings as errors. Open `android` in Android Studio, or follow the [Windows, WSL and Linux build instructions](android/README.md#setup).

- [Android app](android/README.md)
- [Backend status](backend/README.md)
- [Architecture](docs/architecture.md)
- [Team workflow](WORKFLOW.md)
- [Changelog](CHANGELOG.md)

Keep personal files in `.local`, experiments in `.scratch` and APK exports in `exports`. These directories are Git-ignored.

## License

Ovrly's original code is source-available under [PolyForm Noncommercial 1.0.0](LICENSE.md), not an open-source license. It permits noncommercial use, modification and sharing, including the institutional uses specified in the license. Commercial use outside those permissions requires a separate agreement with the relevant copyright holders.

Third-party components retain their own licenses.
