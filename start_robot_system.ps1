$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$WebPort = 18923
$PostgresContainer = "robot-pg"

function Write-Step([string]$Message) {
    Write-Host "[robot] $Message" -ForegroundColor Cyan
}

function Test-TcpPort([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1000)) {
            return $false
        }
        $client.EndConnect($async)
        return $client.Connected
    } catch {
        return $false
    } finally {
        if ($null -ne $client) {
            $client.Close()
        }
    }
}

function Test-WebReady([int]$Port) {
    try {
        $request = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/login")
        $request.Timeout = 2000
        $response = $request.GetResponse()
        $response.Close()
        return $true
    } catch {
        return $false
    }
}

function Start-ServiceWindow([string]$Title, [string]$ScriptPath, [string[]]$ScriptArguments = @()) {
    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
        throw "Service script not found: $ScriptPath"
    }

    $allArguments = @(
        "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass",
        "-File", $ScriptPath
    ) + $ScriptArguments

    $powershell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    $child = Start-Process -FilePath $powershell -WorkingDirectory $ProjectRoot -ArgumentList $allArguments -WindowStyle Normal -PassThru
    Start-Sleep -Milliseconds 500
    $child.Refresh()
    if ($child.HasExited) {
        throw "$Title process exited immediately with code $($child.ExitCode). Script: $ScriptPath"
    }
    if ($Title) {
        Write-Host "$Title window opened (PID $($child.Id))." -ForegroundColor Green
    }
}

Set-Location -LiteralPath $ProjectRoot
Write-Host ""
Write-Host "Robot System startup" -ForegroundColor Green
Write-Host "Project: $ProjectRoot"
Write-Host ""

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment not found: $Python"
}

try {
    $docker = Get-Command docker -ErrorAction Stop
} catch {
    throw "Docker is not available. Start Docker Desktop and run this script again."
}

Write-Step "Checking PostgreSQL container"
$containerNames = @(& $docker.Source ps -a --format "{{.Names}}")
if ($containerNames -contains $PostgresContainer) {
    $running = (& $docker.Source inspect -f "{{.State.Running}}" $PostgresContainer 2>$null).Trim()
    if ($running -ne "true") {
        Write-Step "Starting $PostgresContainer"
        & $docker.Source start $PostgresContainer | Out-Host
    } else {
        Write-Step "$PostgresContainer is already running"
    }
} else {
    throw "PostgreSQL container '$PostgresContainer' was not found. Create it first, then run this script again."
}

$dbReady = $false
for ($i = 0; $i -lt 30; $i++) {
    if (Test-TcpPort 15432) {
        $dbReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $dbReady) {
    throw "PostgreSQL did not become ready on port 15432."
}
Write-Step "PostgreSQL is ready"

$publicTunnel = $args -contains "-Public"
if (-not $publicTunnel) {
    $publicAnswer = Read-Host "Start a public Cloudflare demo tunnel? [Y/n]"
    $publicTunnel = $publicAnswer -notmatch "^n$"
}

Write-Host ""
$confirm = Read-Host "Press Enter to start Web, Research Worker, and Cloudflare together (Q to cancel)"
if ($confirm -ieq "q") {
    Write-Host "Startup cancelled."
    exit 0
}

Write-Step "Starting Web service"
$webScript = Join-Path $ProjectRoot "start_web_service.ps1"
Start-ServiceWindow -Title "DeepResearch Web" -ScriptPath $webScript -ScriptArguments @(
    "-HostAddress", "0.0.0.0",
    "-Port", $WebPort
)
Write-Host "Web window opened; it will continue starting independently." -ForegroundColor Green

Write-Step "Starting Research Worker"
$workerScript = Join-Path $ProjectRoot "start_research_worker_service.ps1"
Start-ServiceWindow -Title "DeepResearch Research Worker" -ScriptPath $workerScript
Write-Host "Research Worker window opened." -ForegroundColor Green

if ($publicTunnel) {
    $cloudflared = $null
    try { $cloudflared = (Get-Command cloudflared -ErrorAction Stop).Source } catch {}
    if (-not $cloudflared) {
        foreach ($candidate in @(
            "C:\Program Files (x86)\cloudflared\cloudflared.exe",
            "C:\Program Files\cloudflared\cloudflared.exe"
        )) {
            if (Test-Path -LiteralPath $candidate) {
                $cloudflared = $candidate
                break
            }
        }
    }
    if (-not $cloudflared) {
        Write-Host "cloudflared was not found; Web and Worker were started without a public tunnel." -ForegroundColor Yellow
    } else {
        Write-Step "Starting public quick tunnel"
        $tunnelScript = Join-Path $ProjectRoot "start_cloudflare_tunnel.ps1"
        Start-ServiceWindow -Title "Cloudflare Tunnel" -ScriptPath $tunnelScript -ScriptArguments @(
            "-Port", $WebPort
        )
        Write-Host "Cloudflare Tunnel window opened." -ForegroundColor Green
        Write-Host "The public URL will be printed in the Cloudflare window." -ForegroundColor Green
    }
} else {
    Write-Host "Public tunnel not started. LAN access: http://<this-pc-ip>:$WebPort" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Startup complete. Keep the opened windows running." -ForegroundColor Green
Write-Host "Local URL: http://127.0.0.1:$WebPort"
Start-Process "http://127.0.0.1:$WebPort/login"
