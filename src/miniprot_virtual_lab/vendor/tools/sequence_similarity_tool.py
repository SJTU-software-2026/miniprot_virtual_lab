"""
Sequence similarity tool: compute pairwise similarity scores within an aligned FASTA
using a BLOSUM matrix (default: BLOSUM62).

Input:
- aligned_fasta_path: multiple sequence alignment in FASTA format (all sequences same length).

Output:
- CSV matrix of pairwise average BLOSUM scores (one row/column per sequence).
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/sequence_similarity"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions, safe_run_id
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions, safe_run_id


class SequenceSimilarityTool(BaseTool):
    def __init__(self):
        self._name = "sequence_similarity"
        self._description = (
            "Compute pairwise sequence similarity scores for an aligned FASTA using a BLOSUM matrix "
            "(default: BLOSUM62). Input is a multiple sequence alignment in FASTA format; output is a CSV "
            "matrix of average substitution scores."
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
                        "aligned_fasta_path": {
                            "type": "string",
                            "description": "Path to aligned FASTA file (multiple sequence alignment; all sequences must have the same length).",
                        },
                        "matrix": {
                            "type": "string",
                            "enum": ["BLOSUM62"],
                            "description": "Substitution matrix to use. Currently only BLOSUM62 is supported.",
                            "default": "BLOSUM62",
                        },
                        "gap_penalty": {
                            "type": "number",
                            "description": "Score to apply when either residue is a gap ('-').",
                            "default": -4.0,
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save the similarity matrix CSV (default: data/outputs/sequence_similarity).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "output_basename": {
                            "type": "string",
                            "description": "Optional base filename for the CSV (without extension). If omitted, a timestamped name is used.",
                        },
                    },
                    "required": ["aligned_fasta_path"],
                },
            }
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        aligned_fasta_path = kwargs.get("aligned_fasta_path") or ""
        matrix_name = (kwargs.get("matrix") or "BLOSUM62").upper()
        gap_penalty = float(kwargs.get("gap_penalty", -4.0))
        output_dir = kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR
        output_basename = kwargs.get("output_basename") or ""

        if not aligned_fasta_path:
            return {"success": False, "error": "aligned_fasta_path is required.", "data": {}}
        if not os.path.isfile(aligned_fasta_path):
            return {"success": False, "error": f"Aligned FASTA not found: {aligned_fasta_path}", "data": {}}

        try:
            from Bio import AlignIO
            from Bio.SubsMat import MatrixInfo
        except ImportError as e:
            return {
                "success": False,
                "error": f"Biopython required for sequence_similarity: {e}. Install: pip install biopython",
                "data": {},
            }

        if matrix_name != "BLOSUM62":
            return {"success": False, "error": f"Unsupported matrix '{matrix_name}'. Only BLOSUM62 is supported.", "data": {}}

        try:
            blosum = MatrixInfo.blosum62
        except Exception as e:
            return {"success": False, "error": f"Failed to load BLOSUM62: {e}", "data": {}}

        try:
            aln = AlignIO.read(aligned_fasta_path, "fasta")
        except Exception as e:
            return {"success": False, "error": f"Failed to read alignment FASTA: {e}", "data": {}}

        if len(aln) < 2:
            return {"success": False, "error": "Alignment must contain at least 2 sequences.", "data": {}}

        seq_len = len(aln[0].seq)
        for rec in aln:
            if len(rec.seq) != seq_len:
                return {
                    "success": False,
                    "error": "All sequences must have the same length. Provide a multiple sequence alignment.",
                    "data": {},
                }

        ids: List[str] = [rec.id for rec in aln]

        def _score_pair(s1: str, s2: str) -> float:
            total = 0.0
            n_pos = 0
            for a, b in zip(s1, s2):
                if a == "-" and b == "-":
                    continue
                n_pos += 1
                if a == "-" or b == "-":
                    total += gap_penalty
                    continue
                key = (a, b)
                if key not in blosum:
                    key = (b, a)
                s = blosum.get(key, gap_penalty)
                total += float(s)
            if n_pos == 0:
                return 0.0
            return total / float(n_pos)

        n = len(aln)
        scores: List[List[float]] = [[0.0 for _ in range(n)] for _ in range(n)]
        for i in range(n):
            s_i = str(aln[i].seq)
            for j in range(i, n):
                if i == j:
                    scores[i][j] = 0.0
                    continue
                s_j = str(aln[j].seq)
                val = _score_pair(s_i, s_j)
                scores[i][j] = val
                scores[j][i] = val

        root = workspace_root()
        run_id = safe_run_id(prefix="seqsim_")
        out_dir = resolve_output_dir(output_dir, root)
        safe_dir(out_dir)
        if output_basename:
            base = output_basename
        else:
            base = f"sequence_similarity_{run_id}"
        csv_path = os.path.join(out_dir, f"{base}.csv")

        try:
            import csv

            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["id"] + ids)
                for i, row in enumerate(scores):
                    writer.writerow([ids[i]] + [f"{x:.4f}" for x in row])
        except Exception as e:
            return {"success": False, "error": f"Failed to write CSV: {e}", "data": {}}

        ensure_file_permissions(csv_path)

        return {
            "success": True,
            "error": "",
            "data": {
                "csv_path": os.path.abspath(csv_path),
                "ids": ids,
                "matrix": matrix_name,
                "gap_penalty": gap_penalty,
            },
        }

