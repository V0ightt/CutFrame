$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Error "CutFrame's .venv is missing. Follow the Windows setup steps in README.md first."
    exit 1
}

& $pythonPath (Join-Path $projectRoot "app.py")
exit $LASTEXITCODE
