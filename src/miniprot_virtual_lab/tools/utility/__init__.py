"""
Utility tools — file merging, format conversion helpers.

Primary agent: **Sequence Analysis Specialist** (merging)
Also used by: **Docking Specialist** (ligand merging)
"""

CATEGORY = "utility"
PRIMARY_AGENT = "Sequence Analysis Specialist"
DESCRIPTION = "File merging and utility tools."

TOOLS = {
    "pdb_merge": {
        "description": "Merge multiple PDB or FASTA files into one.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Concatenate FASTA files after filtering/clustering. Merge multiple ligands for docking.",
        "key_params": "pdb_paths, fasta_paths, output_path",
    },
    "merger": {
        "description": "Alias for pdb_merge. Same functionality, preferred name.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Merge FASTA files (preferred over pdb_merge for sequence work).",
        "key_params": "pdb_paths, fasta_paths, output_path",
    },
}
