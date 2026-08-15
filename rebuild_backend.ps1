
$root = "C:\Users\11482\Documents\aiilie"
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }
if (Test-Path (Join-Path $root "build")) { Remove-Item -Recurse -Force (Join-Path $root "build") -ErrorAction SilentlyContinue }
if (Test-Path (Join-Path $root "dist"))  { Remove-Item -Recurse -Force (Join-Path $root "dist")  -ErrorAction SilentlyContinue }
Set-Location $root
& $venvPy -m PyInstaller --noconfirm build.spec *> (Join-Path $root "build_backend2.log")
$code = $LASTEXITCODE
Write-Output ("exit=" + $code)
