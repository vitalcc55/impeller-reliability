# Release Checklist

- Frozen pnpm/uv installs.
- TypeScript typecheck/lint/format/tests; Ruff/mypy/Pyright/pytest.
- Electron build/E2E; worker onedir self-test and SHA-256.
- `win-unpacked` black-box smoke.
- Real portable black-box smoke; size/startup recorded.
- No TCP/orphan process; temp cleanup checked.
- Docs/ADR/changelog/version/licenses synchronized.
- Windows 10 and 11 target checks; signing decision recorded.
