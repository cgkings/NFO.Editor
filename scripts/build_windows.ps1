[CmdletBinding()]
param(
    [string]$OutputRoot = "dist/NFOTools"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    $outputPath = Join-Path $repoRoot $OutputRoot
    $mainDist = Join-Path $repoRoot "dist/NFOEditor"
    $standaloneDist = Join-Path $repoRoot "dist/standalone"
    $standaloneWork = Join-Path $repoRoot "build/standalone"
    $generatedSpecs = Join-Path $repoRoot "build/generated-specs"

    Remove-Item (Join-Path $repoRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $repoRoot "dist") -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
    New-Item -ItemType Directory -Path $standaloneDist -Force | Out-Null
    New-Item -ItemType Directory -Path $standaloneWork -Force | Out-Null
    New-Item -ItemType Directory -Path $generatedSpecs -Force | Out-Null

    Write-Host "Building integrated NFOEditor application..."
    & python -m PyInstaller --noconfirm --clean "nfo_editor.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed while building NFOEditor."
    }
    if (-not (Test-Path (Join-Path $mainDist "NFOEditor.exe"))) {
        throw "NFOEditor.exe was not produced."
    }
    Copy-Item (Join-Path $mainDist "*") $outputPath -Recurse -Force

    function Invoke-StandaloneBuild {
        param(
            [Parameter(Mandatory = $true)][string]$Name,
            [Parameter(Mandatory = $true)][string]$Script,
            [Parameter(Mandatory = $true)][string]$Icon,
            [string[]]$DataFiles = @()
        )

        Write-Host "Building standalone tool: $Name"
        $pyInstallerArgs = @(
            "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name", $Name,
            "--distpath", $standaloneDist,
            "--workpath", (Join-Path $standaloneWork $Name),
            "--specpath", $generatedSpecs,
            "--exclude-module", "PyQt5",
            "--exclude-module", "PyQt6",
            "--exclude-module", "PySide2",
            "--exclude-module", "numpy",
            "--exclude-module", "scipy",
            "--exclude-module", "matplotlib",
            "--exclude-module", "IPython",
            "--exclude-module", "pytest",
            "--exclude-module", "tkinter",
            "--hidden-import", "PIL._imaging",
            "--hidden-import", "winshell"
        )

        if ($Icon -and (Test-Path (Join-Path $repoRoot $Icon))) {
            $pyInstallerArgs += @("--icon", (Join-Path $repoRoot $Icon))
        }

        foreach ($entry in $DataFiles) {
            $parts = $entry.Split("|", 2)
            if ($parts.Count -ne 2) {
                throw "Invalid data declaration: $entry"
            }
            $source = Join-Path $repoRoot $parts[0]
            if (Test-Path $source) {
                $pyInstallerArgs += @("--add-data", "$source;$($parts[1])")
            }
        }

        $pyInstallerArgs += (Join-Path $repoRoot $Script)
        & python @pyInstallerArgs
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller failed while building $Name."
        }

        $builtFolder = Join-Path $standaloneDist $Name
        $builtExe = Join-Path $builtFolder "$Name.exe"
        if (-not (Test-Path $builtExe)) {
            throw "$Name.exe was not produced."
        }

        $toolDestination = Join-Path $outputPath "tools/$Name"
        New-Item -ItemType Directory -Path $toolDestination -Force | Out-Null
        Copy-Item (Join-Path $builtFolder "*") $toolDestination -Recurse -Force
    }

    Invoke-StandaloneBuild -Name "cg_crop" -Script "cg_crop.py" -Icon "cg_crop.ico" -DataFiles @(
        "cg_crop.ico|.",
        "img|img"
    )
    Invoke-StandaloneBuild -Name "cg_rename" -Script "cg_rename.py" -Icon "chuizi.ico" -DataFiles @(
        "chuizi.ico|.",
        "mapping_actor.xml|.",
        "series_mapping.xml|."
    )
    Invoke-StandaloneBuild -Name "cg_dedupe" -Script "cg_dedupe.py" -Icon "cg_dedupe.ico" -DataFiles @(
        "cg_dedupe.ico|."
    )
    Invoke-StandaloneBuild -Name "cg_photo_wall" -Script "cg_photo_wall.py" -Icon "cg_photo_wall.ico" -DataFiles @(
        "cg_photo_wall.ico|."
    )

    $documentationFiles = Get-ChildItem $repoRoot -File | Where-Object {
        $_.Name -like "README*.md" -or
        $_.Name -like "CHANGELOG*.md" -or
        $_.Name -in @("ROUND2_DEBUG_REPORT.md", "FIX_REPORT_QT6.md", "BUILD_GITHUB_ACTIONS.md")
    }
    foreach ($doc in $documentationFiles) {
        Copy-Item $doc.FullName $outputPath -Force
    }

    Write-Host "Build completed: $outputPath"
}
finally {
    Pop-Location
}
