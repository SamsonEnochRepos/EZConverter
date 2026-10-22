# Contributing

Thanks for helping. By contributing you agree that your contribution is licensed under the [Apache License 2.0](LICENSE)
(Section 5 of the license), the same as the project.

## Setup

```bash
scripts/setup.sh                 # conda env "EZ" (Python 3.12) + engines + pnpm install
conda activate EZ
python -m pytest tests/unit tests/integration tests/security -n 4
pnpm -r typecheck && pnpm -r test
```

Before opening a pull request run `scripts/verify.sh` (or the parts that apply to your change) and add a regression test for every bug you fix.
Keep security-relevant changes small and explain the threat in the PR description.

## Building the release artifacts

| Artifact | Command | Needs |
|---|---|---|
| Windows installer + portable zip | `python scripts/fetch_windows_engines.py && python scripts/build_sidecar.py && pnpm --filter @ezconverter/web build && pnpm --filter @ezconverter/desktop build && cd apps/desktop && pnpm exec electron-builder --win` | Windows, Python 3.12 with `pyinstaller` and `backend[sevenzip]` installed, Node 22+ |
| Android APK | `cd apps/mobile && pnpm exec expo prebuild --platform android --clean && cd android && ./gradlew assembleRelease` | JDK 17-25, Android SDK 36, signing variables below |
| Web app | `pnpm --filter @ezconverter/web build` (static files in `apps/web/dist`) | Node 22+ |
| Converter image | `docker build -f deployment/containers/Dockerfile -t ezconverter .` | Docker |

**Android signing.** The release build reads `EZ_KEYSTORE_PATH`, `EZ_KEYSTORE_PASSWORD`, `EZ_KEY_ALIAS` and `EZ_KEY_PASSWORD` from the environment.
Keep the keystore outside the repository and back it up: if it is lost, existing installs cannot be updated. Without these variables the
release build falls back to the debug key and must not be published.

After `scripts/fetch_windows_engines.py` the engines are checked against `scripts/engines.lock.json`; to upgrade an engine, change the URL
and checksum there in the same commit and re-run the conformance suite.

## Style

Python: `ruff` (configured in `backend/pyproject.toml`). TypeScript: strict mode, no `any` without a comment.
