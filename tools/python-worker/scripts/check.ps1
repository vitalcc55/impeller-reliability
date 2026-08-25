[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workerRoot = Split-Path -Parent $PSScriptRoot

Push-Location $workerRoot
try {
    & uv run ruff format --check src tests
    if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed." }
    & uv run ruff check src tests
    if ($LASTEXITCODE -ne 0) { throw "Ruff lint failed." }
    & uv run mypy
    if ($LASTEXITCODE -ne 0) { throw "mypy failed." }
    & uv run pyright
    if ($LASTEXITCODE -ne 0) { throw "Pyright failed." }
    & uv run pytest
    if ($LASTEXITCODE -ne 0) { throw "pytest failed." }
}
finally {
    Pop-Location
}
