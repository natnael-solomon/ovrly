# ovrly

Native Android companion with floating controls and local video-interval capture.

## Get started

Open `android` in Android Studio. Follow the [Android setup instructions](android/README.md#setup) for prerequisites and Windows, WSL or Linux commands.

For the backend, run `sh scripts/backend.sh` on Linux/WSL with uv and Docker Compose. See the [backend setup](backend/README.md#local-setup-linux--wsl).

- [Task board](https://github.com/users/natnael-solomon/projects/3)
- [Team workflow](WORKFLOW.md)
- [Backend](backend/README.md)
- [Evaluation](evaluation/README.md) (synthetic examples and an eleven-clip draft; frozen corpus pending)
- [Architecture](docs/architecture.md)
- [Decision records](docs/decisions/README.md) (scope, tracking, dates, Voxide route)
- [Changelog](CHANGELOG.md)

Team references are shared privately.

Keep personal files in `.local`, experiments in `.scratch` and APK exports in `exports`. These directories are Git-ignored.

## License

Original code uses [PolyForm Noncommercial 1.0.0](LICENSE.md), a source-available license, not an open-source license. Commercial use outside its permissions requires a separate agreement with the relevant copyright holders. Third-party components retain their own licenses.
