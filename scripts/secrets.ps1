Set-StrictMode -Version Latest

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:SecretDir = Join-Path $script:RepoRoot '.data\secrets'
$script:CesiumIonTokenPath = Join-Path $script:SecretDir 'drone-cesium-ion-token.protected'

function Get-DroneCesiumIonTokenSecretPath {
    return $script:CesiumIonTokenPath
}

function Protect-DroneSecretValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $secureValue = ConvertTo-SecureString -String $Value -AsPlainText -Force
    return ConvertFrom-SecureString -SecureString $secureValue
}

function Unprotect-DroneSecretValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProtectedValue
    )

    if ([string]::IsNullOrWhiteSpace($ProtectedValue)) {
        return ''
    }

    $secureValue = ConvertTo-SecureString -String $ProtectedValue.Trim()
    $credential = New-Object System.Management.Automation.PSCredential('drone-app', $secureValue)
    return $credential.GetNetworkCredential().Password
}

function Save-DroneCesiumIonToken {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw 'Cesium ion token cannot be empty.'
    }

    New-Item -ItemType Directory -Path $script:SecretDir -Force | Out-Null
    $protectedToken = Protect-DroneSecretValue -Value $Token.Trim()
    Set-Content -Path $script:CesiumIonTokenPath -Value $protectedToken -NoNewline
    return $script:CesiumIonTokenPath
}

function Get-DroneCesiumIonToken {
    if (-not (Test-Path $script:CesiumIonTokenPath)) {
        return ''
    }

    try {
        $protectedToken = Get-Content -Path $script:CesiumIonTokenPath -Raw
        return Unprotect-DroneSecretValue -ProtectedValue $protectedToken
    }
    catch {
        throw "Failed to read protected Cesium ion token from $script:CesiumIonTokenPath. The stored token is bound to the Windows user context that created it. Re-run .\scripts\set-cesium-ion-token.ps1 in the same context you use to start the app. $_"
    }
}

function Remove-DroneCesiumIonToken {
    if (Test-Path $script:CesiumIonTokenPath) {
        Remove-Item -Path $script:CesiumIonTokenPath -Force
    }
}
