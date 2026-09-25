[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$envFile = Join-Path $repoRoot '.env'

Set-Location -LiteralPath $repoRoot

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Creating Python virtual environment...'
    python -m venv .venv
}

Write-Host 'Installing Python dependencies...'
& $python -m pip install --upgrade pip
& $python -m pip install -e '.[dev]'

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw 'npm was not found. Install Node.js and run .\scripts\dev\setup.ps1 again.'
}

Write-Host 'Installing frontend dependencies...'
Push-Location (Join-Path $repoRoot 'frontend')
try {
    npm.cmd install
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination $envFile
    Write-Host 'Created .env from .env.example. Add any required model credentials before live Agent tests.'
}

Write-Host 'Setup complete. Ensure PostgreSQL is running, then execute .\scripts\dev\start.ps1.'
