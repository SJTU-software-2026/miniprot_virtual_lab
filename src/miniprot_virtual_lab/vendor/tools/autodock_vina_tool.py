"""
AutoDock Vina docking tool: protein–protein or protein–ligand molecular docking.
Follows AutoDock Vina docs: https://autodock-vina.readthedocs.io (basic, config file, exhaustiveness).
Receptor preparation is a mandatory step before docking: only apo protein (ATOM or HETATM only,
waters and optional bound ligands removed) goes to PDBQT and then Vina. Flow: fetch receptor
-> extract first model -> clean to apo (remove_water, optional remove_hetatm) -> prepare
receptor PDBQT -> prepare ligand PDBQT -> run Vina. Fetches PDBs via AlphaFold/UniProt when
needed; outputs receptor/ligand/docked PDBQT, config, log, energies CSV, interacting sites.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import csv
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
    from utils.pdb_utils import (
        RCSB_PDB_BASE,
        is_pdb_id,
        find_local_pdb,
        resolve_file_path,
        fetch_rcsb_file,
        merge_pdb_files,
    )
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
    from ..utils.pdb_utils import (
        RCSB_PDB_BASE,
        is_pdb_id,
        find_local_pdb,
        resolve_file_path,
        fetch_rcsb_file,
        merge_pdb_files,
    )

logger = logging.getLogger(__name__)

# Default output directory under project data
DEFAULT_OUTPUT_DIR = "data/outputs/docking"
# Search box defaults per AutoDock Vina docs (https://autodock-vina.readthedocs.io)
DEFAULT_BOX_CENTER = (0, 0, 0)
DEFAULT_BOX_SIZE = (20, 20, 20)
DEFAULT_BOX_SIZE_AUTO = (30, 30, 30)
DEFAULT_EXHAUSTIVENESS = 32
# Vina limits / PPI-detection heuristics
# AutoDock Vina supports up to 32 active torsions per ligand (docs: "The number of torsions should not exceed 32.").
VINA_MAX_TORSIONS = 32
# Empirical guardrails to detect protein-protein docking attempts so we fail fast with a useful message
# instead of sending Vina a ligand it can't handle (and getting a silent OOM kill).
PPI_LIGAND_ATOM_THRESHOLD = 500  # above this, the "ligand" is almost certainly a protein
PPI_LIGAND_RESIDUE_THRESHOLD = 30  # standalone proteins typically have > 30 residues; most small-molecule ligands don't
# Vina's grid memory scales with (size_x * size_y * size_z / grid_space^3). At 0.375 Å spacing
# a 60x60x60 Å box already needs > 3 M grid points per map — capped to protect low-RAM systems.
MAX_BOX_DIM = 40.0   # hard cap per axis in Å (keeps grid allocation manageable)
MAX_BOX_VOLUME = 50000.0  # hard cap on box volume (Å^3)


def _docking_subprocess_env() -> Dict[str, str]:
    """Env for vina/obabel subprocesses: prepend conda/miniprot bin so the right executables are used."""
    env = dict(os.environ)
    prefix = os.environ.get("MINIPROT_ENV") or os.environ.get("CONDA_PREFIX")
    if prefix:
        bin_dir = os.path.join(prefix, "bin")
        if os.path.isdir(bin_dir):
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _fetch_pdb_by_id(pdb_id: str, output_dir: str) -> Optional[str]:
    """Download a structure by PDB ID from RCSB. Returns path to saved PDB or None."""
    pdb_id = (pdb_id or "").strip().upper()[:4]
    if not pdb_id or not is_pdb_id(pdb_id):
        return None
    safe_dir(output_dir)
    out_path = os.path.join(output_dir, f"{pdb_id}.pdb")
    if fetch_rcsb_file(f"{RCSB_PDB_BASE}/{pdb_id}.pdb", out_path):
        return out_path
    return None


def _compute_receptor_box(pdb_path: str, padding: float = 5.0, default_size: Tuple[float, float, float] = DEFAULT_BOX_SIZE_AUTO) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """
    Compute search box center and size from receptor PDB (ATOM/HETATM coordinates).
    Used when user does not specify center/size; per Vina docs the box should encompass the binding site.
    Returns (center, size). On parse failure returns (0,0,0) and default_size.
    """
    coords: List[Tuple[float, float, float]] = []
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if not line.startswith(("ATOM  ", "HETATM")):
                    continue
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                    coords.append((x, y, z))
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        logger.warning("Compute receptor box failed %s: %s", pdb_path, e)
        return (DEFAULT_BOX_CENTER, default_size)
    if not coords:
        return (DEFAULT_BOX_CENTER, default_size)
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
    sx = max(default_size[0], (max_x - min_x) + 2 * padding)
    sy = max(default_size[1], (max_y - min_y) + 2 * padding)
    sz = max(default_size[2], (max_z - min_z) + 2 * padding)
    return ((cx, cy, cz), (sx, sy, sz))


def _count_atoms_and_residues(pdb_path: str) -> Tuple[int, int]:
    """Return (atom_count, residue_count) for the first MODEL of a PDB file. (0, 0) on failure."""
    atoms = 0
    residues: set = set()
    try:
        with open(pdb_path, "r") as f:
            in_first = True
            for line in f:
                if line.startswith("MODEL"):
                    if not in_first:
                        break
                    continue
                if line.startswith("ENDMDL"):
                    break
                if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 27:
                    continue
                atoms += 1
                ch = line[21:22]
                seq = line[22:26].strip()
                icode = line[26:27]
                residues.add((ch, seq, icode))
    except Exception as e:
        logger.debug("Count atoms/residues failed %s: %s", pdb_path, e)
    return atoms, len(residues)


def _looks_like_protein(pdb_path: str) -> bool:
    """Heuristic: a PDB/SDF input is likely a full protein (vs small-molecule ligand) if it
    has many atoms AND many residues. Used to detect PPI docking attempts early.
    SDF files are treated as small-molecule by construction.
    """
    if not pdb_path or not os.path.isfile(pdb_path):
        return False
    if pdb_path.lower().endswith(".sdf"):
        return False
    atoms, residues = _count_atoms_and_residues(pdb_path)
    return atoms >= PPI_LIGAND_ATOM_THRESHOLD and residues >= PPI_LIGAND_RESIDUE_THRESHOLD


def _count_torsions_in_pdbqt(pdbqt_path: str) -> int:
    """Parse the TORSDOF value from a ligand PDBQT file. Returns 0 when absent."""
    if not pdbqt_path or not os.path.isfile(pdbqt_path):
        return 0
    try:
        with open(pdbqt_path, "r") as f:
            for line in f:
                if line.startswith("TORSDOF"):
                    try:
                        return int(line.split()[1])
                    except (IndexError, ValueError):
                        return 0
    except Exception:
        return 0
    return 0


def _wrap_rigid_ligand_pdbqt(in_path: str, out_path: str) -> bool:
    """Take a bare-atom PDBQT (Open Babel `-xr` output) and wrap it with ROOT/ENDROOT/TORSDOF 0
    so Vina will accept it as a rigid ligand. Returns True on success.
    Used when docking a protein-shaped ligand (PPI) to avoid Vina's 32-torsion limit.
    """
    try:
        with open(in_path, "r") as f:
            lines = f.readlines()
        remark = [line for line in lines if line.startswith("REMARK")]
        atoms = [line for line in lines if line.startswith(("ATOM  ", "HETATM"))]
        if not atoms:
            return False
        with open(out_path, "w") as f:
            f.writelines(remark)
            f.write("ROOT\n")
            f.writelines(atoms)
            f.write("ENDROOT\n")
            f.write("TORSDOF 0\n")
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logger.warning("Wrap rigid ligand PDBQT failed: %s", e)
    return False


def _cap_box_size(
    size: Tuple[float, float, float],
    max_dim: float = MAX_BOX_DIM,
    max_volume: float = MAX_BOX_VOLUME,
) -> Tuple[Tuple[float, float, float], bool]:
    """Shrink the docking box so Vina's grid memory stays bounded on commodity hardware.
    Returns (new_size, was_capped). Enforces per-axis max and overall volume cap,
    scaling axes proportionally when the volume is too large.
    """
    sx, sy, sz = (float(size[0]), float(size[1]), float(size[2]))
    orig = (sx, sy, sz)
    sx = min(sx, max_dim)
    sy = min(sy, max_dim)
    sz = min(sz, max_dim)
    vol = sx * sy * sz
    if vol > max_volume and vol > 0:
        scale = (max_volume / vol) ** (1.0 / 3.0)
        sx = max(DEFAULT_BOX_SIZE[0], sx * scale)
        sy = max(DEFAULT_BOX_SIZE[1], sy * scale)
        sz = max(DEFAULT_BOX_SIZE[2], sz * scale)
    capped = (round(sx, 1), round(sy, 1), round(sz, 1)) != (round(orig[0], 1), round(orig[1], 1), round(orig[2], 1))
    return ((sx, sy, sz), capped)


def _find_executable(name: str) -> Optional[str]:
    """Return path to executable or None."""
    return shutil.which(name)


def check_docking_tools() -> Dict[str, Any]:
    """
    Verify that AutoDock Vina and Open Babel are installed and runnable.
    Returns a dict with keys 'vina' and 'obabel'; each value is a dict with
    'found' (bool), 'path' (str or None), and 'version_ok' (bool, True if executable ran successfully).
    Use at startup to inform the user before attempting docking.
    """
    result: Dict[str, Any] = {
        "vina": {"found": False, "path": None, "version_ok": False},
        "obabel": {"found": False, "path": None, "version_ok": False},
    }
    # Vina
    vina_path = _find_executable("vina")
    if vina_path:
        result["vina"]["found"] = True
        result["vina"]["path"] = vina_path
        try:
            r = subprocess.run([vina_path, "--version"], capture_output=True, text=True, timeout=5)
            result["vina"]["version_ok"] = r.returncode == 0
        except Exception:
            result["vina"]["version_ok"] = False
    # Open Babel: use -V (capital V); some builds don't support --version
    obabel_path = _find_executable("obabel")
    if obabel_path:
        result["obabel"]["found"] = True
        result["obabel"]["path"] = obabel_path
        try:
            r = subprocess.run([obabel_path, "-V"], capture_output=True, text=True, timeout=5, env=_docking_subprocess_env())
            result["obabel"]["version_ok"] = r.returncode == 0
        except Exception:
            result["obabel"]["version_ok"] = False
    return result


def _clean_pdb_atoms(
    path: str,
    out_path: str,
    remove_water: bool = True,
    keep_chains: Optional[List[str]] = None,
    remove_hetatm: bool = False,
) -> bool:
    """
    Write a docking-ready PDB using shared cleaner: remove waters, optional HETATM,
    keep first altloc, 80-char lines. Returns True on success.
    """
    try:
        try:
            from utils.pdb_clean import clean_pdb_for_docking
        except ImportError:
            from ..utils.pdb_clean import clean_pdb_for_docking
        return clean_pdb_for_docking(
            path,
            out_path,
            remove_hetatm=remove_hetatm,
            remove_water=remove_water,
            keep_first_altloc=True,
            keep_chains=keep_chains,
        )
    except Exception as e:
        logger.warning("Clean PDB failed %s: %s", path, e)
        return False


def _prepare_receptor_meeko(pdb_path: str, work_dir: str, basename: str, box_center: Tuple[float, float, float], box_size: Tuple[float, float, float]) -> Tuple[bool, str, Optional[str]]:
    """
    Prepare receptor PDBQT using Meeko (mk_prepare_receptor.py). work_dir=output dir, basename=receptor.
    Returns (success, error_msg, pdbqt_path). See https://autodock-vina.readthedocs.io/en/latest/docking_basic.html
    """
    exe = _find_executable("mk_prepare_receptor.py")
    if not exe:
        return False, "Meeko not found (mk_prepare_receptor.py). Install: pip install meeko", None
    try:
        cx, cy, cz = box_center
        sx, sy, sz = box_size
        cmd = [
            exe, "-i", os.path.abspath(pdb_path), "-o", basename, "-p", "-v",
            "--box_size", str(sx), str(sy), str(sz),
            "--box_center", str(cx), str(cy), str(cz),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=work_dir, env=_docking_subprocess_env())
        pdbqt = os.path.join(work_dir, basename + ".pdbqt")
        if result.returncode != 0 or not os.path.isfile(pdbqt):
            return False, result.stderr or result.stdout or "Meeko receptor failed", None
        return True, "", pdbqt
    except subprocess.TimeoutExpired:
        return False, "Meeko receptor timed out", None
    except Exception as e:
        return False, str(e), None


def _prepare_ligand_meeko(ligand_path: str, work_dir: str, basename: str) -> Tuple[bool, str, Optional[str]]:
    """Prepare ligand PDBQT using Meeko (mk_prepare_ligand.py). Returns (success, error_msg, pdbqt_path)."""
    exe = _find_executable("mk_prepare_ligand.py")
    if not exe:
        return False, "Meeko not found (mk_prepare_ligand.py). Install: pip install meeko", None
    try:
        out_name = basename + ".pdbqt"
        cmd = [exe, "-i", os.path.abspath(ligand_path), "-o", out_name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=work_dir, env=_docking_subprocess_env())
        pdbqt = os.path.join(work_dir, out_name)
        if result.returncode != 0 or not os.path.isfile(pdbqt):
            return False, result.stderr or result.stdout or "Meeko ligand failed", None
        return True, "", pdbqt
    except subprocess.TimeoutExpired:
        return False, "Meeko ligand timed out", None
    except Exception as e:
        return False, str(e), None


def _pdb_to_pdbqt_obabel(pdb_path: str, pdbqt_path: str, is_ligand: bool = False) -> Tuple[bool, str]:
    """
    Convert PDB or SDF to PDBQT using Open Babel.
    Receptor: rigid (-xr) + hydrogens (-h). Ligand: hydrogens (-h).
    Use explicit -i/-o so format is correct. Returns (success, error_message).
    """
    obabel = _find_executable("obabel")
    if not obabel:
        return False, "Open Babel (obabel) not found. Install: conda install -c conda-forge openbabel (or apt install openbabel)."
    inp = os.path.abspath(pdb_path)
    out = os.path.abspath(pdbqt_path)
    is_sdf = (pdb_path or "").lower().endswith(".sdf")
    # Explicit -i/-o format; receptor: rigid (-xr); both: hydrogens (-h)
    fmt_in = "sdf" if is_sdf else "pdb"
    cmd = [obabel, "-i", fmt_in, inp, "-o", "pdbqt"]
    if not is_ligand:
        cmd.append("-xr")
    cmd.extend(["-h", "-O", out])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=_docking_subprocess_env())
        if result.returncode != 0 or not os.path.isfile(pdbqt_path):
            return False, result.stderr or result.stdout or "obabel failed"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "obabel timed out"
    except Exception as e:
        return False, str(e)


def _sanitize_receptor_pdbqt(pdbqt_path: str) -> bool:
    """
    Rewrite receptor PDBQT to only ATOM/HETATM lines. Vina expects a rigid receptor with no
    ROOT/TORSDOF/BRANCH; some Meeko/obabel outputs can cause 'PDBQT parsing' errors otherwise.
    Returns True if file was written.
    """
    try:
        with open(pdbqt_path, "r") as f:
            lines = [line for line in f if line.startswith(("ATOM  ", "HETATM"))]
        if not lines:
            return False
        with open(pdbqt_path, "w") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        logger.warning("Sanitize receptor PDBQT failed: %s", e)
    return False


def _write_vina_config(config_path: str, center: Tuple[float, float, float], size: Tuple[float, float, float]) -> None:
    """Write Vina config file (center_x, center_y, center_z, size_x, size_y, size_z)."""
    cx, cy, cz = center
    sx, sy, sz = size
    with open(config_path, "w") as f:
        f.write("center_x = %.4f\n" % cx)
        f.write("center_y = %.4f\n" % cy)
        f.write("center_z = %.4f\n" % cz)
        f.write("size_x = %.1f\n" % sx)
        f.write("size_y = %.1f\n" % sy)
        f.write("size_z = %.1f\n" % sz)
    ensure_file_permissions(config_path)


def _parse_vina_log(log_path: str) -> List[Dict[str, Any]]:
    """Parse Vina log for mode, affinity (kcal/mol), rmsd l.b., rmsd u.b. (dist from best mode)."""
    rows = []
    try:
        with open(log_path, "r") as f:
            content = f.read()
        # Table: "   1       -7.2      0.000      0.000" (mode, affinity, rmsd_lb, rmsd_ub)
        pattern = re.compile(r"^\s*(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", re.MULTILINE)
        for m in pattern.finditer(content):
            rows.append({
                "mode": int(m.group(1)),
                "affinity_kcal_mol": float(m.group(2)),
                "rmsd_lb": float(m.group(3)),
                "rmsd_ub": float(m.group(4)),
            })
    except Exception as e:
        logger.warning("Parse vina log %s: %s", log_path, e)
    return rows


def _extract_first_model_pdb_lines(path: str) -> List[str]:
    """Read PDB/PDBQT and return ATOM/HETATM lines of the first MODEL (or entire file if no MODEL)."""
    lines = []
    in_first = True
    try:
        with open(path, "r") as f:
            for line in f:
                if line.startswith("MODEL"):
                    if in_first:
                        in_first = False
                    else:
                        break
                if line.startswith(("ATOM  ", "HETATM")):
                    lines.append(line)
                if line.startswith("ENDMDL") and not in_first:
                    break
    except Exception:
        pass
    return lines


def _get_chain_ids(pdb_path: str) -> List[str]:
    """Return sorted list of chain IDs (from ATOM/HETATM) in the first MODEL. Empty if parse fails."""
    chains = set()
    in_first = True
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith("MODEL"):
                    if in_first:
                        in_first = False
                    else:
                        break
                if line.startswith(("ATOM  ", "HETATM")) and len(line) >= 22:
                    ch = (line[21:22] or " ").strip()
                    if ch:
                        chains.add(ch)
                if line.startswith("ENDMDL") and not in_first:
                    break
        if not chains and not in_first:
            with open(pdb_path, "r") as f:
                for line in f:
                    if line.startswith(("ATOM  ", "HETATM")) and len(line) >= 22:
                        ch = (line[21:22] or " ").strip()
                        if ch:
                            chains.add(ch)
    except Exception:
        pass
    return sorted(chains)


def _count_atoms_in_box_by_chain(
    pdb_path: str,
    center: Tuple[float, float, float],
    size: Tuple[float, float, float],
) -> Dict[str, int]:
    """Count ATOM/HETATM per chain (first MODEL only) that fall inside the box. Used to pick active-site chain."""
    cx, cy, cz = center
    sx, sy, sz = size
    half = (sx / 2, sy / 2, sz / 2)
    counts: Dict[str, int] = {}
    in_first = True
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith("MODEL"):
                    in_first = True
                    continue
                if line.startswith("ENDMDL"):
                    break
                if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
                    continue
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                    if abs(x - cx) <= half[0] and abs(y - cy) <= half[1] and abs(z - cz) <= half[2]:
                        ch = (line[21:22] or " ").strip() or " "
                        counts[ch] = counts.get(ch, 0) + 1
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return counts


def _count_residues_in_box_by_chain(
    pdb_path: str,
    center: Tuple[float, float, float],
    size: Tuple[float, float, float],
) -> Dict[str, int]:
    """
    Count unique residues (first MODEL only) per chain that have at least one atom inside the box.
    Used to select the chain with the most interacting/pocket-lining residues when pockets
    span multiple chains or when there are multiple pockets.
    Returns Dict[chain_id, number_of_residues_in_pocket].
    """
    cx, cy, cz = center
    sx, sy, sz = size
    half = (sx / 2, sy / 2, sz / 2)
    # per chain: set of (resSeq, iCode) for residues that have an atom in the box
    residues_by_chain: Dict[str, set] = {}
    in_first = True
    try:
        with open(pdb_path, "r") as f:
            for line in f:
                if line.startswith("MODEL"):
                    in_first = True
                    continue
                if line.startswith("ENDMDL"):
                    break
                if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
                    continue
                try:
                    x = float(line[30:38])
                    y = float(line[38:46])
                    z = float(line[46:54])
                    if abs(x - cx) <= half[0] and abs(y - cy) <= half[1] and abs(z - cz) <= half[2]:
                        ch = (line[21:22] or " ").strip() or " "
                        res_seq = (line[22:26] or "    ").strip()
                        i_code = (line[26:27] or " ").strip()
                        key = (res_seq, i_code)
                        if ch not in residues_by_chain:
                            residues_by_chain[ch] = set()
                        residues_by_chain[ch].add(key)
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    return {ch: len(s) for ch, s in residues_by_chain.items()}


def _select_chain_for_active_pocket(
    pdb_path: str,
    center: Tuple[float, float, float],
    size: Tuple[float, float, float],
) -> Optional[str]:
    """
    For a multi-chain receptor: select the single chain to retain for docking based on the
    active pocket (box). (1) If one chain has the active site, that chain is chosen.
    (2) If multiple pockets exist, the chain containing the pocket is chosen.
    (3) If the pocket spans two or more chains, the chain with the highest number of
    interacting residues (residues with atoms in the box) is chosen; ties broken by atom count.
    Returns chain ID or None if selection fails.
    """
    residue_counts = _count_residues_in_box_by_chain(pdb_path, center, size)
    if not residue_counts:
        # Fallback: try larger box, then use atom count
        residue_counts = _count_residues_in_box_by_chain(
            pdb_path, center, (size[0] * 2, size[1] * 2, size[2] * 2)
        )
    if residue_counts:
        best_by_residues = max(residue_counts.keys(), key=lambda c: residue_counts[c])
        max_res = residue_counts[best_by_residues]
        # Ties: same number of interacting residues → use atom count as tie-breaker
        candidates = [c for c in residue_counts.keys() if residue_counts[c] == max_res]
        if len(candidates) == 1:
            return best_by_residues
        atom_counts = _count_atoms_in_box_by_chain(pdb_path, center, size)
        if not atom_counts:
            atom_counts = _count_atoms_in_box_by_chain(
                pdb_path, center, (size[0] * 2, size[1] * 2, size[2] * 2)
            )
        if atom_counts:
            return max(candidates, key=lambda c: atom_counts.get(c, 0))
        return best_by_residues
    # No residues in box: fall back to atom-based selection
    atom_counts = _count_atoms_in_box_by_chain(pdb_path, center, size)
    if not atom_counts:
        atom_counts = _count_atoms_in_box_by_chain(
            pdb_path, center, (size[0] * 2, size[1] * 2, size[2] * 2)
        )
    if not atom_counts:
        return None
    return max(atom_counts.keys(), key=lambda c: atom_counts[c])


def _retain_single_chain_near_box(
    pdb_path: str,
    center: Tuple[float, float, float],
    size: Tuple[float, float, float],
    out_path: str,
) -> bool:
    """
    Keep only the chain that contains the active pocket in the first MODEL. Selection: chain with
    most interacting residues (residues with atoms in box); if pocket spans chains, the chain with
    highest number of such residues is retained so a single chain is used for preparation and docking.
    Write result to out_path. Returns True on success.
    """
    best_chain = _select_chain_for_active_pocket(pdb_path, center, size)
    if not best_chain:
        return False
    try:
        in_first_model = True
        with open(pdb_path, "r") as f_in:
            with open(out_path, "w") as f_out:
                for line in f_in:
                    if line.startswith("MODEL"):
                        in_first_model = True
                        continue
                    if line.startswith("ENDMDL"):
                        in_first_model = False
                        break
                    if line.startswith(("ATOM  ", "HETATM")) and len(line) >= 22:
                        if not in_first_model:
                            continue
                        ch = (line[21:22] or " ").strip() or " "
                        if ch != best_chain:
                            continue
                        line_80 = line.rstrip("\n\r")
                        if len(line_80) > 80:
                            line_80 = line_80[:80]
                        elif len(line_80) < 80:
                            line_80 = line_80 + " " * (80 - len(line_80))
                        f_out.write(line_80 + "\n")
                    elif in_first_model and not line.startswith(("ATOM  ", "HETATM")):
                        line_80 = line.rstrip("\n\r")
                        if len(line_80) > 80:
                            line_80 = line_80[:80]
                        f_out.write(line_80 + "\n")
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            logger.info("Multi-chain structure: retained chain %s for docking (active pocket).", best_chain)
            return True
    except Exception as e:
        logger.warning("Retain single chain near box failed: %s", e)
    return False


def _extract_first_model_to_pdb(src: str, dst: str) -> bool:
    """Write first MODEL of PDB/PDBQT to dst as PDB (80-char lines). For receptor: use before cleaning so only apo chain goes to docking."""
    try:
        lines = _extract_first_model_pdb_lines(src)
        if not lines:
            return False
        with open(dst, "w") as f:
            for line in lines:
                line = line.rstrip("\n\r")
                if len(line) > 80:
                    line = line[:80]
                elif len(line) < 80:
                    line = line + " " * (80 - len(line))
                f.write(line + "\n")
        return True
    except Exception as e:
        logger.warning("Extract first model to PDB failed: %s", e)
    return False


def _write_complex_pdb(receptor_pdb: str, ligand_pose_pdb: str, out_path: str) -> bool:
    """Write a single PDB file: receptor + TER + ligand pose (first model)."""
    try:
        rec_lines = _extract_first_model_pdb_lines(receptor_pdb)
        lig_lines = _extract_first_model_pdb_lines(ligand_pose_pdb)
        with open(out_path, "w") as f:
            f.writelines(rec_lines)
            if rec_lines:
                f.write("TER\n")
            f.writelines(lig_lines)
            f.write("END\n")
        return True
    except Exception as e:
        logger.warning("Write complex PDB failed: %s", e)
        return False


def _pdbqt_first_model_to_pdb(pdbqt_path: str, pdb_path: str) -> bool:
    """
    Extract the first MODEL from a PDBQT file and write a PDB file (80-char lines).
    Use when obabel fails to convert docked PDBQT to PDB for interface detection.
    Returns True if a PDB was written.
    """
    try:
        in_model = False
        with open(pdbqt_path, "r") as f_in:
            with open(pdb_path, "w") as f_out:
                for line in f_in:
                    if line.startswith("MODEL "):
                        in_model = True
                        continue
                    if in_model and line.startswith("ENDMDL"):
                        break
                    if in_model and (line.startswith("ATOM  ") or line.startswith("HETATM")):
                        # PDBQT matches PDB columns 1-54; pad to 80 chars for PDB
                        out_line = line.rstrip()
                        if len(out_line) < 80:
                            out_line = out_line + " " * (80 - len(out_line))
                        f_out.write(out_line[:80] + "\n")
        return os.path.isfile(pdb_path) and os.path.getsize(pdb_path) > 0
    except Exception as e:
        logger.warning("PDBQT first model to PDB failed: %s", e)
    return False


def _simple_interface_residues(receptor_pdb: str, ligand_pdb: str, cutoff: float = 5.0) -> List[Dict[str, Any]]:
    """
    Naive interface: residues within cutoff (Angstrom) between receptor and ligand.
    Reads PDB ATOM lines, uses first CA or first atom per residue, computes min distance.
    Returns list of dicts with residue info.
    """
    def residue_atoms(path: str) -> Dict[Tuple[str, str, int], List[Tuple[float, float, float]]]:
        out = {}
        try:
            with open(path, "r") as f:
                for line in f:
                    if not line.startswith(("ATOM  ", "HETATM")):
                        continue
                    try:
                        x = float(line[30:38])
                        y = float(line[38:46])
                        z = float(line[46:54])
                        ch = (line[21:22] or " ").strip()
                        res = (line[17:20] or "").strip()
                        seq = int((line[22:26] or "0").strip())
                        key = (ch, res, seq)
                        out.setdefault(key, []).append((x, y, z))
                    except (ValueError, IndexError):
                        continue
        except Exception:
            pass
        return out

    def min_dist(atoms1: List[Tuple[float, float, float]], atoms2: List[Tuple[float, float, float]]) -> float:
        d = 1e9
        for a in atoms1:
            for b in atoms2:
                d = min(d, ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5)
        return d

    rec = residue_atoms(receptor_pdb)
    lig = residue_atoms(ligand_pdb)
    interfaces = []
    for rk, rv in rec.items():
        for lk, lv in lig.items():
            if min_dist(rv, lv) <= cutoff:
                interfaces.append({
                    "receptor_chain": rk[0],
                    "receptor_residue": rk[1],
                    "receptor_seq": rk[2],
                    "ligand_chain": lk[0],
                    "ligand_residue": lk[1],
                    "ligand_seq": lk[2],
                })
                break
    return interfaces


def _fetch_ligand_pubchem(name: str, output_dir: str) -> Optional[str]:
    """
    Try to get a small-molecule structure by name from PubChem (PUG REST).
    Returns path to saved SDF file or None. Use for ligands like 'adenosine'.
    """
    name = (name or "").strip()
    if not name or len(name) > 200:
        return None
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "+", name)[:100]
    safe_dir(output_dir)
    sdf_path = os.path.join(output_dir, f"ligand_{safe}.sdf")
    try:
        from urllib.parse import quote
        quoted = quote(name)
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quoted}/SDF?record_type=3d"
        req = urllib.request.Request(url, headers={"User-Agent": "MiniProt/1.0"})
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy:
            proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(proxy_handler)
            resp = opener.open(req, timeout=15)
        else:
            resp = urllib.request.urlopen(req, timeout=15)
        data = resp.read()
        if data and len(data) > 100:
            with open(sdf_path, "wb") as f:
                f.write(data)
            return sdf_path
    except Exception as e:
        logger.warning("PubChem fetch %s failed: %s", name, e)
    return None


def _fetch_pdb_for_query(query: str, output_dir: str, tool_manager_class) -> Optional[str]:
    """
    Resolve a protein query to a single PDB file path using AlphaFold download or UniProt.
    Returns path to first PDB found or None. tool_manager_class is ToolManager (class).
    """
    try:
        # Try AlphaFold by query (get_structure or download after resolving ID)
        result = tool_manager_class.run_tool("alphafold", action="get_structure", query=query, output_dir=output_dir, limit=1)
        if not isinstance(result, dict) or not result.get("success"):
            return None
        data = result.get("data") or result
        if isinstance(data, dict) and data.get("success") and isinstance(data.get("data"), dict):
            data = data["data"]
        downloaded = (data or {}).get("downloaded") or {}
        for fmt in ("pdb", "cif"):
            paths = downloaded.get(fmt) or []
            if paths:
                return paths[0]
        fasta_paths = downloaded.get("fasta") or []
        if fasta_paths:
            return fasta_paths[0]
    except Exception as e:
        logger.warning("Fetch PDB for query %s: %s", query, e)
    return None


class AutoDockVinaTool(BaseTool):
    """Run AutoDock Vina docking: receptor + ligand (PDB paths or protein names)."""

    def __init__(self):
        self._name = "autodock_vina"
        self._description = (
            "Molecular docking with AutoDock Vina. Use when the user asks to dock two proteins (or protein and ligand). "
            "Receptor preparation is mandatory before docking: receptor is always cleaned to apo (first model only; ATOM or HETATM; waters and optional bound ligands removed), then converted to PDBQT. "
            "Input: (receptor_pdb_path and ligand_pdb_path) or (receptor_query and ligand_query). Outputs: docked PDBQT/PDB, CSV with binding energies and interacting residues. Requires AutoDock Vina and Open Babel."
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
                        "receptor_pdb_path": {
                            "type": "string",
                            "description": "Path to receptor PDB file (e.g. from AlphaFold or UniProt download). Use when PDB already available.",
                        },
                        "ligand_pdb_path": {
                            "type": "string",
                            "description": "Path to ligand PDB file (second protein or small molecule structure). Use when PDB already available.",
                        },
                        "ligand_pdb_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Paths to multiple ligand PDB files to dock simultaneously; they are merged into one structure before docking. Use when user asks to dock receptor with several ligands at once.",
                        },
                        "receptor_query": {
                            "type": "string",
                            "description": "Protein name or UniProt query for the receptor. Tool will fetch PDB via AlphaFold/UniProt if paths not given.",
                        },
                        "ligand_query": {
                            "type": "string",
                            "description": "Protein name or query for the ligand (second protein). Tool will fetch PDB if paths not given. PDB IDs (e.g. 1ADA) are downloaded from RCSB.",
                        },
                        "session_pdb_paths": {
                            "type": "boolean",
                            "description": "If true, use the two most recent PDB paths from this session (e.g. from AlphaFold) as receptor and ligand. Use when user says 'use those two we downloaded' or 'use the structures in the docking folder'.",
                            "default": False,
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Base directory for resolving paths. Runs always go to data/outputs/docking/vina_run_YYYYMMDD_HHMMSS/.",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "clean_pdbs": {
                            "type": "boolean",
                            "description": "If true, remove water and non-essential molecules from PDBs before docking.",
                            "default": True,
                        },
                        "keep_chains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional: keep only these receptor chain IDs (e.g. [\"A\"]).",
                        },
                        "receptor_remove_hetatm": {
                            "type": "boolean",
                            "description": "If true, remove all HETATM from the receptor during cleaning (apo receptor).",
                            "default": True,
                        },
                        "exhaustiveness": {"type": "integer", "description": "Vina exhaustiveness (default 32 per Vina docs).", "default": DEFAULT_EXHAUSTIVENESS},
                        "num_poses": {"type": "integer", "description": "Number of output poses (default 9).", "default": 9},
                        "center_x": {"type": "number", "description": "Search box center X (Angstrom). Optional: if omitted, box is computed from receptor."},
                        "center_y": {"type": "number", "description": "Search box center Y. Optional."},
                        "center_z": {"type": "number", "description": "Search box center Z. Optional."},
                        "size_x": {"type": "number", "description": "Search box size X (Angstrom). Optional: default from receptor or 20."},
                        "size_y": {"type": "number", "description": "Search box size Y. Optional."},
                        "size_z": {"type": "number", "description": "Search box size Z. Optional."},
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        receptor_path = (kwargs.get("receptor_pdb_path") or "").strip()
        ligand_path = (kwargs.get("ligand_pdb_path") or "").strip()
        receptor_query = (kwargs.get("receptor_query") or "").strip()
        ligand_query = (kwargs.get("ligand_query") or "").strip()
        output_dir = (kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip()
        output_dir = resolve_output_dir(output_dir)
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        else:
            output_dir = os.path.normpath(output_dir)
        safe_dir(output_dir)

        # Docking runs always go to data/outputs/docking/vina_run_YYYYMMDD_HHMMSS/
        # (ignore any project subpath in output_dir)
        docking_base = resolve_output_dir(DEFAULT_OUTPUT_DIR.strip())
        if not os.path.isabs(docking_base):
            docking_base = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), docking_base)))
        else:
            docking_base = os.path.normpath(docking_base)
        safe_dir(docking_base)
        clean_pdbs = bool(kwargs.get("clean_pdbs", True))
        keep_chains = kwargs.get("keep_chains")
        if isinstance(keep_chains, str):
            keep_chains = [keep_chains]
        if keep_chains is not None and not isinstance(keep_chains, list):
            keep_chains = None
        receptor_remove_hetatm = bool(kwargs.get("receptor_remove_hetatm", True))
        session_pdb_paths = bool(kwargs.get("session_pdb_paths", False))
        exhaustiveness = int(kwargs.get("exhaustiveness") or DEFAULT_EXHAUSTIVENESS)
        num_poses = int(kwargs.get("num_poses") or 9)
        center_x = kwargs.get("center_x")
        center_y = kwargs.get("center_y")
        center_z = kwargs.get("center_z")
        size_x = kwargs.get("size_x")
        size_y = kwargs.get("size_y")
        size_z = kwargs.get("size_z")
        user_specified_center = (center_x is not None or center_y is not None or center_z is not None)
        user_specified_size = (size_x is not None or size_y is not None or size_z is not None)
        if not user_specified_center:
            center_x, center_y, center_z = DEFAULT_BOX_CENTER
        if not user_specified_size:
            size_x, size_y, size_z = DEFAULT_BOX_SIZE[0], DEFAULT_BOX_SIZE[1], DEFAULT_BOX_SIZE[2]
        size_x = float(size_x)
        size_y = float(size_y)
        size_z = float(size_z)

        # Resolve PDBs from queries if paths not provided (lazy import to avoid circular import)
        try:
            from tool_runner import ToolManager
        except ImportError:
            from ..tool_runner import ToolManager
        tm = ToolManager

        # Resolve provided paths (relative paths or basenames in common output dirs)
        if receptor_path:
            receptor_path = resolve_file_path(receptor_path, output_dir) or receptor_path
        if ligand_path:
            ligand_path = resolve_file_path(ligand_path, output_dir) or ligand_path

        # Multiple ligands for simultaneous docking: merge into one PDB
        ligand_pdb_paths = kwargs.get("ligand_pdb_paths") or []
        if isinstance(ligand_pdb_paths, list) and len(ligand_pdb_paths) >= 2:
            resolved_ligs = []
            for lp in ligand_pdb_paths:
                p = (lp or "").strip()
                if not p:
                    continue
                r = resolve_file_path(p, output_dir) or p
                if r and os.path.isfile(r):
                    resolved_ligs.append(r)
            if len(resolved_ligs) >= 2:
                merge_path = os.path.join(output_dir, f"merged_ligands_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdb")
                if merge_pdb_files(resolved_ligs, merge_path):
                    ligand_path = merge_path

        # Fetch receptor: prefer local file, then PDB ID from RCSB, then AlphaFold/UniProt
        if not receptor_path and receptor_query:
            if is_pdb_id(receptor_query):
                receptor_path = find_local_pdb(receptor_query, output_dir)
                if not receptor_path:
                    receptor_path = _fetch_pdb_by_id(receptor_query, output_dir)
            if not receptor_path:
                receptor_path = _fetch_pdb_for_query(receptor_query, output_dir, tm)
        if not receptor_path:
            if session_pdb_paths:
                return {
                    "success": False,
                    "error": "session_pdb_paths was set but no PDB files found in this session.",
                    "data": {},
                }
            return {
                "success": False,
                "error": "Could not resolve or fetch receptor. Provide receptor_pdb_path or receptor_query (PDB ID or protein name).",
                "data": {},
            }
        # Fetch ligand: prefer local file, then PDB ID, then AlphaFold/UniProt, then PubChem
        if not ligand_path and ligand_query:
            if is_pdb_id(ligand_query):
                ligand_path = find_local_pdb(ligand_query, output_dir)
                if not ligand_path:
                    ligand_path = _fetch_pdb_by_id(ligand_query, output_dir)
            if not ligand_path:
                ligand_path = _fetch_pdb_for_query(ligand_query, output_dir, tm)
            if not ligand_path:
                ligand_path = _fetch_ligand_pubchem(ligand_query, output_dir)
            if not ligand_path:
                return {
                    "success": False,
                    "error": f"Could not resolve or fetch ligand for '{ligand_query}'. Provide ligand_pdb_path or ligand_query (PDB ID, protein name, or compound name).",
                    "data": {},
                }

        if not receptor_path or not os.path.isfile(receptor_path):
            return {
                "success": False,
                "error": "Receptor file not found. Provide receptor_pdb_path or receptor_query.",
                "data": {},
            }
        if not ligand_path or not os.path.isfile(ligand_path):
            return {
                "success": False,
                "error": "Ligand file not found. Provide ligand_pdb_path or ligand_query.",
                "data": {},
            }

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.abspath(os.path.join(docking_base, f"vina_run_{run_id}"))
        safe_dir(base)
        rec_work = os.path.join(base, "receptor.pdb")
        ligand_is_sdf = (ligand_path or "").lower().endswith(".sdf")
        lig_work = os.path.join(base, "ligand.sdf") if ligand_is_sdf else os.path.join(base, "ligand.pdb")
        # Receptor: extract first model, then (if multi-chain) retain only the chain containing the active site, then clean to apo
        rec_first = os.path.join(base, "receptor_first_model.pdb")
        if not _extract_first_model_to_pdb(receptor_path, rec_first):
            shutil.copy2(receptor_path, rec_first)
        # Compute box from full receptor for active-site chain selection and (if not user-set) for docking
        computed_center, computed_size = _compute_receptor_box(rec_first)
        rec_chains = _get_chain_ids(rec_first)
        if len(rec_chains) > 1:
            rec_single = os.path.join(base, "receptor_single_chain.pdb")
            if _retain_single_chain_near_box(rec_first, computed_center, computed_size, rec_single):
                _clean_pdb_atoms(rec_single, rec_work, remove_water=True, keep_chains=keep_chains, remove_hetatm=receptor_remove_hetatm)
            else:
                _clean_pdb_atoms(rec_first, rec_work, remove_water=True, keep_chains=keep_chains, remove_hetatm=receptor_remove_hetatm)
        else:
            _clean_pdb_atoms(rec_first, rec_work, remove_water=True, keep_chains=keep_chains, remove_hetatm=receptor_remove_hetatm)
        # Ligand: clean or copy
        if clean_pdbs and not ligand_is_sdf:
            _clean_pdb_atoms(ligand_path, lig_work, remove_water=True)
        elif ligand_is_sdf:
            shutil.copy2(ligand_path, lig_work)
        else:
            shutil.copy2(ligand_path, lig_work)

        # When user did not specify search box, use defaults from receptor (already computed from rec_first)
        if not user_specified_center:
            center_x, center_y, center_z = computed_center
        if not user_specified_size:
            size_x, size_y, size_z = computed_size

        box_center = (float(center_x), float(center_y), float(center_z))
        box_size = (size_x, size_y, size_z)

        # Detect protein-protein docking (PPI): both receptor and ligand look like full proteins.
        # Vina is designed for protein-small-molecule docking (max 32 ligand torsions) and will
        # either refuse or OOM on large ligands. In this case we switch to a "rigid ligand" path
        # (TORSDOF 0, via obabel -xr) and cap the box so the grid fits in RAM. We also include a
        # strong hint in the output so the planner/user know Vina is not the right tool for PPI.
        ligand_is_protein = _looks_like_protein(lig_work)
        ppi_mode = ligand_is_protein and not ligand_is_sdf
        lig_atoms, lig_residues = _count_atoms_and_residues(lig_work) if not ligand_is_sdf else (0, 0)
        if ppi_mode:
            logger.warning(
                "autodock_vina: ligand looks like a full protein (atoms=%d, residues=%d). "
                "Vina cannot do proper protein-protein docking; falling back to rigid-ligand mode with a capped box. "
                "For real PPI use a dedicated tool (HDOCK, ClusPro, HADDOCK, pyDock).",
                lig_atoms, lig_residues,
            )

        # If ligand is PDB and has multiple chains, retain only the chain near the binding site (same box)
        if not ligand_is_sdf:
            lig_chains = _get_chain_ids(lig_work)
            if len(lig_chains) > 1:
                lig_single = os.path.join(base, "ligand_single_chain.pdb")
                if _retain_single_chain_near_box(lig_work, box_center, box_size, lig_single):
                    shutil.copy2(lig_single, lig_work)

        # Cap the box so Vina's grid allocation stays in a sane range (protects low-RAM boxes).
        capped_box_size, was_capped = _cap_box_size(box_size)
        if was_capped:
            logger.warning(
                "autodock_vina: box size %s exceeded limits (max %.0f Å/axis, vol %.0f Å³); capped to %s.",
                tuple(round(v, 1) for v in box_size), MAX_BOX_DIM, MAX_BOX_VOLUME,
                tuple(round(v, 1) for v in capped_box_size),
            )
            box_size = capped_box_size

        # Output paths for prepared PDBQT files (used by Meeko or Open Babel fallback)
        rec_pdbqt = os.path.join(base, "receptor.pdbqt")
        lig_pdbqt = os.path.join(base, "ligand.pdbqt")
        # Prefer Meeko for preparation (docs); fallback to Open Babel
        ok_rec, err_rec, rec_pdbqt_out = _prepare_receptor_meeko(rec_work, base, "receptor", box_center, box_size)
        if not ok_rec:
            ok_rec, err_rec = _pdb_to_pdbqt_obabel(rec_work, rec_pdbqt, is_ligand=False)
            if not ok_rec:
                return {"success": False, "error": f"Receptor PDBQT failed (Meeko and obabel): {err_rec}", "data": {"output_dir": output_dir}}
        elif rec_pdbqt_out:
            rec_pdbqt = rec_pdbqt_out

        # Ligand preparation: PPI mode → rigid ligand via obabel -xr + ROOT/ENDROOT/TORSDOF 0 wrapper
        # (skip Meeko for protein ligands — it tries to build a torsion tree and either fails or
        # produces >32 torsions, which Vina rejects). Otherwise prefer Meeko, fall back to obabel.
        if ppi_mode:
            rigid_raw = os.path.join(base, "ligand_rigid_raw.pdbqt")
            ok_lig, err_lig = _pdb_to_pdbqt_obabel(lig_work, rigid_raw, is_ligand=False)
            if ok_lig and _wrap_rigid_ligand_pdbqt(rigid_raw, lig_pdbqt):
                logger.info("autodock_vina: prepared rigid ligand PDBQT (TORSDOF 0) for PPI mode.")
            else:
                # Leave a clear message: Vina cannot proceed with PPI-sized flexible ligands.
                return {
                    "success": False,
                    "error": (
                        "autodock_vina cannot dock this receptor/ligand pair: the ligand appears to be a full "
                        f"protein (~{lig_atoms} atoms / {lig_residues} residues) and rigid-ligand preparation failed ("
                        f"{err_lig}). AutoDock Vina is designed for protein-small-molecule docking and has a 32-torsion "
                        "cap on the ligand, so it is not suitable for protein-protein docking. "
                        "For protein-protein docking use a dedicated PPI tool (e.g. HDOCK, ClusPro, HADDOCK, pyDock). "
                        "Do not retry pdb_repair + autodock_vina — the tool is the wrong match for the task."
                    ),
                    "data": {
                        "output_dir": output_dir,
                        "ppi_detected": True,
                        "ligand_atoms": lig_atoms,
                        "ligand_residues": lig_residues,
                        "hint": "use HDOCK / ClusPro / HADDOCK / pyDock for protein-protein docking",
                    },
                }
        else:
            ok_lig, err_lig, lig_pdbqt_out = _prepare_ligand_meeko(lig_work, base, "ligand")
            if not ok_lig:
                ok_lig, err_lig = _pdb_to_pdbqt_obabel(lig_work, lig_pdbqt, is_ligand=True)
                if not ok_lig:
                    return {"success": False, "error": f"Ligand PDBQT failed (Meeko and obabel): {err_lig}", "data": {"output_dir": output_dir}}
            elif lig_pdbqt_out:
                lig_pdbqt = lig_pdbqt_out

        # Sanity check: Vina refuses ligands with more than 32 active torsions. Fail fast if the
        # prepared ligand exceeds this, with a PPI-specific hint.
        torsdof = _count_torsions_in_pdbqt(lig_pdbqt)
        if torsdof > VINA_MAX_TORSIONS:
            return {
                "success": False,
                "error": (
                    f"Prepared ligand has {torsdof} torsional DOF — AutoDock Vina's limit is {VINA_MAX_TORSIONS}. "
                    f"(ligand atoms={lig_atoms}, residues={lig_residues}). This typically means the 'ligand' is "
                    "actually a protein and you intended protein-protein docking (PPI). "
                    "Use a dedicated PPI tool (HDOCK, ClusPro, HADDOCK, pyDock) — Vina cannot handle this. "
                    "Do not retry pdb_repair + autodock_vina; change the approach."
                ),
                "data": {
                    "output_dir": output_dir,
                    "ppi_detected": True,
                    "ligand_torsions": torsdof,
                    "ligand_atoms": lig_atoms,
                    "ligand_residues": lig_residues,
                    "hint": "use HDOCK / ClusPro / HADDOCK / pyDock for protein-protein docking",
                },
            }

        # Write Vina config file (center_x, center_y, center_z, size_x, size_y, size_z)
        config_path = os.path.join(base, "vina_config.txt")
        _write_vina_config(config_path, box_center, box_size)

        vina_exe = _find_executable("vina")
        if not vina_exe:
            return {
                "success": False,
                "error": "AutoDock Vina not found. Install: conda install -c conda-forge autodock-vina (see https://vina.scripps.edu).",
                "data": {"output_dir": output_dir, "receptor_pdbqt": rec_pdbqt, "ligand_pdbqt": lig_pdbqt},
            }
        # Sanitize receptor PDBQT to only ATOM/HETATM (avoids Vina "PDBQT parsing" errors from ROOT/TORSDOF)
        _sanitize_receptor_pdbqt(rec_pdbqt)

        out_pdbqt = os.path.join(base, "docked.pdbqt")
        log_path = os.path.join(base, "vina.log")
        # Use absolute paths so vina finds files regardless of subprocess cwd; use env so conda/miniprot bin is first
        env = _docking_subprocess_env()
        abs_rec = os.path.abspath(rec_pdbqt)
        abs_lig = os.path.abspath(lig_pdbqt)
        abs_config = os.path.abspath(config_path)
        abs_out = os.path.abspath(out_pdbqt)
        abs_log = os.path.abspath(log_path)
        cmd_with_log = [
            vina_exe,
            "--receptor", abs_rec,
            "--ligand", abs_lig,
            "--config", abs_config,
            "--exhaustiveness", str(exhaustiveness),
            "--num_modes", str(num_poses),
            "--out", abs_out,
            "--log", abs_log,
        ]
        cmd_no_log = [
            vina_exe,
            "--receptor", abs_rec,
            "--ligand", abs_lig,
            "--config", abs_config,
            "--exhaustiveness", str(exhaustiveness),
            "--num_modes", str(num_poses),
            "--out", abs_out,
        ]
        def _vina_diagnostic(code: int, stderr: str, stdout: str) -> str:
            """Build a descriptive error string from a failed Vina run. Reads the log file when present
            and special-cases common silent failure modes (OOM kill, insufficient memory, PPI)."""
            parts: List[str] = []
            if code == 137 or code == -9:
                parts.append(
                    "Vina process was killed (exit 137 / SIGKILL) — almost always the Linux OOM killer because "
                    "the docking box or ligand is too large. This agent caps box size, so if you hit this the "
                    "likely cause is a PPI attempt (huge flexible ligand)."
                )
            elif code < 0:
                parts.append(f"Vina terminated by signal {-code}.")
            else:
                parts.append(f"Vina exit code {code}.")
            log_snippet = ""
            try:
                if os.path.isfile(log_path) and os.path.getsize(log_path) > 0:
                    with open(log_path, "r", errors="replace") as f:
                        log_snippet = f.read()[-1200:]
            except Exception:
                log_snippet = ""
            detail = (stderr or "").strip() or (stdout or "").strip() or log_snippet.strip()
            if detail:
                parts.append("Vina output: " + detail[-1000:])
            if not detail and not log_snippet:
                parts.append("(no output captured — likely crashed before writing any diagnostics)")
            dl = (detail or "").lower()
            if "insufficient memory" in dl or "bad_alloc" in dl or "std::bad_alloc" in dl:
                parts.append(
                    "'Insufficient memory' from Vina means the grid for the search box is too big — "
                    "use a smaller box (size_x/y/z) or centre the box on the known binding site."
                )
            if ppi_mode:
                parts.append(
                    "NOTE: this run was flagged as PPI (large protein ligand). AutoDock Vina is not a "
                    "PPI docking tool — use HDOCK / ClusPro / HADDOCK / pyDock instead. Do not retry "
                    "pdb_repair + autodock_vina; change the approach."
                )
            return " | ".join(parts)

        try:
            result = subprocess.run(cmd_with_log, capture_output=True, text=True, timeout=600, cwd=base, env=env)
            stderr_lower = (result.stderr or "").lower()
            # Some Vina builds (e.g. older conda) don't support --log; run without it and capture stdout to log
            if result.returncode != 0 and ("unrecognised option" in stderr_lower or "unrecognized option" in stderr_lower) and "log" in stderr_lower:
                result = subprocess.run(cmd_no_log, capture_output=True, text=True, timeout=600, cwd=base, env=env)
                if result.returncode == 0 and result.stdout:
                    with open(log_path, "w") as f:
                        f.write(result.stdout)
            # Ligand PDBQT parsing errors (e.g. complex sugars): retry with obabel-only preparation once.
            # Skip this retry in PPI mode (we already used the rigid-ligand path).
            if result.returncode != 0 and not ppi_mode:
                stderr_lower = (result.stderr or "").lower()
                if "pars" in stderr_lower and os.path.isfile(lig_work):
                    ok_obabel, _err = _pdb_to_pdbqt_obabel(lig_work, lig_pdbqt, is_ligand=True)
                    if ok_obabel:
                        result = subprocess.run(cmd_with_log, capture_output=True, text=True, timeout=600, cwd=base, env=env)
                        if result.returncode != 0 and ("unrecognised option" in (result.stderr or "") or "unrecognized option" in (result.stderr or "")):
                            result = subprocess.run(cmd_no_log, capture_output=True, text=True, timeout=600, cwd=base, env=env)
            if result.returncode != 0:
                err = _vina_diagnostic(result.returncode, result.stderr, result.stdout)
                data: Dict[str, Any] = {
                    "output_dir": output_dir,
                    "log_path": log_path,
                    "returncode": result.returncode,
                }
                if ppi_mode:
                    data["ppi_detected"] = True
                    data["hint"] = "use HDOCK / ClusPro / HADDOCK / pyDock for protein-protein docking"
                return {"success": False, "error": f"Vina failed: {err}", "data": data}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Vina timed out (600s).", "data": {"output_dir": output_dir}}
        except Exception as e:
            return {"success": False, "error": str(e), "data": {"output_dir": output_dir}}

        # Parse energies and write CSV (mode, affinity_kcal_mol, rmsd_lb, rmsd_ub)
        energies = _parse_vina_log(log_path)
        csv_path = os.path.join(base, "energies.csv")
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["mode", "affinity_kcal_mol", "rmsd_lb", "rmsd_ub"])
            w.writeheader()
            w.writerows(energies)

        # Convert best docked pose (first model) to PDB for complex and interface analysis
        docked_ligand_pdb = os.path.join(base, "docked_ligand.pdb")
        obabel = _find_executable("obabel")
        if obabel and os.path.isfile(out_pdbqt):
            subprocess.run(
                [obabel, "-i", "pdbqt", os.path.abspath(out_pdbqt), "-o", "pdb", "-O", os.path.abspath(docked_ligand_pdb), "-m", "1", "-h"],
                capture_output=True, timeout=30, env=_docking_subprocess_env(),
            )
        if not os.path.isfile(docked_ligand_pdb) or os.path.getsize(docked_ligand_pdb) == 0:
            _pdbqt_first_model_to_pdb(out_pdbqt, docked_ligand_pdb)
        docked_pdb = docked_ligand_pdb if (os.path.isfile(docked_ligand_pdb) and os.path.getsize(docked_ligand_pdb) > 0) else lig_work
        interfaces = _simple_interface_residues(rec_work, docked_pdb, cutoff=5.0)
        interface_csv = os.path.join(base, "interacting_sites.csv")
        interface_txt = os.path.join(base, "interacting_sites.txt")
        fieldnames = ["receptor_chain", "receptor_residue", "receptor_seq", "ligand_chain", "ligand_residue", "ligand_seq"]
        with open(interface_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            if interfaces:
                w.writerows(interfaces)
            else:
                w.writerow({k: "no_contacts_detected" for k in fieldnames})
        with open(interface_txt, "w") as f:
            f.write("Interacting residues (receptor residues within 5.0 A of docked ligand)\n")
            f.write("Cutoff: 5.0 A. Receptor/ligand PDB used for detection.\n\n")
            if interfaces:
                seen = set()
                for row in interfaces:
                    key = (row.get("receptor_residue"), row.get("receptor_seq"))
                    if key not in seen:
                        seen.add(key)
                        f.write(f"  {row.get('receptor_residue', '')}{row.get('receptor_seq', '')} (chain {row.get('receptor_chain', '')})\n")
                f.write(f"\nTotal unique receptor residues: {len(seen)}\n")
            else:
                f.write("No receptor residues within cutoff. Possible causes: docked ligand PDB was not available (obabel/fallback failed), or ligand was SDF and not converted for this step.\n")

        # Receptor–ligand complex PDB (receptor + best pose ligand)
        complex_pdb = os.path.join(base, "complex_receptor_ligand.pdb")
        _write_complex_pdb(rec_work, docked_pdb, complex_pdb)

        # Set readable permissions on all written output files
        for p in (config_path, log_path, csv_path, interface_csv, interface_txt, complex_pdb, out_pdbqt, docked_ligand_pdb):
            ensure_file_permissions(p)

        # Collect all outputs: PDBQTs, config, log, CSVs, PDBs (all absolute paths)
        def _abs(p):
            return os.path.abspath(p) if p else p
        downloaded = {
            "receptor_pdbqt": [_abs(rec_pdbqt)],
            "ligand_pdbqt": [_abs(lig_pdbqt)],
            "docked_pdbqt": [_abs(out_pdbqt)],
            "config": [_abs(config_path)],
            "log": [_abs(log_path)],
            "energies_csv": [_abs(csv_path)],
            "interacting_sites_csv": [_abs(interface_csv)],
            "interacting_sites_txt": [_abs(interface_txt)],
        }
        if os.path.isfile(docked_ligand_pdb):
            downloaded["docked_ligand_pdb"] = [_abs(docked_ligand_pdb)]
        if os.path.isfile(complex_pdb):
            downloaded["complex_pdb"] = [_abs(complex_pdb)]
        downloaded["pdbqt"] = [_abs(out_pdbqt)]
        downloaded["csv"] = [_abs(csv_path)]

        # Verify key outputs exist at reported path
        if not os.path.isfile(out_pdbqt):
            return {"success": False, "error": f"Docking ran but output file not found at {out_pdbqt}. Check write permissions.", "data": {"output_dir": base}}
        if not os.path.isfile(csv_path):
            return {"success": False, "error": f"Docking ran but energies CSV not found at {csv_path}. Check write permissions.", "data": {"output_dir": base}}

        return {
            "success": True,
            "data": {
                "message": (
                    f"Docking completed. {len(energies)} pose(s); best affinity: {energies[0]['affinity_kcal_mol'] if energies else 'N/A'} kcal/mol. "
                    "Outputs: receptor/ligand/docked PDBQT, vina_config.txt, vina.log, energies.csv, interacting_sites.csv, interacting_sites.txt, complex_receptor_ligand.pdb, docked_ligand.pdb."
                ),
                "downloaded": downloaded,
                "output_dir": base,
                "energies_preview": energies[:5],
                "num_interface_residues": len(interfaces),
            },
        }
