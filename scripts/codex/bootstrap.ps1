[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $repositoryRoot
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed." }
    & uv sync --project tools/python-worker --frozen
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed." }
}
finally { Pop-Location }
