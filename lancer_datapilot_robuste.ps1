$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$docker = (Get-Command docker -ErrorAction Stop).Source
$python = (Get-Command python -ErrorAction Stop).Source

& $docker compose up -d redis
if ($LASTEXITCODE -ne 0) {
    throw "Redis n'a pas pu démarrer. Vérifiez que Docker Desktop est lancé."
}

$redisReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 500
    $ping = & $docker compose exec -T redis redis-cli ping 2>$null
    if ($LASTEXITCODE -eq 0 -and $ping -match "PONG") {
        $redisReady = $true
        break
    }
}
if (-not $redisReady) {
    throw "Redis ne répond pas. Consultez « docker compose logs redis »."
}

$pidPath = Join-Path $PSScriptRoot "datapilot_worker.pid"
$workerRunning = $false
if (Test-Path -LiteralPath $pidPath) {
    $savedPid = Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($savedPid -match '^\d+$') {
        $workerRunning = [bool](Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue)
    }
}

if (-not $workerRunning) {
    $worker = Start-Process `
        -FilePath $python `
        -ArgumentList "-m", "celery", "-A", "task_queue.celery_app", "worker", "--loglevel=INFO", "--pool=solo", "--hostname=datapilot@$env:COMPUTERNAME" `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $PSScriptRoot "datapilot_worker_stdout.log") `
        -RedirectStandardError (Join-Path $PSScriptRoot "datapilot_worker_stderr.log") `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $worker.Id -Encoding ascii
}

& (Join-Path $PSScriptRoot "lancer_datapilot.ps1")
