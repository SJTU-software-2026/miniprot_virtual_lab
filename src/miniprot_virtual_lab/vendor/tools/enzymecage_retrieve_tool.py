"""
EnzymeCAGE retrieval: rank candidate enzymes for a reaction SMILES via the external
EnzymeCAGE mining pipeline (separate conda env; subprocess only — no import in MiniProt).
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/enzymecage_retrieve"
AF_API = "https://alphafold.ebi.ac.uk/api/prediction"
AF_FILES_BASE = "https://alphafold.ebi.ac.uk/files"
AF_MODEL_VERSIONS = ("v6", "v5", "v4", "v3", "v2")
# Swiss-Prot / TrEMBL: 1 letter + 5–9 alphanumeric (e.g. P00918, Q8IWU9, A0A0B4J1F0)
_UNIPROT_RE = re.compile(r"^[A-Z][A-Z0-9]{5,9}$", re.I)
_AF_NAME_RE = re.compile(r"AF-([A-NR-Z0-9]+)-F\d+", re.I)


def _miniprot_project_root() -> str:
    """enzyme_update root (parent of src/), even when cwd is src/."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..", "..")),
        os.path.abspath(os.path.join(here, "..")),
    ]
    try:
        from utils.path_utils import workspace_root
    except ImportError:
        from ..utils.path_utils import workspace_root

    candidates.insert(0, workspace_root())
    for base in candidates:
        if os.path.isfile(os.path.join(base, "config", "tool_registry.json")):
            return base
        if os.path.isfile(os.path.join(base, ".env")):
            return base
    return candidates[0]


def _load_miniprot_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = os.path.join(_miniprot_project_root(), ".env")
    if os.path.isfile(env_path):
        load_dotenv(env_path, override=False)


def _enzymecage_root() -> str:
    _load_miniprot_dotenv()
    env = (os.environ.get("ENZYMECAGE_ROOT") or "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)

    wr = _miniprot_project_root()
    pipeline = os.path.join("scripts", "run_mining_pipeline.py")
    search_bases = [
        wr,
        os.path.dirname(wr),
        os.path.abspath(os.path.join(wr, "..")),
    ]
    for base in search_bases:
        for rel in ("EnzymeCAGE", os.path.join("enzyme_mining", "EnzymeCAGE"), "../EnzymeCAGE"):
            c = os.path.abspath(os.path.join(base, rel))
            if os.path.isfile(os.path.join(c, pipeline)):
                return c
    return os.path.abspath(os.path.join(os.path.dirname(wr), "EnzymeCAGE"))


def _enzymecage_python() -> str:
    for key in ("ENZYMECAGE_PYTHON", "ENZYMECAGE_PYTHON_BIN"):
        p = (os.environ.get(key) or "").strip()
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    env_name = (os.environ.get("ENZYMECAGE_CONDA_ENV") or "enzymecage").strip()
    for base in (
        os.environ.get("CONDA_PREFIX", ""),
        os.path.expanduser(f"~/miniconda3/envs/{env_name}"),
        os.path.expanduser(f"~/anaconda3/envs/{env_name}"),
        os.path.expanduser(f"~/micromamba/envs/{env_name}"),
    ):
        if not base:
            continue
        candidate = os.path.join(base, "bin", "python")
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def _p2rank_home() -> str:
    explicit = (os.environ.get("P2RANK_HOME") or "").strip()
    if explicit and os.path.isdir(explicit):
        return os.path.abspath(explicit)
    return os.path.join(_enzymecage_root(), "tools", "p2rank_2.5.1")


def _checkpoint_dir() -> str:
    env = (os.environ.get("ENZYMECAGE_CHECKPOINT_DIR") or "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.path.join(_enzymecage_root(), "checkpoints", "pretrain", "seed_42")


def _model_name() -> str:
    return (os.environ.get("ENZYMECAGE_MODEL_NAME") or "epoch_19.pth").strip()


def _normalize_uniprot_ids(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,;\s]+", raw.strip())
    elif isinstance(raw, list):
        parts = [str(x).strip() for x in raw]
    else:
        return []
    out: List[str] = []
    seen = set()
    for p in parts:
        p = p.strip().upper()
        if not p or p in seen:
            continue
        if _UNIPROT_RE.match(p):
            seen.add(p)
            out.append(p)
    return out


def _accession_from_filename(path: str) -> Optional[str]:
    base = os.path.splitext(os.path.basename(path))[0]
    m = _AF_NAME_RE.match(base)
    if m:
        return m.group(1).upper()
    if _UNIPROT_RE.match(base):
        return base.upper()
    return None


def _download_alphafold_pdb(uniprot_id: str, dest_path: str) -> bool:
    """Download canonical AF-{uid}-F1 PDB; uses API pdbUrl then versioned file URLs (v6 first)."""
    uid = uniprot_id.strip().upper()
    entry_id = f"AF-{uid}-F1"
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    try:
        resp = requests.get(f"{AF_API}/{uid}", timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                eid = (entry.get("entryId") or entry.get("entry_id") or "").upper()
                if eid and eid != entry_id:
                    continue
                pdb_url = entry.get("pdbUrl") or entry.get("pdb_url")
                if pdb_url:
                    r2 = requests.get(pdb_url, timeout=120)
                    if r2.status_code == 200 and len(r2.content) > 500:
                        with open(dest_path, "wb") as f:
                            f.write(r2.content)
                        return True
    except requests.RequestException as exc:
        logger.warning("AlphaFold API %s: %s", uid, exc)

    for ver in AF_MODEL_VERSIONS:
        url = f"{AF_FILES_BASE}/{entry_id}-model_{ver}.pdb"
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 200 and len(resp.content) > 500:
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                return True
        except requests.RequestException as exc:
            logger.warning("AlphaFold download %s: %s", url, exc)
    return False


def _prepare_structures(
    structure_dir: str,
    uniprot_ids: List[str],
    download_missing: bool,
) -> Dict[str, str]:
    """Return map uniprot_id -> pdb path named <UID>.pdb under structure_dir."""
    os.makedirs(structure_dir, exist_ok=True)
    uid_to_path: Dict[str, str] = {}

    for ext in ("*.pdb", "*.cif"):
        for path in glob.glob(os.path.join(structure_dir, ext)):
            acc = _accession_from_filename(path)
            if acc:
                dest = os.path.join(structure_dir, f"{acc}.pdb")
                if os.path.abspath(path) != os.path.abspath(dest):
                    if ext.endswith(".pdb"):
                        shutil.copy2(path, dest)
                    elif acc not in uid_to_path:
                        uid_to_path[acc] = path
                else:
                    uid_to_path[acc] = path

    for uid in uniprot_ids:
        dest = os.path.join(structure_dir, f"{uid}.pdb")
        if os.path.isfile(dest):
            uid_to_path[uid] = dest
            continue
        if download_missing:
            if _download_alphafold_pdb(uid, dest):
                uid_to_path[uid] = dest

    return uid_to_path


class EnzymeCAGERetrieveTool(BaseTool):
    """Run EnzymeCAGE mining pipeline in a separate Python env; returns ranked enzyme–reaction scores."""

    def __init__(self) -> None:
        self._name = "enzymecage_retrieve"
        self._description = (
            "Rank candidate enzymes for a chemical reaction (CANO_RXN_SMILES, format substrates>>products) "
            "using the EnzymeCAGE geometric retrieval model. Requires ENZYMECAGE_PYTHON (enzymecage conda env), "
            "checkpoint under ENZYMECAGE_CHECKPOINT_DIR, and P2RANK_HOME. Provide reaction_smiles and "
            "uniprot_ids (structures downloaded from AlphaFold DB) or an existing structure_dir with "
            "<UniProtID>.pdb files. Runs scripts/run_mining_pipeline.py via subprocess; keep candidate count "
            "small (typically ≤50). Not for full RHEA database feature builds."
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
                        "reaction_smiles": {
                            "type": "string",
                            "description": (
                                "Reaction SMILES in EnzymeCAGE format: substrates>>products "
                                "(e.g. CC(C)(O)C#N>>C#N.CC(C)=O)."
                            ),
                        },
                        "uniprot_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Candidate enzyme UniProt accessions (e.g. P00918, P06213).",
                        },
                        "structure_dir": {
                            "type": "string",
                            "description": (
                                "Directory with candidate structures named <UniProtID>.pdb or .cif. "
                                "If omitted, structures are written under the run work_dir/structures."
                            ),
                        },
                        "download_structures": {
                            "type": "boolean",
                            "description": "Download missing structures from AlphaFold DB when uniprot_ids are set.",
                            "default": True,
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of top-ranked rows to return in the summary (default 20).",
                            "default": 20,
                        },
                        "p2rank_threads": {
                            "type": "integer",
                            "description": "P2Rank thread count (default 4).",
                            "default": 4,
                        },
                        "skip_p2rank": {"type": "boolean", "default": False},
                        "skip_feature": {"type": "boolean", "default": False},
                        "skip_infer": {"type": "boolean", "default": False},
                        "output_dir": {
                            "type": "string",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                    },
                    "required": ["reaction_smiles"],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        _load_miniprot_dotenv()

        try:
            from utils.path_utils import ensure_file_permissions, resolve_output_dir, safe_dir, safe_run_id
        except ImportError:
            from ..utils.path_utils import ensure_file_permissions, resolve_output_dir, safe_dir, safe_run_id

        reaction_smiles = (kwargs.get("reaction_smiles") or "").strip()
        if not reaction_smiles or ">>" not in reaction_smiles:
            return {
                "success": False,
                "error": "reaction_smiles is required and must contain '>>' (substrates>>products).",
                "data": {},
            }

        uniprot_ids = _normalize_uniprot_ids(kwargs.get("uniprot_ids"))
        if not uniprot_ids and kwargs.get("_session_uniprot_ids"):
            uniprot_ids = _normalize_uniprot_ids(kwargs.get("_session_uniprot_ids"))

        root = _enzymecage_root()
        pipeline_script = os.path.join(root, "scripts", "run_mining_pipeline.py")
        if not os.path.isfile(pipeline_script):
            return {
                "success": False,
                "error": (
                    f"EnzymeCAGE not found at {root}. Set ENZYMECAGE_ROOT in "
                    f"{os.path.join(_miniprot_project_root(), '.env')} "
                    "(expected e.g. /home/.../enzyme_mining/EnzymeCAGE)."
                ),
                "data": {"enzymecage_root_guess": root, "project_root": _miniprot_project_root()},
            }

        python_bin = _enzymecage_python()
        p2rank_home = _p2rank_home()
        if not os.path.isdir(p2rank_home):
            return {
                "success": False,
                "error": f"P2Rank not found at {p2rank_home}. Set P2RANK_HOME.",
                "data": {},
            }

        ckpt_dir = _checkpoint_dir()
        model_name = _model_name()
        ckpt_path = os.path.join(ckpt_dir, model_name)
        if not os.path.isfile(ckpt_path):
            return {
                "success": False,
                "error": f"Checkpoint not found: {ckpt_path}. Set ENZYMECAGE_CHECKPOINT_DIR / ENZYMECAGE_MODEL_NAME.",
                "data": {"checkpoint_dir": ckpt_dir},
            }

        run_id = safe_run_id()
        base_out = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        work_dir = safe_dir(os.path.join(base_out, run_id))
        structures_dir = (kwargs.get("structure_dir") or "").strip()
        if structures_dir:
            structures_dir = os.path.abspath(structures_dir)
        else:
            structures_dir = os.path.join(work_dir, "structures")

        uid_to_path = _prepare_structures(
            structures_dir,
            uniprot_ids,
            download_missing=bool(kwargs.get("download_structures", True)),
        )
        if not uniprot_ids and kwargs.get("uniprot_ids"):
            return {
                "success": False,
                "error": (
                    "No valid UniProt accessions after parsing uniprot_ids. "
                    "Use standard IDs like P00918, P06213."
                ),
                "data": {"received": kwargs.get("uniprot_ids")},
            }

        if not uid_to_path:
            return {
                "success": False,
                "error": (
                    "No candidate structures available. Provide uniprot_ids (with download_structures=true) "
                    "or structure_dir with <UniProtID>.pdb files."
                ),
                "data": {
                    "structure_dir": structures_dir,
                    "uniprot_ids_requested": uniprot_ids,
                    "download_structures": bool(kwargs.get("download_structures", True)),
                },
            }

        reaction_csv = os.path.join(work_dir, "reaction.csv")
        pd.DataFrame({"CANO_RXN_SMILES": [reaction_smiles]}).to_csv(reaction_csv, index=False)
        ensure_file_permissions(reaction_csv)

        cmd = [
            python_bin,
            pipeline_script,
            "--data_dir",
            work_dir,
            "--p2rank_home",
            p2rank_home,
            "--checkpoint_dir",
            ckpt_dir,
            "--model_name",
            model_name,
            "--threads",
            str(int(kwargs.get("p2rank_threads") or 4)),
        ]
        if kwargs.get("skip_p2rank"):
            cmd.append("--skip_p2rank")
        if kwargs.get("skip_feature"):
            cmd.append("--skip_feature")
        if kwargs.get("skip_infer"):
            cmd.append("--skip_infer")

        timeout = int(os.environ.get("ENZYMECAGE_SUBPROCESS_TIMEOUT", "7200"))
        env = os.environ.copy()
        java_home = (os.environ.get("JAVA_HOME") or "").strip()
        if java_home:
            env["JAVA_HOME"] = java_home
            env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")

        logger.info("EnzymeCAGE subprocess: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"EnzymeCAGE pipeline timed out after {timeout}s.",
                "data": {"work_dir": work_dir},
            }

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-4000:]
            return {
                "success": False,
                "error": f"EnzymeCAGE pipeline failed (exit {proc.returncode}).",
                "data": {"work_dir": work_dir, "stderr_tail": tail},
            }

        pred_dir = os.path.join(work_dir, "predictions")
        ranked_glob = glob.glob(os.path.join(pred_dir, "*_ranked.csv"))
        ranked_path = ranked_glob[0] if ranked_glob else ""
        if not ranked_path or not os.path.isfile(ranked_path):
            return {
                "success": False,
                "error": "Pipeline finished but ranked CSV not found under predictions/.",
                "data": {"work_dir": work_dir, "stdout_tail": (proc.stdout or "")[-2000:]},
            }

        ensure_file_permissions(ranked_path)
        df = pd.read_csv(ranked_path)
        top_k = max(1, int(kwargs.get("top_k") or 20))
        preview_cols = [c for c in ("rank", "UniprotID", "pred", "CANO_RXN_SMILES") if c in df.columns]
        preview = df.head(top_k)[preview_cols].to_dict(orient="records") if preview_cols else df.head(top_k).to_dict(orient="records")

        pdb_paths = [uid_to_path.get(u, "") for u in df.head(top_k)["UniprotID"].tolist() if "UniprotID" in df.columns]
        pdb_paths = [p for p in pdb_paths if p and os.path.isfile(p)]

        return {
            "success": True,
            "data": {
                "work_dir": work_dir,
                "ranked_csv_path": ranked_path,
                "mining_csv_path": os.path.join(work_dir, "mining.csv"),
                "structure_dir": structures_dir,
                "n_candidates": len(uid_to_path),
                "n_pairs_scored": len(df),
                "top_ranked": preview,
                "enzymecage_python": python_bin,
                "enzymecage_root": root,
            },
            "artifacts": {
                "format": "csv",
                "paths": [ranked_path],
                "tool": self.name,
                "pdb_paths": pdb_paths,
            },
        }
