# Release Checklist

- Frozen pnpm/uv installs.
- TypeScript typecheck/lint/format/tests; Ruff/mypy/Pyright/pytest.
- Electron build/E2E; worker onedir self-test and SHA-256.
- `win-unpacked` black-box smoke.
- Реальный NSIS install/upgrade/repair/downgrade-refusal/uninstall/reinstall; N+1 читает дело и профиль N, hashes/history/provenance сохранены.
- Интерактивные Retry/Cancel, dirty-save и штатное закрытие всех экземпляров/worker без kill; отказ/повтор Setup проверены по [Windows Setup](WINDOWS_SETUP.md).
- No TCP/orphan process; temp cleanup checked.
- Docs/ADR/changelog/version/licenses synchronized.
- Windows 10 and 11 target checks; signing decision recorded.
