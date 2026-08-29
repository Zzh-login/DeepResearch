param(
    [string]$CloudflaredPath,
    [int]$Port = 18923
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($CloudflaredPath)) {
    try {
        $CloudflaredPath = (Get-Command cloudflared -ErrorAction Stop).Source
    } catch {
        foreach ($candidate in @(
            "C:\Program Files (x86)\cloudflared\cloudflared.exe",
            "C:\Program Files\cloudflared\cloudflared.exe"
        )) {
            if (Test-Path -LiteralPath $candidate) {
                $CloudflaredPath = $candidate
                break
            }
        }
    }
}

if (-not $CloudflaredPath -or -not (Test-Path -LiteralPath $CloudflaredPath -PathType Leaf)) {
    Write-Host "cloudflared.exe was not found." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Starting Cloudflare Quick Tunnel..." -ForegroundColor Cyan
Write-Host "Origin: http://127.0.0.1:$Port" -ForegroundColor DarkGray
Write-Host "The public URL will appear below." -ForegroundColor Green
Write-Host ""

try {
    & $CloudflaredPath tunnel --protocol http2 --url "http://127.0.0.1:$Port"
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Host "cloudflared exited with code $exitCode." -ForegroundColor Red
    }
} catch {
    Write-Host "Failed to start cloudflared: $($_.Exception.Message)" -ForegroundColor Red
}

Read-Host "Press Enter to close"
