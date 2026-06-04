from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool  # type: ignore


class EnzymeRedesignTool(BaseTool):
    """
    Automated computational enzyme redesign pipeline.

    7-step workflow:
      1. Structure preparation (OpenBabel)
      2. Ligand preparation (OpenBabel MMFF94 + PDBQT conversion)
      3. Pocket detection (distance-based, large/small pocket classification)
      4. Mutation design (smart or full scanning of pocket residues)
      5. Mutation modeling (SCWRL4 or OpenBabel fallback)
      6. Ensemble docking (AutoDock Vina)
      7. Ranking & summary (ranked CSV + recommendations)
    """

    name = "enzyme_redesign"
    description = (
        "Run a computational enzyme redesign pipeline to improve enzymatic activity "
        "toward a target substrate. The pipeline prepares the enzyme structure, "
        "identifies the active-site pocket, generates targeted mutations, models "
        "mutant structures, docks the ligand to each mutant with AutoDock Vina, "
        "and outputs ranked variant recommendations for experimental testing. "
        "Based on Meng et al. (2021) ACS Catalysis 11, 10733-10747."
    )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "enzyme_pdb": {
                    "type": "string",
                    "description": "Path to the input enzyme PDB file (required).",
                },
                "ligand_sdf": {
                    "type": "string",
                    "description": "Path to the input ligand SDF or PDB file (required).",
                },
                "work_dir": {
                    "type": "string",
                    "description": "Working directory for all pipeline outputs.",
                },
                "plp_resname": {
                    "type": "string",
                    "description": "Residue name of PLP cofactor in the PDB (default: 'PLP'). "
                    "Used for PLP-dependent transaminase redesign as in the Meng et al. paper.",
                },
                "pocket_cutoff": {
                    "type": "number",
                    "description": "Distance cutoff (Angstrom) from ligand for pocket detection (default: 8.0).",
                },
                "large_pocket_residues": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of residue numbers in the large binding pocket. "
                    "For PjTA-R6 (paper): [54, 57, 58, 151, 230, 261, 417].",
                },
                "small_pocket_residues": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of residue numbers in the small binding pocket. "
                    "For PjTA-R6 (paper): [86].",
                },
                "mutation_strategy": {
                    "type": "string",
                    "enum": ["smart", "full"],
                    "description": "Mutation design strategy: 'smart' (curated AA subsets, default) "
                    "or 'full' (all 19 substitutions per position).",
                },
                "target_positions": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Specific residue IDs to mutate. If empty, all pocket residues are used.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of top mutants to model, dock, and report (default: 20).",
                },
                "exhaustiveness": {
                    "type": "integer",
                    "description": "AutoDock Vina exhaustiveness parameter (default: 8, higher = more thorough).",
                },
                "build_external_aldimine": {
                    "type": "boolean",
                    "description": "If True, build PLP-substrate external aldimine intermediate "
                    "for transaminase redesign (paper-specific mode). Default: False.",
                },
            },
            "required": ["enzyme_pdb", "ligand_sdf", "work_dir"],
        }

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        from ..workflows.enzyme_redesign.pipeline import EnzymeRedesignPipeline

        enzyme_pdb = Path(kwargs["enzyme_pdb"])
        ligand_sdf = Path(kwargs["ligand_sdf"])
        work_dir = Path(kwargs["work_dir"])

        # Build pipeline with user-specified parameters
        pipeline = EnzymeRedesignPipeline(
            enzyme_pdb=enzyme_pdb,
            ligand_sdf=ligand_sdf,
            work_dir=work_dir,
            plp_resname=kwargs.get("plp_resname", "PLP"),
            pocket_cutoff=kwargs.get("pocket_cutoff", 8.0),
            large_pocket_residues=kwargs.get("large_pocket_residues"),
            small_pocket_residues=kwargs.get("small_pocket_residues"),
            mutation_strategy=kwargs.get("mutation_strategy", "smart"),
            target_positions=kwargs.get("target_positions"),
            top_n=kwargs.get("top_n", 20),
            exhaustiveness=kwargs.get("exhaustiveness", 8),
            build_external_aldimine=kwargs.get("build_external_aldimine", False),
        )

        state = pipeline.run()

        # Build response
        generated_files = []
        for step_dir in [
            "01_structure_prep",
            "02_ligand_prep",
            "03_pocket_detection",
            "04_mutation_design",
            "05_mutation_models",
            "06_docking",
            "07_results",
        ]:
            d = work_dir / step_dir
            if d.exists():
                for f in d.iterdir():
                    if f.is_file():
                        generated_files.append(str(f))

        return {
            "success": not state.failed,
            "error": state.error_message if state.failed else None,
            "data": {
                "completed_steps": state.completed_steps,
                "pocket_residues": state.pocket_residues,
                "large_pocket_detected": state.large_pocket_detected,
                "small_pocket_detected": state.small_pocket_detected,
                "total_mutants_designed": len(state.generated_mutants),
                "total_models_built": len(state.generated_models),
                "total_docked": len(state.vina_scores),
                "ranked_results": state.ranked_results[:10],  # top 10
                "summary_csv": str(state.summary_csv) if state.summary_csv else None,
            },
            "generated_files": generated_files,
        }
