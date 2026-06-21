[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

function Rename-WithExactCase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExpectedName
    )

    $parent = Split-Path -Parent $ExpectedName
    $leaf = Split-Path -Leaf $ExpectedName
    if ([string]::IsNullOrWhiteSpace($parent)) {
        $parent = "."
    }

    $parentPath = Join-Path $repoRoot $parent
    if (-not (Test-Path $parentPath -PathType Container)) {
        throw "Parent directory not found: $parent"
    }

    $candidate = Get-ChildItem -LiteralPath $parentPath -Force |
        Where-Object { $_.Name -ieq $leaf } |
        Select-Object -First 1

    if ($null -eq $candidate) {
        throw "Cannot find a case-insensitive match for: $ExpectedName"
    }

    if ($candidate.Name -ceq $leaf) {
        Write-Host "[OK] Exact case already correct: $ExpectedName"
        return
    }

    $temporaryLeaf = "__casefix_$([Guid]::NewGuid().ToString('N')).tmp"
    $temporaryRelative = if ($parent -eq ".") {
        $temporaryLeaf
    }
    else {
        (Join-Path $parent $temporaryLeaf)
    }

    $actualRelative = if ($parent -eq ".") {
        $candidate.Name
    }
    else {
        (Join-Path $parent $candidate.Name)
    }

    Write-Host "Renaming '$actualRelative' -> '$ExpectedName'"

    if (Test-Path (Join-Path $repoRoot ".git") -PathType Container) {
        & git mv -- $actualRelative $temporaryRelative
        if ($LASTEXITCODE -ne 0) {
            throw "git mv to temporary path failed: $actualRelative"
        }

        & git mv -- $temporaryRelative $ExpectedName
        if ($LASTEXITCODE -ne 0) {
            throw "git mv to exact-case path failed: $ExpectedName"
        }
    }
    else {
        Rename-Item -LiteralPath $candidate.FullName -NewName $temporaryLeaf
        Rename-Item -LiteralPath (Join-Path $parentPath $temporaryLeaf) -NewName $leaf
    }
}

try {
    Rename-WithExactCase -ExpectedName "nfo_editor_ui.py"
    Rename-WithExactCase -ExpectedName "img"

    Write-Host ""
    Write-Host "Exact-case rename completed."
    Write-Host "Review with: git status --short"
    Write-Host "Then commit and push the QT6 branch."
}
finally {
    Pop-Location
}
