"""
Foldseek tool: fast and sensitive protein structure search and clustering.

https://github.com/steineggerlab/foldseek

Actions:
- easy_search: Search query structure(s) or FASTA against target (folder or DB). Output: tab-separated alignments.
- createdb: Create Foldseek DB from PDB/mmCIF folder or from FASTA (with ProstT5 model).
- easy_cluster: Cluster structures (or FASTA via ProstT5). Output: _clu.tsv, _repseq.fasta, _allseq.fasta.
- databases: Download pre-built database (PDB, Alphafold/Proteome, Alphafold/UniProt50, etc.).

Query = probe (e.g. query.db from query.fasta); target = DB to search in (e.g. target.db from target.fasta = HMMER hits concatenated). FASTA input requires createdb with --prostt5-model first.

Install: conda install -c conda-forge -c bioconda foldseek (or micromamba).
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

DEFAULT_OUTPUT_DIR = "data/outputs/foldseek"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _find_foldseek() -> Optional[str]:
    """Return path to foldseek executable or None."""
    return shutil.which("foldseek")


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


class FoldseekTool(BaseTool):
    """Run Foldseek: structure search (easy_search), create DB (createdb), structure clustering (easy_cluster), download DBs (databases)."""

    @property
    def name(self) -> str:
        return "foldseek"

    @property
    def description(self) -> str:
        return (
            "Foldseek: fast and sensitive protein structure search and clustering. "
            "Actions: easy_search (query structure or FASTA vs target folder/DB), createdb (PDB/mmCIF folder or FASTA+ProstT5 → DB), "
            "easy_cluster (cluster structures; output: _clu.tsv, _repseq.fasta, _allseq.fasta), databases (download PDB, Alphafold/Proteome, etc.). "
            "Query/target: path to PDB/mmCIF file or folder, or a Foldseek DB. Install: conda install -c bioconda foldseek."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "foldseek",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: easy_search, createdb, easy_cluster, databases.",
                        "enum": ["easy_search", "createdb", "easy_cluster", "databases"],
                    },
                    "query_path": {
                        "type": "string",
                        "description": "Query: path to probe (query.db or PDB/folder/FASTA). In HMMER pipeline: DB built from query.fasta (sequence(s) used as HMMER input). For easy_search.",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "Target: path to database to search in (target.db or PDB folder). In HMMER pipeline: DB built from target.fasta (HMMER hit sequences fetched and concatenated). For easy_search.",
                    },
                    "input_path": {
                        "type": "string",
                        "description": "Input: PDB/mmCIF folder or FASTA path. For createdb and easy_cluster.",
                    },
                    "db_path": {
                        "type": "string",
                        "description": "Output DB path (createdb) or path to existing DB. For createdb this is the output; for easy_search can be target_path.",
                    },
                    "database_name": {
                        "type": "string",
                        "description": "Pre-built DB name: PDB, Alphafold/Proteome, Alphafold/UniProt50, Alphafold/Swiss-Prot, ESMAtlas30, or ProstT5 (weights for FASTA→3Di; use db_path as output dir).",
                    },
                    "prostt5_model": {
                        "type": "string",
                        "description": "Path to ProstT5 weights dir (from 'foldseek databases ProstT5 weights tmp'). Use for createdb from FASTA or FASTA search.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory. Default: data/outputs/foldseek.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "sensitivity": {
                        "type": "number",
                        "description": "Search sensitivity -s (lower=faster, higher=more sensitive). Default 9.5.",
                        "default": 9.5,
                    },
                    "evalue": {
                        "type": "number",
                        "description": "Max E-value for search/cluster. Default 0.001.",
                        "default": 0.001,
                    },
                    "coverage": {
                        "type": "number",
                        "description": "Min fraction of aligned residues -c (0-1). Default 0.0.",
                        "default": 0.0,
                    },
                    "gpu": {
                        "type": "boolean",
                        "description": "Use GPU for search (--gpu 1). Default false.",
                        "default": False,
                    },
                },
                "required": ["action"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        exe = _find_foldseek()
        if not exe:
            return {
                "success": False,
                "error": "foldseek not found. Install e.g. conda install -c conda-forge -c bioconda foldseek (https://github.com/steineggerlab/foldseek).",
                "data": {},
            }

        action = (kwargs.get("action") or "").strip().lower()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        if action == "easy_search":
            query_path = (kwargs.get("query_path") or "").strip()
            target_path = (kwargs.get("target_path") or "").strip()
            if not query_path or not target_path:
                return {"success": False, "error": "easy_search requires query_path and target_path.", "data": {}}
            if not os.path.exists(query_path):
                return {"success": False, "error": f"Query path not found: {query_path}", "data": {}}
            if not os.path.exists(target_path):
                return {"success": False, "error": f"Target path not found: {target_path}", "data": {}}
            sensitivity = float(kwargs.get("sensitivity") or 9.5)
            evalue = float(kwargs.get("evalue") or 0.001)
            coverage = float(kwargs.get("coverage") or 0.0)
            use_gpu = bool(kwargs.get("gpu", False))
            prostt5 = (kwargs.get("prostt5_model") or "").strip()
            result_prefix = os.path.join(output_dir, f"search_{run_id}")
            tmp_dir = os.path.join(output_dir, "tmp_search")
            safe_dir(tmp_dir)
            cmd = [
                exe, "easy-search",
                query_path,
                target_path,
                result_prefix,
                tmp_dir,
                "-s", str(sensitivity),
                "-e", str(evalue),
                "-c", str(coverage),
            ]
            if use_gpu:
                cmd.extend(["--gpu", "1"])
            if prostt5 and os.path.isdir(prostt5):
                cmd.extend(["--prostt5-model", prostt5])
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"foldseek easy-search failed: {err}", "data": {}}
            # Default output is tab-separated; Foldseek may write result_prefix or result_prefix.m8 etc.
            out_files = []
            for name in [result_prefix, result_prefix + ".m8", result_prefix + ".tsv"]:
                if os.path.isfile(name):
                    ensure_file_permissions(name)
                    out_files.append(os.path.abspath(name))
            if not out_files:
                out_files = [os.path.abspath(result_prefix)]
            return {
                "success": True,
                "data": {
                    "message": "Foldseek search completed.",
                    "result_prefix": os.path.abspath(result_prefix),
                    "downloaded": {"search_result": out_files},
                },
            }

        if action == "createdb":
            input_path = (kwargs.get("input_path") or "").strip()
            db_path = (kwargs.get("db_path") or "").strip()
            if not input_path or not db_path:
                return {"success": False, "error": "createdb requires input_path and db_path.", "data": {}}
            if not os.path.exists(input_path):
                return {"success": False, "error": f"Input path not found: {input_path}", "data": {}}
            if not os.path.isabs(db_path):
                db_path = os.path.normpath(os.path.join(output_dir, db_path))
            prostt5 = (kwargs.get("prostt5_model") or "").strip()
            cmd = [exe, "createdb", input_path, db_path]
            if prostt5 and os.path.isdir(prostt5):
                cmd.extend(["--prostt5-model", prostt5])
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"foldseek createdb failed: {err}", "data": {}}
            return {
                "success": True,
                "data": {
                    "message": "Foldseek database created.",
                    "db_path": os.path.abspath(db_path),
                },
            }

        if action == "easy_cluster":
            input_path = (kwargs.get("input_path") or "").strip()
            if not input_path:
                return {"success": False, "error": "easy_cluster requires input_path (PDB/mmCIF folder or DB).", "data": {}}
            if not os.path.exists(input_path):
                return {"success": False, "error": f"Input path not found: {input_path}", "data": {}}
            coverage = float(kwargs.get("coverage") or 0.0)
            evalue = float(kwargs.get("evalue") or 0.001)
            result_prefix = os.path.join(output_dir, f"cluster_{run_id}")
            tmp_dir = os.path.join(output_dir, "tmp_cluster")
            safe_dir(tmp_dir)
            cmd = [
                exe, "easy-cluster",
                input_path,
                result_prefix,
                tmp_dir,
                "-c", str(coverage),
                "-e", str(evalue),
            ]
            ok, err = _run_cmd(cmd, cwd=output_dir)
            if not ok:
                return {"success": False, "error": f"foldseek easy-cluster failed: {err}", "data": {}}
            out_files = []
            for suffix in ["_clu.tsv", "_repseq.fasta", "_allseq.fasta"]:
                p = result_prefix + suffix
                if os.path.isfile(p):
                    ensure_file_permissions(p)
                    out_files.append(os.path.abspath(p))
            return {
                "success": True,
                "data": {
                    "message": "Foldseek clustering completed.",
                    "result_prefix": os.path.abspath(result_prefix),
                    "clu_tsv": os.path.abspath(result_prefix + "_clu.tsv") if os.path.isfile(result_prefix + "_clu.tsv") else None,
                    "repseq_fasta": os.path.abspath(result_prefix + "_repseq.fasta") if os.path.isfile(result_prefix + "_repseq.fasta") else None,
                    "allseq_fasta": os.path.abspath(result_prefix + "_allseq.fasta") if os.path.isfile(result_prefix + "_allseq.fasta") else None,
                    "downloaded": {"cluster": out_files},
                },
            }

        if action == "databases":
            database_name = (kwargs.get("database_name") or "").strip()
            if not database_name:
                return {"success": False, "error": "databases requires database_name (e.g. PDB, Alphafold/Proteome, ProstT5).", "data": {}}
            # ProstT5: foldseek databases ProstT5 <weights_dir> <tmp_dir> [--remove-tmp-files 1]
            if database_name.lower() == "prostt5":
                db_path = (kwargs.get("db_path") or "").strip()
                if not db_path:
                    db_path = os.path.join(output_dir, "ProstT5")
                if not os.path.isabs(db_path):
                    db_path = os.path.normpath(os.path.join(output_dir, db_path))
                safe_dir(db_path)
                tmp_dir = os.path.join(output_dir, "tmp_prostt5")
                safe_dir(tmp_dir)
                cmd = [exe, "databases", "ProstT5", db_path, tmp_dir, "--remove-tmp-files", "1"]
            else:
                db_path = os.path.join(output_dir, database_name.replace("/", "_"))
                tmp_dir = os.path.join(output_dir, "tmp_db")
                safe_dir(tmp_dir)
                cmd = [exe, "databases", database_name, db_path, tmp_dir]
            ok, err = _run_cmd(cmd, cwd=output_dir, timeout=36000)
            if not ok:
                return {"success": False, "error": f"foldseek databases failed: {err}", "data": {}}
            return {
                "success": True,
                "data": {
                    "message": f"Database '{database_name}' downloaded. Use db_path as prostt5_model for createdb/easy_search with FASTA.",
                    "db_path": os.path.abspath(db_path),
                },
            }

        return {"success": False, "error": f"Unknown action: {action}. Use easy_search, createdb, easy_cluster, databases.", "data": {}}
