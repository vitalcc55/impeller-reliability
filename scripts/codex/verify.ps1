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
        & pnpm package:win-unpacked
        & pnpm smoke:win-unpacked
        & pnpm package:portable
        & pnpm smoke:portable
    }
}
finally { Pop-Location }
