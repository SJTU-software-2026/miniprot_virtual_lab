"""
Chemistry tools — small molecule lookup and preparation.

Primary agent: **Chemistry Specialist**
"""

CATEGORY = "chemistry"
PRIMARY_AGENT = "Chemistry Specialist"
DESCRIPTION = "Chemical compound database and format conversion tools."

TOOLS = {
    "smiles": {
        "description": "Look up SMILES, formula, InChI by compound name, PubChem CID, or ChEMBL ID.",
        "primary_agent": "Chemistry Specialist",
        "typical_use": "Get SMILES string + generate 3D SDF for docking. Use output_sdf=true for ligand prep.",
        "key_params": "query, source (auto|pubchem|chembl), output_sdf, save_smi",
    },
}
