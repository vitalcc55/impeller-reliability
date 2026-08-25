[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$workerExecutable = Join-Path $repositoryRoot "tools\python-worker\dist\impeller-reliability-worker\impeller-reliability-worker.exe"
if (-not (Test-Path -LiteralPath $workerExecutable)) { throw "Packaged worker not found." }
$result = & $workerExecutable --self-test | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $result.passed -ne $true) { throw "Worker smoke failed." }
Write-Output ($result | ConvertTo-Json -Depth 5)
