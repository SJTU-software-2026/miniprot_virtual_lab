"""
Structure-from-FASTA tool: two modes controlled by prefer_prediction.

- prefer_prediction=False (default): User did NOT explicitly ask to "predict" structure.
  First try AlphaFold download (extract UniProt IDs from FASTA → fetch from AlphaFold DB).
  If no UniProt IDs or download fails, then try OmegaFold → ESMFold.

- prefer_prediction=True: User explicitly asked to "predict" structure (e.g. "predict 3D structure").
  Use OmegaFold first, then ESMFold, then AlphaFold download as fallback.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/structure_from_fasta"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, safe_run_id
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, safe_run_id

try:
    from utils.fasta_parser import parse_fasta_paths, extract_uniprot_ids_from_entries
except ImportError:
    from ..utils.fasta_parser import parse_fasta_paths, extract_uniprot_ids_from_entries


class StructureFromFastaTool(BaseTool):
    """Get or predict structure from FASTA: AlphaFold download first (default) or OmegaFold/ESMFold when user explicitly asks to predict."""

    @property
    def name(self) -> str:
        return "structure_from_fasta"

    @property
    def description(self) -> str:
        return (
            "Get or predict 3D structure from FASTA. "
            "Default (prefer_prediction=false): Extract UniProt IDs from FASTA headers and download from AlphaFold DB first; if that fails, try OmegaFold then ESMFold. "
            "When user explicitly says 'predict structure' or 'predict 3D structure' (prefer_prediction=true): Use OmegaFold first, then ESMFold, then AlphaFold download as fallback. "
            "Use when user wants structure from FASTA or session FASTA. Set prefer_prediction=true only when they explicitly ask to predict."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "fasta_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths to FASTA file(s). UniProt IDs are extracted from headers for AlphaFold download; first file is used for OmegaFold/ESMFold.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for PDB. Default: data/outputs/structure_from_fasta.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "prefer_prediction": {
                        "type": "boolean",
                        "description": "If true, use OmegaFold then ESMFold (user explicitly asked to 'predict' structure). If false (default), try AlphaFold download first (UniProt ID → AlphaFold DB).",
                        "default": False,
                    },
                },
                "required": ["fasta_paths"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        fasta_paths = list(kwargs.get("fasta_paths") or [])
        if not fasta_paths:
            return {"success": False, "error": "fasta_paths is required.", "data": {}}
        root = workspace_root()
        resolved: List[str] = []
        for p in fasta_paths:
            p = (p or "").strip()
            if not p:
                continue
            if not os.path.isabs(p):
                p = os.path.normpath(os.path.join(root, p))
            if os.path.isfile(p):
                resolved.append(p)
        if not resolved:
            return {"success": False, "error": "No existing FASTA file found in fasta_paths.", "data": {}}

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(root, output_dir)))
        default_base = os.path.normpath(os.path.join(root, DEFAULT_OUTPUT_DIR))
        if output_dir == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)

        first_fasta = resolved[0]
        prefer_prediction = bool(kwargs.get("prefer_prediction", False))
        try:
            from tool_runner import ToolManager
        except ImportError:
            from ..tool_runner import ToolManager

        entries = parse_fasta_paths(resolved)
        uniprot_ids = extract_uniprot_ids_from_entries(entries) if entries else []

        def _try_alphafold_download() -> Dict[str, Any]:
            if not uniprot_ids:
                return {"success": False, "error": "No UniProt IDs in FASTA headers (use >sp|P12345|... or >UniProtKB:P12345).", "data": {}}
            return ToolManager.run_tool(
                "alphafold",
                action="download",
                uniprot_ids=uniprot_ids,
                output_dir=output_dir,
                formats=["pdb"],
            )

        def _try_omegafold_then_esmfold() -> Dict[str, Any]:
            omegafold_result = ToolManager.run_tool("omegafold", fasta_path=first_fasta, output_dir=output_dir)
            if omegafold_result.get("success"):
                logger.info("structure_from_fasta: OmegaFold succeeded; returning PDB result.")
                return omegafold_result
            esmfold_result = ToolManager.run_tool("esmfold", fasta_path=first_fasta, output_dir=output_dir)
            if esmfold_result.get("success"):
                logger.info("structure_from_fasta: ESMFold succeeded; returning PDB result.")
                return esmfold_result
            return omegafold_result  # return last failure for error message

        if prefer_prediction:
            # User explicitly asked to predict: OmegaFold → ESMFold → AlphaFold fallback
            pred_result = _try_omegafold_then_esmfold()
            if pred_result.get("success"):
                return pred_result
            af_result = _try_alphafold_download()
            if af_result.get("success"):
                data = af_result.get("data") or {}
                data["message"] = (
                    (data.get("message") or "")
                    + " (Prediction failed; structures fetched from AlphaFold DB by sequence ID.)"
                )
                af_result["data"] = data
            return af_result
        else:
            # Default: try AlphaFold download first (UniProt ID → AlphaFold DB)
            af_result = _try_alphafold_download()
            if af_result.get("success"):
                logger.info("structure_from_fasta: AlphaFold download succeeded (UniProt ID → AlphaFold DB).")
                return af_result
            # No IDs or download failed: try OmegaFold then ESMFold
            if not uniprot_ids:
                logger.info("structure_from_fasta: No UniProt IDs in FASTA; trying OmegaFold then ESMFold.")
            else:
                logger.info("structure_from_fasta: AlphaFold download failed; trying OmegaFold then ESMFold.")
            pred_result = _try_omegafold_then_esmfold()
            if pred_result.get("success"):
                return pred_result
            if not uniprot_ids:
                return {
                    "success": False,
                    "error": (
                        "No UniProt IDs in FASTA headers and OmegaFold/ESMFold failed. "
                        "Use headers like >sp|P12345|... for AlphaFold, or set prefer_prediction=true to predict from sequence."
                    ),
                    "data": {
                        "omegafold_error": pred_result.get("error", "unknown"),
                    },
                }
            return af_result
