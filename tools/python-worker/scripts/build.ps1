[CmdletBinding()]
param([switch]$SkipCheck)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workerRoot = Split-Path -Parent $PSScriptRoot
$distPath = Join-Path $workerRoot "dist"
$buildPath = Join-Path $workerRoot "build"
$artifactDirectory = Join-Path $distPath "impeller-reliability-worker"
$executablePath = Join-Path $artifactDirectory "impeller-reliability-worker.exe"
$manifestPath = Join-Path $artifactDirectory "worker-manifest.json"

if (-not $SkipCheck) { & (Join-Path $PSScriptRoot "check.ps1") }

Push-Location $workerRoot
try {
    & uv run python -I -m PyInstaller --clean --noconfirm --distpath $distPath --workpath $buildPath worker.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller onedir build failed." }
}
finally { Pop-Location }

if (-not (Test-Path -LiteralPath $executablePath)) { throw "Worker executable is missing." }
$digest = (Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    schemaVersion = 1
    executable = "impeller-reliability-worker.exe"
    sha256 = $digest
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8

$probeOutput = & $executablePath --self-test
if ($LASTEXITCODE -ne 0) { throw "Packaged worker self-test failed." }
$probe = $probeOutput | ConvertFrom-Json
if ($probe.passed -ne $true) { throw "Packaged worker self-test returned failure." }
Write-Output "PyInstaller onedir worker built and verified: $artifactDirectory"
