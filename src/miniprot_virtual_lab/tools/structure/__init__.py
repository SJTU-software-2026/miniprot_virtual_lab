"""
Structure tools — 3D protein structure retrieval, prediction, and comparison.

Primary agent: **Structure Specialist**
"""

CATEGORY = "structure"
PRIMARY_AGENT = "Structure Specialist"
DESCRIPTION = "Protein 3D structure download, prediction, and comparison tools."

TOOLS = {
    "alphafold": {
        "description": "Download predicted structures from AlphaFold DB by UniProt accession or protein name.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Get 3D structure by UniProt ID (uniprot_id) or protein name (query + action=get_structure).",
        "key_params": "action, query, uniprot_id, uniprot_ids, fasta_paths, session_fasta_paths",
    },
    "pdb": {
        "description": "Fetch experimental structures from RCSB Protein Data Bank by 4-character PDB ID.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Download PDB/CIF files by PDB ID (e.g. 1ADA). ONLY for 4-char IDs.",
        "key_params": "pdb_ids, formats",
    },
    "structure_from_fasta": {
        "description": "Get structure from FASTA: try AlphaFold download first, then OmegaFold/ESMFold prediction.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Batch convert FASTA files to PDB structures. Pass all fasta_paths in one call.",
        "key_params": "fasta_paths, prefer_prediction",
    },
    "foldseek": {
        "description": "Structure search and clustering. Default for batch structure alignment.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Find structurally similar proteins, easy_search, easy_cluster.",
        "key_params": "action, query_path, target_path, input_path, sensitivity",
    },
    "tmalign": {
        "description": "Pairwise protein structure alignment (TM-score, RMSD).",
        "primary_agent": "Structure Specialist",
        "typical_use": "Compare two structures, get TM-score and superposed PDB.",
        "key_params": "structure1, structure2, seq_mode",
    },
    "structure_alignment_batch": {
        "description": "Batch all-vs-all structure alignment. Default: Foldseek.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Compare many structures at once, produce similarity matrix input.",
        "key_params": "input_dir, pdb_paths, use_tmalign",
    },
    "similarity_matrix": {
        "description": "Build similarity/clustermap from alignment results (.m8, .npz, structure JSON).",
        "primary_agent": "Structure Specialist",
        "typical_use": "Visualize sequence/structure similarity as heatmap. Highlight query IDs.",
        "key_params": "action, m8_path, npz_path, alignment_json_path, query_ids",
    },
    "omegafold": {
        "description": "OmegaFold: predict 3D structure from sequence (PDB). First choice for de novo prediction.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Predict structure when no AlphaFold DB entry exists.",
        "key_params": "fasta_path, timeout",
    },
    "esmfold": {
        "description": "ESMFold: predict 3D structure from sequence (max 1024 aa). HuggingFace model.",
        "primary_agent": "Structure Specialist",
        "typical_use": "Fallback predictor when OmegaFold fails. GPU recommended.",
        "key_params": "fasta_path, device",
    },
}
