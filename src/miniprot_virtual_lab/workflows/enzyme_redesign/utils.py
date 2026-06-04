"""
Shared utilities for the enzyme redesign pipeline.
"""
import os
import subprocess
from pathlib import Path
from typing import List, Optional


# Known OpenBabel data directory (needed for ring fragments, force field params)
_OBABEL_DATADIR = "C:/miniconda3/envs/biolab/share/openbabel"


def find_obabel(binary: str = "obabel") -> str:
    """Find OpenBabel executable, trying PATH then known locations."""
    import shutil

    exe = shutil.which(binary)
    if exe:
        return exe
    candidates = [
        Path("C:/miniconda3/envs/biolab/Library/bin/obabel.exe"),
        Path("C:/miniconda3/envs/miniprot/Library/bin/obabel.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise FileNotFoundError(
        f"OpenBabel binary '{binary}' not found on PATH or in known locations."
    )


def run_obabel(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run OpenBabel with BABEL_DATADIR set correctly."""
    env = os.environ.copy()
    env["BABEL_DATADIR"] = _OBABEL_DATADIR
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def find_vina(binary: str = "vina") -> Optional[str]:
    """Find AutoDock Vina executable."""
    import shutil

    exe = shutil.which(binary)
    if exe:
        return exe
    candidates = [
        Path("C:/miniprot/tools/vina.exe"),
        Path("C:/miniprot/tools/vina_1.2.7_win.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def find_scwrl(binary: str = "Scwrl4") -> Optional[str]:
    """Find SCWRL4 executable."""
    import shutil

    exe = shutil.which(binary)
    if exe:
        return exe
    candidates = [
        Path("C:/miniprot/tools/Scwrl4.exe"),
        Path("C:/miniprot/tools/scwrl4.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None
