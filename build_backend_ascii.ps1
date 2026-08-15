# build_backend_ascii.ps1 - build backend EXE via build.spec (ASCII only)
$ErrorActionPreference = "Continue"
$root = "C:\Users\11482\Documents\aiilie"
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

Write-Output "[build] cleaning build/ and dist/"
if (Test-Path (Join-Path $root "build")) { Remove-Item -Recurse -Force (Join-Path $root "build") -ErrorAction SilentlyContinue }
if (Test-Path (Join-Path $root "dist"))  { Remove-Item -Recurse -Force (Join-Path $root "dist")  -ErrorAction SilentlyContinue }

Write-Output "[build] running PyInstaller build.spec"
Set-Location $root
& $venvPy -m PyInstaller --noconfirm build.spec *> (Join-Path $root "build_backend.log")
$code = $LASTEXITCODE
Write-Output "[build] PyInstaller exit code: $code"

$exe = Join-Path $root "dist\No0_AI_V4.exe"
if (Test-Path $exe) {
    $size = (Get-Item $exe).Length
    $mb = [math]::Round($size/1MB,1)
    Write-Output "[build] EXE generated: $exe ($mb MB)"
} else {
    Write-Output "[build] FAILED: exe not generated."
}
exit $code
