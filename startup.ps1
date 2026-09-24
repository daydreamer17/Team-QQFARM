[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$EnvFile = Join-Path $ProjectRoot ".env"
$FrontendUrl = "http://127.0.0.1:5173"
$ApiUrl = "http://127.0.0.1:8000"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Import-DotEnv([string]$Path) {
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $separator = $trimmed.IndexOf("=")
        if ($separator -le 0) {
            continue
        }

        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            continue
        }
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Test-TcpPort([string]$HostName, [int]$Port, [int]$TimeoutMilliseconds = 1500) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync($HostName, $Port)
        return $connection.Wait($TimeoutMilliseconds) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-HttpReady([string]$Uri, [int]$TimeoutSeconds = 2) {
    try {
        $response = Invoke-RestMethod -Uri $Uri -TimeoutSec $TimeoutSeconds
        return $response.status -eq "ready"
    }
    catch {
        return $false
    }
}

function Wait-Until([scriptblock]$Condition, [string]$Description, [int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "Timed out waiting for $Description. Check the logs under $LogRoot."
}

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing $EnvFile. Copy .env.example to .env and configure it first."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing Python environment: $Python"
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules"))) {
    throw "Frontend dependencies are missing. Run: cd frontend; npm.cmd ci"
}

Import-DotEnv $EnvFile

# On this Windows setup, localhost can prefer an unusable IPv6 route. Keep the
# persisted .env unchanged and use IPv4 for this process and all child services.
if ($env:DATABASE_URL) {
    $env:DATABASE_URL = $env:DATABASE_URL.Replace("@localhost:", "@127.0.0.1:")
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogRoot = Join-Path $ProjectRoot "logs\startup\$timestamp"
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

Write-Step "Checking PostgreSQL"
$postgresPort = 5432
if ($env:POSTGRES_PORT -and $env:POSTGRES_PORT -match "^\d+$") {
    $postgresPort = [int]$env:POSTGRES_PORT
}
if (-not (Test-TcpPort "127.0.0.1" $postgresPort)) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        throw "PostgreSQL is not reachable on 127.0.0.1:$postgresPort and Docker is not available. Start the database first."
    }
    & $docker.Source compose up -d --wait postgres
    if ($LASTEXITCODE -ne 0) {
        throw "Docker could not start PostgreSQL."
    }
}

Write-Step "Applying database migrations"
& $Python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed."
}
& $Python -m supplier_comparison.checkpoints setup
if ($LASTEXITCODE -ne 0) {
    throw "LangGraph checkpoint setup failed."
}

$startedProcesses = @()

Write-Step "Starting API"
if (Test-HttpReady "$ApiUrl/health/ready") {
    Write-Host "API is already ready."
}
elseif (Test-TcpPort "127.0.0.1" 8000) {
    throw "Port 8000 is occupied by another process, but the API health check failed."
}
else {
    $api = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "supplier_comparison.backend.api:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "api.out.log") `
        -RedirectStandardError (Join-Path $LogRoot "api.err.log") `
        -PassThru
    $startedProcesses += [pscustomobject]@{ service = "api"; pid = $api.Id }
    Wait-Until { Test-HttpReady "$ApiUrl/health/ready" } "API readiness"
}

Write-Step "Starting worker"
if (Test-HttpReady "$ApiUrl/health/worker") {
    Write-Host "Worker is already ready."
}
else {
    $worker = Start-Process -FilePath $Python `
        -ArgumentList @("-m", "supplier_comparison.worker", "run-loop", "--poll-interval", "1") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "worker.out.log") `
        -RedirectStandardError (Join-Path $LogRoot "worker.err.log") `
        -PassThru
    $startedProcesses += [pscustomobject]@{ service = "worker"; pid = $worker.Id }
    Wait-Until { Test-HttpReady "$ApiUrl/health/worker" } "worker heartbeat"
}

Write-Step "Starting frontend"
try {
    $frontendResponse = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2
    $frontendReady = $frontendResponse.StatusCode -eq 200
}
catch {
    $frontendReady = $false
}

if ($frontendReady) {
    Write-Host "Frontend is already ready."
}
elseif (Test-TcpPort "127.0.0.1" 5173) {
    throw "Port 5173 is occupied by another process, but the frontend health check failed."
}
else {
    $npm = Get-Command npm.cmd -ErrorAction Stop
    $frontend = Start-Process -FilePath $npm.Source `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1") `
        -WorkingDirectory $FrontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "frontend.out.log") `
        -RedirectStandardError (Join-Path $LogRoot "frontend.err.log") `
        -PassThru
    $startedProcesses += [pscustomobject]@{ service = "frontend"; pid = $frontend.Id }
    Wait-Until {
        try {
            (Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
        }
        catch {
            $false
        }
    } "frontend readiness"
}

if ($startedProcesses.Count -gt 0) {
    $startedProcesses | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $LogRoot "processes.json") -Encoding UTF8
}

Write-Step "System ready"
Write-Host "Frontend: $FrontendUrl" -ForegroundColor Green
Write-Host "API docs: $ApiUrl/docs" -ForegroundColor Green
Write-Host "Logs:     $LogRoot"

if (-not $NoBrowser) {
    Start-Process $FrontendUrl
}
