$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$listener = Get-NetTCPConnection -LocalPort 5071 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $web = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($web -and $web.ProcessName -like "python*") {
        Stop-Process -Id $web.Id
    }
}

$pidPath = Join-Path $PSScriptRoot "datapilot_worker.pid"
if (Test-Path -LiteralPath $pidPath) {
    $savedPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($savedPid -match '^\d+$') {
        $worker = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
        if ($worker -and $worker.ProcessName -like "python*") {
            Stop-Process -Id $worker.Id
        }
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    & $docker.Source compose stop redis
}

Write-Host "DataPilot, Celery et Redis sont arrêtés."
