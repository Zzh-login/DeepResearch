$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$EntryPoint = Join-Path $ProjectRoot "run_research_worker.py"
$Host.UI.RawUI.WindowTitle = "DeepResearch Research Worker"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Host "Python virtual environment not found: $Python" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Starting DeepResearch Research Worker..." -ForegroundColor Cyan
Write-Host "Research task logs will appear in this window." -ForegroundColor Green
Write-Host ""

try {
    & $Python -u $EntryPoint
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Host "Research Worker exited with code $exitCode." -ForegroundColor Red
    }
} catch {
    Write-Host "Failed to start Research Worker: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}

Read-Host "Press Enter to close"
exit $exitCode
