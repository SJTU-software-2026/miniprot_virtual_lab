#!/usr/bin/env bash
# =============================================================================
#  P2Rank Download Script — Linux / macOS / WSL
#  =============================================================================
#
#  Downloads and extracts P2Rank 2.5.1 for ligand-binding site prediction.
#  P2Rank is a prerequisite for the pocket_picker tool.
#
#  Usage:
#    cd tools_src/p2rank
#    bash download_p2rank.sh
#
#  After download, configure the path in config/tool_paths.yaml:
#    docking:
#      p2rank_home: tools_src/p2rank/p2rank_2.5.1
#
#  Requirements: Java 17+ (conda install -c conda-forge openjdk=17)
# =============================================================================
set -euo pipefail

P2RANK_VERSION="2.5.1"
P2RANK_URL="https://github.com/rdk/p2rank/releases/download/${P2RANK_VERSION}/p2rank_${P2RANK_VERSION}.tar.gz"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${SCRIPT_DIR}/p2rank_${P2RANK_VERSION}"

echo "=============================================="
echo "  P2Rank ${P2RANK_VERSION} Download"
echo "=============================================="
echo ""

if [ -f "${TARGET_DIR}/prank" ]; then
    echo "P2Rank ${P2RANK_VERSION} already installed at:"
    echo "  ${TARGET_DIR}"
    echo ""
    echo "To re-download, delete this directory first:"
    echo "  rm -rf ${TARGET_DIR}"
    exit 0
fi

echo "Downloading from:"
echo "  ${P2RANK_URL}"
echo ""

if command -v wget &>/dev/null; then
    wget -q --show-progress -O "${SCRIPT_DIR}/p2rank_${P2RANK_VERSION}.tar.gz" "${P2RANK_URL}"
elif command -v curl &>/dev/null; then
    curl -L -o "${SCRIPT_DIR}/p2rank_${P2RANK_VERSION}.tar.gz" "${P2RANK_URL}"
else
    echo "ERROR: wget or curl required."
    echo "  Download manually: ${P2RANK_URL}"
    echo "  Extract to: ${TARGET_DIR}"
    exit 1
fi

echo ""
echo "Extracting..."
tar -xzf "${SCRIPT_DIR}/p2rank_${P2RANK_VERSION}.tar.gz" -C "${SCRIPT_DIR}"
rm "${SCRIPT_DIR}/p2rank_${P2RANK_VERSION}.tar.gz"

echo ""
echo "=============================================="
echo "  P2Rank installed to:"
echo "    ${TARGET_DIR}"
echo ""
echo "  Add to config/tool_paths.yaml:"
echo "    docking:"
echo "      p2rank_home: $(realpath "${TARGET_DIR}" 2>/dev/null || echo "${TARGET_DIR}")"
echo ""
echo "  Test: ${TARGET_DIR}/prank -h"
echo "=============================================="
