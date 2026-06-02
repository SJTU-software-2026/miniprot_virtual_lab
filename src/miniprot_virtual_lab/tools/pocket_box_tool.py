"""
Pocket / box picker for AutoDock Vina.

Goal: compute a reasonable Vina search box (center_x/y/z and size_x/y/z) from:
- a co-crystallized ligand inside the receptor PDB (by residue name), OR
- a provided ligand PDB/SDF file, OR
- receptor geometry (fallback).

PocketPickerTool extends this with binding pocket / active-site prediction using:
- p2rank: P2Rank (DEFAULT, highest priority) — ML-based ligand-binding site prediction.
  Requires Java 17+ and the P2Rank distribution (https://github.com/rdk/p2rank).
- fpocket: fpocket CLI (Voronoi-based pocket detection; requires fpocket installed).
- dogsite_api: DoGSiteScorer via Proteins Plus REST API.
- geometry: ligand-based or receptor-based box + active residues in box (no external tools).

This follows the Vina requirement that the user must define the search space (center + size):
https://autodock-vina.readthedocs.io/en/latest/docking_basic.html
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import glob
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/docking"

# Proteins Plus API (DoGSiteScorer); see https://www.proteins.plus/help/dogsite_rest
PROTEINS_PLUS_API_BASE = "https://proteins.plus/api"

try:
    from utils.path_utils import safe_dir, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, resolve_output_dir, ensure_file_permissions


def _find_latest_structure_pdb(workspace_root: str) -> Optional[str]:
    """Best-effort: find the newest .pdb produced by structure tools in this workspace."""
    candidates: List[str] = []
    for sub in ("data/outputs/structure_from_fasta", "data/outputs/alphafold"):
        base = os.path.join(workspace_root, sub)
        if not os.path.isdir(base):
            continue
        # include nested run dirs
        candidates.extend(glob.glob(os.path.join(base, "**", "*.pdb"), recursive=True))
    existing = [p for p in candidates if os.path.isfile(p)]
    if not existing:
        return None
    existing.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return existing[0]


def _extract_coords_from_pdb(path: str, only_hetatm: bool = False, resname: Optional[str] = None) -> List[Tuple[float, float, float]]:
    coords: List[Tuple[float, float, float]] = []
    resname = (resname or "").strip().upper() or None
    try:
        with open(path, "r") as f:
            for line in f:
                if only_hetatm and not line.startswith("HETATM"):
                    continue
                if not line.startswith(("ATOM  ", "HETATM")):
                    continue
                if resname:
                    rn = (line[17:20] or "").strip().upper()
                    if rn != resname:
                        continue
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                    coords.append((x, y, z))
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        logger.warning("Failed to parse coords from %s: %s", path, e)
    return coords


def _box_from_coords(
    coords: List[Tuple[float, float, float]],
    padding: float = 5.0,
    min_size: Tuple[float, float, float] = (20.0, 20.0, 20.0),
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    if not coords:
        return ((0.0, 0.0, 0.0), min_size)
    n = len(coords)
    cx = sum(c[0] for c in coords) / n
    cy = sum(c[1] for c in coords) / n
    cz = sum(c[2] for c in coords) / n
    min_x = min(c[0] for c in coords)
    max_x = max(c[0] for c in coords)
    min_y = min(c[1] for c in coords)
    max_y = max(c[1] for c in coords)
    min_z = min(c[2] for c in coords)
    max_z = max(c[2] for c in coords)
    sx = max(min_size[0], (max_x - min_x) + 2 * padding)
    sy = max(min_size[1], (max_y - min_y) + 2 * padding)
    sz = max(min_size[2], (max_z - min_z) + 2 * padding)
    return ((cx, cy, cz), (sx, sy, sz))


def _get_residues_in_box(
    pdb_path: str,
    center: Tuple[float, float, float],
    size: Tuple[float, float, float],
    first_model_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Return list of residues that have at least one atom inside the box.
    Each item: {"chain": str, "resSeq": str, "resName": str, "key": "chain:resSeq"}.
    Used to report active-site / binding-pocket residues for AutoDock.
    """
    cx, cy, cz = center
    half = (size[0] / 2, size[1] / 2, size[2] / 2)
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    in_first = True
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith("MODEL"):
                    in_first = True
                    continue
                if line.startswith("ENDMDL"):
                    if first_model_only:
                        break
                    continue
                if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
                    continue
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                    if abs(x - cx) <= half[0] and abs(y - cy) <= half[1] and abs(z - cz) <= half[2]:
                        ch = (line[21:22] or " ").strip() or " "
                        res_seq = (line[22:26] or "    ").strip()
                        res_name = (line[17:20] or "   ").strip()
                        key = (ch, res_seq)
                        if key not in seen:
                            seen[key] = {"chain": ch, "resSeq": res_seq, "resName": res_name, "key": f"{ch}:{res_seq}"}
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        logger.warning("get_residues_in_box failed %s: %s", pdb_path, e)
    return list(seen.values())


def _write_vina_config(path: str, center: Tuple[float, float, float], size: Tuple[float, float, float]) -> None:
    cx, cy, cz = center
    sx, sy, sz = size
    with open(path, "w") as f:
        f.write(f"center_x = {cx:.4f}\n")
        f.write(f"center_y = {cy:.4f}\n")
        f.write(f"center_z = {cz:.4f}\n")
        f.write(f"size_x = {sx:.1f}\n")
        f.write(f"size_y = {sy:.1f}\n")
        f.write(f"size_z = {sz:.1f}\n")
    ensure_file_permissions(path)


# ---------- P2Rank backend ----------

# P2Rank distribution path; auto-detected or overridden via env var P2RANK_HOME.
_P2RANK_HOME_DEFAULT = os.path.expanduser("~/tools/p2rank_2.5.1")

def _find_p2rank() -> Optional[str]:
    """Return path to the P2Rank `prank` executable, or None."""
    p2rank_home = os.environ.get("P2RANK_HOME", "").strip() or _P2RANK_HOME_DEFAULT
    prank = os.path.join(p2rank_home, "prank")
    if os.path.isfile(prank) and os.access(prank, os.X_OK):
        return prank
    return shutil.which("prank") or shutil.which("p2rank")


def _p2rank_subprocess_env() -> Dict[str, str]:
    """Env for P2Rank: ensure Java 17+ is on PATH (from conda/micromamba env if present)."""
    env = dict(os.environ)
    prefix = os.environ.get("MINIPROT_ENV") or os.environ.get("CONDA_PREFIX")
    if prefix:
        bin_dir = os.path.join(prefix, "bin")
        if os.path.isdir(bin_dir):
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _run_p2rank(
    pdb_path: str,
    work_dir: str,
    use_alphafold_config: bool = True,
    threads: int = 2,
    timeout_sec: int = 120,
) -> Tuple[bool, str, Optional[str]]:
    """Run P2Rank predict on a single PDB.

    Returns (success, error_message, predictions_csv_path).
    Uses -c alphafold config by default (best for AlphaFold models / NMR / cryo-EM since it
    doesn't rely on B-factor).
    """
    exe = _find_p2rank()
    if not exe:
        return False, "P2Rank not found. Install: download from https://github.com/rdk/p2rank/releases and set P2RANK_HOME.", None
    abs_pdb = os.path.abspath(pdb_path)
    base = os.path.splitext(os.path.basename(pdb_path))[0]
    cmd = [exe, "predict", "-f", abs_pdb, "-o", work_dir, "-threads", str(threads)]
    if use_alphafold_config:
        cmd.extend(["-c", "alphafold"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=_p2rank_subprocess_env(),
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout or f"P2Rank exited {result.returncode}", None
        # P2Rank writes <basename>.pdb_predictions.csv in the output dir
        csv_path = os.path.join(work_dir, f"{os.path.basename(pdb_path)}_predictions.csv")
        if not os.path.isfile(csv_path):
            candidates = glob.glob(os.path.join(work_dir, "*_predictions.csv"))
            csv_path = candidates[0] if candidates else csv_path
        if os.path.isfile(csv_path):
            return True, "", csv_path
        return False, "P2Rank ran but predictions CSV not found.", None
    except subprocess.TimeoutExpired:
        return False, f"P2Rank timed out ({timeout_sec}s).", None
    except Exception as e:
        return False, str(e), None


def _parse_p2rank_predictions(
    csv_path: str,
    receptor_path: str,
    padding: float = 5.0,
    min_size: Tuple[float, float, float] = (20.0, 20.0, 20.0),
) -> List[Dict[str, Any]]:
    """Parse P2Rank _predictions.csv into a list of pockets with center, size, score, residues.

    CSV columns (whitespace-stripped):
      name, rank, score, probability, sas_points, surf_atoms, center_x, center_y, center_z,
      residue_ids, surf_atom_ids
    """
    pockets: List[Dict[str, Any]] = []
    try:
        with open(csv_path, "r") as f:
            header = f.readline()
            if not header:
                return pockets
            cols = [c.strip() for c in header.split(",")]
            for line in f:
                vals = [v.strip() for v in line.split(",")]
                if len(vals) < len(cols):
                    continue
                row = dict(zip(cols, vals))
                try:
                    cx = float(row.get("center_x", "0"))
                    cy = float(row.get("center_y", "0"))
                    cz = float(row.get("center_z", "0"))
                except (ValueError, KeyError):
                    continue
                center = (cx, cy, cz)
                score = float(row.get("score", "0") or "0")
                probability = float(row.get("probability", "0") or "0")
                rank = int(row.get("rank", "0") or "0")
                # Residue IDs look like "A_197 A_198 A_200"; convert to "A:197" format
                raw_res = row.get("residue_ids", "")
                residue_ids = []
                for r in raw_res.split():
                    r = r.strip()
                    if "_" in r:
                        parts = r.split("_", 1)
                        residue_ids.append(f"{parts[0]}:{parts[1]}")
                    elif r:
                        residue_ids.append(r)
                # Compute a Vina-appropriate box size from the residues (P2Rank gives center but not box size)
                if residue_ids and receptor_path and os.path.isfile(receptor_path):
                    res_coords = _extract_coords_for_residue_ids(receptor_path, raw_res.split())
                    if res_coords:
                        _, size = _box_from_coords(res_coords, padding=padding, min_size=min_size)
                    else:
                        size = min_size
                else:
                    size = min_size
                pockets.append({
                    "pocket_id": str(rank),
                    "center": center,
                    "size": size,
                    "score": score,
                    "probability": probability,
                    "active_residues": residue_ids,
                    "raw_residue_ids": raw_res.strip(),
                })
    except Exception as e:
        logger.warning("Failed to parse P2Rank predictions %s: %s", csv_path, e)
    pockets.sort(key=lambda p: p.get("score", 0), reverse=True)
    return pockets


def _extract_coords_for_residue_ids(
    pdb_path: str,
    residue_tokens: List[str],
) -> List[Tuple[float, float, float]]:
    """Extract ATOM coordinates for residues identified by P2Rank tokens like 'A_197', 'A_48'.

    Used to compute a box size from the pocket residues (P2Rank gives center but not box dimensions).
    """
    targets: set = set()
    for tok in residue_tokens:
        tok = tok.strip()
        if "_" in tok:
            parts = tok.split("_", 1)
            targets.add((parts[0].strip(), parts[1].strip()))
    if not targets:
        return []
    coords: List[Tuple[float, float, float]] = []
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
                    continue
                ch = (line[21:22] or " ").strip() or " "
                seq = (line[22:26] or "    ").strip()
                if (ch, seq) in targets:
                    try:
                        x = float(line[30:38])
                        y = float(line[38:46])
                        z = float(line[46:54])
                        coords.append((x, y, z))
                    except (ValueError, IndexError):
                        continue
    except Exception:
        pass
    return coords


# ---------- fpocket backend ----------


def _find_fpocket() -> Optional[str]:
    """Return path to fpocket executable or None."""
    return shutil.which("fpocket") or shutil.which("fpocket2")


def _run_fpocket(pdb_path: str, work_dir: str, timeout_sec: int = 120) -> Tuple[bool, str, Optional[str]]:
    """
    Run fpocket -f <pdb>. Returns (success, message, output_dir).
    fpocket creates <basename>_out/ in the same directory as the input.
    """
    exe = _find_fpocket()
    if not exe:
        return False, "fpocket not found. Install e.g. conda install -c conda-forge fpocket", None
    base = os.path.splitext(os.path.basename(pdb_path))[0]
    # Run from work_dir so _out is created there; use abs path for -f
    abs_pdb = os.path.abspath(pdb_path)
    try:
        result = subprocess.run(
            [exe, "-f", abs_pdb],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout or f"fpocket exited {result.returncode}", None
        # fpocket writes <name>_out in the dir of the input file, not cwd
        possible_out = os.path.join(os.path.dirname(abs_pdb), f"{base}_out")
        if not os.path.isdir(possible_out):
            possible_out = os.path.join(work_dir, f"{base}_out")
        if os.path.isdir(possible_out):
            return True, "", possible_out
        return False, "fpocket ran but output dir not found", None
    except subprocess.TimeoutExpired:
        return False, "fpocket timed out", None
    except Exception as e:
        return False, str(e), None


def _parse_fpocket_output(out_dir: str) -> List[Dict[str, Any]]:
    """
    Parse fpocket output dir. Return list of pockets, each with center, size, coords, score.
    Pockets are in pockets/ subdir as pocket*_atm.pdb; info in info.txt or similar.
    """
    pockets: List[Dict[str, Any]] = []
    pockets_dir = os.path.join(out_dir, "pockets")
    if not os.path.isdir(pockets_dir):
        # Some fpocket versions put pocket PDBs directly in _out
        pockets_dir = out_dir
    pdb_files = sorted(glob.glob(os.path.join(pockets_dir, "*_atm.pdb")) or glob.glob(os.path.join(pockets_dir, "pocket*.pdb")))
    if not pdb_files:
        pdb_files = [f for f in glob.glob(os.path.join(out_dir, "*.pdb")) if "pocket" in os.path.basename(f).lower()]
    scores: Dict[str, float] = {}
    info_path = os.path.join(out_dir, "info.txt")
    if os.path.isfile(info_path):
        try:
            with open(info_path, "r") as f:
                for line in f:
                    # Typical: Pocket 1 : 0.45 (druggability or score)
                    m = re.search(r"Pocket\s+(\d+)\s*[:\s]+([\d.]+)", line, re.I)
                    if m:
                        scores[m.group(1)] = float(m.group(2))
        except Exception:
            pass
    for i, pdb_file in enumerate(pdb_files):
        coords = _extract_coords_from_pdb(pdb_file, only_hetatm=False, resname=None)
        if not coords:
            continue
        center, size = _box_from_coords(coords, padding=5.0, min_size=(15.0, 15.0, 15.0))
        pocket_id = str(i + 1)
        pockets.append({
            "pocket_id": pocket_id,
            "center": center,
            "size": size,
            "coords": coords,
            "score": scores.get(pocket_id, 0.0),
            "pdb_path": pdb_file,
        })
    # Sort by score descending (higher = more druggable)
    pockets.sort(key=lambda p: p["score"], reverse=True)
    return pockets


# ---------- DoGSiteScorer API backend ----------


def _dogsite_upload_pdb(pdb_path: str) -> Tuple[bool, Optional[str], str]:
    """Upload PDB to Proteins Plus. Returns (success, structure_id, error_message)."""
    try:
        import requests
    except ImportError:
        return False, None, "requests not installed (pip install requests)"
    url = f"{PROTEINS_PLUS_API_BASE}/pdb_files_rest"
    try:
        with open(pdb_path, "rb") as f:
            r = requests.post(url, files={"pdb_file[pathvar]": (os.path.basename(pdb_path), f)}, timeout=60)
        if r.status_code in (200, 202):
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            sid = data.get("id") or data.get("structure_id") or (r.text.strip() if r.text else None)
            if sid:
                return True, str(sid).strip(), ""
            return False, None, "Upload succeeded but no id in response"
        if r.status_code == 429:
            return False, None, "Proteins Plus rate limit (429). Retry later."
        return False, None, f"Upload failed: {r.status_code} {r.text[:200]}"
    except Exception as e:
        return False, None, str(e)


def _dogsite_submit(structure_id: str) -> Tuple[bool, Optional[str], str]:
    """Submit DoGSiteScorer job. Returns (success, job_id, error_message)."""
    try:
        import requests
    except ImportError:
        return False, None, "requests not installed"
    # DoGSiteScorer REST: typically POST with structure reference
    url = f"{PROTEINS_PLUS_API_BASE}/dogsite_rest"
    try:
        r = requests.post(url, data={"pdb_id": structure_id}, timeout=30)
        if r.status_code in (200, 201, 202):
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            jid = data.get("job_id") or data.get("id") or data.get("job")
            if jid:
                return True, str(jid).strip(), ""
            return False, None, "Submit succeeded but no job_id in response"
        if r.status_code == 429:
            return False, None, "Proteins Plus rate limit (429). Retry later."
        return False, None, f"Submit failed: {r.status_code} {r.text[:200]}"
    except Exception as e:
        return False, None, str(e)


def _dogsite_get_result(job_id: str, poll_interval: float = 5.0, max_wait: float = 300.0) -> Tuple[bool, Optional[Dict], str]:
    """Poll DoGSiteScorer job and return (success, result_dict, error_message)."""
    try:
        import requests
    except ImportError:
        return False, None, "requests not installed"
    url = f"{PROTEINS_PLUS_API_BASE}/dogsite_rest/{job_id}"
    start = time.monotonic()
    while (time.monotonic() - start) < max_wait:
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                time.sleep(poll_interval)
                continue
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            status = (data.get("status") or "").lower()
            if status in ("failed", "error"):
                return False, None, data.get("message") or data.get("error") or "Job failed"
            if status in ("done", "success", "completed"):
                return True, data, ""
            time.sleep(poll_interval)
        except Exception as e:
            return False, None, str(e)
    return False, None, "DoGSiteScorer job timed out"


def _dogsite_parse_pockets(result: Dict[str, Any], receptor_path: str) -> List[Dict[str, Any]]:
    """
    Parse DoGSiteScorer API result into list of pockets with center, size, active_residues.
    Fallback: if no pockets in result, return single pocket from receptor geometry.
    """
    pockets: List[Dict[str, Any]] = []
    # Common API shapes: result['pockets'] or result['binding_sites'] or nested
    raw = result.get("pockets") or result.get("binding_sites") or result.get("results") or []
    if isinstance(raw, dict):
        raw = list(raw.values()) if raw else []
    for i, p in enumerate(raw) if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        center = p.get("center") or p.get("center_x") or []
        if isinstance(center, dict):
            center = (center.get("x"), center.get("y"), center.get("z"))
        if isinstance(center, (list, tuple)) and len(center) >= 3:
            center = (float(center[0]), float(center[1]), float(center[2]))
        else:
            center = None
        size = p.get("size") or p.get("size_x") or []
        if isinstance(size, dict):
            size = (size.get("x"), size.get("y"), size.get("z"))
        if isinstance(size, (list, tuple)) and len(size) >= 3:
            size = (float(size[0]), float(size[1]), float(size[2]))
        else:
            size = (20.0, 20.0, 20.0)
        residues = p.get("residues") or p.get("active_residues") or p.get("residue_list") or []
        if isinstance(residues, str):
            residues = [r.strip() for r in residues.split(",") if r.strip()]
        active = [r if isinstance(r, str) else f"{r.get('chain','')}:{r.get('resSeq', r.get('residue',''))}" for r in residues]
        if center:
            pockets.append({"pocket_id": str(i + 1), "center": center, "size": size, "active_residues": active})
    if not pockets and os.path.isfile(receptor_path):
        coords = _extract_coords_from_pdb(receptor_path, only_hetatm=False, resname=None)
        if coords:
            center, size = _box_from_coords(coords, padding=5.0)
            pockets = [{"pocket_id": "1", "center": center, "size": size, "active_residues": []}]
    return pockets


# ---------- PocketBoxTool (existing) ----------


class PocketBoxTool(BaseTool):
    def __init__(self):
        self._name = "pocket_box"
        self._description = (
            "Compute an AutoDock Vina search box (center and size). "
            "Use when user says 'pick pocket', 'find active site box', or when docking needs a box. "
            "Inputs: receptor_pdb_path and (optional) ligand_resname to use co-crystal ligand, "
            "or ligand_pdb_path to use an external ligand pose. Outputs: center_x/y/z and size_x/y/z, "
            "optionally writes a vina_box.txt config file."
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
                        "receptor_pdb_path": {"type": "string", "description": "Path to receptor PDB file."},
                        "ligand_resname": {"type": "string", "description": "Optional: 3-letter residue name of bound ligand in receptor PDB (e.g. ADO, LTR)."},
                        "ligand_pdb_path": {"type": "string", "description": "Optional: path to ligand PDB file (pose) to center the box on."},
                        "padding": {"type": "number", "description": "Padding added around coords in Angstrom (default 5).", "default": 5.0},
                        "min_size": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Minimum box size [x,y,z] in Angstrom (default [20,20,20]).",
                            "default": [20.0, 20.0, 20.0],
                        },
                        "output_dir": {"type": "string", "description": "Output dir for vina_box.txt (default data/outputs/docking).", "default": DEFAULT_OUTPUT_DIR},
                        "write_config": {"type": "boolean", "description": "Write vina_box.txt config file.", "default": True},
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        receptor = (kwargs.get("receptor_pdb_path") or "").strip()
        ligand_resname = (kwargs.get("ligand_resname") or "").strip()
        ligand_path = (kwargs.get("ligand_pdb_path") or "").strip()
        padding = float(kwargs.get("padding") or 5.0)
        min_size_raw = kwargs.get("min_size") or [20.0, 20.0, 20.0]
        try:
            min_size = (float(min_size_raw[0]), float(min_size_raw[1]), float(min_size_raw[2]))
        except Exception:
            min_size = (20.0, 20.0, 20.0)
        output_dir = safe_dir(resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip()))
        write_config = bool(kwargs.get("write_config", True))

        if not receptor or not os.path.isfile(receptor):
            # Try to recover from missing receptor by reusing the latest downloaded structure in this workspace.
            root = os.getcwd()
            found = _find_latest_structure_pdb(root)
            if found and os.path.isfile(found):
                receptor = found
            else:
                return {"success": False, "error": "receptor_pdb_path must exist.", "data": {}}

        coords: List[Tuple[float, float, float]] = []
        used = ""

        # 1) Use co-crystal ligand inside receptor (HETATM by residue name)
        if ligand_resname:
            coords = _extract_coords_from_pdb(receptor, only_hetatm=True, resname=ligand_resname)
            used = f"receptor HETATM resname={ligand_resname}"

        # 2) Use external ligand pose PDB (ATOM/HETATM)
        if not coords and ligand_path and os.path.isfile(ligand_path):
            coords = _extract_coords_from_pdb(ligand_path, only_hetatm=False, resname=None)
            used = "external ligand file"

        # 3) Fallback: receptor geometry
        if not coords:
            coords = _extract_coords_from_pdb(receptor, only_hetatm=False, resname=None)
            used = "receptor geometry (fallback)"

        center, size = _box_from_coords(coords, padding=padding, min_size=min_size)
        config_path = os.path.join(output_dir, "vina_box.txt")
        downloaded: Dict[str, List[str]] = {}
        if write_config:
            _write_vina_config(config_path, center, size)
            downloaded["config"] = [config_path]

        return {
            "success": True,
            "data": {
                "message": f"Computed Vina box from {used}.",
                "center_x": center[0],
                "center_y": center[1],
                "center_z": center[2],
                "size_x": size[0],
                "size_y": size[1],
                "size_z": size[2],
                "downloaded": downloaded,
                "output_dir": output_dir,
            },
        }


# ---------- PocketPickerTool (binding pocket + active residues → Vina inputs) ----------


class PocketPickerTool(BaseTool):
    """
    Identify binding pocket / active residues and provide AutoDock Vina inputs (center, size, vina_box.txt).
    Methods (priority order):
      p2rank (default): ML-based ligand-binding site prediction — most accurate, recommended.
      fpocket: Voronoi-based pocket detection (CLI).
      dogsite_api: DoGSiteScorer via Proteins Plus REST API.
      geometry: ligand-based or receptor-based box (no external tools, lowest accuracy).
    """

    def __init__(self):
        self._name = "pocket_picker"
        self._description = (
            "Predict ligand-binding sites on a receptor and output AutoDock Vina search box (center/size). "
            "MUST be called before autodock_vina when the user has not specified box coordinates. "
            "Default method: p2rank (machine learning; highest accuracy). "
            "Methods: p2rank (default, recommended), fpocket (CLI), dogsite_api (Proteins Plus API), geometry (fallback). "
            "Inputs: receptor_pdb_path, method (p2rank|fpocket|dogsite_api|geometry), optional ligand_resname or ligand_pdb_path for geometry. "
            "Outputs: center_x/y/z, size_x/y/z, active_residues list, vina_box.txt path for use with autodock_vina."
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
                        "receptor_pdb_path": {"type": "string", "description": "Path to receptor PDB file."},
                        "method": {
                            "type": "string",
                            "enum": ["p2rank", "fpocket", "dogsite_api", "geometry"],
                            "description": "Pocket detection method: p2rank (default, ML-based, highest accuracy), fpocket (CLI), dogsite_api (Proteins Plus API), geometry (fallback).",
                            "default": "p2rank",
                        },
                        "ligand_resname": {"type": "string", "description": "Optional: 3-letter residue name of bound ligand in receptor (for geometry method)."},
                        "ligand_pdb_path": {"type": "string", "description": "Optional: path to ligand PDB (for geometry method)."},
                        "padding": {"type": "number", "description": "Padding around pocket in Angstrom (default 5).", "default": 5.0},
                        "min_size": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Minimum box size [x,y,z] Angstrom (default [20,20,20]).",
                            "default": [20.0, 20.0, 20.0],
                        },
                        "pocket_index": {"type": "integer", "description": "When multiple pockets (fpocket/dogsite): use this 1-based index (default 1 = best).", "default": 1},
                        "output_dir": {"type": "string", "description": "Output dir for vina_box.txt.", "default": DEFAULT_OUTPUT_DIR},
                        "write_config": {"type": "boolean", "description": "Write vina_box.txt for AutoDock Vina.", "default": True},
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        receptor = (kwargs.get("receptor_pdb_path") or "").strip()
        method = (kwargs.get("method") or "p2rank").strip().lower() or "p2rank"
        if method not in ("p2rank", "geometry", "fpocket", "dogsite_api"):
            method = "p2rank"
        ligand_resname = (kwargs.get("ligand_resname") or "").strip()
        ligand_path = (kwargs.get("ligand_pdb_path") or "").strip()
        padding = float(kwargs.get("padding") or 5.0)
        min_size_raw = kwargs.get("min_size") or [20.0, 20.0, 20.0]
        try:
            min_size = (float(min_size_raw[0]), float(min_size_raw[1]), float(min_size_raw[2]))
        except Exception:
            min_size = (20.0, 20.0, 20.0)
        pocket_index = max(1, int(kwargs.get("pocket_index") or 1))
        output_dir = safe_dir(resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip()))
        write_config = bool(kwargs.get("write_config", True))

        if not receptor or not os.path.isfile(receptor):
            root = os.getcwd()
            found = _find_latest_structure_pdb(root)
            if found and os.path.isfile(found):
                receptor = found
            else:
                return {"success": False, "error": "receptor_pdb_path must exist.", "data": {}}

        center: Optional[Tuple[float, float, float]] = None
        size: Optional[Tuple[float, float, float]] = None
        active_residues: List[str] = []
        method_used = method
        message = ""

        # Cascading fallback chain: p2rank → fpocket → geometry.
        # Each method attempts execution; on failure it falls through to the next.
        # dogsite_api is standalone (API-based; no local fallback).

        # --- P2Rank (ML-based, default, highest accuracy) ---
        if method == "p2rank" and center is None:
            p2rank_dir = os.path.join(output_dir, "p2rank")
            safe_dir(p2rank_dir)
            ok, err, csv_path = _run_p2rank(receptor, p2rank_dir)
            if ok and csv_path:
                pockets = _parse_p2rank_predictions(csv_path, receptor, padding=padding, min_size=min_size)
                if pockets:
                    idx = min(pocket_index, len(pockets))
                    p = pockets[idx - 1]
                    center = p["center"]
                    size = p["size"]
                    active_residues = p.get("active_residues") or []
                    if not active_residues and center and size:
                        active_residues = [r["key"] for r in _get_residues_in_box(receptor, center, size)]
                    method_used = "p2rank"
                    message = (
                        f"P2Rank pocket {idx}/{len(pockets)} (score={p.get('score', 'N/A')}, "
                        f"probability={p.get('probability', 'N/A')}); {len(active_residues)} active residues."
                    )
                else:
                    logger.warning("P2Rank returned no pockets; trying fpocket.")
            else:
                logger.warning("P2Rank failed (%s); trying fpocket.", err)

        # --- fpocket (Voronoi-based) ---
        if method in ("p2rank", "fpocket") and center is None:
            ok, err, out_dir = _run_fpocket(receptor, output_dir)
            if ok and out_dir:
                pockets = _parse_fpocket_output(out_dir)
                if pockets:
                    idx = min(pocket_index, len(pockets))
                    p = pockets[idx - 1]
                    center = p["center"]
                    size = p["size"]
                    pocket_pdb = p.get("pdb_path")
                    if pocket_pdb and os.path.isfile(pocket_pdb):
                        active_residues = [r["key"] for r in _get_residues_in_box(receptor, center, size)]
                    method_used = "fpocket" if method == "fpocket" else "fpocket (P2Rank fallback)"
                    message = f"fpocket pocket {idx}/{len(pockets)} (score={p.get('score', 'N/A')}); {len(active_residues)} residues in box."
                else:
                    logger.warning("fpocket found no pockets; falling back to geometry.")
            else:
                logger.warning("fpocket failed (%s); falling back to geometry.", err)

        # --- DoGSiteScorer API (standalone, no fallback chain) ---
        if method == "dogsite_api" and center is None:
            ok, sid, err = _dogsite_upload_pdb(receptor)
            if not ok:
                return {"success": False, "error": f"DoGSiteScorer upload: {err}", "data": {}}
            ok, job_id, err = _dogsite_submit(sid)
            if not ok:
                return {"success": False, "error": f"DoGSiteScorer submit: {err}", "data": {}}
            ok, result, err = _dogsite_get_result(job_id)
            if not ok or not result:
                return {"success": False, "error": f"DoGSiteScorer result: {err}", "data": {}}
            pockets = _dogsite_parse_pockets(result, receptor)
            if not pockets:
                return {"success": False, "error": "DoGSiteScorer returned no pockets", "data": {}}
            idx = min(pocket_index, len(pockets))
            p = pockets[idx - 1]
            center = p["center"]
            size = p["size"]
            active_residues = p.get("active_residues") or []
            if not active_residues and center and size:
                active_residues = [r["key"] for r in _get_residues_in_box(receptor, center, size)]
            method_used = "dogsite_api"
            message = f"DoGSiteScorer API pocket {idx}/{len(pockets)}; {len(active_residues)} active residues."

        # --- geometry (final fallback for p2rank/fpocket chain, or explicit method) ---
        if center is None:
            coords: List[Tuple[float, float, float]] = []
            used = ""
            if ligand_resname:
                coords = _extract_coords_from_pdb(receptor, only_hetatm=True, resname=ligand_resname)
                used = f"co-crystal ligand resname={ligand_resname}"
            if not coords and ligand_path and os.path.isfile(ligand_path):
                coords = _extract_coords_from_pdb(ligand_path, only_hetatm=False, resname=None)
                used = "external ligand file"
            if not coords:
                coords = _extract_coords_from_pdb(receptor, only_hetatm=False, resname=None)
                used = "receptor geometry (fallback)"
            center, size = _box_from_coords(coords, padding=padding, min_size=min_size)
            active_residues = [r["key"] for r in _get_residues_in_box(receptor, center, size)]
            method_used = method_used if "fallback" in method_used else "geometry"
            message = f"Pocket from {used}; {len(active_residues)} residues in box."

        if center is None or size is None:
            return {"success": False, "error": "Could not compute pocket box", "data": {}}

        config_path = os.path.join(output_dir, "vina_box.txt")
        downloaded: Dict[str, List[str]] = {}
        if write_config:
            _write_vina_config(config_path, center, size)
            downloaded["config"] = [config_path]

        return {
            "success": True,
            "data": {
                "message": message,
                "method_used": method_used,
                "center_x": center[0],
                "center_y": center[1],
                "center_z": center[2],
                "size_x": size[0],
                "size_y": size[1],
                "size_z": size[2],
                "active_residues": active_residues,
                "active_residue_count": len(active_residues),
                "downloaded": downloaded,
                "output_dir": output_dir,
                "vina_config_path": config_path if write_config else None,
            },
        }
