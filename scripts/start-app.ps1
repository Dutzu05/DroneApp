param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
)

$ErrorActionPreference = 'Stop'

if (-not $ComposeArgs -or $ComposeArgs.Count -eq 0) {
    $ComposeArgs = @('up', '--build')
}

& (Join-Path $PSScriptRoot 'dev-env.ps1')
if (($null -ne $LASTEXITCODE) -and ($LASTEXITCODE -ne 0)) {
    exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot 'docker-compose.ps1') @ComposeArgs
if ($null -eq $LASTEXITCODE) {
    exit 0
}

exit $LASTEXITCODE
