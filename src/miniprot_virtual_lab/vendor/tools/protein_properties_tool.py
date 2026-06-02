"""
Protein properties tool: compute basic physicochemical properties for protein
sequences from a FASTA file or a single inline sequence.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import csv
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/protein_properties"
DEFAULT_PROPERTIES = [
    "length",
    "molecular_weight",
    "aromaticity",
    "instability_index",
    "isoelectric_point",
    "gravy",
    "charge_at_ph",
]
STANDARD_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
MAX_INLINE_ROWS = 50

try:
    from utils.fasta_parser import parse_fasta
except ImportError:
    from ..utils.fasta_parser import parse_fasta

try:
    from utils.path_utils import (
        ensure_file_permissions,
        resolve_output_dir,
        safe_dir,
        safe_filename,
        safe_run_id,
        workspace_root,
    )
except ImportError:
    from ..utils.path_utils import (
        ensure_file_permissions,
        resolve_output_dir,
        safe_dir,
        safe_filename,
        safe_run_id,
        workspace_root,
    )


def _normalize_sequence(seq: str) -> str:
    """Uppercase and strip whitespace/terminal stop symbols."""
    return "".join((seq or "").upper().split()).replace("*", "")


def _invalid_residues(seq: str) -> List[str]:
    """Return sorted non-standard residues in the sequence."""
    return sorted({aa for aa in seq if aa not in STANDARD_AA})


def _normalize_properties(value: Any) -> List[str]:
    """Normalize requested property names and validate them."""
    if value is None:
        return list(DEFAULT_PROPERTIES)
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, Sequence):
        raw = [str(part).strip() for part in value if str(part).strip()]
    else:
        raw = []
    if not raw:
        return list(DEFAULT_PROPERTIES)
    normalized: List[str] = []
    for item in raw:
        key = item.lower().replace("-", "_").replace(" ", "_")
        if key == "charge_at_ph":
            key = "charge_at_ph"
        if key not in DEFAULT_PROPERTIES:
            raise ValueError(
                f"Unsupported property '{item}'. Supported: {', '.join(DEFAULT_PROPERTIES)}"
            )
        if key not in normalized:
            normalized.append(key)
    return normalized


def _build_output_basename(
    requested: str,
    input_fasta: str,
    sequence_id: str,
) -> str:
    """Choose a stable CSV basename."""
    if requested:
        return safe_filename(requested)
    if input_fasta:
        return safe_filename(os.path.splitext(os.path.basename(input_fasta))[0] or "protein_properties")
    return safe_filename(sequence_id or "protein_properties")


class ProteinPropertiesTool(BaseTool):
    """Compute protein physicochemical properties for FASTA or inline sequences."""

    def __init__(self) -> None:
        self._name = "protein_properties"
        self._description = (
            "Compute protein physicochemical properties from a FASTA file or a single protein sequence. "
            "Outputs include length, molecular weight, aromaticity, instability index, "
            "isoelectric point, GRAVY, and charge at a chosen pH."
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
                        "input_fasta": {
                            "type": "string",
                            "description": "Path to a FASTA file containing one or more protein sequences.",
                        },
                        "sequence": {
                            "type": "string",
                            "description": "Single inline protein sequence. Use this when no FASTA file is available.",
                        },
                        "sequence_id": {
                            "type": "string",
                            "description": "Identifier for the inline sequence. Ignored when input_fasta is used.",
                            "default": "sequence_1",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional description for the inline sequence.",
                        },
                        "properties": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": DEFAULT_PROPERTIES,
                            },
                            "description": "Subset of properties to compute. Defaults to all supported properties.",
                        },
                        "ph": {
                            "type": "number",
                            "description": "pH used for charge_at_ph.",
                            "default": 7.0,
                        },
                        "skip_invalid_sequences": {
                            "type": "boolean",
                            "description": "If true, skip sequences containing non-standard amino-acid letters; otherwise fail.",
                            "default": True,
                        },
                        "output_csv": {
                            "type": "boolean",
                            "description": "If true, write a CSV file with all computed rows.",
                            "default": True,
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to write the CSV output.",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "output_basename": {
                            "type": "string",
                            "description": "Optional basename for the CSV file, without extension.",
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        input_fasta = (kwargs.get("input_fasta") or "").strip()
        sequence = (kwargs.get("sequence") or "").strip()
        sequence_id = (kwargs.get("sequence_id") or "sequence_1").strip() or "sequence_1"
        sequence_description = (kwargs.get("description") or "").strip()
        output_csv = bool(kwargs.get("output_csv", True))
        output_dir = kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR
        output_basename = (kwargs.get("output_basename") or "").strip()
        skip_invalid = bool(kwargs.get("skip_invalid_sequences", True))
        try:
            properties = _normalize_properties(kwargs.get("properties"))
        except ValueError as e:
            return {"success": False, "error": str(e), "data": {}}
        try:
            ph = float(kwargs.get("ph", 7.0))
        except (TypeError, ValueError):
            return {"success": False, "error": "ph must be a number.", "data": {}}

        if bool(input_fasta) == bool(sequence):
            return {
                "success": False,
                "error": "Provide exactly one of input_fasta or sequence.",
                "data": {},
            }

        if input_fasta:
            if not os.path.isfile(input_fasta):
                return {"success": False, "error": f"FASTA not found: {input_fasta}", "data": {}}
            try:
                entries = parse_fasta(input_fasta)
            except Exception as e:
                return {"success": False, "error": f"Failed to parse FASTA: {e}", "data": {}}
            if not entries:
                return {"success": False, "error": "No sequences found in FASTA.", "data": {}}
        else:
            entries = [{
                "id": sequence_id,
                "sequence": sequence,
                "description": sequence_description,
            }]

        try:
            from Bio.SeqUtils.ProtParam import ProteinAnalysis
        except ImportError as e:
            return {
                "success": False,
                "error": f"Biopython is required for protein_properties: {e}. Install: pip install biopython",
                "data": {},
            }

        rows: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for idx, entry in enumerate(entries, start=1):
            entry_id = (entry.get("id") or f"sequence_{idx}").strip() or f"sequence_{idx}"
            desc = (entry.get("description") or "").strip()
            normalized = _normalize_sequence(entry.get("sequence") or "")
            if not normalized:
                reason = "empty sequence after normalization"
                if skip_invalid:
                    skipped.append({"id": entry_id, "reason": reason})
                    continue
                return {"success": False, "error": f"{entry_id}: {reason}", "data": {}}

            invalid = _invalid_residues(normalized)
            if invalid:
                reason = f"contains non-standard residues: {', '.join(invalid)}"
                if skip_invalid:
                    skipped.append({"id": entry_id, "reason": reason})
                    continue
                return {"success": False, "error": f"{entry_id}: {reason}", "data": {}}

            try:
                analysis = ProteinAnalysis(normalized)
                row: Dict[str, Any] = {"id": entry_id, "description": desc}
                if "length" in properties:
                    row["length"] = len(normalized)
                if "molecular_weight" in properties:
                    row["molecular_weight"] = round(float(analysis.molecular_weight()), 4)
                if "aromaticity" in properties:
                    row["aromaticity"] = round(float(analysis.aromaticity()), 6)
                if "instability_index" in properties:
                    row["instability_index"] = round(float(analysis.instability_index()), 6)
                if "isoelectric_point" in properties:
                    row["isoelectric_point"] = round(float(analysis.isoelectric_point()), 6)
                if "gravy" in properties:
                    row["gravy"] = round(float(analysis.gravy()), 6)
                if "charge_at_ph" in properties:
                    row["charge_at_ph"] = round(float(analysis.charge_at_pH(ph)), 6)
                rows.append(row)
            except Exception as e:
                logger.warning("protein_properties failed for %s: %s", entry_id, e)
                if skip_invalid:
                    skipped.append({"id": entry_id, "reason": str(e)})
                    continue
                return {"success": False, "error": f"{entry_id}: {e}", "data": {}}

        if not rows:
            return {
                "success": False,
                "error": "No valid protein sequences were processed.",
                "data": {"skipped": skipped[:MAX_INLINE_ROWS]},
            }

        csv_path: Optional[str] = None
        downloaded: Dict[str, List[str]] = {}
        if output_csv:
            out_dir = resolve_output_dir(str(output_dir).strip() or DEFAULT_OUTPUT_DIR)
            if not os.path.isabs(out_dir):
                out_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), out_dir)))
            safe_dir(out_dir)
            basename = _build_output_basename(output_basename, input_fasta, sequence_id)
            csv_path = os.path.join(out_dir, f"{basename}_{safe_run_id()}.csv")
            fieldnames = ["id", "description"] + properties
            try:
                with open(csv_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(row)
                ensure_file_permissions(csv_path)
                downloaded["csv"] = [os.path.abspath(csv_path)]
            except Exception as e:
                return {"success": False, "error": f"Failed to write CSV: {e}", "data": {}}

        preview_rows = rows[:MAX_INLINE_ROWS]
        data: Dict[str, Any] = {
            "message": (
                f"Computed {', '.join(properties)} for {len(rows)} sequence(s)"
                + (f"; skipped {len(skipped)} invalid sequence(s)." if skipped else ".")
            ),
            "processed_count": len(rows),
            "skipped_count": len(skipped),
            "properties": properties,
            "ph": ph,
            "rows": preview_rows,
            "rows_truncated": len(rows) > len(preview_rows),
        }
        if input_fasta:
            data["input_fasta"] = os.path.abspath(input_fasta)
        if skipped:
            data["skipped"] = skipped[:MAX_INLINE_ROWS]
            data["skipped_truncated"] = len(skipped) > MAX_INLINE_ROWS
        if csv_path:
            data["csv_path"] = os.path.abspath(csv_path)
            data["downloaded"] = downloaded
        return {"success": True, "error": "", "data": data}
