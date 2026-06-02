# =============================================================================
#  MiniProt Virtual Lab — Tool Setup Script (Windows PowerShell)
#  =============================================================================
#
#  Downloads external tools that cannot be git submodules.
#  Run this once after cloning the repository.
#
#  Usage:
#    powershell -File scripts/setup_tools.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ToolsDir = Join-Path $ProjectRoot "tools_src"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  MiniProt Virtual Lab - Tool Setup" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# ── OmegaFold (submodule) ──────────────────────────────────────────
Write-Host "[1/3] OmegaFold (git submodule)..." -ForegroundColor Yellow
Push-Location $ProjectRoot
if (Test-Path (Join-Path $ToolsDir "omegafold\README.md")) {
    Write-Host "  Already present, skipping."
} else {
    Write-Host "  Run: git submodule update --init tools_src/omegafold"
    git submodule update --init tools_src/omegafold 2>$null
    if (-not (Test-Path (Join-Path $ToolsDir "omegafold\README.md"))) {
        Write-Host "  WARNING: Could not fetch OmegaFold. Run manually: git submodule update --init" -ForegroundColor Red
    }
}
Pop-Location

# ── P2Rank ─────────────────────────────────────────────────────────
$P2RankVersion = "2.5.1"
$P2RankUrl = "https://github.com/rdk/p2rank/releases/download/$P2RankVersion/p2rank_$P2RankVersion.tar.gz"
$P2RankDir = Join-Path $ToolsDir "p2rank\p2rank_$P2RankVersion"

Write-Host "[2/3] P2Rank $P2RankVersion..." -ForegroundColor Yellow
if (Test-Path (Join-Path $P2RankDir "prank.bat")) {
    Write-Host "  Already present, skipping."
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $ToolsDir "p2rank") | Out-Null
    $TarFile = Join-Path $ToolsDir "p2rank\p2rank_$P2RankVersion.tar.gz"
    Write-Host "  Downloading... (~260 MB, may take a while)" -ForegroundColor Gray
    Invoke-WebRequest -Uri $P2RankUrl -OutFile $TarFile
    Write-Host "  Extracting..."
    tar -xzf $TarFile -C (Join-Path $ToolsDir "p2rank")
    Remove-Item $TarFile
    Write-Host "  P2Rank installed to $P2RankDir" -ForegroundColor Green
}

# ── Java 17 ────────────────────────────────────────────────────────
Write-Host "[3/3] Java 17..." -ForegroundColor Yellow
try {
    $javaVer = java -version 2>&1 | Select-Object -First 1
    Write-Host "  Found: $javaVer"
    if ($javaVer -match 'version "(\d+)') {
        $major = [int]$Matches[1]
        if ($major -ge 17) {
            Write-Host "  Java $major - OK." -ForegroundColor Green
        } else {
            Write-Host "  Java $major found but P2Rank needs Java 17+." -ForegroundColor Red
            Write-Host "  Download from: https://adoptium.net/download/" -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "  Java not found. P2Rank needs Java 17+." -ForegroundColor Red
    Write-Host "  Download from: https://adoptium.net/download/" -ForegroundColor Yellow
    Write-Host "  Or: conda install -c conda-forge openjdk=17" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next: configure tool paths (optional)"
Write-Host "    cp config/tool_paths.example.yaml config/tool_paths.yaml"
Write-Host "    # Edit tool_paths.yaml if you installed tools elsewhere"
Write-Host ""
Write-Host "  Run: python run.py --demo"
Write-Host "==============================================" -ForegroundColor Cyan
