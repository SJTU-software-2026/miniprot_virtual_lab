"""
ESMFold tool: predict protein 3D structure (PDB) from sequence using ESMFold (Hugging Face).

Uses transformers EsmForProteinFolding (facebook/esmfold_v1). Sequence length limit ~1024 aa.
Install: pip install transformers torch; GPU recommended.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/esmfold"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, safe_run_id, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, safe_run_id, ensure_file_permissions

# Max sequence length ESMFold typically supports
ESMFOLD_MAX_LENGTH = 1024


def _parse_fasta(path: str) -> List[Tuple[str, str]]:
    """Return list of (header_line, sequence) from FASTA. Header includes '>'."""
    entries = []
    with open(path, "r") as f:
        current_header = None
        current_seq: List[str] = []
        for line in f:
            line = line.rstrip("\n\r")
            if line.startswith(">"):
                if current_header is not None:
                    entries.append((current_header, "".join(current_seq)))
                current_header = line
                current_seq = []
            else:
                current_seq.append(line.strip())
        if current_header is not None:
            entries.append((current_header, "".join(current_seq)))
    return entries


def _run_esmfold_impl(fasta_path: str, output_dir: str, device: Optional[str] = None, timeout: int = 600) -> Dict[str, Any]:
    """
    Run ESMFold on FASTA and write PDBs. Uses Hugging Face transformers.
    Returns dict with success, paths, or error.
    """
    try:
        import torch
        from transformers import AutoTokenizer, EsmForProteinFolding
    except ImportError as e:
        return {
            "success": False,
            "error": f"ESMFold requires: pip install transformers torch. {e}",
            "data": {},
        }

    entries = _parse_fasta(fasta_path)
    if not entries:
        return {"success": False, "error": "No sequences in FASTA.", "data": {}}

    if device is None:
        device = "cuda:0" if torch.cuda.is_available() else "cpu"

    try:
        tokenizer = AutoTokenizer.from_pretrained("facebook/esmfold_v1")
        model = EsmForProteinFolding.from_pretrained("facebook/esmfold_v1", low_cpu_mem_usage=True)
        model = model.to(device)
        if device != "cpu":
            try:
                model.esm = model.esm.half()
            except Exception:
                pass
    except Exception as e:
        return {"success": False, "error": f"Failed to load ESMFold model: {e}", "data": {}}

    try:
        from transformers.models.esm.openfold_utils.protein import to_pdb, Protein as OFProtein
        from transformers.models.esm.openfold_utils.feats import atom14_to_atom37
    except ImportError:
        return {
            "success": False,
            "error": "ESMFold PDB export requires transformers with ESM openfold_utils.",
            "data": {},
        }

    def convert_one_to_pdb(outputs, i: int = 0) -> str:
        """Convert batch index i of model outputs to PDB string."""
        final_atom_positions = atom14_to_atom37(outputs["positions"][-1], outputs)
        final_atom_positions = final_atom_positions[i].cpu().numpy()
        mask = outputs["atom37_atom_exists"][i].cpu().numpy()
        aa = outputs["aatype"][i].cpu().numpy()
        resid = (outputs["residue_index"][i].cpu().numpy() + 1)
        bf = outputs["plddt"][i].cpu().numpy()
        chain = outputs["chain_index"][i].cpu().numpy() if "chain_index" in outputs else None
        pred = OFProtein(
            aatype=aa,
            atom_positions=final_atom_positions,
            atom_mask=mask,
            residue_index=resid,
            b_factors=bf,
            chain_index=chain,
        )
        return to_pdb(pred)

    safe_dir(output_dir)
    pdbs: List[str] = []
    base_name = os.path.splitext(os.path.basename(fasta_path))[0]

    for idx, (header, seq) in enumerate(entries):
        seq_clean = "".join(seq.split()).upper()
        if not seq_clean:
            continue
        if len(seq_clean) > ESMFOLD_MAX_LENGTH:
            logger.warning("ESMFold skipping sequence %d (len %d > %d)", idx + 1, len(seq_clean), ESMFOLD_MAX_LENGTH)
            continue
        try:
            tokenized = tokenizer([seq_clean], return_tensors="pt", add_special_tokens=False)
            input_ids = tokenized["input_ids"].to(device)
            with torch.no_grad():
                outputs = model(input_ids)
            # Extract tensors for PDB (names may vary by transformers version)
            aatype = outputs["aatype"]
            atom37_atom_exists = outputs["atom37_atom_exists"]
            residue_index = outputs["residue_index"]
            plddt = outputs["plddt"]
            pdb_str = convert_one_to_pdb(outputs, 0)
            # Use header-derived name or default
            name = header[1:].split()[0][:50] if header.startswith(">") else f"seq{idx+1}"
            safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
            out_path = os.path.join(output_dir, f"{base_name}_{safe_name}_{idx+1}.pdb")
            with open(out_path, "w") as f:
                f.write(pdb_str)
            ensure_file_permissions(out_path)
            pdbs.append(os.path.abspath(out_path))
        except Exception as e:
            logger.exception("ESMFold failed for sequence %d: %s", idx + 1, e)
            continue

    if not pdbs:
        return {
            "success": False,
            "error": "ESMFold produced no PDB files (check sequence length <= 1024 and model load).",
            "data": {"output_dir": os.path.abspath(output_dir)},
        }
    return {
        "success": True,
        "data": {
            "message": f"ESMFold predicted {len(pdbs)} structure(s).",
            "output_dir": os.path.abspath(output_dir),
            "downloaded": {"pdb": pdbs},
            "output_paths": pdbs,
        },
    }


class ESMFoldTool(BaseTool):
    """Predict 3D structure (PDB) from protein sequence using ESMFold."""

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "esmfold",
                "description": (
                    "Predict protein 3D structure (PDB) from sequence using ESMFold. "
                    "Input: FASTA file. Max sequence length 1024 aa. Use when OmegaFold is not available or for shorter sequences."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fasta_path": {"type": "string", "description": "Path to input FASTA file."},
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to write PDB files. Default: data/outputs/esmfold.",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "device": {"type": "string", "description": "Device: cuda:0 or cpu. Default: auto."},
                    },
                    "required": ["fasta_path"],
                },
            },
        }

    @property
    def name(self) -> str:
        return "esmfold"

    @property
    def description(self) -> str:
        return (
            "Predict protein 3D structure (PDB) from sequence using ESMFold. "
            "Max length 1024 aa. Fallback when OmegaFold fails or for shorter sequences."
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        fasta_path = (kwargs.get("fasta_path") or "").strip()
        if not fasta_path:
            return {"success": False, "error": "fasta_path is required.", "data": {}}
        root = workspace_root()
        if not os.path.isabs(fasta_path):
            fasta_path = os.path.normpath(os.path.join(root, fasta_path))
        if not os.path.isfile(fasta_path):
            return {"success": False, "error": f"FASTA file not found: {fasta_path}", "data": {}}

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(root, output_dir)))
        default_base = os.path.normpath(os.path.join(root, DEFAULT_OUTPUT_DIR))
        if output_dir == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)

        device = (kwargs.get("device") or "").strip() or None
        return _run_esmfold_impl(fasta_path, output_dir, device=device)
