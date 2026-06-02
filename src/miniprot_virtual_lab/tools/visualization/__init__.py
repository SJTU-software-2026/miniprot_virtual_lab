"""
Visualization tools — protein structure rendering, docking complex figures.

Primary agent: **Sequence Analysis Specialist** (visualization tasks)
"""

CATEGORY = "visualization"
PRIMARY_AGENT = "Sequence Analysis Specialist"
DESCRIPTION = "Protein structure viewing, image rendering, and figure generation."

TOOLS = {
    "pymol": {
        "description": "PyMOL API: load structures and render publication-quality images.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Render docked complex figures (docked_complex=true), structure cartoons, superposition images.",
        "key_params": "action (load|render_image), pdb_path, docked_complex, background, ray",
    },
}
