[CmdletBinding()]
param([switch]$IncludePackaging)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $repositoryRoot
try {
    & pnpm check
    if ($LASTEXITCODE -ne 0) { throw "Static checks or tests failed." }
    & pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Application build failed." }
    & pnpm test:e2e
    if ($LASTEXITCODE -ne 0) { throw "Electron E2E failed." }
    if ($IncludePackaging) {
        # All prerequisites are fresh in this invocation; packaging uses
        # the same verified worker and application instead of rebuilding them.
        & pwsh.exe -NoProfile -File tools/python-worker/scripts/build.ps1 -SkipCheck
        if ($LASTEXITCODE -ne 0) { throw "Worker build failed." }
        & pnpm --filter @impeller-reliability/desktop package:win-unpacked
        if ($LASTEXITCODE -ne 0) { throw "win-unpacked packaging failed." }
        & pnpm smoke:win-unpacked
        if ($LASTEXITCODE -ne 0) { throw "win-unpacked smoke failed." }
        & pnpm --filter @impeller-reliability/desktop package:setup --prepackaged dist/win-unpacked
        if ($LASTEXITCODE -ne 0) { throw "Setup packaging failed." }
        & pnpm smoke:setup
        if ($LASTEXITCODE -ne 0) { throw "Installed lifecycle smoke failed." }
    }
}
finally { Pop-Location }
