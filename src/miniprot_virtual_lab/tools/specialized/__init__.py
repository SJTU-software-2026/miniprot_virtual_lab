"""
Specialized tools — enzyme engineering ML models and knowledge base.

These tools are available to agents on an as-needed basis and are not
assigned to a single specialist by default.
"""

CATEGORY = "specialized"
PRIMARY_AGENT = "Principal Investigator"
DESCRIPTION = "Enzyme specificity prediction, reaction-enzyme ranking, and documentation search."

TOOLS = {
    "enzyme_specificity_predict": {
        "description": "Run trained enzyme_prediction SS Lightning model for enzyme-substrate specificity scores.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Predict enzyme specificity from a trained checkpoint. Requires .ckpt + YAML config.",
        "key_params": "checkpoint_path, config_path, test_data_path, accelerator, devices",
    },
    "enzymecage_retrieve": {
        "description": "Rank candidate enzymes for a reaction SMILES using EnzymeCAGE (substrates>>products).",
        "primary_agent": "Docking Specialist",
        "typical_use": "Given a reaction SMILES (contains >>), find the best enzyme candidates.",
        "key_params": "reaction_smiles, uniprot_ids, structure_dir, top_k",
    },
    "miniprot_rag": {
        "description": "Search the local MiniProt Markdown knowledge base (Chroma). For docs, not live data.",
        "primary_agent": "Principal Investigator",
        "typical_use": "How does MiniProt work? What tools are available? Look up workflow guides.",
        "key_params": "query, k, expert_mode",
    },
    "enzyme_redesign": {
        "description": "End-to-end computational enzyme redesign pipeline (Meng et al. 2021). SCWRL4 + Vina.",
        "primary_agent": "Docking Specialist",
        "typical_use": "Full redesign workflow: structure prep → mutation design → modeling → docking → ranking.",
        "key_params": "enzyme_pdb, ligand_sdf, work_dir, mutation_strategy, top_n",
    },
    "echo_tool": {
        "description": "Echo back input text. For testing tool call infrastructure.",
        "primary_agent": "Principal Investigator",
        "typical_use": "Verify that the tool execution loop is working correctly.",
        "key_params": "text",
    },
}
