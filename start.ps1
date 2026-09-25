[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$frontendRoot = Join-Path $repoRoot 'frontend'
$envFile = Join-Path $repoRoot '.env'
$stateDirectory = Join-Path $repoRoot '.local-data'
$stateFile = Join-Path $stateDirectory 'dev-processes.json'
$logDirectory = Join-Path $repoRoot 'logs\dev'

function Test-PortInUse([int]$Port) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-Http([string]$Url, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) { return $true }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Start-LoggedProcess(
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$WorkingDirectory,
    [string]$Name
) {
    $stdout = Join-Path $logDirectory "$Name.out.log"
    $stderr = Join-Path $logDirectory "$Name.err.log"
    return Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
}

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment is missing. Run .\setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules'))) {
    throw 'Frontend dependencies are missing. Run .\setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw '.env is missing. Run .\setup.ps1 first, then review the generated configuration.'
}
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source

if (Test-Path -LiteralPath $stateFile) {
    $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $live = @(@($saved.api_pid, $saved.worker_pid, $saved.frontend_pid) | Where-Object {
        Get-Process -Id $_ -ErrorAction SilentlyContinue
    })
    if ($live.Count -eq 3 -and (Test-PortInUse $ApiPort) -and (Test-PortInUse $FrontendPort)) {
        Write-Host 'API, Worker and frontend are already running.'
        Write-Host "Frontend: http://127.0.0.1:$FrontendPort"
        Write-Host "API:      http://127.0.0.1:$ApiPort"
        exit 0
    }
    if ($live.Count -gt 0) {
        & (Join-Path $repoRoot 'stop.ps1') -Quiet
    } else {
        Remove-Item -LiteralPath $stateFile -Force
    }
}

if (Test-PortInUse $ApiPort) { throw "API port $ApiPort is already in use." }
$existingFrontend = $null
if (Test-PortInUse $FrontendPort) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $FrontendPort -ErrorAction Stop | Select-Object -First 1
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if (-not $candidate.CommandLine -or $candidate.CommandLine -notlike "*$frontendRoot*" -or $candidate.CommandLine -notmatch 'vite') {
        throw "Frontend port $FrontendPort is already in use by another program."
    }
    $existingFrontend = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    Write-Host "Reusing the existing project frontend on port $FrontendPort."
}

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

Set-Location -LiteralPath $repoRoot
Write-Host 'Applying database migrations...'
& $python -m dotenv -f $envFile run -- $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed. Check PostgreSQL and .env.' }
& $python -m dotenv -f $envFile run -- $python -m supplier_comparison.checkpoints setup
if ($LASTEXITCODE -ne 0) { throw 'Checkpoint initialization failed.' }

$api = $null
$worker = $null
$frontend = $null
try {
    $api = Start-LoggedProcess $python @(
        '-m', 'dotenv', '-f', $envFile, 'run', '--', $python,
        '-m', 'uvicorn', 'supplier_comparison.backend.api:app',
        '--host', '127.0.0.1', '--port', [string]$ApiPort
    ) $repoRoot 'api'
    $worker = Start-LoggedProcess $python @(
        '-m', 'dotenv', '-f', $envFile, 'run', '--', $python,
        '-m', 'supplier_comparison.worker', 'run-loop', '--poll-interval', '1'
    ) $repoRoot 'worker'
    $frontend = if ($existingFrontend) {
        $existingFrontend
    } else {
        Start-LoggedProcess $npm @(
            'run', 'dev', '--', '--host', '127.0.0.1', '--port', [string]$FrontendPort
        ) $frontendRoot 'frontend'
    }

    @{
        repo_root = $repoRoot
        api_pid = $api.Id
        worker_pid = $worker.Id
        frontend_pid = $frontend.Id
        api_port = $ApiPort
        frontend_port = $FrontendPort
        started_at = (Get-Date).ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8

    if (-not (Wait-Http "http://127.0.0.1:$ApiPort/health/live")) {
        throw "API did not become ready. Check logs\dev\api.err.log."
    }
    if (-not (Wait-Http "http://127.0.0.1:$FrontendPort")) {
        throw "Frontend did not become ready. Check logs\dev\frontend.err.log."
    }
    if (-not (Wait-Http "http://127.0.0.1:$ApiPort/health/worker" 15)) {
        throw "Worker heartbeat was not detected. Check logs\dev\worker.err.log."
    }

    Write-Host ''
    Write-Host 'Development services started:'
    Write-Host "  Frontend  http://127.0.0.1:$FrontendPort"
    Write-Host "  API       http://127.0.0.1:$ApiPort"
    Write-Host "  Swagger   http://127.0.0.1:$ApiPort/docs"
    Write-Host '  Worker    heartbeat detected'
    Write-Host 'Stop all services with .\stop.ps1'
} catch {
    Write-Error $_
    & (Join-Path $repoRoot 'stop.ps1') -Quiet
    exit 1
}
