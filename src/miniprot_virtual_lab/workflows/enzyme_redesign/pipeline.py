"""
Enzyme Redesign Pipeline (Paper-Based)

Implements the computational workflow described in:
  Meng et al. (2021) ACS Catalysis 11, 10733-10747.
  "Computational Redesign of an ω-Transaminase from Pseudomonas jessenii
   for Asymmetric Synthesis of Enantiopure Bulky Amines"

Adapted from Rosetta-based dock-and-design to an open-source toolchain:
  SCWRL4 (side-chain optimization) + AutoDock Vina (docking) + OpenBabel (prep)

Pipeline (7 steps):
  1. Structure Preparation    -- clean PDB, add hydrogens (OpenBabel)
  2. Ligand Preparation       -- convert to PDBQT, compute centroid
  3. Pocket Detection         -- identify active-site residues
  4. Mutation Design          -- generate targeted mutation library
  5. Mutation Modeling        -- build mutant structures (SCWRL4 or OpenBabel)
  6. Ensemble Docking         -- dock ligand to each mutant (AutoDock Vina)
  7. Ranking & Summary        -- sort by binding affinity, output CSV

Usage:
    from workflows.enzyme_redesign.pipeline import EnzymeRedesignPipeline

    pipeline = EnzymeRedesignPipeline(
        enzyme_pdb=Path("enzyme.pdb"),
        ligand_sdf=Path("ligand.sdf"),
        work_dir=Path("work"),
    )
    state = pipeline.run()
    print(state.ranked_results)
"""
from pathlib import Path

from .state import WorkflowState
from .checkpoint import CheckpointManager
from .steps import (
    StructurePrepStep,
    LigandPrepStep,
    PocketDetectionStep,
    MutationDesignStep,
    MutationModelingStep,
    MutationDockingStep,
    RankingStep,
)


class EnzymeRedesignPipeline:
    """
    End-to-end computational enzyme redesign pipeline.

    Configurable for both PLP-dependent transaminases (paper mode)
    and general enzymes.
    """

    def __init__(
        self,
        enzyme_pdb: Path,
        ligand_sdf: Path,
        work_dir: Path,
        *,
        # PLP / transaminase-specific
        plp_resname: str = "PLP",
        build_external_aldimine: bool = False,
        # Pocket
        large_pocket_residues: list = None,
        small_pocket_residues: list = None,
        pocket_cutoff: float = 8.0,
        # Mutation design
        mutation_strategy: str = "smart",
        target_positions: list = None,
        # Modeling
        scwrl_binary: str = "Scwrl4",
        # Docking
        vina_binary: str = "vina",
        obabel_binary: str = "obabel",
        box_padding: float = 5.0,
        exhaustiveness: int = 8,
        # Limits
        top_n: int = 20,
    ):
        self.state = WorkflowState(
            enzyme_pdb=enzyme_pdb,
            ligand_sdf=ligand_sdf,
            work_dir=work_dir,
            plp_resname=plp_resname,
            large_pocket_residues=large_pocket_residues or [],
            small_pocket_residues=small_pocket_residues or [],
            pocket_cutoff=pocket_cutoff,
            top_n=top_n,
        )

        self.checkpoint = CheckpointManager(work_dir / "checkpoint.json")

        # Build the step chain
        self.steps = [
            StructurePrepStep(
                target_chain="A",
            ),
            LigandPrepStep(
                obabel_binary=obabel_binary,
                build_external_aldimine=build_external_aldimine,
                plp_resname=plp_resname,
            ),
            PocketDetectionStep(
                ligand_resname=plp_resname,
                cutoff=pocket_cutoff,
            ),
            MutationDesignStep(
                strategy=mutation_strategy,
                target_positions=target_positions or [],
            ),
            MutationModelingStep(
                scwrl_binary=scwrl_binary,
                obabel_binary=obabel_binary,
                top_n=top_n,
            ),
            MutationDockingStep(
                vina_binary=vina_binary,
                obabel_binary=obabel_binary,
                box_padding=box_padding,
                exhaustiveness=exhaustiveness,
                top_n=top_n,
            ),
            RankingStep(
                top_n=top_n,
            ),
        ]

    def run(self) -> WorkflowState:
        """Execute all pipeline steps sequentially with checkpointing."""
        print("\n" + "=" * 60)
        print("  Enzyme Redesign Pipeline")
        print("  Meng et al. (2021) workflow -- SCWRL4 + Vina toolchain")
        print("=" * 60)

        for step in self.steps:
            if step.name in self.state.completed_steps:
                print(f"\n⏭  Skipping '{step.name}' (already completed)")
                continue

            try:
                self.state = step.run(self.state)
                self.checkpoint.save(self.state)
            except Exception as e:
                self.state.failed = True
                self.state.error_message = str(e)
                self.checkpoint.save(self.state)
                print(f"\n  ERROR in '{step.name}': {e}")
                raise

        print("\n" + "=" * 60)
        print("  Pipeline Complete!")
        if self.state.summary_csv:
            print(f"  Results: {self.state.summary_csv}")
        print("=" * 60)
        return self.state

    def resume(self) -> WorkflowState:
        """Resume from a previous checkpoint."""
        if not self.checkpoint.exists():
            raise FileNotFoundError("No checkpoint found to resume from")

        saved = self.checkpoint.load()
        self.state = saved
        print(f"Resuming from step '{self.state.current_step}'")
        print(f"Completed: {self.state.completed_steps}")
        return self.run()
