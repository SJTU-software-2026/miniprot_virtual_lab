"""
ETE Toolkit: phylogenetic tree analysis and visualization.
Supports Tree/PhyloTree operations: load, prune, root, ladderize, render, compare,
NCBI taxonomy annotation, evolutionary events, speciation trees, clustering metrics.
Reference: https://etetoolkit.org/docs/latest/reference/index.html
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/ete"

try:
    from utils.path_utils import safe_dir, resolve_output_dir, workspace_root, ensure_file_permissions, safe_run_id
except ImportError:
    from ..utils.path_utils import safe_dir, resolve_output_dir, workspace_root, ensure_file_permissions, safe_run_id


def _run_id() -> str:
    """Timestamp-based run id for unique filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_ete() -> Tuple[bool, str]:
    """Import ete3 and core modules; return (success, error_message). Captures full exception (e.g. Qt/runtime) for debugging."""
    try:
        import ete3  # noqa: F401
        from ete3 import Tree, TreeStyle
        return True, ""
    except Exception as e:
        err = str(e).strip() or type(e).__name__
        return False, (
            f"ete3 failed to load: {err}. "
            "Install in the same Python that runs this app: pip install ete3 (or conda install -c etetoolkit ete3). "
            "If already installed, ete3 may need Qt; try: conda install -c conda-forge pyqt."
        )


def _build_nj_from_alignment(alignment_path: str, output_path: str) -> Tuple[bool, str]:
    """Build neighbor-joining tree from alignment FASTA using Biopython. Returns (success, error_message)."""
    try:
        from Bio import AlignIO, Phylo
        from Bio.Phylo.TreeConstruction import DistanceCalculator, DistanceTreeConstructor
    except ImportError as e:
        return False, f"Biopython required for build_nj: {e}. Install: pip install biopython"
    if not os.path.isfile(alignment_path):
        return False, f"Alignment file not found: {alignment_path}"
    try:
        aln = AlignIO.read(alignment_path, "fasta")
    except Exception as e:
        return False, f"Failed to read alignment: {e}"
    if len(aln) < 2:
        return False, "Alignment must contain at least 2 sequences"
    try:
        calculator = DistanceCalculator("identity")
        dm = calculator.get_distance(aln)
        constructor = DistanceTreeConstructor()
        tree = constructor.nj(dm)
        out_dir = os.path.dirname(output_path)
        if out_dir:
            safe_dir(out_dir)
        Phylo.write(tree, output_path, "newick")
        ensure_file_permissions(output_path)
        return True, ""
    except Exception as e:
        return False, str(e)


def _find_fasttree() -> Optional[str]:
    """Return path to FastTree or fasttree executable, or None."""
    for name in ("FastTree", "fasttree"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _run_fasttree(alignment_path: str, output_path: str, nucleotide: bool = False) -> Tuple[bool, str]:
    """Run FastTree on alignment (FASTA/PHYLIP); write Newick to output_path. Returns (success, error_message)."""
    exe = _find_fasttree()
    if not exe:
        return False, "FastTree not found. Install: conda install -c bioconda fasttree or install FastTree 2 and add to PATH"
    if not os.path.isfile(alignment_path):
        return False, f"Alignment file not found: {alignment_path}"
    out_dir = os.path.dirname(output_path)
    if out_dir:
        safe_dir(out_dir)
    cmd = [exe]
    if nucleotide:
        cmd.append("-nt")
    cmd.append(alignment_path)
    try:
        with open(output_path, "w") as f_out:
            result = subprocess.run(
                cmd,
                stdout=f_out,
                stderr=subprocess.PIPE,
                text=True,
            )
        if result.returncode != 0:
            return False, result.stderr or f"FastTree exited {result.returncode}"
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            return False, "FastTree produced no output"
        ensure_file_permissions(output_path)
        return True, ""
    except Exception as e:
        return False, str(e)


class ETETool(BaseTool):
    """
    Phylogenetic tree analysis and visualization via ETE Toolkit.
    build_nj: NJ tree from alignment_path (Biopython). build_fasttree: tree from alignment using FastTree (use when build_nj fails or for better trees).
    Output Newick from build_nj/build_fasttree is then used with load_tree, set_outgroup, render for annotation and analysis.
    load_tree, write_newick, prune, set_outgroup, ladderize, convert_ultrametric, render, get_ascii, robinson_foulds, phylotree_*, cluster_*.
    """

    def __init__(self):
        self._name = "ete"
        self._description = (
            "Phylogenetic tree analysis and visualization (ETE Toolkit). "
            "build_nj: NJ tree from alignment_path (Biopython). build_fasttree: tree from alignment via FastTree binary (fallback when build_nj fails). "
            "Use output Newick with set_outgroup, render, etc. for annotation and analysis. "
            "Load trees from Newick; prune, root, ladderize, render to PNG/SVG/PDF; compare trees; PhyloTree and ClusterTree operations."
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
                            "enum": [
                                "build_nj",
                                "build_fasttree",
                                "load_tree",
                                "write_newick",
                                "prune",
                                "set_outgroup",
                                "ladderize",
                                "convert_ultrametric",
                                "render",
                                "get_ascii",
                                "robinson_foulds",
                                "phylotree_annotate_ncbi",
                                "phylotree_evolutionary_events",
                                "phylotree_speciation_trees",
                                "phylotree_split_by_dups",
                                "cluster_silhouette",
                                "cluster_dunn",
                            ],
                            "description": (
                                "build_nj: build neighbor-joining tree from alignment_path (FASTA); uses Biopython. "
                                "build_fasttree: build ML-like tree from alignment_path using FastTree binary; use when build_nj fails or for better trees. "
                                "Output Newick from build_nj/build_fasttree can be used with load_tree, set_outgroup, render for annotation and analysis. "
                                "load_tree: load from newick_path or newick_string (Newick file, not FASTA). "
                                "write_newick: save tree to file. prune: keep only specified leaves. "
                                "set_outgroup: root tree by outgroup. ladderize: sort branches. "
                                "convert_ultrametric: make ultrametric. render: render to PNG/SVG/PDF. "
                                "get_ascii: ASCII representation. robinson_foulds: compare two trees. "
                                "phylotree_*: PhyloTree operations. cluster_*: ClusterTree silhouette/Dunn index."
                            ),
                        },
                        "newick_path": {
                            "type": "string",
                            "description": "Path to Newick tree file (for load_tree, or first tree for robinson_foulds).",
                        },
                        "newick_string": {
                            "type": "string",
                            "description": "Newick string instead of file (for load_tree).",
                        },
                        "newick_path2": {
                            "type": "string",
                            "description": "Second tree path for robinson_foulds comparison.",
                        },
                        "alignment_path": {
                            "type": "string",
                            "description": "Path to alignment FASTA for build_nj, build_fasttree, or PhyloTree link_to_alignment.",
                        },
                        "nucleotide": {
                            "type": "boolean",
                            "description": "For build_fasttree: true if alignment is nucleotide (uses -nt).",
                            "default": False,
                        },
                        "leaves_to_keep": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Leaf names to keep for prune action.",
                        },
                        "outgroup": {
                            "type": "string",
                            "description": "Leaf or node name to use as outgroup for set_outgroup.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output file path (newick, PNG, SVG, PDF).",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Output directory (default: data/outputs/ete).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "format": {
                            "type": "integer",
                            "description": "Newick format 0-9, 100 (topology only). Default 0.",
                            "default": 0,
                        },
                        "tree_length": {
                            "type": "number",
                            "description": "Total tree length for convert_ultrametric (optional).",
                        },
                        "strategy": {
                            "type": "string",
                            "enum": ["balanced", "fixed"],
                            "description": "Strategy for convert_ultrametric: balanced or fixed.",
                            "default": "balanced",
                        },
                        "image_format": {
                            "type": "string",
                            "enum": ["png", "svg", "pdf"],
                            "description": "Output format for render (default png).",
                            "default": "png",
                        },
                        "arraytable_path": {
                            "type": "string",
                            "description": "Path to array/table file for ClusterTree (rows = leaf names).",
                        },
                        "session_tree_path": {
                            "type": "boolean",
                            "description": "Use most recent tree from session artifacts.",
                            "default": False,
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or kwargs.get("method") or "").strip().lower()
        if not action and (kwargs.get("alignment_path") or kwargs.get("fasta_path")):
            action = "build_fasttree"
        if not action:
            return {"success": False, "error": "action is required (e.g. build_nj, build_fasttree, render).", "data": {}}

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        else:
            output_dir = os.path.normpath(output_dir)
        default_base = os.path.normpath(os.path.join(workspace_root(), DEFAULT_OUTPUT_DIR))
        if output_dir == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)
        run_id = _run_id()

        # build_nj: build NJ tree from alignment FASTA (does not require ete3). Fallback to FastTree if NJ fails.
        if action == "build_nj":
            alignment_path = (kwargs.get("alignment_path") or kwargs.get("fasta_path") or "").strip()
            if not alignment_path or not os.path.isfile(alignment_path):
                return {"success": False, "error": "build_nj requires alignment_path to an existing alignment FASTA file.", "data": {}}
            output_path = (kwargs.get("output_path") or "").strip()
            if output_path and output_path.lower().endswith((".png", ".svg", ".pdf")):
                output_path = ""  # user passed image path; use default Newick path
            if not output_path:
                output_path = os.path.join(output_dir, f"nj_tree_{run_id}.nw")
            elif not os.path.isabs(output_path):
                output_path = os.path.join(output_dir, output_path)
            if not output_path.endswith(".nw") and not output_path.endswith(".newick"):
                output_path = output_path + ".nw"
            ok, err = _build_nj_from_alignment(alignment_path, output_path)
            if not ok:
                fasttree_path = os.path.join(output_dir, f"fasttree_{run_id}.nw")
                ok_ft, err_ft = _run_fasttree(alignment_path, fasttree_path, nucleotide=bool(kwargs.get("nucleotide", False)))
                if ok_ft:
                    logger.info("build_nj failed (%s); FastTree fallback succeeded.", err)
                    return {
                        "success": True,
                        "data": {
                            "message": f"build_nj failed ({err}); FastTree tree built instead. Use newick_path with ete set_outgroup, render, etc.",
                            "output_path": os.path.abspath(fasttree_path),
                            "newick_path": os.path.abspath(fasttree_path),
                            "downloaded": {"newick": [os.path.abspath(fasttree_path)]},
                        },
                    }
                return {"success": False, "error": f"build_nj failed: {err}. FastTree fallback also failed: {err_ft}", "data": {}}
            return {
                "success": True,
                "data": {
                    "message": "Neighbor-joining tree built from alignment.",
                    "output_path": os.path.abspath(output_path),
                    "downloaded": {"newick": [os.path.abspath(output_path)]},
                },
            }

        # build_fasttree: build tree from alignment using FastTree binary (does not require ete3)
        if action == "build_fasttree":
            alignment_path = (kwargs.get("alignment_path") or kwargs.get("fasta_path") or "").strip()
            if not alignment_path or not os.path.isfile(alignment_path):
                return {"success": False, "error": "build_fasttree requires alignment_path (or fasta_path) to an existing alignment FASTA file.", "data": {}}
            output_path = (kwargs.get("output_path") or "").strip()
            if output_path and output_path.lower().endswith((".png", ".svg", ".pdf")):
                output_path = ""
            if not output_path:
                output_path = os.path.join(output_dir, f"fasttree_{run_id}.nw")
            elif not os.path.isabs(output_path):
                output_path = os.path.join(output_dir, output_path)
            if not output_path.endswith(".nw") and not output_path.endswith(".newick"):
                output_path = output_path + ".nw"
            nucleotide = bool(kwargs.get("nucleotide", False))
            ok, err = _run_fasttree(alignment_path, output_path, nucleotide=nucleotide)
            if not ok:
                return {"success": False, "error": err, "data": {}}
            return {
                "success": True,
                "data": {
                    "message": "FastTree tree built from alignment. Use newick_path with ete set_outgroup, render, etc. for annotation and analysis.",
                    "output_path": os.path.abspath(output_path),
                    "newick_path": os.path.abspath(output_path),
                    "downloaded": {"newick": [os.path.abspath(output_path)]},
                },
            }

        # All other actions require ete3 and a Newick tree (not FASTA)
        ok, err = _ensure_ete()
        if not ok:
            return {"success": False, "error": err, "data": {}}

        try:
            from ete3 import Tree, PhyloTree, TreeStyle, NodeStyle, ClusterTree
        except Exception as e:
            err = str(e).strip() or type(e).__name__
            return {"success": False, "error": f"ete3 import failed: {err}. Install ete3 in the same Python (pip install ete3). If already installed, try: conda install -c conda-forge pyqt.", "data": {}}

        # Resolve tree input (must be Newick content, not FASTA)
        newick_path = (kwargs.get("newick_path") or "").strip()
        newick_string = (kwargs.get("newick_string") or "").strip()
        if newick_path and os.path.isfile(newick_path):
            with open(newick_path, "r") as f:
                raw = f.read().strip()
            if raw.startswith("(") or raw.startswith(";"):
                newick_string = raw
            else:
                return {"success": False, "error": f"File {newick_path} is not Newick format. To build a tree from alignment use action=build_nj or action=build_fasttree with alignment_path.", "data": {}}
        if not newick_string:
            return {"success": False, "error": "Provide newick_path or newick_string. To build a tree from alignment use action=build_nj or build_fasttree with alignment_path.", "data": {}}

        use_phylotree = action.startswith("phylotree_")
        try:
            if use_phylotree:
                tree = PhyloTree(newick_string, format=kwargs.get("format", 0))
                alignment_path = (kwargs.get("alignment_path") or "").strip()
                if alignment_path and os.path.isfile(alignment_path):
                    tree.link_to_alignment(alignment_path)
            else:
                tree = Tree(newick_string, format=kwargs.get("format", 0))
        except Exception as e:
            return {"success": False, "error": f"Failed to parse tree: {e}", "data": {}}

        output_path = (kwargs.get("output_path") or "").strip()
        if output_path and not os.path.isabs(output_path):
            output_path = os.path.join(output_dir, output_path)

        # Dispatch actions (default outputs use run_id for safe unique filenames)
        if action == "load_tree":
            n_leaves = len(tree.get_leaves())
            return {
                "success": True,
                "data": {
                    "message": f"Tree loaded: {n_leaves} leaves",
                    "n_leaves": n_leaves,
                    "leaf_names": tree.get_leaf_names()[:50],
                },
            }

        if action == "write_newick":
            out = output_path or os.path.join(output_dir, f"tree_{run_id}.nw")
            tree.write(outfile=out, format=kwargs.get("format", 0))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": "Newick written",
                    "output_path": os.path.abspath(out),
                    "downloaded": {"newick": [os.path.abspath(out)]},
                },
            }

        if action == "prune":
            leaves = kwargs.get("leaves_to_keep") or []
            if not leaves:
                return {"success": False, "error": "leaves_to_keep required for prune", "data": {}}
            tree.prune(leaves, preserve_branch_length=kwargs.get("preserve_branch_length", False))
            out = output_path or os.path.join(output_dir, f"pruned_tree_{run_id}.nw")
            tree.write(outfile=out, format=kwargs.get("format", 0))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": f"Pruned to {len(leaves)} leaves",
                    "output_path": os.path.abspath(out),
                    "downloaded": {"newick": [os.path.abspath(out)]},
                },
            }

        if action == "set_outgroup":
            outgroup_name = (kwargs.get("outgroup") or "").strip()
            if not outgroup_name:
                return {"success": False, "error": "outgroup required", "data": {}}
            nodes = tree.search_nodes(name=outgroup_name)
            if not nodes:
                nodes = tree.get_leaves_by_name(outgroup_name)
            if not nodes:
                return {"success": False, "error": f"Outgroup '{outgroup_name}' not found", "data": {}}
            tree.set_outgroup(nodes[0])
            out = output_path or os.path.join(output_dir, f"rooted_tree_{run_id}.nw")
            tree.write(outfile=out, format=kwargs.get("format", 0))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": f"Rooted with outgroup {outgroup_name}",
                    "output_path": os.path.abspath(out),
                    "downloaded": {"newick": [os.path.abspath(out)]},
                },
            }

        if action == "ladderize":
            tree.ladderize(direction=kwargs.get("direction", 0))
            out = output_path or os.path.join(output_dir, f"ladderized_tree_{run_id}.nw")
            tree.write(outfile=out, format=kwargs.get("format", 0))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": "Tree ladderized",
                    "output_path": os.path.abspath(out),
                    "downloaded": {"newick": [os.path.abspath(out)]},
                },
            }

        if action == "convert_ultrametric":
            tree_length = kwargs.get("tree_length")
            tree.convert_to_ultrametric(tree_length=tree_length, strategy=kwargs.get("strategy", "balanced"))
            out = output_path or os.path.join(output_dir, f"ultrametric_tree_{run_id}.nw")
            tree.write(outfile=out, format=kwargs.get("format", 0))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": "Tree converted to ultrametric",
                    "output_path": os.path.abspath(out),
                    "downloaded": {"newick": [os.path.abspath(out)]},
                },
            }

        if action == "render":
            fmt = (kwargs.get("image_format") or "png").strip().lower()
            if fmt not in ("png", "svg", "pdf"):
                fmt = "png"
            out = output_path or os.path.join(output_dir, f"tree_{run_id}.{fmt}")
            if out.endswith(".nw") or out.endswith(".newick"):
                out = os.path.splitext(out)[0] + f".{fmt}"
            if not out.endswith(f".{fmt}"):
                out = out + f".{fmt}"
            ts = TreeStyle()
            ts.show_leaf_name = True
            ts.show_branch_support = True
            ts.show_branch_length = kwargs.get("show_branch_length", False)
            tree.render(out, tree_style=ts, w=kwargs.get("width"), h=kwargs.get("height"), units="px", dpi=kwargs.get("dpi", 300))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": f"Tree rendered to {fmt}",
                    "output_path": os.path.abspath(out),
                    "downloaded": {fmt: [os.path.abspath(out)]},
                },
            }

        if action == "get_ascii":
            ascii_str = tree.get_ascii(show_internal=kwargs.get("show_internal", True), compact=kwargs.get("compact", False))
            return {
                "success": True,
                "data": {"message": "ASCII tree", "ascii": ascii_str},
            }

        if action == "robinson_foulds":
            path2 = (kwargs.get("newick_path2") or "").strip()
            if not path2 or not os.path.isfile(path2):
                return {"success": False, "error": "newick_path2 required (path to second tree)", "data": {}}
            with open(path2, "r") as f:
                tree2 = Tree(f.read().strip(), format=kwargs.get("format", 0))
            rf, rf_max, common, names, e1, e2, d1, d2 = tree.robinson_foulds(
                tree2,
                attr_t1="name",
                attr_t2="name",
                unrooted_trees=kwargs.get("unrooted", False),
            )
            return {
                "success": True,
                "data": {
                    "message": "Robinson-Foulds comparison",
                    "rf_distance": rf,
                    "rf_max": rf_max,
                    "rf_normalized": rf / rf_max if rf_max else 0,
                },
            }

        if action == "phylotree_annotate_ncbi":
            taxid_attr = kwargs.get("taxid_attr", "name")
            tree.annotate_ncbi_taxa(taxid_attr=taxid_attr)
            out = output_path or os.path.join(output_dir, f"annotated_tree_{run_id}.nw")
            tree.write(outfile=out, format=kwargs.get("format", 0))
            ensure_file_permissions(out)
            return {
                "success": True,
                "data": {
                    "message": "NCBI taxonomy annotated",
                    "output_path": os.path.abspath(out),
                    "downloaded": {"newick": [os.path.abspath(out)]},
                },
            }

        if action == "phylotree_evolutionary_events":
            events = tree.get_descendant_evol_events(sos_thr=kwargs.get("sos_thr", 0.0))
            out_list = []
            for ev in events[:100]:
                out_list.append({"etype": ev.etype, "in_seqs": ev.in_seqs, "out_seqs": ev.out_seqs})
            return {
                "success": True,
                "data": {
                    "message": f"Found {len(events)} evolutionary events",
                    "n_events": len(events),
                    "events_preview": out_list,
                },
            }

        if action == "phylotree_speciation_trees":
            n_sptrees, n_dups, _ = tree.get_speciation_trees(autodetect_duplications=kwargs.get("autodetect_duplications", True))
            return {
                "success": True,
                "data": {
                    "message": f"Speciation trees: {n_sptrees} trees, {n_dups} duplications",
                    "n_speciation_trees": n_sptrees,
                    "n_duplications": n_dups,
                },
            }

        if action == "phylotree_split_by_dups":
            subtrees = tree.split_by_dups(autodetect_duplications=kwargs.get("autodetect_duplications", True))
            out_dir = output_dir
            paths = []
            for i, st in enumerate(subtrees[:20]):
                p = os.path.join(out_dir, f"subtree_{run_id}_{i + 1}.nw")
                st.write(outfile=p, format=kwargs.get("format", 0))
                paths.append(os.path.abspath(p))
            ensure_file_permissions(out_dir)
            return {
                "success": True,
                "data": {
                    "message": f"Split into {len(subtrees)} subtrees",
                    "n_subtrees": len(subtrees),
                    "output_paths": paths,
                    "downloaded": {"newick": paths},
                },
            }

        if action == "cluster_silhouette":
            array_path = (kwargs.get("arraytable_path") or "").strip()
            if not array_path or not os.path.isfile(array_path):
                return {"success": False, "error": "arraytable_path required for cluster_silhouette", "data": {}}
            try:
                from ete3 import ArrayTable
                at = ArrayTable()
                at.read_from_file(array_path)
                ct = ClusterTree(newick_string, text_array=at)
                sil = ct.get_silhouette()
                return {
                    "success": True,
                    "data": {"message": "Silhouette calculated", "silhouette": sil},
                }
            except Exception as e:
                return {"success": False, "error": str(e), "data": {}}

        if action == "cluster_dunn":
            array_path = (kwargs.get("arraytable_path") or "").strip()
            if not array_path or not os.path.isfile(array_path):
                return {"success": False, "error": "arraytable_path required for cluster_dunn", "data": {}}
            try:
                from ete3 import ArrayTable
                at = ArrayTable()
                at.read_from_file(array_path)
                ct = ClusterTree(newick_string, text_array=at)
                clusters = list(ct.get_children())
                dunn = ct.get_dunn(clusters)
                return {
                    "success": True,
                    "data": {"message": "Dunn index calculated", "dunn_index": dunn},
                }
            except Exception as e:
                return {"success": False, "error": str(e), "data": {}}

        return {"success": False, "error": f"Unknown action: {action}", "data": {}}
