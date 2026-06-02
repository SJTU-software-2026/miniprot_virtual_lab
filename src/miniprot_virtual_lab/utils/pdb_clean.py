"""
Shared PDB cleaning for docking: remove waters, HETATM (for receptor), keep first altloc.
Used by AlphaFold (clean right after download) and AutoDock Vina (before PDBQT).
"""
from typing import List, Optional


def clean_pdb_for_docking(
    in_path: str,
    out_path: Optional[str] = None,
    remove_hetatm: bool = True,
    remove_water: bool = True,
    keep_first_altloc: bool = True,
    keep_chains: Optional[List[str]] = None,
) -> bool:
    """
    Write a docking-ready PDB: keep ATOM (and optionally HETATM), remove waters,
    optionally remove all HETATM (receptor-only), keep only first altloc (avoid parser errors).
    PDB lines are normalized to 80 chars. Optionally keep only given chain IDs.
    Returns True on success.
    """
    if out_path is None:
        out_path = in_path
    try:
        with open(in_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        out = []
        in_first_model = True
        for line in lines:
            if line.startswith("MODEL"):
                if in_first_model:
                    in_first_model = False
                else:
                    break
            if line.startswith("ENDMDL") and not in_first_model:
                break
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            if len(line) < 27:
                continue
            if remove_hetatm and line.startswith("HETATM"):
                continue
            if remove_water and line.startswith("HETATM"):
                res_name = (line[17:20] or "").strip()
                if res_name in ("HOH", "WAT", "H2O", "OH2"):
                    continue
            if keep_first_altloc:
                altloc = (line[16:17] or " ").strip()
                if altloc and altloc != "A":
                    continue
            if keep_chains is not None:
                ch = (line[21:22] or " ").strip()
                if ch and ch not in keep_chains:
                    continue
            # Normalize to 80 chars (PDB standard)
            line = line.rstrip("\n\r")
            if len(line) > 80:
                line = line[:80]
            elif len(line) < 80:
                line = line + " " * (80 - len(line))
            out.append(line + "\n")
        with open(out_path, "w") as f:
            f.writelines(out)
        return True
    except Exception:
        return False
