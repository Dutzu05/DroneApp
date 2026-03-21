param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'secrets.ps1')

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    $dockerPath = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    if (-not (Test-Path $dockerPath)) {
        throw 'Docker CLI not found. Install Docker Desktop or add docker.exe to PATH.'
    }
    $dockerCmd = Get-Item $dockerPath
}

if (-not $ComposeArgs -or $ComposeArgs.Count -eq 0) {
    throw 'Usage: .\scripts\docker-compose.ps1 <compose args>. Example: .\scripts\docker-compose.ps1 up --build'
}

if ([string]::IsNullOrWhiteSpace($env:DRONE_CESIUM_ION_TOKEN)) {
    $storedCesiumIonToken = Get-DroneCesiumIonToken
    if ($storedCesiumIonToken) {
        $env:DRONE_CESIUM_ION_TOKEN = $storedCesiumIonToken
        Write-Host "Loaded protected Cesium ion token from $(Get-DroneCesiumIonTokenSecretPath)"
    }
}

$dockerExecutable = if ($dockerCmd.PSObject.Properties['Source']) {
    $dockerCmd.Source
}
elseif ($dockerCmd.PSObject.Properties['Path']) {
    $dockerCmd.Path
}
elseif ($dockerCmd.PSObject.Properties['FullName']) {
    $dockerCmd.FullName
}
else {
    throw 'Unable to resolve docker executable path.'
}

& $dockerExecutable compose @ComposeArgs
exit $LASTEXITCODE
