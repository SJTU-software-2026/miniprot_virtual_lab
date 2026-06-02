"""
PDB tool: fetch structure(s) from the Protein Data Bank (RCSB) by PDB ID.
Output paths can be used by autodock_vina or other tools.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import os
from typing import Dict, Any, List

import re
from typing import Tuple

try:
    from utils.path_utils import safe_dir
    from utils.pdb_utils import RCSB_PDB_BASE, normalize_pdb_ids, fetch_rcsb_file
except ImportError:
    from ..utils.path_utils import safe_dir
    from ..utils.pdb_utils import RCSB_PDB_BASE, normalize_pdb_ids, fetch_rcsb_file

DEFAULT_OUTPUT_DIR = "data/outputs/pdb"

# UniProt accession patterns (used to generate helpful errors when the planner
# sends a UniProt ID to the pdb tool). Reference: https://www.uniprot.org/help/accession_numbers
_UNIPROT_ACC_PATTERNS = (
    re.compile(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$", re.I),                        # classic 6-char
    re.compile(r"^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$", re.I),          # extended 6/10-char
)


def _classify_tokens(raw: str) -> Tuple[list, list]:
    """Split an identifier string into (pdb_ids, uniprot_like_tokens).

    PDB IDs are strictly 4 alphanumeric characters. Longer alphanumeric tokens
    that look like UniProt accessions are returned separately so the caller can
    tell the planner to reroute them.
    """
    pdb_ids: list = []
    uniprot_like: list = []
    if not raw:
        return pdb_ids, uniprot_like
    tokens = [t.strip().upper() for t in re.split(r"[\s,;]+", raw) if t.strip()]
    for t in tokens:
        if len(t) == 4 and t.isalnum():
            pdb_ids.append(t)
        elif any(p.match(t) for p in _UNIPROT_ACC_PATTERNS):
            uniprot_like.append(t)
    return pdb_ids, uniprot_like


class PDBTool(BaseTool):
    """Fetch structure(s) from RCSB PDB by PDB ID(s)."""

    def __init__(self):
        self._name = "pdb"
        self._description = (
            "Fetch structure files from the Protein Data Bank (RCSB) by PDB ID. "
            "Use ONLY when the user gives a 4-character alphanumeric RCSB ID (e.g. 1ADA, 2XYZ). "
            "Do NOT use for UniProt accessions such as P00760, Q8IWU9, O15528, A0A0B4J1F0 — those belong to "
            "uniprot_search (FASTA) or structure_from_fasta / alphafold (3D structure). "
            "Accepts one or more 4-character PDB IDs (comma or space separated). Saves PDB (and optionally CIF) files to output_dir. "
            "Downloaded paths can be passed to autodock_vina as receptor_pdb_path or ligand_pdb_path."
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
                        "pdb_ids": {
                            "type": "string",
                            "description": "One or more RCSB PDB IDs (4 characters, e.g. '1ADA' or '1ADA, 2XYZ'). Comma or space separated. Do NOT pass UniProt accessions here (P00760, Q8IWU9, A0A...) — those need uniprot_search or structure_from_fasta.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save downloaded files (default: data/outputs/pdb).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "formats": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["pdb", "cif"]},
                            "description": "File formats to download (default: ['pdb']). Add 'cif' for mmCIF.",
                            "default": ["pdb"],
                        },
                    },
                    "required": ["pdb_ids"],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        # Accept common alias 'pdb_id' (singular) from planners that send a single ID.
        pdb_ids_arg = kwargs.get("pdb_ids") or kwargs.get("pdb_id") or ""
        output_dir = safe_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        formats = list(kwargs.get("formats") or ["pdb"])
        if "pdb" not in formats and "cif" not in formats:
            formats = ["pdb"]

        ids = normalize_pdb_ids(pdb_ids_arg)
        if not ids:
            # Help the planner reroute: if the input looks like a UniProt accession (P00760,
            # Q8IWU9, A0A...), say so explicitly. The pdb tool is for 4-char RCSB IDs only.
            _, uniprot_like = _classify_tokens(str(pdb_ids_arg))
            if uniprot_like:
                sample = ", ".join(uniprot_like[:3])
                return {
                    "success": False,
                    "error": (
                        f"'{sample}' look(s) like UniProt accession(s), not PDB IDs. "
                        "The pdb tool only accepts 4-character RCSB IDs (e.g. 1ADA). "
                        "For UniProt accessions, use uniprot_search (FASTA) or structure_from_fasta / alphafold (3D structure) instead."
                    ),
                    "data": {"uniprot_like_ids": uniprot_like},
                }
            return {
                "success": False,
                "error": "No valid PDB IDs provided. Use 4-character IDs (e.g. 1ADA, 2XYZ). Multiple IDs: comma or space separated. UniProt accessions (P00760, Q8IWU9, A0A...) belong to uniprot_search or structure_from_fasta — not this tool.",
                "data": {},
            }

        downloaded: Dict[str, List[str]] = {"pdb": [], "cif": []}
        for pdb_id in ids:
            if "pdb" in formats:
                path = os.path.join(output_dir, f"{pdb_id}.pdb")
                if os.path.isfile(path):
                    downloaded["pdb"].append(path)
                else:
                    url = f"{RCSB_PDB_BASE}/{pdb_id}.pdb"
                    if fetch_rcsb_file(url, path):
                        try:
                            from utils.pdb_clean import clean_pdb_for_docking
                        except ImportError:
                            from ..utils.pdb_clean import clean_pdb_for_docking
                        clean_pdb_for_docking(path, remove_hetatm=True, remove_water=True)
                        downloaded["pdb"].append(path)
            if "cif" in formats:
                path = os.path.join(output_dir, f"{pdb_id}.cif")
                if os.path.isfile(path):
                    downloaded["cif"].append(path)
                else:
                    url = f"{RCSB_PDB_BASE}/{pdb_id}.cif"
                    if fetch_rcsb_file(url, path):
                        downloaded["cif"].append(path)

        if not downloaded["pdb"] and not downloaded["cif"]:
            return {
                "success": False,
                "error": f"Failed to download any structure for PDB ID(s): {ids}. Check IDs at https://www.rcsb.org/.",
                "data": {"pdb_ids": ids, "output_dir": output_dir},
            }

        message = f"Downloaded {len(downloaded['pdb'])} PDB, {len(downloaded['cif'])} CIF to {output_dir}."
        return {
            "success": True,
            "data": {
                "message": message,
                "downloaded": downloaded,
                "output_dir": output_dir,
                "pdb_ids": ids,
            },
        }
