[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [switch]$RunSmokeTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$rootPath = (Resolve-Path $Root).Path

$bundles = @(
    @{ Name = "NFOEditor"; Folder = $rootPath; Exe = "NFOEditor.exe" },
    @{ Name = "cg_crop"; Folder = (Join-Path $rootPath "tools/cg_crop"); Exe = "cg_crop.exe" },
    @{ Name = "cg_rename"; Folder = (Join-Path $rootPath "tools/cg_rename"); Exe = "cg_rename.exe" },
    @{ Name = "cg_dedupe"; Folder = (Join-Path $rootPath "tools/cg_dedupe"); Exe = "cg_dedupe.exe" },
    @{ Name = "cg_photo_wall"; Folder = (Join-Path $rootPath "tools/cg_photo_wall"); Exe = "cg_photo_wall.exe" }
)

foreach ($bundle in $bundles) {
    $folder = $bundle.Folder
    $exePath = Join-Path $folder $bundle.Exe

    if (-not (Test-Path $folder -PathType Container)) {
        throw "Missing application folder: $folder"
    }
    if (-not (Test-Path $exePath -PathType Leaf)) {
        throw "Missing executable: $exePath"
    }
    if ((Get-Item $exePath).Length -lt 100KB) {
        throw "Executable is unexpectedly small: $exePath"
    }

    $platformPlugin = Get-ChildItem $folder -Recurse -File -Filter "qwindows.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $platformPlugin) {
        throw "Qt Windows platform plugin is missing from $($bundle.Name)."
    }

    $headlessPlugin = Get-ChildItem $folder -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("qoffscreen.dll", "qminimal.dll") } |
        Select-Object -First 1
    if (-not $headlessPlugin) {
        throw "Qt offscreen/minimal platform plugin is missing from $($bundle.Name)."
    }

    Write-Host "[OK] $($bundle.Name): executable and Qt platform plugins found."
}

$requiredResources = @("mapping_actor.xml", "series_mapping.xml", "chuizi.ico")
foreach ($resource in $requiredResources) {
    $found = Get-ChildItem $rootPath -Recurse -File -Filter $resource -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) {
        throw "Required resource is missing: $resource"
    }
}

$forbiddenPatterns = @("PyQt5", "PyQt6", "PySide2")
foreach ($pattern in $forbiddenPatterns) {
    $found = Get-ChildItem $rootPath -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*$pattern*" } |
        Select-Object -First 1
    if ($found) {
        throw "Forbidden Qt binding content found: $($found.FullName)"
    }
}

$pysideContent = Get-ChildItem $rootPath -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*PySide6*" } |
    Select-Object -First 1
if (-not $pysideContent) {
    throw "No PySide6 runtime content was found in the artifact."
}

if ($RunSmokeTests) {
    $previousPlatform = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"

    try {
        foreach ($bundle in $bundles) {
            $exePath = Join-Path $bundle.Folder $bundle.Exe
            Write-Host "Starting frozen smoke test: $($bundle.Name)"

            $process = Start-Process -FilePath $exePath -WorkingDirectory $bundle.Folder -PassThru
            Start-Sleep -Seconds 6
            $process.Refresh()

            if ($process.HasExited) {
                throw "$($bundle.Name) exited during startup smoke test with code $($process.ExitCode)."
            }

            Stop-Process -Id $process.Id -Force
            $process.WaitForExit()
            Write-Host "[OK] $($bundle.Name) remained alive after startup."
        }
    }
    finally {
        $env:QT_QPA_PLATFORM = $previousPlatform
    }
}

$allFiles = Get-ChildItem $rootPath -Recurse -File
$totalBytes = ($allFiles | Measure-Object Length -Sum).Sum
if ($totalBytes -lt 5MB) {
    throw "Artifact directory is unexpectedly small."
}

Write-Host "Artifact validation passed. Files: $($allFiles.Count); size: $([Math]::Round($totalBytes / 1MB, 2)) MB"
