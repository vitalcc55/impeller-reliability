[CmdletBinding()]
param([string]$EvidenceDirectory)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path $repositoryRoot ('.tmp\.codex\evidence\setup-lifecycle-' + [guid]::NewGuid().ToString('N')) }
$evidence = [System.IO.Path]::GetFullPath($EvidenceDirectory)
if (Test-Path -LiteralPath $evidence) { throw 'Installer evidence directory must be new; previous evidence is preserved.' }
$installation = Join-Path $evidence 'Installed application'
$data = Join-Path $evidence 'User data and cases'
$metadataPath = Join-Path $repositoryRoot 'apps\desktop\package.json'
$originalMetadata = [System.IO.File]::ReadAllText($metadataPath)
$metadata = $originalMetadata | ConvertFrom-Json
$version = [version]$metadata.version
$nextVersion = '{0}.{1}.{2}' -f $version.Major, $version.Minor, ($version.Build + 1)
$results = [System.Collections.Generic.List[object]]::new()
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$shortcuts = @((Join-Path $desktop 'Impeller Reliability.lnk'), (Join-Path $programs 'Impeller Reliability.lnk'))
$identityScript = @'
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
const req=createRequire(process.argv[1]);
const builderReq=createRequire(req.resolve('electron-builder'));
const libReq=createRequire(builderReq.resolve('app-builder-lib'));
const {UUID}=libReq('builder-util-runtime');
const config=libReq('js-yaml').load(readFileSync(process.argv[2],'utf8'));
process.stdout.write(config.nsis.guid ?? UUID.v5(config.appId,UUID.parse('50e065bc-3134-11e6-9bab-38c9862bdaf3')));
'@
$registryGuid = & node --input-type=module -e $identityScript $metadataPath (Join-Path $repositoryRoot 'apps\desktop\electron-builder.yml')
if ($LASTEXITCODE -ne 0 -or $registryGuid -notmatch '^[0-9a-f-]{36}$') { throw 'Cannot determine canonical installer identity.' }
$installKey = "Software\$registryGuid"
$uninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\$registryGuid"

function Get-Registration {
    if (Test-Path -LiteralPath "HKCU:\$uninstallKey" -ErrorAction Stop) { return @(Get-ItemProperty -LiteralPath "HKCU:\$uninstallKey" -ErrorAction Stop) }
    return @()
}

function Get-OwnedRegistration {
    $records = @(Get-Registration)
    if ($records.Count -ne 1) { throw 'Expected exactly one installed application registration.' }
    $uninstall = [string]$records[0].UninstallString
    if ($uninstall -notmatch '^"([^"]+)"') { throw 'Unrecognized uninstall registration.' }
    $uninstallerPath = [System.IO.Path]::GetFullPath($Matches[1])
    if ([System.IO.Path]::GetDirectoryName($uninstallerPath) -ne $installation) { throw 'Registration belongs to another installation; refusing to change it.' }
    return $records[0]
}

function Invoke-Setup {
    param([string]$Path, [bool]$MustSucceed = $true)
    Write-Output "Setup: $Path; expected success: $MustSucceed" | Out-Host
    $process = Start-Process -FilePath $Path -ArgumentList @('/S', "/D=$installation") -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(90000)) {
        $process.Kill($true)
        throw 'Installer did not finish within its acceptance deadline; the owned test installer was stopped.'
    }
    if ($MustSucceed -and $process.ExitCode -ne 0) { throw "Setup failed: $($process.ExitCode)" }
    if (-not $MustSucceed -and $process.ExitCode -eq 0) { throw 'Setup unexpectedly reported success.' }
    return $process.ExitCode
}

function Invoke-Readback {
    param([string]$ExpectedVersion)
    & (Join-Path $PSScriptRoot 'desktop.ps1') -Target Installed -ExecutablePath (Join-Path $installation 'ImpellerReliabilityCalc.exe') -Phase Readback -EvidenceDirectory $data -ExpectedVersion $ExpectedVersion
    if ($LASTEXITCODE -ne 0) { throw 'Installed readback failed.' }
}

function Uninstall-OwnedApplication {
    $record = Get-OwnedRegistration
    if ([string]$record.UninstallString -notmatch '^"([^"]+)"') { throw 'Unrecognized uninstall registration.' }
    $process = Start-Process -FilePath $Matches[1] -ArgumentList @('/S', '/KEEP_APP_DATA', "_?=$installation") -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(90000)) {
        $process.Kill($true)
        throw 'Owned uninstall exceeded its acceptance deadline.'
    }
    if ($process.ExitCode -ne 0) { throw 'Owned uninstall failed.' }
    if (@(Get-Registration).Count -ne 0 -or (Test-Path -LiteralPath (Join-Path $installation 'ImpellerReliabilityCalc.exe'))) { throw 'Uninstall left an application or registration.' }
    foreach ($shortcut in $shortcuts) { if (Test-Path -LiteralPath $shortcut) { throw 'Uninstall left a shortcut.' } }
}

foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        $registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
        try {
            foreach ($name in @($installKey, $uninstallKey)) {
                $existing = $registry.OpenSubKey($name)
                if ($null -ne $existing) {
                    $existing.Dispose()
                    throw "Canonical installation registration already exists ($hive/$view/$name). Use an isolated profile; it is not replaced by smoke."
                }
            }
        } finally { $registry.Dispose() }
    }
}
foreach ($shortcut in $shortcuts) { if (Test-Path -LiteralPath $shortcut) { throw "Existing shortcut must not be overwritten: $shortcut" } }
if (@(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction Stop | Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -like 'Impeller Reliability*' }).Count -ne 0) { throw 'A machine installation exists; use an isolated test environment.' }
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$sourceFiles = @(& git -C $repositoryRoot ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Cannot record source identity.' }
$sourceIdentity = @($sourceFiles | Sort-Object -Unique | ForEach-Object {
    $path = Join-Path $repositoryRoot $_
    if (Test-Path -LiteralPath $path -PathType Leaf) { '{0} {1}' -f $_, (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
}) -join "`n"
$sourceHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($sourceIdentity))).ToLowerInvariant()
$previousOutput = Join-Path $evidence 'previous'
$nextOutput = Join-Path $evidence 'next'
$previousPayload = Join-Path $evidence 'previous-payload'
$basePayload = Join-Path $repositoryRoot 'apps\desktop\dist\win-unpacked'
if (-not (Test-Path -LiteralPath (Join-Path $basePayload 'ImpellerReliabilityCalc.exe'))) { throw 'Build fresh WinUnpacked before installer smoke.' }
Copy-Item -LiteralPath $basePayload -Destination $previousPayload -Recurse
Set-Content -LiteralPath (Join-Path $previousPayload 'resources\obsolete-payload.txt') -Value 'Only the previous release contains this file.' -Encoding utf8
$previousSetup = Join-Path $previousOutput "ImpellerReliabilityCalc-$version-Setup-x64.exe"
$nextSetup = Join-Path $nextOutput "ImpellerReliabilityCalc-$nextVersion-Setup-x64.exe"

Push-Location $repositoryRoot
try {
    & pnpm --filter @impeller-reliability/desktop package:setup --prepackaged $previousPayload "--config.directories.output=$previousOutput"
    if ($LASTEXITCODE -ne 0) { throw 'Previous Setup build failed.' }
    try {
        $metadata.version = $nextVersion
        [System.IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json -Depth 10) + "`n", [Text.UTF8Encoding]::new($false))
        & pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Next application build failed.' }
        & pnpm --filter @impeller-reliability/desktop package:setup "--config.directories.output=$nextOutput"
        if ($LASTEXITCODE -ne 0) { throw 'Next Setup build failed.' }
    } finally {
        [System.IO.File]::WriteAllText($metadataPath, $originalMetadata, [Text.UTF8Encoding]::new($false))
        & pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Restoring current application build failed.' }
    }
    $null = Invoke-Setup $previousSetup
    if ((Get-OwnedRegistration).DisplayVersion -ne $version.ToString()) { throw 'Initial version registration mismatch.' }
    $results.Add(@{ scenario = 'initial-install'; passed = $true })
    & (Join-Path $PSScriptRoot 'desktop.ps1') -Target Installed -ExecutablePath (Join-Path $installation 'ImpellerReliabilityCalc.exe') -Phase Seed -EvidenceDirectory $data -ExpectedVersion $version.ToString()
    if ($LASTEXITCODE -ne 0) { throw 'Installed seed failed.' }
    $null = Invoke-Setup $nextSetup
    if ((Get-OwnedRegistration).DisplayVersion -ne $nextVersion) { throw 'Upgrade version registration mismatch.' }
    if (Test-Path -LiteralPath (Join-Path $installation 'resources\obsolete-payload.txt')) { throw 'Upgrade retained an obsolete payload file.' }
    Invoke-Readback $nextVersion
    $results.Add(@{ scenario = 'upgrade-and-readback'; passed = $true })
    $programFile = Join-Path $installation 'resources\licenses\README.md'
    if (-not (Test-Path -LiteralPath $programFile)) { $programFile = Join-Path $installation 'resources\app.asar' }
    $expectedHash = (Get-FileHash -LiteralPath $programFile -Algorithm SHA256).Hash
    Remove-Item -LiteralPath $programFile -Force
    $null = Invoke-Setup $nextSetup
    if ((Get-FileHash -LiteralPath $programFile -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Same-version repair did not restore payload.' }
    Invoke-Readback $nextVersion
    $results.Add(@{ scenario = 'same-version-repair'; passed = $true })
    $before = (Get-FileHash -LiteralPath (Join-Path $installation 'resources\app.asar') -Algorithm SHA256).Hash
    $null = Invoke-Setup $previousSetup $false
    if ((Get-OwnedRegistration).DisplayVersion -ne $nextVersion -or (Get-FileHash -LiteralPath (Join-Path $installation 'resources\app.asar') -Algorithm SHA256).Hash -ne $before) { throw 'Rejected downgrade changed the installation.' }
    Invoke-Readback $nextVersion
    $results.Add(@{ scenario = 'downgrade-rejected'; passed = $true })
    Uninstall-OwnedApplication
    $null = Invoke-Setup $nextSetup
    Invoke-Readback $nextVersion
    $results.Add(@{ scenario = 'uninstall-reinstall'; passed = $true })
    # A locked destination produces a controlled real extraction failure after
    # uninstall. Closing our lock and rerunning the complete Setup must repair it.
    Uninstall-OwnedApplication
    New-Item -ItemType Directory -Path (Join-Path $installation 'resources') -Force | Out-Null
    $lock = [System.IO.File]::Open((Join-Path $installation 'resources\app.asar'), [IO.FileMode]::Create, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try { $null = Invoke-Setup $nextSetup $false } finally { $lock.Dispose() }
    $null = Invoke-Setup $nextSetup
    Invoke-Readback $nextVersion
    $results.Add(@{ scenario = 'extraction-failure-recovery'; passed = $true; failurePoint = 'resources/app.asar locked exclusively after uninstall' })
    Uninstall-OwnedApplication
    $summary = [ordered]@{
        passed = $true
        sourceCommit = (& git rev-parse HEAD)
        sourceSnapshotSha256 = $sourceHash
        os = (Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture)
        previousVersion = $version.ToString()
        nextVersion = $nextVersion
        previousSetupSha256 = (Get-FileHash -LiteralPath $previousSetup -Algorithm SHA256).Hash.ToLowerInvariant()
        nextSetupSha256 = (Get-FileHash -LiteralPath $nextSetup -Algorithm SHA256).Hash.ToLowerInvariant()
        results = $results
        interactiveAcceptance = 'Retry/Cancel, dirty-save, multiple instances and orphan worker require the documented UI pass.'
    }
    $summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding utf8
    $summary | ConvertTo-Json -Depth 10
} catch {
    [ordered]@{ passed = $false; error = $_.Exception.Message; results = $results; evidenceDirectory = $evidence } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding utf8
    throw
} finally {
    Pop-Location
    if (@(Get-Registration).Count -gt 0) {
        try { Uninstall-OwnedApplication } catch { Write-Warning "Owned cleanup failed; inspect $evidence. $($_.Exception.Message)" }
    }
}
