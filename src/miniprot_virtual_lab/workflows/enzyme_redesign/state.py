"""
Workflow state for enzyme redesign pipeline.
Based on: Meng et al. (2021) ACS Catalysis 11, 10733-10747.
  "Computational Redesign of an ω-Transaminase from Pseudomonas jessenii
   for Asymmetric Synthesis of Enantiopure Bulky Amines"
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class WorkflowState:
    """
    Holds all data flowing through the enzyme redesign pipeline.

    Adapts the paper's Rosetta-based dock-and-design strategy to a
    SCWRL4 + AutoDock Vina toolchain.
    """

    # -- Inputs ----------------------------------------------
    enzyme_pdb: Path           # input enzyme structure (PDB)
    ligand_sdf: Optional[Path] = None  # ligand / substrate (SDF or PDB)
    work_dir: Path = Path("work")

    # -- Configuration ---------------------------------------
    # which residues to consider as the "large pocket" (paper: 54,57,58,151,230,261,417)
    large_pocket_residues: List[int] = field(default_factory=list)
    # which residues for the "small pocket" (paper: 86)
    small_pocket_residues: List[int] = field(default_factory=list)
    # PLP cofactor residue name (for transaminases); leave empty for non-PLP enzymes
    plp_resname: str = "PLP"
    # distance cutoff for pocket detection (A)
    pocket_cutoff: float = 8.0
    # number of top mutants to model & dock
    top_n: int = 20

    # -- Progress tracking -----------------------------------
    current_step: str = "start"
    completed_steps: List[str] = field(default_factory=list)

    # -- Intermediate outputs --------------------------------
    prepared_receptor: Optional[Path] = None    # Step 1 -> cleaned receptor PDB
    prepared_ligand: Optional[Path] = None      # Step 2 -> prepared ligand PDBQT
    ligand_center: Optional[List[float]] = None  # Step 2 -> ligand centroid [x,y,z]

    pocket_residues: List[int] = field(default_factory=list)     # Step 3
    large_pocket_detected: List[int] = field(default_factory=list)
    small_pocket_detected: List[int] = field(default_factory=list)

    generated_mutants: List[str] = field(default_factory=list)   # Step 4
    mutant_design_list: List[Dict] = field(default_factory=list)  # [{pos, wt, mut, pocket}]

    generated_models: Dict[str, str] = field(default_factory=dict)  # Step 5: {mutant: pdb_path}

    vina_scores: Dict[str, float] = field(default_factory=dict)    # Step 6: {mutant: score}
    docking_outputs: Dict[str, str] = field(default_factory=dict)   # {mutant: pdbqt_path}

    # -- Final output ----------------------------------------
    ranked_results: List[Dict] = field(default_factory=list)  # [{rank, mutant, score, ...}]
    summary_csv: Optional[Path] = None

    # -- Error handling --------------------------------------
    failed: bool = False
    error_message: Optional[str] = None
