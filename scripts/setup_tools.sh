#!/usr/bin/env bash
# =============================================================================
#  MiniProt Virtual Lab — Tool Setup Script (Linux / macOS)
#  =============================================================================
#
#  Downloads external tools that cannot be git submodules.
#  Run this once after cloning the repository.
#
#  Usage:
#    bash scripts/setup_tools.sh
#
#  What this does:
#    - Downloads P2Rank 2.5.1 (~260 MB)
#    - Extracts to tools_src/p2rank/
#    - Skips Java (use your system's Java 17+ or conda)
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TOOLS_DIR="$PROJECT_ROOT/tools_src"

echo "=============================================="
echo "  MiniProt Virtual Lab — Tool Setup"
echo "=============================================="
echo ""

# ── OmegaFold (submodule) ──────────────────────────────────────────
echo "[1/3] OmegaFold (git submodule)..."
cd "$PROJECT_ROOT"
if [ -f "$TOOLS_DIR/omegafold/README.md" ]; then
    echo "  Already present, skipping."
else
    echo "  Run: git submodule update --init tools_src/omegafold"
    git submodule update --init tools_src/omegafold 2>/dev/null || \
        echo "  WARNING: Could not fetch OmegaFold (network issue?). Run manually later."
fi

# ── P2Rank ─────────────────────────────────────────────────────────
P2RANK_VERSION="2.5.1"
P2RANK_URL="https://github.com/rdk/p2rank/releases/download/${P2RANK_VERSION}/p2rank_${P2RANK_VERSION}.tar.gz"
P2RANK_DIR="$TOOLS_DIR/p2rank/p2rank_${P2RANK_VERSION}"

echo "[2/3] P2Rank ${P2RANK_VERSION}..."
if [ -f "$P2RANK_DIR/prank" ]; then
    echo "  Already present, skipping."
else
    mkdir -p "$TOOLS_DIR/p2rank"
    echo "  Downloading from $P2RANK_URL ..."
    if command -v wget &>/dev/null; then
        wget -q --show-progress -O "$TOOLS_DIR/p2rank/p2rank_${P2RANK_VERSION}.tar.gz" "$P2RANK_URL"
    elif command -v curl &>/dev/null; then
        curl -L -o "$TOOLS_DIR/p2rank/p2rank_${P2RANK_VERSION}.tar.gz" "$P2RANK_URL"
    else
        echo "  ERROR: wget or curl required. Please download manually:"
        echo "    $P2RANK_URL"
        echo "  Extract to: $P2RANK_DIR"
        exit 1
    fi
    echo "  Extracting..."
    cd "$TOOLS_DIR/p2rank"
    tar -xzf "p2rank_${P2RANK_VERSION}.tar.gz"
    rm "p2rank_${P2RANK_VERSION}.tar.gz"
    echo "  P2Rank installed to $P2RANK_DIR"
fi

# ── Java 17 ────────────────────────────────────────────────────────
echo "[3/3] Java 17..."
if command -v java &>/dev/null; then
    JAVA_VER=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d'.' -f1)
    if [ "$JAVA_VER" -ge 17 ] 2>/dev/null; then
        echo "  Java $JAVA_VER found on PATH — OK."
    else
        echo "  Java $JAVA_VER found but P2Rank needs Java 17+."
        echo "  Install: conda install -c conda-forge openjdk=17"
        echo "  Or download from: https://adoptium.net/download/"
    fi
else
    echo "  Java not found. P2Rank needs Java 17+."
    echo "  Install: conda install -c conda-forge openjdk=17"
    echo "  Or download from: https://adoptium.net/download/"
fi

echo ""
echo "=============================================="
echo "  Setup complete!"
echo ""
echo "  Next: configure tool paths (optional)"
echo "    cp config/tool_paths.example.yaml config/tool_paths.yaml"
echo "    # Edit tool_paths.yaml if you installed tools elsewhere"
echo ""
echo "  Run: python run.py --demo"
echo "=============================================="
