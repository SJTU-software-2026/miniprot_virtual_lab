"""
SMILES lookup tool: get SMILES (and related chemical data) from PubChem (PUG REST)
and ChEMBL so they can be used for docking, Open Babel conversion, or analysis.

References:
- PubChem PUG REST: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest (input: name, cid; property: SMILES)
- ChEMBL Data Web Services: https://chembl.gitbook.io/chembl-interface-documentation/web-services
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
import urllib.request
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PUBCHEM_PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
CHEMBL_API = "https://www.ebi.ac.uk/chembl/api/data"

try:
    from utils.path_utils import safe_dir, safe_filename
except ImportError:
    from ..utils.path_utils import safe_dir, safe_filename

DEFAULT_OUTPUT_DIR = "data/outputs/smiles"

# Output-format / artifact tokens that must never be used as PubChem "name" queries.
SMILES_QUERY_DENYLIST = frozenset({
    "csv",
    "tsv",
    "sdf",
    "smi",
    "pdb",
    "mmcif",
    "cif",
    "pdf",
    "png",
    "json",
    "xml",
    "txt",
    "pose",
    "poses",
    "energies",
    "energy",
    "binding",
    "vina",
    "autodock",
    "pdbqt",
    "ligand",
    "receptor",
    "compound",
    "file",
    "files",
    "output",
    "outputs",
    "path",
    "paths",
    "box",
    "pocket",
    "docking",
    "dock",
})


def _strip_noise_token(q: str) -> str:
    return (q or "").strip().strip("\"'.,;:()[]{}").lower().rstrip(",.;:")


def is_noise_ligand_query(q: str) -> bool:
    """True when `query` is clearly an output-format word, not a compound name."""
    low = _strip_noise_token(q)
    if not low:
        return True
    if low in SMILES_QUERY_DENYLIST:
        return True
    if low.removesuffix(",").strip() in SMILES_QUERY_DENYLIST:
        return True
    if len(low) <= 6 and "." in low:
        return True
    return False


def _looks_like_provided_smiles_string(value: str) -> bool:
    """Heuristic: distinguish inline SMILES from compound names."""
    v = (value or "").strip()
    if len(v) < 4:
        return False
    if not re.search(r"[A-Za-z0-9]", v):
        return False
    return bool(
        re.search(r"[=#@+\/\[\(\\]", v)
        or re.search(r"\b(Br|Cl|Si)\b", v)
    )


def normalize_smiles_tool_arguments(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonical argument priority for docking-safe calls:
      smiles_string > name/ligand_name > query (only if not noise).

    Optional kwargs['_compound_hint']: short user text for last-resort name extraction (not supervisor blobs).
    """
    out = dict(kwargs or {})
    hint = str(out.pop("_compound_hint", "") or "").strip()

    ss = str(out.get("smiles_string") or "").strip()
    name = str(out.get("name") or out.get("ligand_name") or "").strip()
    q_raw = str(out.get("query") or "").strip()
    noisy_q = is_noise_ligand_query(q_raw) if q_raw else True

    if ss and _looks_like_provided_smiles_string(ss):
        if q_raw:
            if noisy_q or name:
                logger.info(
                    "smiles: ignoring noisy query=%r because smiles_string is present",
                    q_raw[:80],
                )
            else:
                logger.info(
                    "smiles: using explicit smiles_string (non-noisy query=%r kept as label only)",
                    q_raw[:80],
                )
        else:
            logger.info("smiles: using explicit smiles_string")
        out["smiles_string"] = ss
        if noisy_q or (q_raw and q_raw != ss):
            out.pop("query", None)
        return out

    if ss and not _looks_like_provided_smiles_string(ss):
        # Treat as name-like mistake; do not use as inline SMILES
        out.pop("smiles_string", None)
        ss = ""

    if name and not is_noise_ligand_query(name):
        if q_raw and q_raw != name and (noisy_q or is_noise_ligand_query(q_raw)):
            logger.info(
                "smiles: ignoring noisy query=%r because name=%r is present",
                q_raw[:80],
                name[:80],
            )
            out.pop("query", None)
        elif q_raw and q_raw != name and not noisy_q:
            logger.info(
                "smiles: preferring name=%r over query=%r for lookup",
                name[:80],
                q_raw[:80],
            )
            out.pop("query", None)
        out["name"] = name
        out["query"] = name
        logger.info("smiles: using name lookup %s", name)
        return out

    if q_raw and not noisy_q:
        out["query"] = q_raw
        logger.info("smiles: using query lookup %s", q_raw[:120])
        return out

    if q_raw and noisy_q:
        logger.info("smiles: dropping noisy query=%r", q_raw[:80])
        out.pop("query", None)

    # Optional plain-text hint (caller must pass a short user slice, not supervisor blobs).
    if hint and not out.get("query"):
        m = re.search(
            r"\b(acetazolamide|aspirin|adenosine|methotrexate|indole|tryptophan)\b",
            hint,
            re.IGNORECASE,
        )
        if m:
            token = m.group(1)
            if not is_noise_ligand_query(token):
                out["query"] = token
                logger.info("smiles: using common ligand token from hint: %s", token)

    return out


def _urlopen_with_proxy(request, timeout: int = 15):
    """Open request with optional HTTP(S) proxy from env (HTTP_PROXY / HTTPS_PROXY)."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(proxy_handler)
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def _get_pubchem_by_name(name: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
    """
    Fetch compound property table (SMILES, MolecularFormula, InChIKey) by name from PubChem PUG REST.
    Returns dict with PropertyTable or None on failure.
    """
    name = (name or "").strip()
    if not name:
        return None
    url = f"{PUBCHEM_PUG}/compound/name/{quote(name)}/property/SMILES,MolecularFormula,InChIKey/JSON"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MiniProt/1.0"})
        with _urlopen_with_proxy(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return data
    except Exception as e:
        logger.warning("PubChem name lookup %s failed: %s", name, e)
    return None


def _get_pubchem_by_cid(cid: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
    """Fetch compound properties by PubChem CID (numeric)."""
    cid = (cid or "").strip()
    if not cid or not cid.isdigit():
        return None
    url = f"{PUBCHEM_PUG}/compound/cid/{cid}/property/SMILES,MolecularFormula,InChIKey/JSON"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MiniProt/1.0"})
        with _urlopen_with_proxy(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return data
    except Exception as e:
        logger.warning("PubChem CID %s failed: %s", cid, e)
    return None


def _get_chembl_smiles(query: str, timeout: int = 15) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """
    Search ChEMBL by molecule name, then get canonical SMILES for first hit.
    Returns (smiles, chembl_id, formula, inchi_key) or None.
    """
    query = (query or "").strip()
    if not query:
        return None
    try:
        search_url = f"{CHEMBL_API}/molecule/search.json?q={quote(query)}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "MiniProt/1.0"})
        with _urlopen_with_proxy(req, timeout=timeout) as resp:
            search_data = json.loads(resp.read().decode())
        molecules = search_data.get("molecules") or search_data.get("page") or []
        if not molecules:
            return None
        first = molecules[0] if isinstance(molecules[0], dict) else {}
        chembl_id = first.get("molecule_chembl_id") or first.get("chembl_id")
        if not chembl_id:
            return None
        mol_url = f"{CHEMBL_API}/molecule/{chembl_id}.json"
        req2 = urllib.request.Request(mol_url, headers={"User-Agent": "MiniProt/1.0"})
        with _urlopen_with_proxy(req2, timeout=timeout) as resp2:
            mol_data = json.loads(resp2.read().decode())
        structures = (mol_data.get("molecule_structures") or {}) if isinstance(mol_data, dict) else {}
        smiles = structures.get("canonical_smiles") or structures.get("molecule_smiles")
        if not smiles:
            return None
        props = mol_data.get("molecule_properties") or {} if isinstance(mol_data, dict) else {}
        formula = props.get("full_molformula") or props.get("molecular_formula")
        inchi_key = structures.get("standard_inchi_key")
        return (smiles, chembl_id, formula, inchi_key)
    except Exception as e:
        logger.warning("ChEMBL lookup %s failed: %s", query, e)
    return None


def _parse_pubchem_property_table(data: Dict[str, Any]) -> Optional[Tuple[str, Optional[str], Optional[str], Optional[str]]]:
    """
    Parse PUG REST property JSON. Returns (smiles, formula, inchi_key, cid) or None.
    Format: {"PropertyTable": {"Properties": [{"CID": x, "SMILES": "...", ...}]}}
    """
    try:
        pt = data.get("PropertyTable") or {}
        props_list = pt.get("Properties") or []
        if not props_list:
            return None
        p = props_list[0] if isinstance(props_list[0], dict) else {}
        smiles = p.get("SMILES")
        if not smiles:
            return None
        cid = str(p.get("CID", "")) if p.get("CID") is not None else None
        return (smiles, p.get("MolecularFormula"), p.get("InChIKey"), cid)
    except Exception:
        return None


def _smiles_to_sdf_rdkit(smiles: str, out_path: str) -> bool:
    """Convert SMILES to 3D SDF using RDKit (no Open Babel). Optional: pip install rdkit."""
    try:
        from rdkit import Chem  # type: ignore[import-untyped]
        from rdkit.Chem import AllChem  # type: ignore[import-untyped]
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return False
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
        AllChem.MMFFOptimizeMolecule(mol)
        w = Chem.SDWriter(out_path)
        w.write(mol)
        w.close()
        return os.path.isfile(out_path)
    except Exception as e:
        logger.debug("RDKit SMILES->SDF failed: %s", e)
    return False


def _smiles_to_sdf(smiles: str, out_path: str, timeout: int = 30) -> bool:
    """Convert SMILES to 3D SDF. Prefer RDKit if available; else Open Babel (obabel -:SMILES -O out.sdf -h)."""
    if not (smiles or "").strip():
        return False
    # Prefer RDKit (no subprocess, no obabel required)
    if _smiles_to_sdf_rdkit(smiles.strip(), out_path):
        return True
    obabel = shutil.which("obabel")
    if not obabel:
        return False
    try:
        cmd = [obabel, "-:" + smiles.strip(), "-O", out_path, "-h"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0 and os.path.isfile(out_path)
    except Exception as e:
        logger.warning("obabel SMILES->SDF failed: %s", e)
    return False


class SMILESTool(BaseTool):
    """Get SMILES and chemical identifiers from PubChem or ChEMBL for use in docking or analysis."""

    def __init__(self):
        self._name = "smiles"
        self._description = (
            "Look up SMILES (Simplified Molecular Input Line Entry System) and related chemical data "
            "by compound name, PubChem CID, or ChEMBL ID. Use when the user asks for SMILES, chemical structure, "
            "or to get a small molecule for docking. Sources: PubChem (PUG REST), ChEMBL. "
            "Optionally save SMILES to a .smi file or convert to SDF (for autodock_vina ligand)."
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
                        "query": {
                            "type": "string",
                            "description": "Last-resort: compound name, PubChem CID, or ChEMBL ID. Ignored when smiles_string or name is set.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Ligand common name (e.g. acetazolamide). Preferred over query when smiles_string is absent.",
                        },
                        "smiles_string": {
                            "type": "string",
                            "description": (
                                "Explicit SMILES line. When set, PubChem name lookup is skipped and this SMILES is used for .smi/.sdf."
                            ),
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional full path for SDF output when output_sdf is true (.sdf suffix).",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pubchem", "chembl"],
                            "description": "Which database to use: auto (try PubChem then ChEMBL), pubchem, or chembl.",
                            "default": "auto",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save .smi or .sdf file (if output_sdf or save_smi is true).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "save_smi": {
                            "type": "boolean",
                            "description": "If true, save SMILES string to a .smi file in output_dir.",
                            "default": True,
                        },
                        "output_sdf": {
                            "type": "boolean",
                            "description": "If true, convert SMILES to 3D SDF with Open Babel and save; the SDF path can be used as ligand_pdb_path (or ligand SDF) in autodock_vina.",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs = normalize_smiles_tool_arguments(dict(kwargs))

        provided_smiles = (kwargs.get("smiles_string") or "").strip()
        query_in_raw = (kwargs.get("query") or "").strip()
        output_path_hint = (kwargs.get("output_path") or "").strip()
        candidate_inline = ""
        if provided_smiles and _looks_like_provided_smiles_string(provided_smiles):
            candidate_inline = provided_smiles
        elif query_in_raw and _looks_like_provided_smiles_string(query_in_raw):
            candidate_inline = query_in_raw.strip()

        source = (kwargs.get("source") or "auto").strip().lower() or "auto"
        output_dir_kw = (kwargs.get("output_dir") or "").strip()

        explicitly_sdf = bool(kwargs.get("output_sdf", False))

        sdf_target_explicit: Optional[str] = None
        if output_path_hint.lower().endswith(".sdf"):
            sdf_target_explicit = os.path.abspath(output_path_hint)
            explicitly_sdf = True

        parent_for_output = ""
        if sdf_target_explicit:
            parent_for_output = os.path.dirname(sdf_target_explicit)
        elif output_dir_kw:
            parent_for_output = output_dir_kw
        else:
            parent_for_output = DEFAULT_OUTPUT_DIR
        output_dir = safe_dir(parent_for_output.strip())
        save_smi = bool(kwargs.get("save_smi", True))
        output_sdf = explicitly_sdf

        smiles: Optional[str] = None
        molecular_formula: Optional[str] = None
        inchi_key: Optional[str] = None
        cid: Optional[str] = None
        chembl_id: Optional[str] = None
        used_source = ""

        explicit_name_candidate = ""
        if query_in_raw and not _looks_like_provided_smiles_string(query_in_raw):
            explicit_name_candidate = query_in_raw
        ligand_kw = (kwargs.get("ligand_label") or kwargs.get("ligand_name") or "").strip()
        if ligand_kw and not _looks_like_provided_smiles_string(ligand_kw):
            explicit_name_candidate = explicit_name_candidate or ligand_kw

        lookup_query = ""
        if candidate_inline:
            smiles = candidate_inline.strip()
            used_source = "inline_smiles_string"
        else:
            lookup_query = (query_in_raw or "").strip()

        # PubChem: by CID (numeric) or by name
        if not smiles and lookup_query and source in ("auto", "pubchem"):
            if lookup_query.isdigit():
                data = _get_pubchem_by_cid(lookup_query)
                if data:
                    parsed = _parse_pubchem_property_table(data)
                    if parsed:
                        smiles, molecular_formula, inchi_key, cid = parsed
                        used_source = "pubchem"
                        cid = cid or lookup_query
            if not smiles:
                data = _get_pubchem_by_name(lookup_query)
                if data:
                    parsed = _parse_pubchem_property_table(data)
                    if parsed:
                        smiles, molecular_formula, inchi_key, cid = parsed
                        used_source = "pubchem"

        # ChEMBL: search by name or direct by ChEMBL ID
        if not smiles and lookup_query and source in ("auto", "chembl"):
            if lookup_query.upper().startswith("CHEMBL"):
                try:
                    url = f"{CHEMBL_API}/molecule/{lookup_query}.json"
                    req = urllib.request.Request(url, headers={"User-Agent": "MiniProt/1.0"})
                    with _urlopen_with_proxy(req, timeout=15) as resp:
                        mol_data = json.loads(resp.read().decode())
                    structures = (mol_data.get("molecule_structures") or {}) if isinstance(mol_data, dict) else {}
                    smiles = structures.get("canonical_smiles") or structures.get("molecule_smiles")
                    if smiles:
                        chembl_id = lookup_query
                        used_source = "chembl"
                        props = mol_data.get("molecule_properties") or {} if isinstance(mol_data, dict) else {}
                        molecular_formula = props.get("full_molformula") or props.get("molecular_formula")
                        inchi_key = structures.get("standard_inchi_key")
                except Exception as e:
                    logger.warning("ChEMBL direct %s failed: %s", lookup_query, e)
            else:
                result = _get_chembl_smiles(lookup_query)
                if result:
                    smiles, chembl_id, molecular_formula, inchi_key = result
                    used_source = "chembl"

        basename_from_hint = ""
        if output_path_hint.lower().endswith(".sdf"):
            basename_from_hint = os.path.splitext(os.path.basename(output_path_hint))[0]
        elif sdf_target_explicit and sdf_target_explicit.lower().endswith(".sdf"):
            basename_from_hint = os.path.splitext(os.path.basename(sdf_target_explicit))[0]

        label = (
            (lookup_query or "").strip()
            or explicit_name_candidate.strip()
            or basename_from_hint
            or "ligand_inline"
        )
        if not (smiles or "").strip():
            return {
                "success": False,
                "error": (
                    "Provide compound `query` or `name`, or set `smiles_string` / pass SMILES into `query`. "
                    "If the network blocks access for names, use inline SMILES or set HTTP_PROXY/HTTPS_PROXY."
                ),
                "data": {"query": kwargs.get("query"), "provided_smiles": bool(provided_smiles)},
            }

        downloaded: Dict[str, List[str]] = {}
        query = label
        base_name = safe_filename((label.replace(" ", "_") or "ligand"))

        smiles = smiles.strip()
        if save_smi:
            smi_path = os.path.join(output_dir, f"{base_name}.smi")
            try:
                with open(smi_path, "w") as f:
                    f.write(smiles)
                downloaded["smi"] = [smi_path]
            except Exception as e:
                logger.warning("Write .smi failed: %s", e)
        if output_sdf:
            if sdf_target_explicit:
                sdf_path = sdf_target_explicit
            else:
                sdf_path = os.path.join(output_dir, f"{base_name}.sdf")
            os.makedirs(os.path.dirname(sdf_path) or ".", exist_ok=True)
            if _smiles_to_sdf(smiles, sdf_path):
                downloaded["sdf"] = [sdf_path]
            else:
                logger.warning("SMILES to SDF conversion failed (Open Babel required).")

        message = f"SMILES for '{query}' from {used_source}: {smiles[:60]}{'...' if len(smiles) > 60 else ''}"
        if downloaded:
            message += f" Saved to {output_dir}."

        return {
            "success": True,
            "data": {
                "message": message,
                "smiles": smiles,
                "molecular_formula": molecular_formula,
                "inchi_key": inchi_key,
                "pubchem_cid": cid,
                "chembl_id": chembl_id,
                "source": used_source,
                "downloaded": downloaded,
                "output_dir": output_dir,
                "query": query,
                "ligand_source": used_source,
            },
        }
