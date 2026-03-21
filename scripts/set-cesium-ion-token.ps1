param(
    [string]$Token,
    [switch]$ShowStatus,
    [switch]$Clear
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'secrets.ps1')

if ($Clear) {
    Remove-DroneCesiumIonToken
    Write-Host 'Cleared protected Cesium ion token.'
    exit 0
}

$configuredToken = Get-DroneCesiumIonToken

if ($ShowStatus) {
    if ($configuredToken) {
        Write-Host "Protected Cesium ion token is configured at $(Get-DroneCesiumIonTokenSecretPath)."
    }
    else {
        Write-Host 'Protected Cesium ion token is not configured.'
    }
    exit 0
}

if (-not $PSBoundParameters.ContainsKey('Token') -or [string]::IsNullOrWhiteSpace($Token)) {
    throw 'Usage: .\scripts\set-cesium-ion-token.ps1 -Token <cesium-ion-token>'
}

$secretPath = Save-DroneCesiumIonToken -Token $Token
Write-Host "Stored protected Cesium ion token for the current Windows user at $secretPath."
