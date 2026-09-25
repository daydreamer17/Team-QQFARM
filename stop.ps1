[CmdletBinding()]
param([switch]$Quiet)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = $PSScriptRoot
$stateFile = Join-Path $repoRoot '.local-data\dev-processes.json'

function Get-ProcessTree([int[]]$RootIds) {
    $all = @(Get-CimInstance Win32_Process)
    $selected = [System.Collections.Generic.List[int]]::new()
    $pending = [System.Collections.Generic.Queue[int]]::new()
    foreach ($rootId in $RootIds) { $pending.Enqueue($rootId) }
    while ($pending.Count -gt 0) {
        $currentId = $pending.Dequeue()
        if ($selected.Contains($currentId)) { continue }
        $selected.Add($currentId)
        foreach ($child in $all | Where-Object ParentProcessId -eq $currentId) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    return @($selected)
}

if (-not (Test-Path -LiteralPath $stateFile)) {
    if (-not $Quiet) { Write-Host 'No saved API, Worker or frontend processes were found.' }
    exit 0
}

$saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$rootIds = @($saved.api_pid, $saved.worker_pid, $saved.frontend_pid) | ForEach-Object { [int]$_ }
$tree = Get-ProcessTree $rootIds
[array]::Reverse($tree)
foreach ($processId in $tree) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $stateFile -Force

if (-not $Quiet) {
    Write-Host 'API, Worker and frontend have been stopped.'
}
