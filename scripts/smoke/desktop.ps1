[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("WinUnpacked", "Portable")]
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$desktopDist = Join-Path $repositoryRoot "apps\desktop\dist"
$smokeDirectory = Join-Path $repositoryRoot ".tmp\.codex\evidence\$($Target.ToLowerInvariant())"
$summaryPath = Join-Path $smokeDirectory "summary.json"
$projectPath = Join-Path $smokeDirectory "Packaged smoke project.irproj"
$runPackagePath = Join-Path $repositoryRoot "fixtures\contracts\r130run\v1\m9a\packages\exact_methodical_rounding.r130run"
$rptRunPackagePath = Join-Path $repositoryRoot "fixtures\contracts\r130run\v1\m9a\packages\normal_final_rpt_full_stop.r130run"
$pmnFixtureDirectory = Join-Path $repositoryRoot "fixtures\contracts\r130run\v1\pmn-reference"
$pmnFixtureMetadataPath = Join-Path $pmnFixtureDirectory "UPSTREAM_SOURCE.json"
$materialFixtureDirectory = Join-Path $repositoryRoot "fixtures\contracts\r130run\v1\source-materials"
$materialFixtureMetadataPath = Join-Path $materialFixtureDirectory "UPSTREAM_SOURCE.json"
$packageMetadata = Get-Content -LiteralPath (Join-Path $repositoryRoot "apps\desktop\package.json") -Raw | ConvertFrom-Json
$applicationExecutable = Join-Path $desktopDist "win-unpacked\ImpellerReliabilityCalc.exe"

function Update-OwnedProcessIds {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.HashSet[int]]$OwnedProcessIds
    )
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $snapshot) {
            $processId = [int]$process.ProcessId
            $parentProcessId = [int]$process.ParentProcessId
            if ($OwnedProcessIds.Contains($parentProcessId) -and $OwnedProcessIds.Add($processId)) {
                $changed = $true
            }
        }
    }
    return $snapshot
}

function Stop-OwnedProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.HashSet[int]]$OwnedProcessIds
    )
    foreach ($processId in @($OwnedProcessIds) | Sort-Object -Descending) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $smokeDirectory | Out-Null
Remove-Item -LiteralPath $summaryPath -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $projectPath) {
    $resolvedProjectPath = [System.IO.Path]::GetFullPath($projectPath)
    $resolvedSmokeDirectory = [System.IO.Path]::GetFullPath($smokeDirectory).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedProjectPath.StartsWith($resolvedSmokeDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove project outside smoke directory."
    }
    Remove-Item -LiteralPath $resolvedProjectPath -Recurse -Force
}

if ($Target -eq "WinUnpacked") {
    $executablePath = $applicationExecutable
}
else {
    $artifactName = "ImpellerReliabilityCalc-$($packageMetadata.version)-portable-x64.exe"
    $executablePath = Join-Path $desktopDist $artifactName
}
if (-not (Test-Path -LiteralPath $executablePath)) { throw "Desktop artifact not found: $executablePath" }
if (-not (Test-Path -LiteralPath $applicationExecutable)) { throw "Packaged application executable not found: $applicationExecutable" }

& node (Join-Path $repositoryRoot "apps\desktop\scripts\verify-packaged-fuses.mjs") $applicationExecutable
if ($LASTEXITCODE -ne 0) { throw "Electron fuse verification failed." }

if (-not (Test-Path -LiteralPath $runPackagePath)) { throw "Producer M9a package not found: $runPackagePath" }
if (-not (Test-Path -LiteralPath $rptRunPackagePath)) { throw "Producer RPT package not found: $rptRunPackagePath" }
if (-not (Test-Path -LiteralPath $pmnFixtureMetadataPath)) { throw "Producer PMN metadata not found: $pmnFixtureMetadataPath" }
$pmnFixtureMetadata = Get-Content -LiteralPath $pmnFixtureMetadataPath -Raw | ConvertFrom-Json
$pmnRunPackagePath = Join-Path $pmnFixtureDirectory ([string]$pmnFixtureMetadata.package.file)
if (-not (Test-Path -LiteralPath $pmnRunPackagePath)) { throw "Producer PMN package not found: $pmnRunPackagePath" }
$pmnPackageSha256 = (Get-FileHash -LiteralPath $pmnRunPackagePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($pmnPackageSha256 -ne [string]$pmnFixtureMetadata.package.sha256) { throw "Producer PMN package hash mismatch." }
$materialFixtureMetadata = Get-Content -LiteralPath $materialFixtureMetadataPath -Raw | ConvertFrom-Json
$materialRecords = @($materialFixtureMetadata.packages | Where-Object { $_.scenario -eq "protocol_revision_2" })
if ($materialRecords.Count -ne 1) { throw "Producer material fixture record missing or ambiguous." }
$materialRecord = $materialRecords[0]
if ($materialRecord.file -ne "protocol_revision_2.r130run") { throw "Unexpected producer material filename." }
$materialRunPackagePath = Join-Path $materialFixtureDirectory "protocol_revision_2.r130run"
$materialPackageSha256 = (Get-FileHash -LiteralPath $materialRunPackagePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($materialPackageSha256 -ne [string]$materialRecord.sha256) { throw "Producer material package hash mismatch." }

$env:IMPELLER_SMOKE_OUTPUT = $summaryPath
$env:IMPELLER_SMOKE_HOLD_MS = "1500"
$env:IMPELLER_AUTOMATED_PROJECT_PATH = $projectPath
$env:IMPELLER_AUTOMATED_R130RUN_PATH = $runPackagePath
$env:IMPELLER_AUTOMATED_RPT_RUN_PATH = $rptRunPackagePath
$env:IMPELLER_AUTOMATED_PMN_RUN_PATH = $pmnRunPackagePath
$env:IMPELLER_AUTOMATED_PMN_PACKAGE_SHA256 = $pmnPackageSha256
$env:IMPELLER_AUTOMATED_PMN_PRODUCER_COMMIT = [string]$pmnFixtureMetadata.producer.commit
$env:IMPELLER_AUTOMATED_MATERIAL_RUN_PATH = $materialRunPackagePath
$env:IMPELLER_AUTOMATED_MATERIAL_PACKAGE_SHA256 = $materialPackageSha256
$launchStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$ownedProcessIds = [System.Collections.Generic.HashSet[int]]::new()
trap {
    Stop-OwnedProcesses -OwnedProcessIds $ownedProcessIds
    throw
}
$networkObserved = $false
try {
    $desktopProcess = Start-Process -FilePath $executablePath -PassThru -WindowStyle Hidden
    $ownedProcessIds.Add([int]$desktopProcess.Id) | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline -and -not (Test-Path -LiteralPath $summaryPath)) {
        $snapshot = @(Update-OwnedProcessIds -OwnedProcessIds $ownedProcessIds)
        foreach ($process in $snapshot) {
            $processId = [int]$process.ProcessId
            if ($ownedProcessIds.Contains($processId) -and (Get-NetTCPConnection -OwningProcess $processId -ErrorAction SilentlyContinue)) {
                $networkObserved = $true
            }
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $summaryPath)) {
        Stop-OwnedProcesses -OwnedProcessIds $ownedProcessIds
        throw "Desktop smoke timed out."
    }
    $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
    $ownedProcessIds.Add([int]$summary.pid) | Out-Null
    if ($null -ne $summary.workerPid) { $ownedProcessIds.Add([int]$summary.workerPid) | Out-Null }
    $snapshot = @(Update-OwnedProcessIds -OwnedProcessIds $ownedProcessIds)
    foreach ($process in $snapshot) {
        $processId = [int]$process.ProcessId
        if ($ownedProcessIds.Contains($processId) -and (Get-NetTCPConnection -OwningProcess $processId -ErrorAction SilentlyContinue)) {
            $networkObserved = $true
        }
    }
}
finally {
    Remove-Item Env:IMPELLER_SMOKE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_SMOKE_HOLD_MS -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_PROJECT_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_R130RUN_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_RPT_RUN_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_PMN_RUN_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_PMN_PACKAGE_SHA256 -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_PMN_PRODUCER_COMMIT -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_MATERIAL_RUN_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:IMPELLER_AUTOMATED_MATERIAL_PACKAGE_SHA256 -ErrorAction SilentlyContinue
}

$launchStopwatch.Stop()
$summary | Add-Member -NotePropertyName launcherElapsedMs -NotePropertyValue $launchStopwatch.ElapsedMilliseconds
$summary | Add-Member -NotePropertyName observedProcessIds -NotePropertyValue @($ownedProcessIds)
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding utf8
if ($summary.passed -ne $true) { throw "Desktop smoke returned failure." }
if ($summary.projectScenarioPassed -ne $true) { throw "Desktop smoke project create/update/close/reopen failed." }
if ($summary.runPackageValidationPassed -ne $true) { throw "Desktop smoke R130SH contract validation failed." }
if ($summary.runPackageImportPassed -ne $true) { throw "Desktop smoke R130SH production import/reopen failed." }
if ($summary.rbdCalculationPassed -ne $true) { throw "Desktop smoke RBD calculation/reopen failed." }
if ($summary.rptCalculationPassed -ne $true) { throw "Desktop smoke RPT calculation/reopen failed." }
if ($summary.pmnCalculationPassed -ne $true) { throw "Desktop smoke PMN calculation/reopen failed." }
if ($summary.sourceMaterialsPassed -ne $true -or $summary.sourceMaterials.sqliteUnchanged -ne $true) { throw "Desktop smoke source materials/copy/reopen changed project or failed." }
if ($summary.sourceMaterials.origin.packageId -ne $materialRecord.packageId -or $summary.sourceMaterials.origin.runId -ne $materialRecord.runId -or $summary.sourceMaterials.origin.exportRevision -ne $materialRecord.exportRevision -or $summary.sourceMaterials.origin.outerPackageSha256 -ne $materialRecord.sha256 -or $summary.sourceMaterials.protocolReleaseId -ne $materialRecord.protocolReleaseId -or $summary.sourceMaterials.protocolRevision -ne $materialRecord.protocolRevision) { throw "Desktop smoke material origin or saved protocol revision mismatch." }
$expectedMaterialMembers = @($materialRecord.members | Where-Object { $_.media_type -in @("image/jpeg", "image/png", "application/pdf") })
if (@($summary.sourceMaterials.copiedMaterials).Count -ne $expectedMaterialMembers.Count -or $expectedMaterialMembers.Count -ne 3) { throw "Desktop smoke must copy JPEG, PNG and PDF." }
foreach ($member in $expectedMaterialMembers) {
    $copies = @($summary.sourceMaterials.copiedMaterials | Where-Object { $_.mediaType -eq $member.media_type })
    if ($copies.Count -ne 1 -or $copies[0].sha256 -ne $member.sha256 -or $copies[0].sizeBytes -ne $member.size) { throw "Desktop smoke material bytes/hash do not match producer provenance." }
}
if ($networkObserved) { throw "Desktop smoke observed a TCP connection in its process tree." }

$shutdownDeadline = [DateTime]::UtcNow.AddSeconds(5)
while ([DateTime]::UtcNow -lt $shutdownDeadline -and (Get-Process -Id ([int]$summary.pid) -ErrorAction SilentlyContinue)) {
    $null = Update-OwnedProcessIds -OwnedProcessIds $ownedProcessIds
    Start-Sleep -Milliseconds 250
}
Start-Sleep -Milliseconds 500
$remainingOwnedProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $ownedProcessIds.Contains([int]$_.ProcessId)
})
$orphanWorkers = @($remainingOwnedProcesses | Where-Object { $_.Name -eq "impeller-reliability-worker.exe" })
if ($orphanWorkers.Count -gt 0) {
    Stop-OwnedProcesses -OwnedProcessIds $ownedProcessIds
    throw "Desktop smoke left an orphan worker in its process tree."
}
Write-Output ($summary | ConvertTo-Json -Depth 8)
