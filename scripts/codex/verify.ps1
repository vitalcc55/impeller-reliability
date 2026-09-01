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
        & pwsh.exe -NoProfile -File scripts/build/package.ps1 -Target WinUnpacked -SkipWorkerCheck
        if ($LASTEXITCODE -ne 0) { throw "win-unpacked packaging failed." }
        & pnpm smoke:win-unpacked
        if ($LASTEXITCODE -ne 0) { throw "win-unpacked smoke failed." }
        & pwsh.exe -NoProfile -File scripts/build/package.ps1 -Target Portable -SkipWorkerCheck
        if ($LASTEXITCODE -ne 0) { throw "Portable packaging failed." }
        & pnpm smoke:portable
        if ($LASTEXITCODE -ne 0) { throw "Portable smoke failed." }
    }
}
finally { Pop-Location }
