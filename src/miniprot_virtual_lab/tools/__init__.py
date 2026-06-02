"""
Tools package for MiniProt Virtual Lab.

Each subdirectory represents a tool category. Each category's __init__.py
defines a TOOLS dict with metadata for every tool in that category.

The TOOL_REGISTRY (built at import time) aggregates all tools across
all categories, providing a single lookup point for tool metadata.

Usage:
    from miniprot_virtual_lab.tools import ToolBridge, get_bridge, TOOL_REGISTRY

    bridge = get_bridge()
    tools = bridge.list_tools()
    meta = TOOL_REGISTRY.get("uniprot_search", {})
    print(meta["primary_agent"])  # → "Protein Search Specialist"
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
#         "manual" = needs manual binary install (conda/pip)
#         "unavailable" = needs license/model/env that is not bundled
#
# Tested: 2025-06-02

TOOL_STATUS: dict = {}
for tid in TOOL_REGISTRY:
    TOOL_STATUS[tid] = "manual"  # default

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

# Locally verified (tested on Windows 2025-06-02)
for tid in ("pocket_picker",):
    TOOL_STATUS[tid] = "available"

# Docker-installed (binary via conda in Dockerfile)
for tid in ("sequence_alignment", "mmseqs2", "cdhit", "foldseek",
            "tmalign", "autodock_vina", "pocket_box", "pdb_repair",
            "pymol", "ete", "omegafold", "esmfold",
            "miniprot_rag"):
    TOOL_STATUS[tid] = "docker"

# Unavailable (needs license, model file, or separate env)
for tid in ("enzyme_specificity_predict", "enzymecage_retrieve",
            "enzyme_redesign"):
    TOOL_STATUS[tid] = "unavailable"

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
