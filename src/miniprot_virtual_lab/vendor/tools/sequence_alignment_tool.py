"""
Sequence alignment: MAFFT (default), Clustal Omega (faster for large sets), or NCBI BLAST when requested.
- MAFFT: run mafft --auto on a FASTA file; output aligned FASTA. Best accuracy; can be slow on huge sets. Requires mafft (conda install -c bioconda mafft).
- clustalo: Clustal Omega; faster than MAFFT for large numbers of sequences. Requires clustalo (conda install -c bioconda clustalo).
- BLAST: NCBI BLAST search/align. Uses NCBI Common URL API.
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
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/sequence_alignment"
NCBI_BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

try:
    from utils.path_utils import safe_dir, resolve_output_dir, workspace_root, ensure_file_permissions, safe_run_id
except ImportError:
    from ..utils.path_utils import safe_dir, resolve_output_dir, workspace_root, ensure_file_permissions, safe_run_id

try:
    from utils.fasta_parser import parse_fasta, parse_fasta_paths
except ImportError:
    from ..utils.fasta_parser import parse_fasta, parse_fasta_paths


def _find_mafft() -> Optional[str]:
    """Return path to mafft executable or None."""
    return shutil.which("mafft")


# No time limit for MAFFT/Clustal Omega (user requested removal). Optional timeout_seconds in schema is ignored for alignment runs.


def _run_mafft(
    fasta_path: str,
    output_path: str,
    auto: bool = True,
) -> tuple[bool, str]:
    """
    Run MAFFT on a FASTA file. Returns (success, error_message). No time limit.
    """
    exe = _find_mafft()
    if not exe:
        return False, "mafft not found. Install e.g. conda install -c bioconda mafft"
    if not os.path.isfile(fasta_path):
        return False, f"Input FASTA not found: {fasta_path}"
    out_dir = os.path.dirname(output_path)
    if out_dir:
        safe_dir(out_dir)
    cmd = [exe, "--auto", fasta_path] if auto else [exe, fasta_path]
    try:
        with open(output_path, "w") as f_out:
            result = subprocess.run(
                cmd,
                stdout=f_out,
                stderr=subprocess.PIPE,
                text=True,
            )
        if result.returncode != 0:
            return False, result.stderr or f"mafft exited {result.returncode}"
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            return False, "mafft produced no output"
        return True, ""
    except Exception as e:
        return False, str(e)


def _find_clustalo() -> Optional[str]:
    """Return path to clustalo executable or None."""
    return shutil.which("clustalo") or shutil.which("clustal-omega")


def _run_clustalo(
    fasta_path: str,
    output_path: str,
) -> tuple[bool, str]:
    """
    Run Clustal Omega on a FASTA file. Output is aligned FASTA. Returns (success, error_message). No time limit.
    """
    exe = _find_clustalo()
    if not exe:
        return False, "clustalo not found. Install e.g. conda install -c bioconda clustalo"
    if not os.path.isfile(fasta_path):
        return False, f"Input FASTA not found: {fasta_path}"
    out_dir = os.path.dirname(output_path)
    if out_dir:
        safe_dir(out_dir)
    cmd = [exe, "-i", fasta_path, "-o", output_path, "--force", "--outfmt=fa"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or f"clustalo exited {result.returncode}").strip()[:1000]
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            return False, "clustalo produced no output"
        return True, ""
    except Exception as e:
        return False, str(e)


def _blast_submit(
    query: str,
    program: str = "blastp",
    database: str = "nr",
    hitlist_size: int = 50,
    expect: str = "10",
) -> tuple[bool, Optional[str], Optional[int], str]:
    """
    Submit a BLAST job to NCBI. Returns (success, rid, rtoe_seconds, error_message).
    Query can be raw sequence or FASTA (with optional >id line).
    """
    try:
        import requests
    except ImportError:
        return False, None, None, "requests not installed"
    query = (query or "").strip()
    if not query:
        return False, None, None, "Empty query"
    params = {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "QUERY": query,
        "HITLIST_SIZE": str(hitlist_size),
        "EXPECT": expect,
        "FORMAT_TYPE": "XML2",
    }
    try:
        r = requests.get(NCBI_BLAST_URL, params=params, timeout=60)
        r.raise_for_status()
        text = r.text
        rid = None
        rtoe = None
        for line in text.splitlines():
            if line.startswith("RID ="):
                rid = line.split("=", 1)[-1].strip()
            elif line.startswith("RTOE ="):
                try:
                    rtoe = int(line.split("=", 1)[-1].strip())
                except ValueError:
                    pass
        if not rid:
            if "Error" in text or "error" in text:
                return False, None, None, text[:500]
            return False, None, None, "No RID in BLAST response"
        return True, rid, rtoe or 60, ""
    except Exception as e:
        return False, None, None, str(e)


def _blast_get_result(rid: str, poll_interval: int = 10, max_wait_sec: int = 600) -> tuple[bool, Optional[str], str]:
    """
    Poll NCBI BLAST for result. Returns (success, result_xml_text, error_message).
    """
    try:
        import requests
    except ImportError:
        return False, None, "requests not installed"
    params = {"CMD": "Get", "RID": rid, "FORMAT_TYPE": "XML2"}
    start = time.monotonic()
    while (time.monotonic() - start) < max_wait_sec:
        try:
            r = requests.get(NCBI_BLAST_URL, params=params, timeout=30)
            r.raise_for_status()
            text = r.text
            if "Status=READY" in text:
                # Result is in the response
                if "<BlastOutput>" in text:
                    return True, text, ""
                # Sometimes results come in a separate request
                if "http" in text and "results" in text.lower():
                    pass
            if "Status=WAITING" in text:
                time.sleep(poll_interval)
                continue
            if "Status=UNKNOWN" in text or "Status=FAILED" in text or "Error" in text:
                return False, None, text[:500] or "BLAST job failed"
            time.sleep(poll_interval)
        except Exception as e:
            return False, None, str(e)
    return False, None, "BLAST job timed out"


class SequenceAlignmentTool(BaseTool):
    """
    Align sequences: default MAFFT (multiple sequence alignment), or NCBI BLAST (search/align vs database) when user asks for BLAST.
    """

    def __init__(self):
        self._name = "sequence_alignment"
        self._description = (
            "Align sequences. Default: MAFFT (multiple sequence alignment). Use method=clustalo for faster alignment on large sets. "
            "If the user asks for NCBI BLAST, use method=blast. Inputs: fasta_path for MAFFT/clustalo; query or fasta_path for BLAST. "
            "Outputs: aligned FASTA path (MAFFT/clustalo) or BLAST result path. No time limit for MAFFT or Clustal Omega."
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
                        "method": {
                            "type": "string",
                            "enum": ["mafft", "clustalo", "blast"],
                            "description": "Alignment method (use this parameter name). mafft = multiple sequence alignment (default). clustalo = Clustal Omega, faster for large sets. blast = NCBI BLAST. Accepts alias 'algorithm'.",
                            "default": "mafft",
                        },
                        "algorithm": {
                            "type": "string",
                            "enum": ["mafft", "clustalo", "blast"],
                            "description": "Alias for 'method'. Use method=mafft or method=clustalo (same as algorithm).",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Ignored: no time limit is applied for mafft or clustalo.",
                        },
                        "fasta_path": {
                            "type": "string",
                            "description": "Path to FASTA file (for MAFFT: multiple sequences to align; for BLAST: query sequence in FASTA format).",
                        },
                        "query": {
                            "type": "string",
                            "description": "For BLAST: query sequence (raw or FASTA). Optional if fasta_path is set.",
                        },
                        "program": {
                            "type": "string",
                            "enum": ["blastp", "blastn", "blastx", "tblastn", "tblastx"],
                            "description": "BLAST program (default blastp for protein).",
                            "default": "blastp",
                        },
                        "database": {
                            "type": "string",
                            "description": "BLAST database (default nr). e.g. nr, swissprot, refseq_protein.",
                            "default": "nr",
                        },
                        "hitlist_size": {"type": "integer", "description": "Max BLAST hits (default 50).", "default": 50},
                        "output_dir": {
                            "type": "string",
                            "description": "Output directory (default: data/outputs/sequence_alignment).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "output_filename": {
                            "type": "string",
                            "description": "Output file name (MAFFT: aligned FASTA; BLAST: result XML). Optional.",
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        # Accept both 'method' and 'algorithm' (LLMs often use algorithm=... by mistake).
        method = (kwargs.get("method") or kwargs.get("algorithm") or "mafft").strip().lower() or "mafft"
        if method not in ("mafft", "clustalo", "blast"):
            method = "mafft"
        fasta_path = (kwargs.get("fasta_path") or "").strip()
        query = (kwargs.get("query") or "").strip()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        else:
            output_dir = os.path.normpath(output_dir)
        default_base = os.path.normpath(os.path.join(workspace_root(), DEFAULT_OUTPUT_DIR))
        if output_dir == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)

        if method == "mafft":
            if not fasta_path or not os.path.isfile(fasta_path):
                return {"success": False, "error": "For MAFFT, provide fasta_path to an existing FASTA file.", "data": {}}
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_name = kwargs.get("output_filename") or f"aligned_{run_id}.fasta"
            if not out_name.endswith(".fasta") and not out_name.endswith(".fa"):
                out_name = out_name + ".fasta"
            output_path = os.path.join(output_dir, out_name)
            ok, err = _run_mafft(fasta_path, output_path)
            if not ok:
                return {"success": False, "error": err, "data": {}}
            ensure_file_permissions(output_path)
            return {
                "success": True,
                "data": {
                    "message": "MAFFT alignment completed.",
                    "method": "mafft",
                    "aligned_path": os.path.abspath(output_path),
                    "output_dir": output_dir,
                    "downloaded": {"aligned_fasta": [os.path.abspath(output_path)]},
                },
            }

        if method == "clustalo":
            if not fasta_path or not os.path.isfile(fasta_path):
                return {"success": False, "error": "For Clustal Omega, provide fasta_path to an existing FASTA file.", "data": {}}
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_name = kwargs.get("output_filename") or f"aligned_clustalo_{run_id}.fasta"
            if not out_name.endswith(".fasta") and not out_name.endswith(".fa"):
                out_name = out_name + ".fasta"
            output_path = os.path.join(output_dir, out_name)
            ok, err = _run_clustalo(fasta_path, output_path)
            if not ok:
                return {"success": False, "error": err, "data": {}}
            ensure_file_permissions(output_path)
            return {
                "success": True,
                "data": {
                    "message": "Clustal Omega alignment completed.",
                    "method": "clustalo",
                    "aligned_path": os.path.abspath(output_path),
                    "output_dir": output_dir,
                    "downloaded": {"aligned_fasta": [os.path.abspath(output_path)]},
                },
            }

        # BLAST
        if not query and fasta_path and os.path.isfile(fasta_path):
            with open(fasta_path, "r") as f:
                query = f.read()
        if not query or not query.strip():
            return {"success": False, "error": "For BLAST, provide query (sequence or FASTA) or fasta_path.", "data": {}}
        program = (kwargs.get("program") or "blastp").strip().lower() or "blastp"
        database = (kwargs.get("database") or "nr").strip() or "nr"
        hitlist_size = max(10, min(500, int(kwargs.get("hitlist_size") or 50)))
        expect = str(kwargs.get("expect") or "10").strip()

        ok, rid, rtoe, err = _blast_submit(query, program=program, database=database, hitlist_size=hitlist_size, expect=expect)
        if not ok:
            return {"success": False, "error": f"BLAST submit failed: {err}", "data": {}}
        ok, result_xml, err = _blast_get_result(rid, poll_interval=max(5, min(rtoe or 10, 30)))
        if not ok:
            return {"success": False, "error": f"BLAST result failed: {err}", "data": {"rid": rid}}
        out_name = kwargs.get("output_filename") or f"blast_{program}_{rid}.xml"
        if not out_name.endswith(".xml"):
            out_name = out_name + ".xml"
        output_path = os.path.join(output_dir, out_name)
        try:
            with open(output_path, "w") as f:
                f.write(result_xml)
            ensure_file_permissions(output_path)
        except Exception as e:
            return {"success": False, "error": str(e), "data": {"rid": rid}}
        hits_count = len(re.findall(r"<Hit>", result_xml or ""))
        return {
            "success": True,
            "data": {
                "message": f"NCBI BLAST ({program} vs {database}) completed. {hits_count} hit(s).",
                "method": "blast",
                "rid": rid,
                "result_path": os.path.abspath(output_path),
                "output_dir": output_dir,
                "hits_count": hits_count,
                "downloaded": {"blast_result": [os.path.abspath(output_path)]},
            },
        }
