"""
Filter FASTA sequences by length (amino acids or nucleotides).

Use cases:
- After CD-HIT in enzyme mining: remove sequences much shorter/longer than the query
  by using reference_fasta (query) and length_range (e.g. ±30).
- User-defined limits: set min_length and/or max_length directly from the user query.

Length is counted as number of residues/bases (no gap stripping).
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import statistics
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/sequence_length_filter"
DEFAULT_LENGTH_RANGE = 30  # ±30 when using reference_fasta

try:
    from utils.fasta_parser import parse_fasta, write_fasta
except ImportError:
    from ..utils.fasta_parser import parse_fasta, write_fasta

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _sequence_lengths(entries: List[Dict[str, Any]]) -> List[int]:
    """Return list of sequence lengths (residues/bases only, no gaps)."""
    return [len((e.get("sequence") or "").replace(" ", "").replace("-", "")) for e in entries]


def _reference_limits(reference_fasta: str, length_range: int) -> tuple[Optional[int], Optional[int]]:
    """Compute min/max length from reference FASTA as median ± length_range. Returns (min_len, max_len)."""
    entries = parse_fasta(reference_fasta)
    if not entries:
        return None, None
    lengths = _sequence_lengths(entries)
    if not lengths:
        return None, None
    median_len = int(statistics.median(lengths))
    return max(1, median_len - length_range), median_len + length_range


class SequenceLengthFilterTool(BaseTool):
    """Filter FASTA sequences by length. Supports user-defined min/max or reference-based ±range (e.g. after CD-HIT in enzyme mining)."""

    @property
    def name(self) -> str:
        return "sequence_length_filter"

    @property
    def description(self) -> str:
        return (
            "Filter sequences in a FASTA file by length (amino acids or nucleotides). "
            "Provide min_length and/or max_length directly, or use reference_fasta (e.g. query sequences) with length_range (default ±30) to keep sequences within typical length. "
            "Use after CD-HIT in enzyme mining to remove unusually short or long sequences."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "sequence_length_filter",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "input_fasta": {
                        "type": "string",
                        "description": "Path to input FASTA to filter (e.g. CD-HIT representatives).",
                    },
                    "min_length": {
                        "type": "integer",
                        "description": "Minimum sequence length (residues/bases) to keep. Optional if reference_fasta is used.",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum sequence length (residues/bases) to keep. Optional if reference_fasta is used.",
                    },
                    "reference_fasta": {
                        "type": "string",
                        "description": "Path to reference FASTA (e.g. query sequences). Used to compute typical length (median) and apply length_range. In enzyme mining use the query FASTA after CD-HIT.",
                    },
                    "length_range": {
                        "type": "integer",
                        "description": "When using reference_fasta: keep sequences within median ± length_range (default 30). Ignored if min_length/max_length are set.",
                        "default": DEFAULT_LENGTH_RANGE,
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory. Default: data/outputs/sequence_length_filter.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                },
                "required": ["input_fasta"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        input_fasta = (kwargs.get("input_fasta") or "").strip()
        if not input_fasta or not os.path.isfile(input_fasta):
            return {"success": False, "error": "input_fasta is required and must be an existing file.", "data": {}}

        min_length = kwargs.get("min_length")
        max_length = kwargs.get("max_length")
        reference_fasta = (kwargs.get("reference_fasta") or "").strip()
        length_range = int(kwargs.get("length_range") or DEFAULT_LENGTH_RANGE)
        length_range = max(0, length_range)

        if min_length is not None:
            min_length = int(min_length)
        if max_length is not None:
            max_length = int(max_length)

        if min_length is None and max_length is None:
            if reference_fasta and os.path.isfile(reference_fasta):
                min_length, max_length = _reference_limits(reference_fasta, length_range)
                if min_length is None and max_length is None:
                    return {"success": False, "error": "reference_fasta has no valid sequences.", "data": {}}
            else:
                return {
                    "success": False,
                    "error": "Set min_length/max_length or reference_fasta to define length limits.",
                    "data": {},
                }

        if min_length is None:
            min_length = 1
        if max_length is None:
            max_length = 10**9
        if min_length > max_length:
            min_length, max_length = max_length, min_length

        entries = parse_fasta(input_fasta)
        if not entries:
            return {"success": False, "error": "input_fasta has no sequences.", "data": {}}

        lengths = _sequence_lengths(entries)
        kept = [e for e, L in zip(entries, lengths) if min_length <= L <= max_length]
        removed_count = len(entries) - len(kept)

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(output_dir, f"filtered_length_{run_id}.fasta")
        write_fasta(kept, out_path)
        ensure_file_permissions(out_path)

        return {
            "success": True,
            "data": {
                "message": f"Filtered by length [{min_length}, {max_length}]: kept {len(kept)} of {len(entries)} sequences, removed {removed_count}.",
                "filtered_fasta": os.path.abspath(out_path),
                "total_sequences": len(entries),
                "kept": len(kept),
                "removed": removed_count,
                "min_length_used": min_length,
                "max_length_used": max_length,
                "downloaded": {"fasta": [os.path.abspath(out_path)]},
            },
        }
