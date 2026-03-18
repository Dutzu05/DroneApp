$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pythonBin = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonBin)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonBin = $pythonCommand.Source
    }
}

if (-not $pythonBin) {
    throw 'Python was not found. Create the .venv or install Python 3.'
}

& $pythonBin -m coverage --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "coverage is not installed for $pythonBin. Install dev dependencies with: $pythonBin -m pip install -r requirements-dev.txt"
}

Write-Host '[unit] Running backend unit tests with coverage gate...'
& $pythonBin -m coverage erase
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonBin -m coverage run -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonBin -m coverage report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonBin -m coverage xml -o coverage.xml
exit $LASTEXITCODE
