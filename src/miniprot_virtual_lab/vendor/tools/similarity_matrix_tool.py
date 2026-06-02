"""
Similarity matrix tool: build and visualize sequence similarity from MMseqs2 .m8 results.

Actions:
- matrix_from_m8: Read .m8 file, compute normalized similarity matrix (log bit-score, z-score, 0-1), save sparse .npz and id list .npy.
- clustermap: Load .npz + .npy, draw hierarchical clustermap, save PNG/PDF.
- clustermap_from_alignments: Load structure_alignment_batch JSON (pdb_id1, pdb_id2, tm_score_*), build TM-score matrix, draw clustermap, save to output_path. Use when user asks to save the structural similarity figure to a directory.

Used with mmseqs2 run_search or structure_alignment_batch output.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/similarity_matrix"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions, safe_run_id
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions, safe_run_id


def _matrix_from_m8_impl(m8_path: str, output_dir: str) -> Dict[str, Any]:
    """Read .m8, build sparse similarity matrix and id list. Returns dict with paths or error."""
    try:
        import pandas as pd
        import numpy as np
        import scipy.sparse
    except ImportError as e:
        return {"success": False, "error": f"Required packages: pandas, numpy, scipy. {e}", "paths": {}}

    if not os.path.isfile(m8_path):
        return {"success": False, "error": f"File not found: {m8_path}", "paths": {}}
    try:
        df = pd.read_csv(m8_path, sep="\t", header=None)
    except Exception as e:
        return {"success": False, "error": f"Failed to read .m8: {e}", "paths": {}}
    # MMseqs2 convertalis: query, target, seqId, alnLen, mismatch, gapOpen, qStart, qEnd, tStart, tEnd, evalue, bitScore
    if df.shape[1] < 12:
        return {"success": False, "error": ".m8 must have at least 12 columns (bitScore at 11).", "paths": {}}
    # Remove self-matches for off-diagonal; diagonal will be set to 1
    df = df.copy()
    df.loc[df[0] == df[1], 11] = np.nan
    df["log_bit_score"] = np.log10(df[11].astype(float) + 1)
    mean_log = df["log_bit_score"].mean()
    std_log = df["log_bit_score"].std()
    if std_log == 0 or np.isnan(std_log):
        std_log = 1.0
    df["z_score"] = (df["log_bit_score"] - mean_log) / std_log
    query_ids = sorted(set(df[0].unique()).union(set(df[1].unique())))
    id_index = {qid: i for i, qid in enumerate(query_ids)}
    n = len(query_ids)
    min_z = df["z_score"].min()
    max_z = df["z_score"].max()
    span = max_z - min_z
    if span == 0 or np.isnan(span):
        span = 1.0
    row, col, data = [], [], []
    for _, r in df.iterrows():
        i = id_index.get(r[0])
        j = id_index.get(r[1])
        if i is None or j is None:
            continue
        if pd.notna(r["z_score"]):
            sim = (r["z_score"] - min_z) / span
            row.append(i)
            col.append(j)
            data.append(float(sim))
    for i in range(n):
        row.append(i)
        col.append(i)
        data.append(1.0)
    similarity_sparse = scipy.sparse.coo_matrix((data, (row, col)), shape=(n, n))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    npz_path = os.path.join(output_dir, f"similarity_sparse_{run_id}.npz")
    npy_path = os.path.join(output_dir, f"id_names_{run_id}.npy")
    safe_dir(output_dir)
    scipy.sparse.save_npz(npz_path, similarity_sparse)
    np.save(npy_path, np.array(query_ids), allow_pickle=True)
    ensure_file_permissions(npz_path)
    ensure_file_permissions(npy_path)
    return {
        "success": True,
        "npz_path": os.path.abspath(npz_path),
        "npy_path": os.path.abspath(npy_path),
        "shape": [n, n],
        "nnz": int(similarity_sparse.nnz),
    }


def _clustermap_impl(
    npz_path: str,
    npy_path: str,
    output_path: str,
    query_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Draw clustermap from .npz and .npy. query_ids optional for row/col colors (red=query, gray=other)."""
    try:
        import numpy as np
        import scipy.sparse
        from scipy.spatial.distance import squareform
        from scipy.cluster.hierarchy import linkage, leaves_list
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError as e:
        return {"success": False, "error": f"Required: numpy, scipy, matplotlib, seaborn. {e}"}

    if not os.path.isfile(npz_path):
        return {"success": False, "error": f"npz not found: {npz_path}"}
    if not os.path.isfile(npy_path):
        return {"success": False, "error": f"npy not found: {npy_path}"}
    sim = scipy.sparse.load_npz(npz_path)
    id_arr = np.load(npy_path, allow_pickle=True)
    if id_arr.ndim == 0:
        pdb_names = id_arr.item()
        if isinstance(pdb_names, str):
            pdb_names = [pdb_names]
        elif not isinstance(pdb_names, list):
            pdb_names = list(pdb_names)
    else:
        pdb_names = id_arr.tolist()
    sim_dense = sim.maximum(sim.T).toarray()
    dist = 1.0 - np.clip(sim_dense, 0, 1)
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="average")
    query_set = set(query_ids or [])
    row_colors = ["red" if name in query_set else "lightgray" for name in pdb_names]
    col_colors = row_colors
    sns.set(font_scale=0.6)
    g = sns.clustermap(
        sim_dense,
        row_linkage=Z,
        col_linkage=Z,
        row_cluster=True,
        col_cluster=True,
        row_colors=row_colors,
        col_colors=col_colors,
        cmap="viridis",
        figsize=(12, 12),
        xticklabels=pdb_names,
        yticklabels=pdb_names,
    )
    plt.title("MMseqs2 similarity clustermap", y=1.02, fontsize=14)
    plt.tight_layout()
    out_dir = os.path.dirname(output_path)
    if out_dir:
        safe_dir(out_dir)
    g.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    ensure_file_permissions(output_path)
    return {"success": True, "output_path": os.path.abspath(output_path)}


def _clustermap_from_alignments_impl(alignment_json_path: str, output_path: str, query_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build symmetric similarity matrix from structure_alignment_batch JSON (pdb_id1, pdb_id2, tm_score_1) and draw clustermap. Saves to output_path."""
    try:
        import numpy as np
        from scipy.spatial.distance import squareform
        from scipy.cluster.hierarchy import linkage
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError as e:
        return {"success": False, "error": f"Required: numpy, scipy, matplotlib, seaborn. {e}"}

    if not os.path.isfile(alignment_json_path):
        return {"success": False, "error": f"Alignment JSON not found: {alignment_json_path}"}
    try:
        with open(alignment_json_path) as f:
            alignments = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to load alignment JSON: {e}"}
    if not isinstance(alignments, list):
        return {"success": False, "error": "Alignment JSON must be a list of pair objects."}

    ids_set = set()
    for a in alignments:
        if isinstance(a, dict):
            ids_set.add(a.get("pdb_id1"))
            ids_set.add(a.get("pdb_id2"))
    proteins = sorted(x for x in ids_set if x)
    n = len(proteins)
    if n < 2:
        return {"success": False, "error": "Need at least 2 distinct IDs in alignment JSON."}
    id_to_idx = {p: i for i, p in enumerate(proteins)}
    matrix = np.identity(n)
    for a in alignments:
        if not isinstance(a, dict):
            continue
        i = id_to_idx.get(a.get("pdb_id1"))
        j = id_to_idx.get(a.get("pdb_id2"))
        if i is None or j is None:
            continue
        t1, t2, t3 = a.get("tm_score_1"), a.get("tm_score_2"), a.get("tm_score_3")
        tm = t1
        if tm is None and (t2 is not None or t3 is not None):
            vals = [x for x in (t1, t2, t3) if x is not None]
            tm = sum(vals) / len(vals) if vals else None
        if tm is not None:
            matrix[i, j] = float(tm)
            matrix[j, i] = float(tm)
    dist = 1.0 - np.clip(matrix, 0, 1)
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="average")
    query_set = set(query_ids or [])
    row_colors = ["red" if name in query_set else "lightgray" for name in proteins]
    col_colors = row_colors
    sns.set(font_scale=0.6)
    g = sns.clustermap(
        matrix,
        row_linkage=Z,
        col_linkage=Z,
        row_cluster=True,
        col_cluster=True,
        row_colors=row_colors,
        col_colors=col_colors,
        cmap="viridis",
        figsize=(12, 12),
        xticklabels=proteins,
        yticklabels=proteins,
    )
    plt.title("Structural similarity (TM-score)", y=1.02, fontsize=14)
    plt.tight_layout()
    out_dir = os.path.dirname(output_path)
    if out_dir:
        safe_dir(out_dir)
    g.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    ensure_file_permissions(output_path)
    return {"success": True, "output_path": os.path.abspath(output_path)}


class SimilarityMatrixTool(BaseTool):
    """Build and visualize sequence similarity matrix from MMseqs2 .m8 results."""

    @property
    def name(self) -> str:
        return "similarity_matrix"

    @property
    def description(self) -> str:
        return (
            "Similarity matrix: matrix_from_m8 (.m8 → .npz+.npy), clustermap (from .npz/.npy), clustermap_from_alignments (from structure_alignment_batch JSON → TM-score clustermap). To save a figure to a new directory, call the tool with output_dir or output_path set to that directory; do not respond with code."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "similarity_matrix",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: matrix_from_m8 (m8 → .npz + .npy), clustermap (draw from .npz/.npy), clustermap_from_alignments (draw from structure alignment JSON; use for 'save figure to X').",
                        "enum": ["matrix_from_m8", "clustermap", "clustermap_from_alignments"],
                    },
                    "alignment_json_path": {
                        "type": "string",
                        "description": "Path to structure_alignment_batch JSON (alignments_*.json). Required for clustermap_from_alignments.",
                    },
                    "m8_path": {
                        "type": "string",
                        "description": "Path to MMseqs2 .m8 result file. Required for matrix_from_m8.",
                    },
                    "npz_path": {
                        "type": "string",
                        "description": "Path to similarity sparse matrix .npz. Required for clustermap.",
                    },
                    "npy_path": {
                        "type": "string",
                        "description": "Path to id names .npy. Required for clustermap.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output image path for clustermap (e.g. .png or .pdf). Default: similarity_clustermap_YYYYMMDD_HHMMSS.png",
                    },
                    "query_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of sequence IDs to color red in clustermap (rest gray).",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for matrix files and figures.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                },
                "required": ["action"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or "").strip().lower()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        # When using default base, use a run-specific subdir (safe naming)
        default_base = os.path.normpath(os.path.join(workspace_root(), DEFAULT_OUTPUT_DIR))
        if os.path.normpath(output_dir) == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)

        if action == "matrix_from_m8":
            m8_path = (kwargs.get("m8_path") or "").strip()
            if not m8_path:
                return {"success": False, "error": "matrix_from_m8 requires m8_path.", "data": {}}
            if not os.path.isfile(m8_path):
                return {"success": False, "error": f"m8 file not found: {m8_path}", "data": {}}
            out = _matrix_from_m8_impl(m8_path, output_dir)
            if not out.get("success"):
                return {"success": False, "error": out.get("error", "matrix_from_m8 failed"), "data": {}}
            return {
                "success": True,
                "data": {
                    "message": f"Similarity matrix saved. Shape {out.get('shape')}, non-zeros {out.get('nnz')}.",
                    "npz_path": out["npz_path"],
                    "npy_path": out["npy_path"],
                    "downloaded": {"npz": [out["npz_path"]], "npy": [out["npy_path"]]},
                },
            }

        if action == "clustermap":
            npz_path = (kwargs.get("npz_path") or "").strip()
            npy_path = (kwargs.get("npy_path") or "").strip()
            output_path = (kwargs.get("output_path") or "").strip()
            query_ids = kwargs.get("query_ids")
            if isinstance(query_ids, str):
                query_ids = [query_ids]
            elif query_ids is not None and not isinstance(query_ids, list):
                query_ids = list(query_ids) if query_ids else None
            if not npz_path or not npy_path:
                return {"success": False, "error": "clustermap requires npz_path and npy_path.", "data": {}}
            if not os.path.isabs(npz_path):
                npz_path = os.path.normpath(os.path.join(output_dir, npz_path))
            if not os.path.isabs(npy_path):
                npy_path = os.path.normpath(os.path.join(output_dir, npy_path))
            if not output_path:
                run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = os.path.join(output_dir, f"similarity_clustermap_{run_id}.png")
            if not os.path.isabs(output_path):
                output_path = os.path.normpath(os.path.join(output_dir, output_path))
            out = _clustermap_impl(npz_path, npy_path, output_path, query_ids=query_ids)
            if not out.get("success"):
                return {"success": False, "error": out.get("error", "clustermap failed"), "data": {}}
            out_path = out.get("output_path")
            if not out_path or not os.path.isfile(out_path):
                return {"success": False, "error": "Clustermap file was not written.", "data": {}}
            out_path = os.path.abspath(out_path)
            return {
                "success": True,
                "data": {
                    "message": "Clustermap saved.",
                    "output_path": out_path,
                    "downloaded": {"figure": [out_path]},
                },
            }

        if action == "clustermap_from_alignments":
            alignment_json_path = (kwargs.get("alignment_json_path") or "").strip()
            output_path = (kwargs.get("output_path") or "").strip()
            query_ids = kwargs.get("query_ids")
            if isinstance(query_ids, str):
                query_ids = [query_ids]
            elif query_ids is not None and not isinstance(query_ids, list):
                query_ids = list(query_ids) if query_ids else None
            if not alignment_json_path:
                return {"success": False, "error": "clustermap_from_alignments requires alignment_json_path (path to structure_alignment_batch JSON).", "data": {}}
            if not os.path.isabs(alignment_json_path):
                alignment_json_path = os.path.normpath(os.path.join(output_dir, alignment_json_path))
            if not output_path:
                run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = os.path.join(output_dir, f"clustermap_tmscore_{run_id}.png")
            if not os.path.isabs(output_path):
                output_path = os.path.normpath(os.path.join(output_dir, output_path))
            out = _clustermap_from_alignments_impl(alignment_json_path, output_path, query_ids=query_ids)
            if not out.get("success"):
                return {"success": False, "error": out.get("error", "clustermap_from_alignments failed"), "data": {}}
            out_path = out.get("output_path")
            if not out_path or not os.path.isfile(out_path):
                return {"success": False, "error": "Clustermap file was not written.", "data": {}}
            out_path = os.path.abspath(out_path)
            return {
                "success": True,
                "data": {
                    "message": "Clustermap from structure alignments saved.",
                    "output_path": out_path,
                    "downloaded": {"figure": [out_path]},
                },
            }

        return {"success": False, "error": f"Unknown action: {action}. Use matrix_from_m8, clustermap, or clustermap_from_alignments.", "data": {}}
