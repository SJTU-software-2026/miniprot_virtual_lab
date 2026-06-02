"""
TM-align tool: sequence-independent protein structure alignment (TM-score).

https://www.aideepmed.com/TM-align/ (Zhang Lab)
https://zhanggroup.org/TM-align/

Compares two protein structures (PDB or mmCIF). Outputs: optimized residue alignment,
TM-score (0–1], >0.5 = same fold), RMSD, and optional superposed structures.
TM-score normalized by length of each structure is reported (two scores).

Install: conda install -c bioconda tmalign (or micromamba).
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/tmalign"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _find_tmalign() -> Optional[str]:
    """Return path to TMalign executable or None."""
    return shutil.which("TMalign") or shutil.which("tmalign")


def _run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 300) -> tuple[bool, str, str]:
    """Run command; return (success, stdout, stderr_or_message)."""
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = (r.stdout or "").strip()
        stderr = (r.stderr or "").strip()
        if r.returncode != 0:
            return False, stdout, stderr or f"exit {r.returncode}"
        return True, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


class TMalignTool(BaseTool):
    """Run TM-align: pairwise protein structure alignment (TM-score, RMSD, optional superposed PDBs)."""

    @property
    def name(self) -> str:
        return "tmalign"

    @property
    def description(self) -> str:
        return (
            "TM-align: sequence-independent protein structure alignment. "
            "Compare two structures (PDB or mmCIF); returns TM-score (0–1], >0.5 = same fold), RMSD, "
            "and optional superposed structure files. Use for pairwise structure comparison. "
            "Install: conda install -c bioconda tmalign (or micromamba)."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "tmalign",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "structure1": {
                        "type": "string",
                        "description": "Path to first structure (PDB or mmCIF).",
                    },
                    "structure2": {
                        "type": "string",
                        "description": "Path to second structure (PDB or mmCIF).",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for result log and superposed files. Default: data/outputs/tmalign.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "seq_mode": {
                        "type": "boolean",
                        "description": "If true, use -seq: TM-score based on sequence alignment (for model vs native). Default false.",
                        "default": False,
                    },
                    "write_superposed": {
                        "type": "boolean",
                        "description": "If true, write superposed structure files (-o). Default true.",
                        "default": True,
                    },
                },
                "required": ["structure1", "structure2"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        exe = _find_tmalign()
        if not exe:
            return {
                "success": False,
                "error": "TMalign not found. Install e.g. conda install -c bioconda tmalign or micromamba install -c bioconda tmalign (https://zhanggroup.org/TM-align/).",
                "data": {},
            }

        structure1 = (kwargs.get("structure1") or "").strip()
        structure2 = (kwargs.get("structure2") or "").strip()
        if not structure1 or not structure2:
            return {"success": False, "error": "structure1 and structure2 are required.", "data": {}}
        if not os.path.isfile(structure1):
            return {"success": False, "error": f"Structure not found: {structure1}", "data": {}}
        if not os.path.isfile(structure2):
            return {"success": False, "error": f"Structure not found: {structure2}", "data": {}}

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_prefix = os.path.join(output_dir, f"tmalign_{run_id}")
        log_path = out_prefix + ".txt"

        cmd = [exe, structure1, structure2]
        if kwargs.get("seq_mode"):
            cmd.append("-seq")
        if kwargs.get("write_superposed", True):
            cmd.extend(["-o", out_prefix])

        ok, stdout, stderr = _run_cmd(cmd, cwd=output_dir)
        if not ok:
            return {"success": False, "error": f"TMalign failed: {stderr or stdout or 'unknown'}", "data": {}}

        with open(log_path, "w") as f:
            f.write(stdout)
            if stderr:
                f.write("\n\nSTDERR:\n")
                f.write(stderr)
        ensure_file_permissions(log_path)

        out_files = [os.path.abspath(log_path)]
        superposed_files = []
        for suffix in [".sup", ".sup_all"]:
            p = out_prefix + suffix
            if os.path.isfile(p):
                ensure_file_permissions(p)
                ap = os.path.abspath(p)
                out_files.append(ap)
                superposed_files.append(ap)

        # Parse TM-scores from stdout by line meaning (Chain_1, Chain_2)
        tm1 = tm2 = rmsd = None
        for line in stdout.splitlines():
            line_stripped = line.strip()
            if "TM-score=" in line_stripped:
                m = re.search(r"TM-score=\s*([\d.]+)", line_stripped)
                if m:
                    try:
                        val = float(m.group(1))
                        low = line_stripped.lower()
                        if "chain_1" in low or "chain 1" in low:
                            tm1 = val
                        elif "chain_2" in low or "chain 2" in low:
                            tm2 = val
                    except ValueError:
                        pass
            if "RMSD=" in line_stripped:
                try:
                    parts = line_stripped.split()
                    for i, p in enumerate(parts):
                        if p == "RMSD=" and i + 1 < len(parts):
                            rmsd = float(parts[i + 1])
                            break
                except (ValueError, IndexError):
                    pass

        return {
            "success": True,
            "data": {
                "message": "TM-align completed.",
                "log_path": os.path.abspath(log_path),
                "tm_score_chain1": tm1,
                "tm_score_chain2": tm2,
                "rmsd": rmsd,
                "downloaded": {"log": [os.path.abspath(log_path)], "superposed": superposed_files},
            },
        }
