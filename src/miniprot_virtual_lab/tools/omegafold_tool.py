"""
OmegaFold tool: predict protein 3D structure (PDB) from sequence via OmegaFold CLI.

https://github.com/HeliXonProtein/OmegaFold

Usage: omegafold INPUT_FILE.fasta OUTPUT_DIRECTORY
Install: pip install git+https://github.com/HeliXonProtein/OmegaFold.git
Or: clone repo and python main.py INPUT.fasta OUTPUT_DIR
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/omegafold"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, safe_run_id, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, safe_run_id, ensure_file_permissions


def _find_omegafold() -> Optional[str]:
    """Return omegafold CLI path if available."""
    return shutil.which("omegafold")


def _run_omegafold(fasta_path: str, output_dir: str, timeout: int = 3600) -> Dict[str, Any]:
    """
    Run OmegaFold on a FASTA file. Returns dict with success, paths to PDBs, or error.
    """
    exe = _find_omegafold()
    if not exe:
        return {
            "success": False,
            "error": "OmegaFold not found. Install: pip install git+https://github.com/HeliXonProtein/OmegaFold.git then ensure 'omegafold' is on PATH.",
            "data": {},
        }
    if not os.path.isfile(fasta_path):
        return {"success": False, "error": f"FASTA file not found: {fasta_path}", "data": {}}
    safe_dir(output_dir)
    try:
        r = subprocess.run(
            [exe, fasta_path, output_dir],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(fasta_path) or ".",
        )
        if r.returncode != 0:
            return {
                "success": False,
                "error": r.stderr or r.stdout or f"Exit code {r.returncode}",
                "data": {},
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"OmegaFold timed out after {timeout}s", "data": {}}
    except Exception as e:
        return {"success": False, "error": str(e), "data": {}}

    # OmegaFold writes one PDB per sequence in OUTPUT_DIRECTORY; names from input or default
    pdbs: List[str] = []
    for name in sorted(os.listdir(output_dir)):
        if name.lower().endswith(".pdb"):
            p = os.path.join(output_dir, name)
            if os.path.isfile(p):
                pdbs.append(os.path.abspath(p))
                ensure_file_permissions(p)
    if not pdbs:
        return {
            "success": False,
            "error": "OmegaFold produced no PDB files in output directory.",
            "data": {"output_dir": os.path.abspath(output_dir)},
        }
    return {
        "success": True,
        "data": {
            "message": f"OmegaFold predicted {len(pdbs)} structure(s).",
            "output_dir": os.path.abspath(output_dir),
            "downloaded": {"pdb": pdbs},
            "output_paths": pdbs,
        },
    }


class OmegaFoldTool(BaseTool):
    """Predict 3D structure (PDB) from protein sequence using OmegaFold."""

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "omegafold",
                "description": (
                    "Predict protein 3D structure (PDB) from sequence using OmegaFold. "
                    "Input: FASTA file. Output: PDB file(s) in output_dir. "
                    "Use when user asks for structure prediction from sequence/FASTA; prefer over ESMFold for longer or complex sequences."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fasta_path": {"type": "string", "description": "Path to input FASTA file (one or more sequences)."},
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to write PDB files. Default: data/outputs/omegafold.",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 3600).", "default": 3600},
                    },
                    "required": ["fasta_path"],
                },
            },
        }

    @property
    def name(self) -> str:
        return "omegafold"

    @property
    def description(self) -> str:
        return (
            "Predict protein 3D structure (PDB) from sequence using OmegaFold. "
            "Input: FASTA file. Output: PDB file(s). Prefer for structure-from-sequence when available."
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        fasta_path = (kwargs.get("fasta_path") or "").strip()
        if not fasta_path:
            return {"success": False, "error": "fasta_path is required.", "data": {}}
        root = workspace_root()
        if not os.path.isabs(fasta_path):
            fasta_path = os.path.normpath(os.path.join(root, fasta_path))
        if not os.path.isfile(fasta_path):
            return {"success": False, "error": f"FASTA file not found: {fasta_path}", "data": {}}

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(root, output_dir)))
        default_base = os.path.normpath(os.path.join(root, DEFAULT_OUTPUT_DIR))
        if output_dir == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)

        timeout = max(60, int(kwargs.get("timeout") or 3600))
        return _run_omegafold(fasta_path, output_dir, timeout=timeout)
