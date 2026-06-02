"""
Shared PDB/RC SB helpers: RCSB URL, is_pdb_id, normalize_pdb_ids, fetch from RCSB,
find local PDB by ID, resolve file path in common output dirs.
Used by pdb_tool and autodock_vina_tool to avoid duplication.
"""
import logging
import os
import re
import urllib.request
from typing import List, Optional, Union

from .path_utils import workspace_root, ensure_file_permissions

logger = logging.getLogger(__name__)

RCSB_PDB_BASE = "https://files.rcsb.org/download"

# Subdirs under workspace where tools write outputs (for resolving paths / finding local files)
OUTPUT_SUBDIRS = (
    "data/outputs/pdb",
    "data/outputs/pdb_repair",
    "data/outputs/alphafold",
    "data/outputs/docking",
    "data/outputs/smiles",
    "data/outputs/uniprot",
    "data/outputs",
)


def is_pdb_id(s: str) -> bool:
    """True if s looks like a PDB ID (4 alphanumeric characters)."""
    s = (s or "").strip().upper()
    return len(s) == 4 and s.isalnum()


def normalize_pdb_ids(ids: Union[str, List[str]]) -> List[str]:
    """Return list of valid 4-char PDB IDs from string (comma/space separated) or list."""
    out: List[str] = []
    if isinstance(ids, list):
        for x in ids:
            if isinstance(x, str) and is_pdb_id(x.strip().upper()):
                out.append(x.strip().upper())
    else:
        s = (ids or "").strip()
        for part in re.split(r"[\s,;]+", s):
            part = part.strip().upper()
            if is_pdb_id(part):
                out.append(part)
    return list(dict.fromkeys(out))


def fetch_rcsb_file(url: str, out_path: str, timeout: int = 30) -> bool:
    """Download url to out_path. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MiniProt/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if data and len(data) > 100:
            with open(out_path, "wb") as f:
                f.write(data)
            return True
    except Exception as e:
        logger.warning("Download %s failed: %s", url, e)
    return False


def find_local_pdb(pdb_id: str, output_dir: str) -> Optional[str]:
    """
    Look for an existing PDB/CIF file by ID in common output dirs.
    Returns absolute path if found, else None.
    """
    pdb_id = (pdb_id or "").strip().upper()[:4]
    if not is_pdb_id(pdb_id):
        return None
    root = workspace_root()
    search_dirs = [os.path.join(root, d) for d in OUTPUT_SUBDIRS]
    search_dirs.insert(1, output_dir)
    for d in search_dirs:
        if not d:
            continue
        for name in (f"{pdb_id}.pdb", f"{pdb_id}.cif"):
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return None


def resolve_file_path(path: str, output_dir: str) -> Optional[str]:
    """
    Resolve a file path: try as-is (with abspath), then relative to workspace (including when
    user gives /data/outputs/... which may mean workspace-relative data/outputs/...), then basename in common output dirs.
    Returns absolute path if found, else None.
    """
    path = (path or "").strip()
    if not path:
        return None
    root = workspace_root()
    candidates = [path, os.path.abspath(path)]
    if not os.path.isabs(path):
        candidates.append(os.path.normpath(os.path.join(root, path)))
    # When user says "files are in /data/outputs/pdb/", path may be absolute but wrong root; try workspace-relative
    if os.path.isabs(path) and not os.path.isfile(path):
        rel = path.lstrip(os.sep)
        candidates.append(os.path.normpath(os.path.join(root, rel)))
        if path.startswith("/data/"):
            candidates.append(os.path.normpath(os.path.join(root, "data", path[6:].lstrip("/"))))
    for p in candidates:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    base = os.path.basename(path)
    if not base:
        return None
    search_dirs = [root, output_dir] + [os.path.join(root, d) for d in OUTPUT_SUBDIRS]
    for d in search_dirs:
        if not d:
            continue
        candidate = os.path.join(d, base)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def merge_pdb_files(paths: List[str], out_path: str, first_model_only: bool = True) -> bool:
    """
    Combine multiple PDB files into one: write ATOM/HETATM lines from the first MODEL of each file,
    with TER between structures. Used to merge multiple ligands for simultaneous docking.
    Returns True if out_path was written successfully.
    """
    if not paths or not out_path:
        return False
    lines: List[str] = []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                in_model = True
                for line in f:
                    if first_model_only:
                        if line.startswith("MODEL"):
                            in_model = True
                            continue
                        if line.startswith("ENDMDL"):
                            break
                    if line.startswith(("ATOM  ", "HETATM")):
                        if not first_model_only or in_model:
                            line_80 = line.rstrip("\n\r")
                            if len(line_80) > 80:
                                line_80 = line_80[:80]
                            elif len(line_80) < 80:
                                line_80 = line_80 + " " * (80 - len(line_80))
                            lines.append(line_80 + "\n")
                if lines and not lines[-1].strip().startswith("TER"):
                    lines.append("TER\n")
        except Exception:
            continue
    if not lines:
        return False
    try:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True, mode=0o755)
        with open(out_path, "w") as f:
            f.writelines(lines)
        ensure_file_permissions(out_path)
        return os.path.isfile(out_path)
    except Exception:
        return False
