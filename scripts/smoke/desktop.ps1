[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("WinUnpacked", "Portable")]
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$desktopDist = Join-Path $repositoryRoot "apps\desktop\dist"
$smokeDirectory = Join-Path $repositoryRoot ".tmp\.codex\evidence\$($Target.ToLowerInvariant())"
$summaryPath = Join-Path $smokeDirectory "summary.json"
New-Item -ItemType Directory -Force -Path $smokeDirectory | Out-Null
Remove-Item -LiteralPath $summaryPath -Force -ErrorAction SilentlyContinue

if ($Target -eq "WinUnpacked") {
    $executablePath = Join-Path $desktopDist "win-unpacked\ImpellerReliabilityCalc.exe"
}
else {
    $executablePath = Join-Path $desktopDist "ImpellerReliabilityCalc-0.1.0-portable-x64.exe"
}
if (-not (Test-Path -LiteralPath $executablePath)) { throw "Desktop artifact not found: $executablePath" }

$env:IMPELLER_SMOKE_OUTPUT = $summaryPath
$launchStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $desktopProcess = Start-Process -FilePath $executablePath -PassThru -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    $networkObserved = $false
    while ([DateTime]::UtcNow -lt $deadline -and -not (Test-Path -LiteralPath $summaryPath)) {
        $ownedProcesses = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessName -like "ImpellerReliabilityCalc*" -or $_.ProcessName -eq "impeller-reliability-worker"
        }
        foreach ($ownedProcess in $ownedProcesses) {
            if (Get-NetTCPConnection -OwningProcess $ownedProcess.Id -ErrorAction SilentlyContinue) {
                $networkObserved = $true
            }
        }
        Start-Sleep -Milliseconds 250
    }
}
finally {
    Remove-Item Env:IMPELLER_SMOKE_OUTPUT -ErrorAction SilentlyContinue
}
if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Desktop smoke timed out." }
$launchStopwatch.Stop()
$summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
$summary | Add-Member -NotePropertyName launcherElapsedMs -NotePropertyValue $launchStopwatch.ElapsedMilliseconds
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding utf8
if ($summary.passed -ne $true) { throw "Desktop smoke returned failure." }
if ($networkObserved) { throw "Desktop smoke observed a TCP connection." }
Start-Sleep -Milliseconds 750
$orphans = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -eq "impeller-reliability-worker"
}
if ($orphans) { throw "Desktop smoke left an orphan worker process." }
Write-Output ($summary | ConvertTo-Json -Depth 8)
