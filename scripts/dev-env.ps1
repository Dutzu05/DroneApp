param(
    [switch]$PersistForUser
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'secrets.ps1')

$repoRoot = Split-Path -Parent $PSScriptRoot
$flutterBin = Join-Path $repoRoot '.tooling\flutter\bin'

$candidatePaths = @(
    $flutterBin,
    'C:\Program Files\Git\cmd',
    'C:\Program Files\nodejs',
    'C:\Program Files\Docker\Docker\resources\bin'
)

$sessionPathEntries = $env:PATH -split ';' | Where-Object { $_ }
$missingEntries = @()

foreach ($candidate in $candidatePaths) {
    if ((Test-Path $candidate) -and ($sessionPathEntries -notcontains $candidate)) {
        $missingEntries += $candidate
    }
}

if ($missingEntries.Count -gt 0) {
    $env:PATH = (($missingEntries + $sessionPathEntries) -join ';')
}

$env:FLUTTER_HOME = Join-Path $repoRoot '.tooling\flutter'
$env:PGHOST = 'localhost'
$env:PGPORT = '5432'
$env:PGUSER = 'drone'
$env:PGPASSWORD = 'drone'
$env:DRONE_DB_NAME = 'drone_app'

$loadedProtectedCesiumIonToken = $false
if ([string]::IsNullOrWhiteSpace($env:DRONE_CESIUM_ION_TOKEN)) {
    $storedCesiumIonToken = Get-DroneCesiumIonToken
    if ($storedCesiumIonToken) {
        $env:DRONE_CESIUM_ION_TOKEN = $storedCesiumIonToken
        $loadedProtectedCesiumIonToken = $true
    }
}

if ($PersistForUser) {
    $userPathEntries = [Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ }
    foreach ($candidate in $candidatePaths) {
        if ((Test-Path $candidate) -and ($userPathEntries -notcontains $candidate)) {
            $userPathEntries += $candidate
        }
    }
    [Environment]::SetEnvironmentVariable('Path', ($userPathEntries -join ';'), 'User')
}

Write-Host 'Loaded Windows development environment:'
Write-Host "FLUTTER_HOME=$env:FLUTTER_HOME"
Write-Host "PGHOST=$env:PGHOST"
Write-Host "PGPORT=$env:PGPORT"
Write-Host "PGUSER=$env:PGUSER"
Write-Host "DRONE_DB_NAME=$env:DRONE_DB_NAME"
Write-Host ("DRONE_CESIUM_ION_TOKEN=" + $(if ([string]::IsNullOrWhiteSpace($env:DRONE_CESIUM_ION_TOKEN)) { 'missing' } else { 'configured' }))
if ($loadedProtectedCesiumIonToken) {
    Write-Host "Loaded protected Cesium ion token from $(Get-DroneCesiumIonTokenSecretPath)"
}
Write-Host ''
Write-Host 'Resolved tools in this session:'
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
Write-Host ("git: " + $(if ($gitCommand) { $gitCommand.Source } else { 'missing' }))
Write-Host ("node: " + $(if ($nodeCommand) { $nodeCommand.Source } else { 'missing' }))
Write-Host ("docker: " + $(if ($dockerCommand) { $dockerCommand.Source } else { 'missing' }))
