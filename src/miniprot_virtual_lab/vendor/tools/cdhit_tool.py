"""
CD-HIT tool: ultra-fast clustering and comparison of protein or nucleotide sequences.

User guide: https://www.bioinformatics.org/cd-hit/cd-hit-user-guide

Actions:
- cluster: Cluster protein sequences (cd-hit). Input FASTA → representatives FASTA + .clstr.
- cluster_est: Cluster nucleotide/DNA/RNA sequences (cd-hit-est).
- compare: Compare two protein datasets (cd-hit-2d). Sequences in db2 similar to db1 → novel db2 + .clstr.
- compare_est: Compare two nucleotide datasets (cd-hit-est-2d).

Word size (-n) by identity threshold: protein: 5 (0.7–1.0), 4 (0.6–0.7), 3 (0.5–0.6), 2 (0.4–0.5);
nucleotide: 8–10 (0.9–1.0), 7 (0.88–0.9), 6 (0.85–0.88), 5 (0.8–0.85), 4 (0.75–0.8).

Requires CD-HIT on PATH (e.g. conda install -c bioconda cd-hit).
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

DEFAULT_OUTPUT_DIR = "data/outputs/cdhit"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _find_exe(name: str) -> Optional[str]:
    return shutil.which(name)


def _run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 7200) -> tuple[bool, str]:
    """Run command; return (success, stderr_or_message)."""
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:2000]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _default_word_size_protein(identity: float) -> int:
    """Word size -n for cd-hit by identity threshold (user guide)."""
    if identity >= 0.7:
        return 5
    if identity >= 0.6:
        return 4
    if identity >= 0.5:
        return 3
    return 2


def _default_word_size_est(identity: float) -> int:
    """Word size -n for cd-hit-est by identity threshold (user guide)."""
    if identity >= 0.9:
        return 8
    if identity >= 0.88:
        return 7
    if identity >= 0.85:
        return 6
    if identity >= 0.8:
        return 5
    return 4


class CDHitTool(BaseTool):
    """Run CD-HIT: cluster proteins (cluster), cluster nucleotides (cluster_est), compare two protein DBs (compare), compare two nucleotide DBs (compare_est)."""

    @property
    def name(self) -> str:
        return "cdhit"

    @property
    def description(self) -> str:
        return (
            "CD-HIT: ultra-fast clustering and comparison of protein or nucleotide sequences. "
            "Actions: cluster (protein FASTA → representatives + .clstr), cluster_est (nucleotide), "
            "compare (two protein FASTAs: find sequences in db2 similar to db1), compare_est (two nucleotide FASTAs). "
            "Uses identity threshold -c and word size -n. Requires cd-hit executables on PATH (conda install -c bioconda cd-hit)."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "cdhit",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: cluster (protein), cluster_est (nucleotide), compare (protein 2d), compare_est (nucleotide 2d).",
                        "enum": ["cluster", "cluster_est", "compare", "compare_est"],
                    },
                    "input_fasta": {
                        "type": "string",
                        "description": "Path to input FASTA. Required for cluster and cluster_est; for compare/compare_est this is db1.",
                    },
                    "input_fasta2": {
                        "type": "string",
                        "description": "Second input FASTA (db2). Required for compare and compare_est.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory. Default: data/outputs/cdhit.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "identity": {
                        "type": "number",
                        "description": "Sequence identity threshold (0–1). E.g. 0.9 = 90%%. Default 0.9 protein, 0.95 nucleotide.",
                        "default": 0.9,
                    },
                    "word_size": {
                        "type": "integer",
                        "description": "Word size -n. If not set, chosen from identity (protein: 5 for 0.7–1.0, 4 for 0.6–0.7, etc.; nucleotide: 8 for 0.9–1.0, etc.).",
                    },
                    "memory_mb": {
                        "type": "integer",
                        "description": "Max memory in MB (-M). Default 2000.",
                        "default": 2000,
                    },
                },
                "required": ["action"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or "").strip().lower()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)

        identity = float(kwargs.get("identity") or (0.95 if "est" in action else 0.9))
        identity = max(0.4, min(1.0, identity))
        word_size = kwargs.get("word_size")
        if word_size is not None:
            word_size = int(word_size)
        else:
            word_size = _default_word_size_est(identity) if "est" in action else _default_word_size_protein(identity)
        memory_mb = max(100, int(kwargs.get("memory_mb") or 2000))

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        if action == "cluster":
            exe = _find_exe("cd-hit")
            if not exe:
                return {"success": False, "error": "cd-hit not found. Install e.g. conda install -c bioconda cd-hit (https://www.bioinformatics.org/cd-hit/).", "data": {}}
            input_fasta = (kwargs.get("input_fasta") or "").strip()
            if not input_fasta or not os.path.isfile(input_fasta):
                return {"success": False, "error": "cluster requires input_fasta (path to protein FASTA).", "data": {}}
            output_prefix = os.path.join(output_dir, f"clusters_{run_id}.fasta")
            cmd = [exe, "-i", input_fasta, "-o", output_prefix, "-c", str(identity), "-n", str(word_size), "-M", str(memory_mb)]
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"cd-hit failed: {err}", "data": {}}
            fasta_out = output_prefix
            clstr_out = output_prefix + ".clstr"
            if os.path.isfile(fasta_out):
                ensure_file_permissions(fasta_out)
            if os.path.isfile(clstr_out):
                ensure_file_permissions(clstr_out)
            return {
                "success": True,
                "data": {
                    "message": "Protein clustering completed.",
                    "representatives_fasta": os.path.abspath(fasta_out),
                    "clstr_file": os.path.abspath(clstr_out),
                    "downloaded": {"fasta": [os.path.abspath(fasta_out)], "clstr": [os.path.abspath(clstr_out)]},
                },
            }

        if action == "cluster_est":
            exe = _find_exe("cd-hit-est")
            if not exe:
                return {"success": False, "error": "cd-hit-est not found. Install e.g. conda install -c bioconda cd-hit.", "data": {}}
            input_fasta = (kwargs.get("input_fasta") or "").strip()
            if not input_fasta or not os.path.isfile(input_fasta):
                return {"success": False, "error": "cluster_est requires input_fasta (path to nucleotide FASTA).", "data": {}}
            output_prefix = os.path.join(output_dir, f"clusters_est_{run_id}.fasta")
            cmd = [exe, "-i", input_fasta, "-o", output_prefix, "-c", str(identity), "-n", str(word_size), "-M", str(memory_mb)]
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"cd-hit-est failed: {err}", "data": {}}
            fasta_out = output_prefix
            clstr_out = output_prefix + ".clstr"
            if os.path.isfile(fasta_out):
                ensure_file_permissions(fasta_out)
            if os.path.isfile(clstr_out):
                ensure_file_permissions(clstr_out)
            return {
                "success": True,
                "data": {
                    "message": "Nucleotide clustering completed.",
                    "representatives_fasta": os.path.abspath(fasta_out),
                    "clstr_file": os.path.abspath(clstr_out),
                    "downloaded": {"fasta": [os.path.abspath(fasta_out)], "clstr": [os.path.abspath(clstr_out)]},
                },
            }

        if action == "compare":
            exe = _find_exe("cd-hit-2d")
            if not exe:
                return {"success": False, "error": "cd-hit-2d not found. Install e.g. conda install -c bioconda cd-hit.", "data": {}}
            input_fasta = (kwargs.get("input_fasta") or "").strip()
            input_fasta2 = (kwargs.get("input_fasta2") or "").strip()
            if not input_fasta or not os.path.isfile(input_fasta):
                return {"success": False, "error": "compare requires input_fasta (db1) and input_fasta2 (db2).", "data": {}}
            if not input_fasta2 or not os.path.isfile(input_fasta2):
                return {"success": False, "error": "compare requires input_fasta2 (db2).", "data": {}}
            output_prefix = os.path.join(output_dir, f"compare_{run_id}.fasta")
            cmd = [exe, "-i", input_fasta, "-i2", input_fasta2, "-o", output_prefix, "-c", str(identity), "-n", str(word_size), "-M", str(memory_mb)]
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"cd-hit-2d failed: {err}", "data": {}}
            fasta_out = output_prefix
            clstr_out = output_prefix + ".clstr"
            if os.path.isfile(fasta_out):
                ensure_file_permissions(fasta_out)
            if os.path.isfile(clstr_out):
                ensure_file_permissions(clstr_out)
            return {
                "success": True,
                "data": {
                    "message": "Protein compare (cd-hit-2d) completed. Output: sequences in db2 not similar to db1.",
                    "novel_fasta": os.path.abspath(fasta_out),
                    "clstr_file": os.path.abspath(clstr_out),
                    "downloaded": {"fasta": [os.path.abspath(fasta_out)], "clstr": [os.path.abspath(clstr_out)]},
                },
            }

        if action == "compare_est":
            exe = _find_exe("cd-hit-est-2d")
            if not exe:
                return {"success": False, "error": "cd-hit-est-2d not found. Install e.g. conda install -c bioconda cd-hit.", "data": {}}
            input_fasta = (kwargs.get("input_fasta") or "").strip()
            input_fasta2 = (kwargs.get("input_fasta2") or "").strip()
            if not input_fasta or not os.path.isfile(input_fasta):
                return {"success": False, "error": "compare_est requires input_fasta (db1) and input_fasta2 (db2).", "data": {}}
            if not input_fasta2 or not os.path.isfile(input_fasta2):
                return {"success": False, "error": "compare_est requires input_fasta2 (db2).", "data": {}}
            output_prefix = os.path.join(output_dir, f"compare_est_{run_id}.fasta")
            cmd = [exe, "-i", input_fasta, "-i2", input_fasta2, "-o", output_prefix, "-c", str(identity), "-n", str(word_size), "-M", str(memory_mb)]
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"cd-hit-est-2d failed: {err}", "data": {}}
            fasta_out = output_prefix
            clstr_out = output_prefix + ".clstr"
            if os.path.isfile(fasta_out):
                ensure_file_permissions(fasta_out)
            if os.path.isfile(clstr_out):
                ensure_file_permissions(clstr_out)
            return {
                "success": True,
                "data": {
                    "message": "Nucleotide compare (cd-hit-est-2d) completed. Output: sequences in db2 not similar to db1.",
                    "novel_fasta": os.path.abspath(fasta_out),
                    "clstr_file": os.path.abspath(clstr_out),
                    "downloaded": {"fasta": [os.path.abspath(fasta_out)], "clstr": [os.path.abspath(clstr_out)]},
                },
            }

        return {"success": False, "error": f"Unknown action: {action}. Use cluster, cluster_est, compare, compare_est.", "data": {}}
