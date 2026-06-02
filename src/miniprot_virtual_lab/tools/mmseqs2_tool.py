"""
MMseqs2 tool: ultra-fast sequence search and clustering (https://github.com/soedinglab/MMseqs2).

Actions:
- createdb: Create MMseqs2 DB from FASTA (mmseqs createdb <fasta> <db_prefix>).
- search: Search query DB against target DB (mmseqs search query_db target_db result_db tmp).
- convertalis: Convert search result to tabular .m8 (mmseqs convertalis query_db target_db result_db out.m8 .).
- run_search: One-shot: create query DB, target DB, run search, convert to .m8. Use for similarity matrix pipelines.

Requires MMseqs2 on PATH (e.g. conda install -c bioconda mmseqs2).
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

DEFAULT_OUTPUT_DIR = "data/outputs/mmseqs2"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _find_mmseqs() -> Optional[str]:
    """Return path to mmseqs executable or None."""
    return shutil.which("mmseqs")


def _run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 3600) -> tuple[bool, str]:
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


class MMseqs2Tool(BaseTool):
    """Run MMseqs2 for sequence search: createdb, search, convertalis, or run_search (query vs target → .m8)."""

    @property
    def name(self) -> str:
        return "mmseqs2"

    @property
    def description(self) -> str:
        return (
            "MMseqs2: ultra-fast sequence search and clustering. "
            "Actions: createdb (FASTA → DB), search (query_db vs target_db), convertalis (result → .m8), "
            "run_search (query FASTA vs target FASTA → .m8 in one shot for similarity matrix pipelines). "
            "Requires mmseqs on PATH (conda install -c bioconda mmseqs2)."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "mmseqs2",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: createdb, search, convertalis, run_search.",
                        "enum": ["createdb", "search", "convertalis", "run_search"],
                    },
                    "fasta_path": {
                        "type": "string",
                        "description": "Path to FASTA file. For createdb; for run_search use query_fasta and target_fasta.",
                    },
                    "db_prefix": {
                        "type": "string",
                        "description": "Output DB path prefix (e.g. ./query_db). Used by createdb.",
                    },
                    "query_db": {
                        "type": "string",
                        "description": "Path to query DB (created by createdb). For search and convertalis.",
                    },
                    "target_db": {
                        "type": "string",
                        "description": "Path to target DB. For search and convertalis.",
                    },
                    "result_db": {
                        "type": "string",
                        "description": "Path for search result DB. For search and convertalis.",
                    },
                    "output_m8": {
                        "type": "string",
                        "description": "Output .m8 tabular file path. For convertalis.",
                    },
                    "query_fasta": {
                        "type": "string",
                        "description": "Path to query FASTA. For run_search (query sequences).",
                    },
                    "target_fasta": {
                        "type": "string",
                        "description": "Path to target FASTA. For run_search (target sequences; can be same as query for self-similarity).",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for DBs and result files.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "sensitivity": {
                        "type": "number",
                        "description": "Search sensitivity 1.0 (fast) to 7.0 (sensitive). Default 4.0.",
                        "default": 4.0,
                    },
                },
                "required": ["action"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        exe = _find_mmseqs()
        if not exe:
            return {
                "success": False,
                "error": "mmseqs not found. Install e.g. conda install -c bioconda mmseqs2 (https://github.com/soedinglab/MMseqs2).",
                "data": {},
            }

        action = (kwargs.get("action") or "").strip().lower()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)

        if action == "createdb":
            fasta_path = (kwargs.get("fasta_path") or "").strip()
            db_prefix = (kwargs.get("db_prefix") or "").strip()
            if not fasta_path or not db_prefix:
                return {"success": False, "error": "createdb requires fasta_path and db_prefix.", "data": {}}
            if not os.path.isfile(fasta_path):
                return {"success": False, "error": f"FASTA not found: {fasta_path}", "data": {}}
            if not os.path.isabs(db_prefix):
                db_prefix = os.path.normpath(os.path.join(output_dir, db_prefix))
            ok, err = _run_cmd([exe, "createdb", fasta_path, db_prefix], cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"mmseqs createdb failed: {err}", "data": {}}
            return {"success": True, "data": {"message": "Database created.", "db_prefix": db_prefix}}

        if action == "search":
            query_db = (kwargs.get("query_db") or "").strip()
            target_db = (kwargs.get("target_db") or "").strip()
            result_db = (kwargs.get("result_db") or "").strip()
            sensitivity = float(kwargs.get("sensitivity") or 4.0)
            if not query_db or not target_db or not result_db:
                return {"success": False, "error": "search requires query_db, target_db, result_db.", "data": {}}
            tmp_dir = os.path.join(output_dir, "tmp_search")
            safe_dir(tmp_dir)
            if not os.path.isabs(result_db):
                result_db = os.path.normpath(os.path.join(output_dir, result_db))
            if not os.path.isabs(query_db):
                query_db = os.path.normpath(os.path.join(output_dir, query_db))
            if not os.path.isabs(target_db):
                target_db = os.path.normpath(os.path.join(output_dir, target_db))
            ok, err = _run_cmd(
                [exe, "search", query_db, target_db, result_db, tmp_dir, "-s", str(sensitivity)],
                cwd=output_dir,
            )
            if not ok:
                return {"success": False, "error": f"mmseqs search failed: {err}", "data": {}}
            return {"success": True, "data": {"message": "Search completed.", "result_db": result_db}}

        if action == "convertalis":
            query_db = (kwargs.get("query_db") or "").strip()
            target_db = (kwargs.get("target_db") or "").strip()
            result_db = (kwargs.get("result_db") or "").strip()
            output_m8 = (kwargs.get("output_m8") or "").strip()
            if not query_db or not target_db or not result_db or not output_m8:
                return {"success": False, "error": "convertalis requires query_db, target_db, result_db, output_m8.", "data": {}}
            if not os.path.isabs(output_m8):
                output_m8 = os.path.normpath(os.path.join(output_dir, output_m8))
            out_dir = os.path.dirname(output_m8)
            if out_dir:
                safe_dir(out_dir)
            if not os.path.isabs(query_db):
                query_db = os.path.normpath(os.path.join(output_dir, query_db))
            if not os.path.isabs(target_db):
                target_db = os.path.normpath(os.path.join(output_dir, target_db))
            if not os.path.isabs(result_db):
                result_db = os.path.normpath(os.path.join(output_dir, result_db))
            ok, err = _run_cmd(
                [exe, "convertalis", query_db, target_db, result_db, output_m8, "."],
                cwd=output_dir,
            )
            if not ok:
                return {"success": False, "error": f"mmseqs convertalis failed: {err}", "data": {}}
            ensure_file_permissions(output_m8)
            return {"success": True, "data": {"message": "Converted to .m8.", "output_m8": os.path.abspath(output_m8), "downloaded": {"m8": [os.path.abspath(output_m8)]}}}

        if action == "run_search":
            query_fasta = (kwargs.get("query_fasta") or "").strip()
            target_fasta = (kwargs.get("target_fasta") or "").strip()
            sensitivity = float(kwargs.get("sensitivity") or 4.0)
            if not query_fasta or not target_fasta:
                return {"success": False, "error": "run_search requires query_fasta and target_fasta.", "data": {}}
            if not os.path.isfile(query_fasta):
                return {"success": False, "error": f"Query FASTA not found: {query_fasta}", "data": {}}
            if not os.path.isfile(target_fasta):
                return {"success": False, "error": f"Target FASTA not found: {target_fasta}", "data": {}}
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = os.path.join(output_dir, f"run_{run_id}")
            safe_dir(base)
            tmp_dir = os.path.join(base, "tmp")
            safe_dir(tmp_dir)
            query_db = os.path.join(base, "query_db")
            target_db = os.path.join(base, "target_db")
            result_db = os.path.join(base, "result_db")
            output_m8 = os.path.join(output_dir, f"result_{run_id}.m8")

            ok, err = _run_cmd([exe, "createdb", query_fasta, query_db], cwd=base)
            if not ok:
                return {"success": False, "error": f"mmseqs createdb (query) failed: {err}", "data": {}}
            ok, err = _run_cmd([exe, "createdb", target_fasta, target_db], cwd=base)
            if not ok:
                return {"success": False, "error": f"mmseqs createdb (target) failed: {err}", "data": {}}
            ok, err = _run_cmd(
                [exe, "search", query_db, target_db, result_db, tmp_dir, "-s", str(sensitivity)],
                cwd=base,
            )
            if not ok:
                return {"success": False, "error": f"mmseqs search failed: {err}", "data": {}}
            ok, err = _run_cmd(
                [exe, "convertalis", query_db, target_db, result_db, output_m8, "."],
                cwd=base,
            )
            if not ok:
                return {"success": False, "error": f"mmseqs convertalis failed: {err}", "data": {}}
            ensure_file_permissions(output_m8)
            return {
                "success": True,
                "data": {
                    "message": "MMseqs2 run_search completed.",
                    "output_m8": os.path.abspath(output_m8),
                    "run_id": run_id,
                    "downloaded": {"m8": [os.path.abspath(output_m8)]},
                },
            }

        return {"success": False, "error": f"Unknown action: {action}. Use createdb, search, convertalis, run_search.", "data": {}}
