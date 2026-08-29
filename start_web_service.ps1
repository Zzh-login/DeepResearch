param(
    [int]$Port = 18923,
    [string]$HostAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$EntryPoint = Join-Path $ProjectRoot "run_web.py"
$host.UI.RawUI.WindowTitle = "DeepResearch Web"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Host "Python virtual environment not found: $Python" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

# Reuse is not enough for log visibility. Restart only a matching project Python process.
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if (-not $process) {
        continue
    }

    $processPath = $null
    try { $processPath = $process.Path } catch {}
    if (-not $processPath -or ([IO.Path]::GetFullPath($processPath) -ine [IO.Path]::GetFullPath($Python))) {
        Write-Host "Port $Port is occupied by another process; Web was not started." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }

    Write-Host "Stopping the existing DeepResearch Web process ($($process.Id))..." -ForegroundColor Yellow
    Stop-Process -Id $process.Id -Force
}

Start-Sleep -Milliseconds 500
$env:SERVER_HOST = $HostAddress
$env:SERVER_PORT = [string]$Port
Write-Host "Starting DeepResearch Web on http://$HostAddress`:$Port" -ForegroundColor Cyan
Write-Host "Web, WebSocket, ASR, and RAG logs will appear in this window." -ForegroundColor Green
Write-Host ""

& $Python -u $EntryPoint
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host "DeepResearch Web exited with code $exitCode." -ForegroundColor Red
    Read-Host "Press Enter to close"
}
exit $exitCode
