"""
Merge (concatenate) multiple PDB or FASTA files into one.
- PDB: combine structures (often ligands) into one file.
- FASTA: concatenate sequences into one FASTA.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/merge"


try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir

try:
    from utils.pdb_utils import resolve_file_path, merge_pdb_files
except ImportError:
    from ..utils.pdb_utils import resolve_file_path, merge_pdb_files

try:
    from utils.fasta_parser import merge_fasta_files
except ImportError:
    from ..utils.fasta_parser import merge_fasta_files

class MergerTool(BaseTool):
    """
    Merge two or more PDB or FASTA files into one.
    - PDB: combine structures (often ligands) into a single PDB for downstream use (e.g. docking).
    - FASTA: concatenate sequences (e.g. after filtering / clustering).
    """

    def __init__(self):
        self._name = "merger"
        self._description = (
            "Merge two or more PDB or FASTA files into one. PDB: combine structures (often ligands). "
            "FASTA: concatenate sequences. Input: pdb_paths or fasta_paths (list), "
            "optional output_path or output_dir. Output: path to merged file."
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pdb_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Paths to PDB or FASTA files to merge. For FASTA use .fasta paths. Also accepted as fasta_paths.",
                        },
                        "fasta_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Paths to FASTA files to concatenate (alias for pdb_paths when merging sequences).",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Full path for merged file. If omitted, written to output_dir as merged_ligands.pdb or merged_sequences.fasta.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Output directory when output_path not set (default: data/outputs/merge).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        paths = list(kwargs.get("pdb_paths") or kwargs.get("fasta_paths") or [])
        if isinstance(paths, str):
            paths = [paths]
        paths = [p for p in paths if p and isinstance(p, str)]
        if len(paths) < 2:
            return {"success": False, "error": "Provide at least two paths in pdb_paths or fasta_paths.", "data": {}}

        output_path = (kwargs.get("output_path") or "").strip()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)

        # Resolve paths (workspace-relative /data/... etc.)
        resolved: List[str] = []
        for p in paths:
            p = (p or "").strip()
            r = resolve_file_path(p, output_dir) or p
            if r and os.path.isfile(r):
                resolved.append(os.path.abspath(r))
            elif os.path.isfile(p):
                resolved.append(os.path.abspath(p))
        if len(resolved) < 2:
            return {"success": False, "error": "At least two existing files required; could not resolve paths.", "data": {}}

        is_fasta = all((r or "").lower().endswith(".fasta") for r in resolved)
        if not output_path:
            output_path = os.path.join(output_dir, "merged_sequences.fasta" if is_fasta else "merged_ligands.pdb")
        elif output_path.replace("\\", "/").startswith("/data/"):
            output_path = os.path.abspath(resolve_output_dir(output_path))
        elif not os.path.isabs(output_path):
            output_path = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_path)))

        if is_fasta:
            ok = merge_fasta_files(resolved, output_path)
            msg = f"Merged {len(resolved)} FASTA files. Use merged_path for AlphaFold or downstream."
        else:
            ok = merge_pdb_files(resolved, output_path)
            msg = f"Merged {len(resolved)} PDB files. Use merged_path as ligand_pdb_path for docking."

        if not ok:
            return {"success": False, "error": "Merge failed (could not write file). Check write permissions on output_dir.", "data": {}}

        return {
            "success": True,
            "data": {
                "merged_path": os.path.abspath(output_path),
                "input_paths": resolved,
                "message": msg,
                "downloaded": {"fasta" if is_fasta else "pdb": [os.path.abspath(output_path)]},
            },
        }


class PDBMergeTool(MergerTool):
    """Backward-compatible alias for the legacy tool name 'pdb_merge'."""

    def __init__(self):
        super().__init__()
        self._name = "pdb_merge"
