"""
Tools package for MiniProt Virtual Lab.

Contains:
  - Tool implementations (33 _tool.py files — the actual bioinformatics tools)
  - ToolBridge (bridge.py) — safe execution + artifact tracking
  - Schema normalization (schemas.py)
  - Local tool path resolver (tool_paths.py)
  - Category metadata subpackages (search, structure, chemistry, etc.)
  - TOOL_GUIDE.md — complete reference

Usage:
    from miniprot_virtual_lab.tools import ToolBridge, get_bridge, TOOL_REGISTRY

    bridge = get_bridge()
    tools = bridge.list_tools()
    meta = TOOL_REGISTRY.get("uniprot_search", {})
"""

from .bridge import ToolBridge, get_bridge
from .schemas import normalize_schema
from .tool_paths import load_tool_paths, get_tool_path, print_tool_paths, TOOL_PATHS

# ── Build tool registry from all categories ───────────────────────

from . import search
from . import structure
from . import chemistry
from . import docking
from . import sequence as _sequence
from . import visualization
from . import utility
from . import specialized

_CATEGORY_MODULES = [
    search,
    structure,
    chemistry,
    docking,
    _sequence,
    visualization,
    utility,
    specialized,
]

# CATEGORY_MAP: category_name → {primary_agent, description, tools: {...}}
CATEGORY_MAP: dict = {}

# TOOL_REGISTRY: tool_id → {description, primary_agent, category, ...}
TOOL_REGISTRY: dict = {}

for mod in _CATEGORY_MODULES:
    cat_name = getattr(mod, "CATEGORY", mod.__name__.split(".")[-1])
    cat_info = {
        "name": cat_name,
        "primary_agent": getattr(mod, "PRIMARY_AGENT", ""),
        "description": getattr(mod, "DESCRIPTION", ""),
        "tools": {},
    }
    tools = getattr(mod, "TOOLS", {})
    for tool_id, tool_meta in tools.items():
        full_meta = dict(tool_meta)
        full_meta["category"] = cat_name
        TOOL_REGISTRY[tool_id] = full_meta
        cat_info["tools"][tool_id] = full_meta

    CATEGORY_MAP[cat_name] = cat_info

# ── Tool availability status ───────────────────────────────────────
# Status: "api" = works via online API (always available)
#         "docker" = binary pre-installed in Docker image
#         "available" = tested and working locally
#         "manual" = needs manual install
#         "unavailable" = needs license/model/env not bundled

TOOL_STATUS: dict = {}
for tid in TOOL_REGISTRY:
    TOOL_STATUS[tid] = "manual"

# API-based tools (tested and working)
for tid in ("uniprot_search", "ncbi_search", "alphafold", "pdb",
            "smiles", "hmmer"):
    TOOL_STATUS[tid] = "api"

# Pure Python (no binaries needed)
for tid in ("protein_properties", "sequence_length_filter",
            "sequence_similarity", "fasta_convert", "similarity_matrix",
            "merger", "pdb_merge", "structure_from_fasta", "echo_tool",
            "structure_alignment_batch"):
    TOOL_STATUS[tid] = "api"

# Locally verified
for tid in ("pocket_picker",):
    TOOL_STATUS[tid] = "available"

# Docker-installed (binary via conda in Dockerfile)
for tid in ("sequence_alignment", "mmseqs2", "cdhit", "foldseek",
            "tmalign", "autodock_vina", "pocket_box", "pdb_repair",
            "pymol", "ete", "omegafold", "esmfold", "miniprot_rag"):
    TOOL_STATUS[tid] = "docker"

# Unavailable (needs license, model, or separate env)
for tid in ("enzyme_specificity_predict", "enzymecage_retrieve"):
    TOOL_STATUS[tid] = "unavailable"

# Available (tested locally with OpenBabel + Vina)
TOOL_STATUS["enzyme_redesign"] = "available"

__all__ = [
    "ToolBridge",
    "get_bridge",
    "normalize_schema",
    "TOOL_REGISTRY",
    "CATEGORY_MAP",
    "TOOL_STATUS",
    "load_tool_paths",
    "get_tool_path",
    "print_tool_paths",
    "TOOL_PATHS",
]
