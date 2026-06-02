"""
Docking tools — molecular docking and binding site prediction.

Primary agent: **Docking Specialist**
"""

CATEGORY = "docking"
PRIMARY_AGENT = "Docking Specialist"
DESCRIPTION = "Molecular docking, pocket prediction, and structure preparation tools."

TOOLS = {
    "autodock_vina": {
        "description": "Run AutoDock Vina molecular docking (receptor + ligand).",
        "primary_agent": "Docking Specialist",
        "typical_use": "Dock a small-molecule ligand to a protein receptor. Run pocket_picker first!",
        "key_params": "receptor_pdb_path, ligand_pdb_path, exhaustiveness, num_poses, center_x/y/z, size_x/y/z",
    },
    "pocket_picker": {
        "description": "Predict ligand-binding sites. MANDATORY before autodock_vina. Default: P2Rank (ML).",
        "primary_agent": "Docking Specialist",
        "typical_use": "Find binding pocket → auto-injects box coordinates into next Vina call.",
        "key_params": "receptor_pdb_path, method (p2rank|fpocket|geometry|dogsite_api)",
    },
    "pocket_box": {
        "description": "Compute Vina search box from receptor geometry (simpler variant, no ML).",
        "primary_agent": "Docking Specialist",
        "typical_use": "Manual box definition from ligand or receptor coordinates.",
        "key_params": "receptor_pdb_path, ligand_resname, ligand_pdb_path, padding",
    },
    "pdb_repair": {
        "description": "Repair PDB structures: missing residues, chain breaks, non-standard residues.",
        "primary_agent": "Docking Specialist",
        "typical_use": "Fix structure issues before docking. Pass pdb_paths as list for batch repair.",
        "key_params": "pdb_path, pdb_paths, force_repair, add_hydrogens, use_modeller",
    },
}
