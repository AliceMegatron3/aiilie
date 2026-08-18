param(
    [ValidateSet('browser', 'electron')]
    [string]$Ui = 'browser',
    [switch]$NoReload
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Frontend = Join-Path $Root 'frontend'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source

if (-not (Test-Path (Join-Path $Root 'main.py'))) { throw "Missing main.py: $Root" }
if (-not (Test-Path (Join-Path $Frontend 'package.json'))) { throw "Missing frontend/package.json" }
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) { throw 'Frontend dependencies are missing. Run npm install in frontend.' }

function Wait-Http([string]$Url, [int]$TimeoutSeconds = 45) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

$ReloadArgs = @()
if (-not $NoReload) { $ReloadArgs = @('--reload') }
$BackendArgs = @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000') + $ReloadArgs
$Backend = Start-Process -FilePath $Python -ArgumentList $BackendArgs -WorkingDirectory $Root -PassThru

if (-not (Wait-Http 'http://127.0.0.1:8000/health')) {
    if (-not $Backend.HasExited) { Stop-Process -Id $Backend.Id -Force }
    throw 'Backend did not pass health check within timeout.'
}
Write-Host "Backend ready: http://127.0.0.1:8000/health (PID $($Backend.Id))"

$FrontendArgs = @('run', 'dev', '--', '--host', '127.0.0.1')
$FrontendProcess = Start-Process -FilePath $Npm -ArgumentList $FrontendArgs -WorkingDirectory $Frontend -PassThru
if (-not (Wait-Http 'http://127.0.0.1:5173/')) {
    if (-not $FrontendProcess.HasExited) { Stop-Process -Id $FrontendProcess.Id -Force }
    if (-not $Backend.HasExited) { Stop-Process -Id $Backend.Id -Force }
    throw 'Vite did not become ready within timeout.'
}
Write-Host "Frontend ready: http://127.0.0.1:5173/ (PID $($FrontendProcess.Id))"

if ($Ui -eq 'browser') {
    Start-Process 'http://127.0.0.1:5173/'
    Write-Host 'Browser development mode started.'
} else {
    $Electron = Join-Path $Frontend 'node_modules\electron\cli.js'
    if (-not (Test-Path $Electron)) { throw 'Electron is missing. Run npm install in frontend.' }
    $env:NO0_ELECTRON_DEV_URL = 'http://127.0.0.1:5173/'
    $env:NO0_EXTERNAL_BACKEND = '1'
    Start-Process -FilePath $Npm -ArgumentList @('exec', '--', 'electron', '.') -WorkingDirectory $Frontend
    Write-Host 'Electron development window started.'
}
