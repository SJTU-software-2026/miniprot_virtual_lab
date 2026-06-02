# =============================================================================
#  P2Rank Download Script — Windows PowerShell
#  =============================================================================
#
#  Downloads and extracts P2Rank 2.5.1 for ligand-binding site prediction.
#
#  Usage:
#    cd tools_src\p2rank
#    powershell -File download_p2rank.ps1
#
#  After download, configure the path in config/tool_paths.yaml:
#    docking:
#      p2rank_home: tools_src/p2rank/p2rank_2.5.1
#
#  Requirements: Java 17+ (https://adoptium.net/download/)
# =============================================================================

$ErrorActionPreference = "Stop"
$P2RankVersion = "2.5.1"
$P2RankUrl = "https://github.com/rdk/p2rank/releases/download/$P2RankVersion/p2rank_$P2RankVersion.tar.gz"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetDir = Join-Path $ScriptDir "p2rank_$P2RankVersion"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  P2Rank $P2RankVersion Download" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path (Join-Path $TargetDir "prank.bat")) {
    Write-Host "P2Rank $P2RankVersion already installed at:" -ForegroundColor Green
    Write-Host "  $TargetDir"
    Write-Host ""
    Write-Host "To re-download, delete this directory first:"
    Write-Host "  Remove-Item -Recurse -Force $TargetDir"
    exit 0
}

$TarFile = Join-Path $ScriptDir "p2rank_$P2RankVersion.tar.gz"
Write-Host "Downloading (~260 MB, may take a while)..." -ForegroundColor Yellow
Write-Host "  $P2RankUrl"
Write-Host ""

Invoke-WebRequest -Uri $P2RankUrl -OutFile $TarFile

Write-Host "Extracting..."
tar -xzf $TarFile -C $ScriptDir
Remove-Item $TarFile

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  P2Rank installed to:" -ForegroundColor Green
Write-Host "    $TargetDir"
Write-Host ""
Write-Host "  Add to config/tool_paths.yaml:"
Write-Host "    docking:"
Write-Host "      p2rank_home: $TargetDir"
Write-Host ""
Write-Host "  Test: $TargetDir\prank.bat -h"
Write-Host "==============================================" -ForegroundColor Cyan
