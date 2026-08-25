[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("WinUnpacked", "Portable")]
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Push-Location $repositoryRoot
try {
    & pwsh.exe -NoProfile -File tools/python-worker/scripts/build.ps1
    if ($LASTEXITCODE -ne 0) { throw "Worker build failed." }
    & pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Electron build failed." }
    if ($Target -eq "WinUnpacked") {
        & pnpm --filter @impeller-reliability/desktop package:win-unpacked
    }
    else {
        & pnpm --filter @impeller-reliability/desktop package:portable
    }
    if ($LASTEXITCODE -ne 0) { throw "Electron packaging failed." }
}
finally { Pop-Location }
