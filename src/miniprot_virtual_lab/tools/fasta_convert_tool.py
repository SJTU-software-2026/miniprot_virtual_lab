"""
FASTA/CSV conversion tool.

Actions:
- csv_to_fasta: convert tabular sequence data into FASTA
- fasta_to_csv: convert FASTA into a CSV table while preserving header text
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import csv
import logging
import os
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/fasta_convert"
DEFAULT_CSV_COLUMNS = ["id", "unique_id", "description", "header", "sequence"]

try:
    from utils.fasta_parser import parse_fasta, write_fasta
except ImportError:
    from ..utils.fasta_parser import parse_fasta, write_fasta

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


def _normalize_delimiter(value: Any) -> str:
    """Support ',', '\\t', and 'tab' style delimiter inputs."""
    raw = str(value or ",").strip()
    if raw.lower() in {"tab", "\\t", "t"}:
        return "\t"
    return raw or ","


def _normalize_sequence(seq: str) -> str:
    """Remove whitespace while preserving sequence content order."""
    return "".join((seq or "").split())


def _resolve_output_file_path(
    output_path: str,
    output_dir: str,
    input_path: str,
    action: str,
) -> str:
    """Resolve output file path under workspace or explicit absolute path."""
    if output_path:
        return output_path if os.path.isabs(output_path) else os.path.normpath(os.path.join(workspace_root(), output_path))
    out_dir = resolve_output_dir(output_dir or DEFAULT_OUTPUT_DIR)
    if not os.path.isabs(out_dir):
        out_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), out_dir)))
    safe_dir(out_dir)
    stem = safe_filename(os.path.splitext(os.path.basename(input_path))[0] or "converted")
    ext = ".fasta" if action == "csv_to_fasta" else ".csv"
    return os.path.join(out_dir, f"{stem}_{safe_run_id()}{ext}")


def _make_unique_id(raw_id: str, counts: Dict[str, int]) -> Tuple[str, bool]:
    """Return a stable unique id and whether a duplicate suffix was applied."""
    base = safe_filename((raw_id or "").strip() or "sequence")
    current = counts.get(base, 0) + 1
    counts[base] = current
    if current == 1:
        return base, False
    return f"{base}_{current}", True


class FastaConvertTool(BaseTool):
    """Convert between CSV and FASTA formats."""

    def __init__(self) -> None:
        self._name = "fasta_convert"
        self._description = (
            "Convert sequence files between CSV and FASTA. "
            "Use action=csv_to_fasta with sequence/id/description columns, or action=fasta_to_csv "
            "to preserve id, unique_id, description, header, and sequence in a CSV table."
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
                        "action": {
                            "type": "string",
                            "enum": ["csv_to_fasta", "fasta_to_csv"],
                            "description": "Conversion direction.",
                        },
                        "input_path": {
                            "type": "string",
                            "description": "Path to the input CSV or FASTA file.",
                        },
                        "sequence_column": {
                            "type": "string",
                            "description": "For csv_to_fasta: column containing sequence strings.",
                            "default": "sequence",
                        },
                        "id_column": {
                            "type": "string",
                            "description": "For csv_to_fasta: optional column containing sequence IDs. If omitted or blank, IDs are generated.",
                            "default": "id",
                        },
                        "desc_column": {
                            "type": "string",
                            "description": "For csv_to_fasta: optional column containing descriptions appended to FASTA headers.",
                            "default": "description",
                        },
                        "delimiter": {
                            "type": "string",
                            "description": "CSV delimiter. Use ',' by default or 'tab' / '\\t' for tab-separated files.",
                            "default": ",",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional full output file path. If omitted, a timestamped file is written under output_dir.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory for auto-generated outputs when output_path is omitted.",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "duplicate_id_strategy": {
                            "type": "string",
                            "enum": ["suffix", "error"],
                            "description": "How to handle duplicate IDs. Default adds _2, _3, ... suffixes.",
                            "default": "suffix",
                        },
                        "skip_empty_lines": {
                            "type": "boolean",
                            "description": "Skip empty rows or rows with blank sequences instead of failing.",
                            "default": True,
                        },
                    },
                    "required": ["action", "input_path"],
                },
            },
        }

    def _csv_to_fasta(self, **kwargs: Any) -> Dict[str, Any]:
        input_path = (kwargs.get("input_path") or "").strip()
        sequence_column = (kwargs.get("sequence_column") or "sequence").strip() or "sequence"
        id_column = (kwargs.get("id_column") or "").strip()
        desc_column = (kwargs.get("desc_column") or "").strip()
        delimiter = _normalize_delimiter(kwargs.get("delimiter"))
        duplicate_strategy = (kwargs.get("duplicate_id_strategy") or "suffix").strip().lower()
        skip_empty = bool(kwargs.get("skip_empty_lines", True))
        output_path = _resolve_output_file_path(
            (kwargs.get("output_path") or "").strip(),
            (kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip(),
            input_path,
            "csv_to_fasta",
        )

        if duplicate_strategy not in {"suffix", "error"}:
            return {"success": False, "error": "duplicate_id_strategy must be 'suffix' or 'error'.", "data": {}}
        if not os.path.isfile(input_path):
            return {"success": False, "error": f"Input file not found: {input_path}", "data": {}}

        rows: List[Dict[str, Any]] = []
        skipped_empty_rows = 0
        duplicate_ids_resolved = 0
        seen_ids: Dict[str, int] = {}

        try:
            with open(input_path, "r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=delimiter)
                fieldnames = reader.fieldnames or []
                if not fieldnames:
                    return {"success": False, "error": "CSV file has no header row.", "data": {}}
                if sequence_column not in fieldnames:
                    return {
                        "success": False,
                        "error": f"sequence_column '{sequence_column}' not found. Available: {fieldnames}",
                        "data": {},
                    }
                if id_column and id_column not in fieldnames:
                    return {
                        "success": False,
                        "error": f"id_column '{id_column}' not found. Available: {fieldnames}",
                        "data": {},
                    }
                if desc_column and desc_column not in fieldnames:
                    return {
                        "success": False,
                        "error": f"desc_column '{desc_column}' not found. Available: {fieldnames}",
                        "data": {},
                    }
                for idx, row in enumerate(reader, start=1):
                    row = row or {}
                    if all(not str(v or "").strip() for v in row.values()):
                        if skip_empty:
                            skipped_empty_rows += 1
                            continue
                        return {"success": False, "error": f"Encountered empty row at CSV line {idx + 1}.", "data": {}}
                    sequence = _normalize_sequence(str(row.get(sequence_column) or ""))
                    if not sequence:
                        if skip_empty:
                            skipped_empty_rows += 1
                            continue
                        return {"success": False, "error": f"Blank sequence at CSV line {idx + 1}.", "data": {}}
                    raw_id = str(row.get(id_column) or "").strip() if id_column else ""
                    if not raw_id:
                        raw_id = f"sequence_{len(rows) + 1}"
                    unique_id, duplicated = _make_unique_id(raw_id, seen_ids)
                    if duplicated and duplicate_strategy == "error":
                        return {"success": False, "error": f"Duplicate ID encountered: {raw_id}", "data": {}}
                    if duplicated:
                        duplicate_ids_resolved += 1
                    desc = str(row.get(desc_column) or "").strip() if desc_column else ""
                    rows.append({"id": unique_id, "sequence": sequence, "description": desc})
        except Exception as e:
            return {"success": False, "error": f"Failed to read CSV: {e}", "data": {}}

        if not rows:
            return {"success": False, "error": "No valid sequence rows were found in the CSV.", "data": {}}

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            safe_dir(out_dir)
        try:
            write_fasta(rows, output_path)
            ensure_file_permissions(output_path)
        except Exception as e:
            return {"success": False, "error": f"Failed to write FASTA: {e}", "data": {}}

        abs_output = os.path.abspath(output_path)
        return {
            "success": True,
            "error": "",
            "data": {
                "message": f"Converted CSV to FASTA with {len(rows)} record(s).",
                "action": "csv_to_fasta",
                "output_path": abs_output,
                "record_count": len(rows),
                "skipped_empty_rows": skipped_empty_rows,
                "duplicate_ids_resolved": duplicate_ids_resolved,
                "columns_used": {
                    "sequence_column": sequence_column,
                    "id_column": id_column or None,
                    "desc_column": desc_column or None,
                    "delimiter": delimiter,
                },
                "downloaded": {"fasta": [abs_output]},
            },
        }

    def _fasta_to_csv(self, **kwargs: Any) -> Dict[str, Any]:
        input_path = (kwargs.get("input_path") or "").strip()
        delimiter = _normalize_delimiter(kwargs.get("delimiter"))
        duplicate_strategy = (kwargs.get("duplicate_id_strategy") or "suffix").strip().lower()
        output_path = _resolve_output_file_path(
            (kwargs.get("output_path") or "").strip(),
            (kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip(),
            input_path,
            "fasta_to_csv",
        )

        if duplicate_strategy not in {"suffix", "error"}:
            return {"success": False, "error": "duplicate_id_strategy must be 'suffix' or 'error'.", "data": {}}
        if not os.path.isfile(input_path):
            return {"success": False, "error": f"Input file not found: {input_path}", "data": {}}

        try:
            entries = parse_fasta(input_path)
        except Exception as e:
            return {"success": False, "error": f"Failed to parse FASTA: {e}", "data": {}}
        if not entries:
            return {"success": False, "error": "No sequences found in FASTA.", "data": {}}

        seen_ids: Dict[str, int] = {}
        duplicate_ids_resolved = 0
        rows: List[Dict[str, str]] = []
        for idx, entry in enumerate(entries, start=1):
            raw_id = str(entry.get("id") or "").strip() or f"sequence_{idx}"
            unique_id, duplicated = _make_unique_id(raw_id, seen_ids)
            if duplicated and duplicate_strategy == "error":
                return {"success": False, "error": f"Duplicate ID encountered: {raw_id}", "data": {}}
            if duplicated:
                duplicate_ids_resolved += 1
            description = str(entry.get("description") or "").strip()
            header = f">{raw_id}"
            if description:
                header += f" {description}"
            rows.append(
                {
                    "id": raw_id,
                    "unique_id": unique_id,
                    "description": description,
                    "header": header,
                    "sequence": _normalize_sequence(str(entry.get("sequence") or "")),
                }
            )

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            safe_dir(out_dir)
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=DEFAULT_CSV_COLUMNS, delimiter=delimiter)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            ensure_file_permissions(output_path)
        except Exception as e:
            return {"success": False, "error": f"Failed to write CSV: {e}", "data": {}}

        abs_output = os.path.abspath(output_path)
        return {
            "success": True,
            "error": "",
            "data": {
                "message": f"Converted FASTA to CSV with {len(rows)} record(s).",
                "action": "fasta_to_csv",
                "output_path": abs_output,
                "record_count": len(rows),
                "duplicate_ids_resolved": duplicate_ids_resolved,
                "columns_used": {
                    "output_columns": list(DEFAULT_CSV_COLUMNS),
                    "delimiter": delimiter,
                },
                "downloaded": {"csv": [abs_output]},
            },
        }

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "csv_to_fasta":
            return self._csv_to_fasta(**kwargs)
        if action == "fasta_to_csv":
            return self._fasta_to_csv(**kwargs)
        return {
            "success": False,
            "error": "action must be one of: csv_to_fasta, fasta_to_csv.",
            "data": {},
        }
