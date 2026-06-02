"""
Batch structure alignment: all-vs-all PDB comparison with chunking and tool selection.

Logic:
- List all PDB (and mmCIF) files in input_dir, generate all pairs (i,j) with i < j.
- Chunk pairs into groups of PAIRS_PER_CHUNK (default 1000).
- If total_pairs <= THRESHOLD_USE_TMALIGN (10000): use TM-align for each pair (parallel).
- If total_pairs > THRESHOLD_USE_TMALIGN: use Foldseek all-vs-all (createdb + search db db).
- Parallelism: use GNU parallel if available, else xargs; default N_CORES (4) — update in code for your machine.
- Results stored in JSON format: array of { pdb_id1, pdb_id2, tm_score_1, tm_score_2, tm_score_3, alignment?, ... }.
  tm_score_1 = TM-score normalized by length of Chain_1, tm_score_2 = by Chain_2, tm_score_3 = by average length.
  When the two structures have the same length, tm_score_1 and tm_score_2 are equal (and often equal to tm_score_3).
- Output layout: under output_dir, results go to a tool-specific and run-specific subdir to avoid naming clashes:
  - TM-align: output_dir/tmalign/YYYYMMDD_HHMMSS/{pair, json_results, logs}
  - Foldseek: output_dir/foldseek/YYYYMMDD_HHMMSS/{pair, json_results, logs, db, result.*, tmp}
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/structure_alignment_batch"

# --- Config: update based on machine / job size ---
# Number of CPU cores for parallel runs (default: auto from CPU count, cap 32)
N_CORES = min(32, (os.cpu_count() or 4))
# Pairs per chunk file (for organizing large jobs)
PAIRS_PER_CHUNK = 1000
# TM-align is only used when user explicitly requests it (use_tmalign=True); default is Foldseek

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _find_tmalign() -> Optional[str]:
    return shutil.which("TMalign") or shutil.which("tmalign")


def _find_foldseek() -> Optional[str]:
    return shutil.which("foldseek")


def _find_parallel() -> Optional[str]:
    """GNU parallel preferred for parallel computing."""
    return shutil.which("parallel")


def _find_xargs() -> Optional[str]:
    """xargs fallback for parallel (usually in PATH)."""
    return shutil.which("xargs")


def _list_structure_files(directory: str) -> List[str]:
    """Return sorted list of full paths to .pdb and .mmcif/.cif in directory (non-recursive by default)."""
    out = []
    directory = os.path.abspath(directory)
    for name in sorted(os.listdir(directory)):
        if name.lower().endswith(".pdb"):
            out.append(os.path.join(directory, name))
        elif name.lower().endswith(".mmcif") or name.lower().endswith(".cif"):
            out.append(os.path.join(directory, name))
    return out


def _get_id(path: str) -> str:
    """Basename without extension for use as pdb_id."""
    base = os.path.basename(path)
    for ext in [".pdb", ".mmcif", ".cif"]:
        if base.lower().endswith(ext):
            return base[: -len(ext)]
    return base


def _parse_tm_scores_from_stdout(stdout: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Parse TM-scores from TM-align stdout by line meaning, not order.
    TM-align prints three lines, e.g.:
      TM-score= 0.80077 (if normalized by length of Chain_1, ...)
      TM-score= 0.81563 (if normalized by length of Chain_2, ...)
      TM-score= 0.80813 (if normalized by average length of two structures, ...)
    When L1 == L2, tm_score_1 and tm_score_2 are equal (same normalization); that is correct.
    """
    tm1 = tm2 = tm3 = None
    for line in stdout.splitlines():
        line = line.strip()
        if "TM-score=" not in line:
            continue
        m = re.search(r"TM-score=\s*([\d.]+)", line)
        if not m:
            continue
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        line_lower = line.lower()
        if "chain_1" in line_lower or "chain 1" in line_lower:
            tm1 = val
        elif "chain_2" in line_lower or "chain 2" in line_lower:
            tm2 = val
        elif "average" in line_lower:
            tm3 = val
    return (tm1, tm2, tm3)


def _run_tmalign_pair(pdb1: str, pdb2: str, tmalign_exe: str, timeout: int = 120) -> Dict[str, Any]:
    """Run TMalign on one pair; return JSON-serializable dict. Used by workers."""
    id1 = _get_id(pdb1)
    id2 = _get_id(pdb2)
    try:
        cmd = [tmalign_exe, pdb1, pdb2, "-a", "T"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=os.path.dirname(pdb1) or ".")
        stdout = (r.stdout or "").strip()
        if r.returncode != 0:
            return {"pdb_id1": id1, "pdb_id2": id2, "error": "TM-align failed"}
    except subprocess.TimeoutExpired:
        return {"pdb_id1": id1, "pdb_id2": id2, "error": "TM-align timed out"}
    except Exception as e:
        return {"pdb_id1": id1, "pdb_id2": id2, "error": str(e)}

    tm1, tm2, tm3 = _parse_tm_scores_from_stdout(stdout)

    seq1 = seq2 = sym = ""
    for i, line in enumerate(stdout.splitlines()):
        if "denotes other aligned residues" in line or "aligned residues" in line.lower():
            lines = stdout.splitlines()
            if i + 1 < len(lines):
                seq1 = (lines[i + 1] or "").rstrip()
            if i + 2 < len(lines):
                sym = (lines[i + 2] or "").rstrip()
            if i + 3 < len(lines):
                seq2 = (lines[i + 3] or "").rstrip()
            break

    return {
        "pdb_id1": id1,
        "pdb_id2": id2,
        "tm_score_1": tm1,
        "tm_score_2": tm2,
        "tm_score_3": tm3,
        "alignment": {"sequence1": seq1, "symbols": sym, "sequence2": seq2},
    }


def _run_tmalign_chunk_parallel(
    pairs: List[Tuple[str, str]],
    tmalign_exe: str,
    n_cores: int,
    use_parallel_cli: bool,
    pair_dir: str,
    chunk_id: int,
) -> List[Dict[str, Any]]:
    """Run TMalign on a chunk of pairs using ProcessPoolExecutor (or optionally GNU parallel/xargs)."""
    if use_parallel_cli and _find_parallel():
        return _run_chunk_with_gnu_parallel(pairs, tmalign_exe, n_cores, pair_dir, chunk_id)
    if use_parallel_cli and _find_xargs():
        return _run_chunk_with_xargs(pairs, tmalign_exe, n_cores, pair_dir, chunk_id)
    # Default: Python ProcessPoolExecutor
    results = []
    with ProcessPoolExecutor(max_workers=n_cores) as executor:
        futures = {executor.submit(_run_tmalign_pair, p1, p2, tmalign_exe): (p1, p2) for p1, p2 in pairs}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                p1, p2 = futures[fut]
                results.append({"pdb_id1": _get_id(p1), "pdb_id2": _get_id(p2), "error": str(e)})
    return results


def _write_tmalign_worker_script(path: str) -> None:
    """Write a self-contained Python worker script that runs TMalign on one pair and writes JSON."""
    content = r'''
import sys, os, re, json, subprocess
def main():
    if len(sys.argv) < 5:
        sys.exit(1)
    p1, p2, exe, outdir = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    def get_id(p):
        b = os.path.basename(p)
        for e in [".pdb", ".mmcif", ".cif"]:
            if b.lower().endswith(e):
                return b[:-len(e)]
        return b
    id1, id2 = get_id(p1), get_id(p2)
    outpath = os.path.join(outdir, f"{id1}_{id2}.json")
    try:
        r = subprocess.run([exe, p1, p2, "-a", "T"], capture_output=True, text=True, timeout=120, cwd=os.path.dirname(p1) or ".")
        stdout = (r.stdout or "").strip()
    except Exception as e:
        json.dump({"pdb_id1": id1, "pdb_id2": id2, "error": str(e)}, open(outpath, "w"), indent=2)
        return
    def parse_tm(s):
        tm1 = tm2 = tm3 = None
        for line in s.splitlines():
            line = line.strip()
            if "TM-score=" not in line:
                continue
            m = re.search(r"TM-score=\s*([\d.]+)", line)
            if not m:
                continue
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            low = line.lower()
            if "chain_1" in low or "chain 1" in low:
                tm1 = val
            elif "chain_2" in low or "chain 2" in low:
                tm2 = val
            elif "average" in low:
                tm3 = val
        return tm1, tm2, tm3
    tm1, tm2, tm3 = parse_tm(stdout)
    seq1 = seq2 = sym = ""
    lines_arr = stdout.splitlines()
    for i, line in enumerate(lines_arr):
        if "aligned residues" in line.lower():
            if i + 1 < len(lines_arr): seq1 = (lines_arr[i + 1] or "").rstrip()
            if i + 2 < len(lines_arr): sym = (lines_arr[i + 2] or "").rstrip()
            if i + 3 < len(lines_arr): seq2 = (lines_arr[i + 3] or "").rstrip()
            break
    d = {"pdb_id1": id1, "pdb_id2": id2, "tm_score_1": tm1, "tm_score_2": tm2, "tm_score_3": tm3,
         "alignment": {"sequence1": seq1, "symbols": sym, "sequence2": seq2}}
    with open(outpath, "w") as f:
        json.dump(d, f, indent=2)
if __name__ == "__main__":
    main()
'''
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o755)


def _run_chunk_with_gnu_parallel(
    pairs: List[Tuple[str, str]],
    tmalign_exe: str,
    n_cores: int,
    pair_dir: str,
    chunk_id: int,
) -> List[Dict[str, Any]]:
    """Run chunk using GNU parallel: write pair file and Python worker, run parallel, collect JSON from temp files."""
    pair_file = os.path.join(pair_dir, f"pairs_chunk_{chunk_id}.txt")
    temp_dir = os.path.join(pair_dir, f"temp_{chunk_id}")
    safe_dir(temp_dir)
    with open(pair_file, "w") as f:
        for p1, p2 in pairs:
            f.write(f"{p1}\t{p2}\n")
    worker_script = os.path.join(temp_dir, "tmalign_worker.py")
    _write_tmalign_worker_script(worker_script)
    parallel_exe = _find_parallel()
    worker_script_abs = os.path.abspath(worker_script)
    # parallel -j N --colsep '\t' python3 worker.py {1} {2} exe temp_dir
    cmd = [
        parallel_exe,
        "-j", str(n_cores),
        "--colsep", "\t",
        "--no-notice",
        "python3", worker_script_abs, "{1}", "{2}", tmalign_exe, temp_dir,
    ]
    with open(pair_file) as pf:
        subprocess.run(cmd, stdin=pf, cwd=pair_dir, capture_output=True, timeout=3600)
    results = []
    for fn in sorted(os.listdir(temp_dir)):
        if fn.endswith(".json"):
            with open(os.path.join(temp_dir, fn)) as jf:
                try:
                    results.append(json.load(jf))
                except Exception:
                    pass
    return results


def _run_chunk_with_xargs(
    pairs: List[Tuple[str, str]],
    tmalign_exe: str,
    n_cores: int,
    pair_dir: str,
    chunk_id: int,
) -> List[Dict[str, Any]]:
    """Fallback: run each pair via Python worker using xargs -P (parallel computing when GNU parallel not available)."""
    pair_file = os.path.join(pair_dir, f"pairs_chunk_{chunk_id}.txt")
    temp_dir = os.path.join(pair_dir, f"temp_{chunk_id}")
    safe_dir(temp_dir)
    worker_script = os.path.join(temp_dir, "tmalign_worker.py")
    _write_tmalign_worker_script(worker_script)
    # Build 4-column input for worker: p1 p2 exe outdir (worker expects 5 args including script name)
    args_file = os.path.join(temp_dir, "args.txt")
    with open(args_file, "w") as f:
        for p1, p2 in pairs:
            f.write(f"{p1}\t{p2}\t{tmalign_exe}\t{temp_dir}\n")
    xargs_exe = _find_xargs()
    if not xargs_exe:
        return _run_tmalign_chunk_parallel(pairs, tmalign_exe, n_cores, use_parallel_cli=False, pair_dir=pair_dir, chunk_id=chunk_id)
    # xargs -P n_cores -L 1: one line = 4 args (p1, p2, exe, outdir) to worker
    worker_script_abs = os.path.abspath(worker_script)
    with open(args_file) as af:
        subprocess.run(
            [xargs_exe, "-P", str(n_cores), "-L", "1", "python3", worker_script_abs],
            stdin=af,
            cwd=temp_dir,
            capture_output=True,
            timeout=3600,
        )
    results = []
    for fn in sorted(os.listdir(temp_dir)):
        if fn.endswith(".json"):
            with open(os.path.join(temp_dir, fn)) as jf:
                try:
                    results.append(json.load(jf))
                except Exception:
                    pass
    return results


def _run_foldseek_all_vs_all(
    input_dir: str,
    output_dir: str,
    foldseek_exe: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], str]:
    """Foldseek createdb + search db db; parse result to JSON list. output_dir is already run-specific (e.g. .../foldseek/YYYYMMDD_HHMMSS)."""
    tmp_dir = os.path.join(output_dir, "tmp")
    safe_dir(tmp_dir)
    db_path = os.path.join(output_dir, "db")
    result_prefix = os.path.join(output_dir, "result")
    cmd_createdb = [foldseek_exe, "createdb", input_dir, db_path]
    r = subprocess.run(cmd_createdb, cwd=output_dir, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"foldseek createdb failed: {r.stderr or r.stdout}")
    cmd_search = [foldseek_exe, "search", db_path, db_path, result_prefix, tmp_dir]
    r = subprocess.run(cmd_search, cwd=output_dir, capture_output=True, text=True, timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(f"foldseek search failed: {r.stderr or r.stdout}")
    # Convert result DB to TSV (createtsv queryDB targetDB resultDB output.tsv)
    tsv_path = result_prefix + ".tsv"
    cmd_tsv = [foldseek_exe, "createtsv", db_path, db_path, result_prefix, tsv_path]
    r = subprocess.run(cmd_tsv, cwd=output_dir, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"foldseek createtsv failed: {r.stderr or r.stdout}")
    results = []
    if os.path.isfile(tsv_path):
        with open(tsv_path) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    q, t = parts[0], parts[1]
                    try:
                        fident = float(parts[2]) if len(parts) > 2 else None
                        alnlen = int(parts[3]) if len(parts) > 3 else None
                        evalue = float(parts[10]) if len(parts) > 10 else None
                        bits = float(parts[11]) if len(parts) > 11 else None
                    except (ValueError, IndexError):
                        fident = alnlen = evalue = bits = None
                    results.append({
                        "pdb_id1": q,
                        "pdb_id2": t,
                        "tm_score_1": None,
                        "tm_score_2": None,
                        "tm_score_3": None,
                        "foldseek_fident": fident,
                        "foldseek_alnlen": alnlen,
                        "foldseek_evalue": evalue,
                        "foldseek_bits": bits,
                        "alignment": {"sequence1": "", "symbols": "", "sequence2": ""},
                    })
    return results, tsv_path


class StructureAlignmentBatchTool(BaseTool):
    """Batch all-vs-all structure alignment: chunk pairs (1000 per chunk), use TM-align if <=10k pairs else Foldseek; output JSON."""

    @property
    def name(self) -> str:
        return "structure_alignment_batch"

    @property
    def description(self) -> str:
        return (
            "Batch structure alignment: all-vs-all comparison of PDB/mmCIF in a directory. "
            "Default: Foldseek (fast, scalable). Use use_tmalign=True only when user explicitly requests TM-align. "
            "Chunks pairs (1000 per chunk). Parallel: GNU parallel if available, else xargs, else Python (n_cores, default auto). Results in JSON."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "structure_alignment_batch",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "input_dir": {
                        "type": "string",
                        "description": "Directory containing PDB or mmCIF files to compare (all-vs-all). Use this or pdb_paths with one directory path.",
                    },
                    "pdb_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Alternative to input_dir: list of paths; the first element is used as the directory of structure files.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Base output directory. Results go to <output_dir>/tmalign/YYYYMMDD_HHMMSS/ or <output_dir>/foldseek/YYYYMMDD_HHMMSS/ so tool and run are separated. Default: data/outputs/structure_alignment_batch.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "n_cores": {
                        "type": "integer",
                        "description": "Number of cores for parallel runs (default: auto from CPU count).",
                        "default": N_CORES,
                    },
                    "use_parallel_cli": {
                        "type": "boolean",
                        "description": "If true, use GNU parallel (preferred) or xargs when available; else Python ProcessPoolExecutor.",
                        "default": False,
                    },
                    "use_tmalign": {
                        "type": "boolean",
                        "description": "If true, use TM-align (only when user explicitly requests it). Default false = Foldseek.",
                        "default": False,
                    },
                },
                "required": [],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        input_dir = (kwargs.get("input_dir") or "").strip()
        # Accept pdb_paths as alias: agent may pass pdb_paths: [directory_path]
        if not input_dir and kwargs.get("pdb_paths"):
            plist = kwargs.get("pdb_paths")
            if isinstance(plist, list) and len(plist) > 0 and isinstance(plist[0], str):
                input_dir = (plist[0] or "").strip()
        if not input_dir or not os.path.isdir(input_dir):
            return {"success": False, "error": "input_dir is required and must be an existing directory. Pass input_dir or pdb_paths (list with one directory path).", "data": {}}

        base_output = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(base_output):
            base_output = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), base_output)))
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        n_cores = max(1, int(kwargs.get("n_cores") or N_CORES))
        use_parallel_cli = bool(kwargs.get("use_parallel_cli", False))
        # Default: Foldseek. TM-align only when user explicitly requests it.
        use_tmalign = bool(kwargs.get("use_tmalign", False))

        files = _list_structure_files(input_dir)
        total_files = len(files)
        if total_files < 2:
            return {"success": False, "error": f"Need at least 2 structure files in {input_dir}; found {total_files}.", "data": {}}

        pairs = []
        for i in range(total_files):
            for j in range(i + 1, total_files):
                pairs.append((files[i], files[j]))
        total_pairs = len(pairs)

        tool_subdir = "tmalign" if use_tmalign else "foldseek"
        output_dir = os.path.join(base_output, tool_subdir, run_id)
        safe_dir(output_dir)
        pairs_dir = os.path.join(output_dir, "pair")
        json_dir = os.path.join(output_dir, "json_results")
        logs_dir = os.path.join(output_dir, "logs")
        safe_dir(pairs_dir)
        safe_dir(json_dir)
        safe_dir(logs_dir)
        tmalign_exe = _find_tmalign() if use_tmalign else None
        foldseek_exe = _find_foldseek() if not use_tmalign else None
        if use_tmalign and not tmalign_exe:
            return {"success": False, "error": "TM-align chosen but TMalign not found. Install: conda install -c bioconda tmalign.", "data": {}}
        if not use_tmalign and not foldseek_exe:
            return {"success": False, "error": "Foldseek chosen but foldseek not found. Install: conda install -c bioconda foldseek.", "data": {}}

        # Chunk pairs (PAIRS_PER_CHUNK per chunk)
        chunks = []
        for start in range(0, total_pairs, PAIRS_PER_CHUNK):
            chunks.append(pairs[start : start + PAIRS_PER_CHUNK])

        all_results = []
        if use_tmalign:
            for chunk_id, chunk in enumerate(chunks):
                chunk_results = _run_tmalign_chunk_parallel(
                    chunk, tmalign_exe, n_cores, use_parallel_cli, pairs_dir, chunk_id,
                )
                all_results.extend(chunk_results)
        else:
            try:
                foldseek_results, _ = _run_foldseek_all_vs_all(input_dir, output_dir, foldseek_exe, run_id)
                all_results = foldseek_results
            except Exception as e:
                return {"success": False, "error": f"Foldseek all-vs-all failed: {e}", "data": {}}

        # Write combined JSON
        json_path = os.path.join(json_dir, f"alignments_{run_id}.json")
        with open(json_path, "w") as f:
            json.dump(all_results, f, indent=2)
        ensure_file_permissions(json_path)

        return {
            "success": True,
            "data": {
                "message": f"Batch alignment completed. Total pairs: {total_pairs}, tool: {'TM-align' if use_tmalign else 'Foldseek'}.",
                "total_pairs": total_pairs,
                "total_structures": total_files,
                "tool_used": "tmalign" if use_tmalign else "foldseek",
                "run_dir": os.path.abspath(output_dir),
                "n_chunks": len(chunks),
                "json_path": os.path.abspath(json_path),
                "downloaded": {"json": [os.path.abspath(json_path)]},
            },
        }
