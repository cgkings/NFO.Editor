$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "Validating repository source layout..."
python scripts/check_source_layout.py
if ($LASTEXITCODE -ne 0) {
    throw "Required source files are missing. See the list above."
}

Write-Host "Creating virtual environment..."
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

Write-Host "Activating virtual environment..."
. .\.venv\Scripts\Activate.ps1

Write-Host "Installing runtime and build dependencies..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-build.txt
python -m pip check

Write-Host "Running source startup smoke test..."
$env:QT_QPA_PLATFORM = "offscreen"
python scripts/qt_source_smoke.py

Write-Host "Building all PySide6 applications..."
.\scripts\build_windows.ps1 -OutputRoot "dist/NFOTools"

Write-Host "Checking build contents and executable startup..."
.\scripts\check_windows_artifact.ps1 -Root "dist/NFOTools" -RunSmokeTests

Write-Host ""
Write-Host "Done. Verified output folder:"
Write-Host "  dist\NFOTools"
