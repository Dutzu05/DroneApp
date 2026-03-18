param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
)

$ErrorActionPreference = 'Stop'

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

& $dockerCmd.FullName compose @ComposeArgs
exit $LASTEXITCODE
