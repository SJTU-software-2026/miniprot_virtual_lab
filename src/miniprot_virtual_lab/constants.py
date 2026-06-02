"""
Constants for MiniProt Virtual Lab.

Model pricing, default settings, and tool category definitions.
"""

# ── LLM Configuration ──────────────────────────────────────────────
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

# The provider preset to use by default (see config.py:PROVIDER_PRESETS)
# Set MINIPROT_PROVIDER env var to override (deepseek, deepseek-v3, sjtu, openai, etc.)
DEFAULT_PROVIDER = "deepseek"

# Temperatures
CONSISTENT_TEMPERATURE = 0.2   # For planning, structured output
CREATIVE_TEMPERATURE = 0.8     # For brainstorming, discussion

# Token limits
PLANNER_MAX_TOKENS = 768
SUMMARIZER_MAX_TOKENS = 2048
AGENT_MAX_TOKENS = 1536

# ── Meeting Configuration ──────────────────────────────────────────
DEFAULT_NUM_ROUNDS = 3          # Default discussion rounds for team meetings
DEFAULT_NUM_ITERATIONS = 3      # Default parallel runs for merge strategy
MAX_TOOL_RESULT_CHARS = 4000    # Truncate tool results for LLM context

# ── Pricing (DeepSeek, USD per token, approximate) ─────────────────
# These are placeholders; adjust based on your provider
MODEL_TO_INPUT_PRICE_PER_TOKEN = {
    "deepseek-chat": 0.27 / 10**6,
    "deepseek-v3": 0.27 / 10**6,
    "deepseek-v4-pro": 0.27 / 10**6,
    "deepseek-reasoner": 0.55 / 10**6,
    "gpt-5.2": 1.75 / 10**6,
}

MODEL_TO_OUTPUT_PRICE_PER_TOKEN = {
    "deepseek-chat": 1.10 / 10**6,
    "deepseek-v3": 1.10 / 10**6,
    "deepseek-v4-pro": 1.10 / 10**6,
    "deepseek-reasoner": 2.19 / 10**6,
    "gpt-5.2": 14 / 10**6,
}

# ── Tool Categories ────────────────────────────────────────────────
# Each specialist agent gets one category of tools.
# Tool IDs match those registered in enzyme_update's ToolManager.

TOOL_CATEGORIES = {
    "search": [
        "uniprot_search",
        "ncbi_search",
    ],
    "structure": [
        "alphafold",
        "pdb",
        "structure_from_fasta",
        "omegafold",
        "esmfold",
    ],
    "chemistry": [
        "smiles",
    ],
    "docking": [
        "autodock_vina",
        "pocket_picker",
        "pocket_box",
        "pdb_repair",
    ],
    "sequence_analysis": [
        "sequence_alignment",
        "hmmer",
        "mmseqs2",
        "cdhit",
        "sequence_length_filter",
        "sequence_similarity",
        "protein_properties",
        "fasta_convert",
    ],
    "structure_analysis": [
        "foldseek",
        "tmalign",
        "structure_alignment_batch",
        "similarity_matrix",
    ],
    "visualization": [
        "pymol",
        "ete",
    ],
    "utility": [
        "pdb_merge",
        "merger",
    ],
}

# Flattened set of all tool IDs for validation
ALL_TOOL_IDS = set()
for _tools in TOOL_CATEGORIES.values():
    ALL_TOOL_IDS.update(_tools)

# ── Agent Tool Assignments ─────────────────────────────────────────
AGENT_TOOL_MAP = {
    "Protein Search Specialist": ["search"],
    "Structure Specialist": ["structure", "structure_analysis"],
    "Chemistry Specialist": ["chemistry"],
    "Docking Specialist": ["docking", "utility"],
    "Sequence Analysis Specialist": ["sequence_analysis", "visualization"],
}

# ── Default Enzyme Mining Workflow Steps ──────────────────────────
# Used as a reference when PI designs the research plan.
ENZYME_MINING_REFERENCE_WORKFLOW = [
    {
        "phase": "Discovery",
        "description": "Search known enzymes by substrate/product name in UniProt",
        "tools": ["uniprot_search"],
        "notes": "Use reviewed_only=True, download FASTA",
    },
    {
        "phase": "Filter",
        "description": "Filter sequences by length to remove outliers",
        "tools": ["sequence_length_filter"],
        "notes": "Use reference_fasta + length_range=30",
    },
    {
        "phase": "Homolog Search",
        "description": "Run phmmer/HMMER to find homologous sequences",
        "tools": ["hmmer"],
        "notes": "Submit job, wait for results",
    },
    {
        "phase": "Cluster",
        "description": "Cluster hits with CD-HIT to remove redundancy",
        "tools": ["cdhit"],
        "notes": "identity=0.9",
    },
    {
        "phase": "Merge",
        "description": "Merge query + clustered homologs into target set",
        "tools": ["merger"],
        "notes": "Merge into single FASTA for downstream analysis",
    },
    {
        "phase": "Align & Tree",
        "description": "Multiple sequence alignment + phylogenetic tree",
        "tools": ["sequence_alignment", "ete"],
        "notes": "MAFFT alignment → FastTree → render",
    },
    {
        "phase": "Similarity Matrix",
        "description": "All-vs-all sequence similarity matrix + clustermap",
        "tools": ["mmseqs2", "similarity_matrix"],
        "notes": "Highlight query sequences in red",
    },
    {
        "phase": "Structure Analysis",
        "description": "Get/predict structures, structural alignment",
        "tools": ["alphafold", "structure_from_fasta", "foldseek", "structure_alignment_batch"],
        "notes": "AlphaFold DB for known, OmegaFold/ESMFold for novel",
    },
    {
        "phase": "Docking",
        "description": "Molecular docking of substrates to top candidates",
        "tools": ["pocket_picker", "autodock_vina", "smiles", "pdb_repair"],
        "notes": "P2Rank for pocket → Vina for docking",
    },
    {
        "phase": "Report",
        "description": "Compile results, visualize top candidates",
        "tools": ["pymol", "protein_properties"],
        "notes": "Generate figures, CSV summary",
    },
]
