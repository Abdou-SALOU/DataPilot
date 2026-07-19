$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:DATAPILOT_PORT = "5071"

# Réutiliser uniquement GROQ_API_KEY depuis CuraMedical, sans dupliquer le secret.
$curaEnv = Get-ChildItem -LiteralPath "C:\2CI-ISI\S2" -Filter ".env" -File -Recurse |
    Where-Object { $_.Directory.Name -eq "CuraMedical" } |
    Select-Object -First 1

if ($curaEnv) {
    foreach ($line in Get-Content -LiteralPath $curaEnv.FullName) {
        if ($line -match '^\s*GROQ_API_KEY\s*=\s*(.+?)\s*$') {
            $key = $matches[1].Trim().Trim('"').Trim("'")
            if ($key) {
                $env:GROQ_API_KEY = $key
            }
            break
        }
    }
}

try {
    Invoke-WebRequest -Uri "http://127.0.0.1:5071/" -TimeoutSec 1 -UseBasicParsing | Out-Null
    Start-Process "http://127.0.0.1:5071/"
    exit 0
} catch {
    # L'application n'est pas encore lancée.
}

$python = (Get-Command python -ErrorAction Stop).Source
$stdout = Join-Path $PSScriptRoot "datapilot_stdout.log"
$stderr = Join-Path $PSScriptRoot "datapilot_stderr.log"
$server = Start-Process `
    -FilePath $python `
    -ArgumentList "app.py" `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 80; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:5071/" -TimeoutSec 1 -UseBasicParsing | Out-Null
        $ready = $true
        break
    } catch {
        if ($server.HasExited) {
            break
        }
    }
}

if (-not $ready) {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
    throw "DataPilot n'a pas pu démarrer. Consultez datapilot_stderr.log."
}

Start-Process "http://127.0.0.1:5071/"
