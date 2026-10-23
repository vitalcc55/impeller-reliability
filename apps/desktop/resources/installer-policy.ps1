[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Version', 'Processes')]
    [string]$Mode,
    [string]$InstalledVersion,
    [string]$IncomingVersion,
    [string]$InstallationDirectory,
    [int]$InstallerProcessId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if ($Mode -eq 'Version') {
        # Only stable application releases are distributed. Do not interpret an
        # unknown registration as a fresh installation or compare versions as text.
        $releasePattern = '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
        if ($InstalledVersion -notmatch $releasePattern -or $IncomingVersion -notmatch $releasePattern) { exit 20 }
        if ([version]$InstalledVersion -gt [version]$IncomingVersion) { exit 30 }
        exit 0
    }

    $root = [System.IO.Path]::GetFullPath($InstallationDirectory).TrimEnd('\') + '\'
    $payloadNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $payloadNames.Add('ImpellerReliabilityCalc.exe') | Out-Null
    $payloadNames.Add('impeller-reliability-worker.exe') | Out-Null
    if (Test-Path -LiteralPath $InstallationDirectory) {
        Get-ChildItem -LiteralPath $InstallationDirectory -Filter '*.exe' -Recurse -File -ErrorAction Stop | ForEach-Object {
            $payloadNames.Add($_.Name) | Out-Null
        }
    }
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    if ($snapshot.ProcessId -notcontains $PID) { exit 20 }
    foreach ($item in $snapshot) {
        if ([int]$item.ProcessId -eq $InstallerProcessId) { continue }
        if ([string]::IsNullOrEmpty($item.ExecutablePath)) {
            # Protected unrelated system processes do not expose paths. A live
            # process named like our payload cannot be declared safe without one.
            if ($payloadNames.Contains([string]$item.Name)) { exit 20 }
            continue
        }
        if ([System.IO.Path]::GetFullPath($item.ExecutablePath).StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            exit 10
        }
    }
    exit 0
} catch {
    # Unknown observer results never authorize removal of the installation.
    exit 20
}
