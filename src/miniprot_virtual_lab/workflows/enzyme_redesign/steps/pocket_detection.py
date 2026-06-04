"""
Step 3: Pocket Detection
  - Identify binding-pocket residues within a distance cutoff of the ligand
  - Classify into "large pocket" and "small pocket" (paper-specific for transaminases)
  - For general enzymes, all pocket residues are treated as designable

Paper reference: Meng et al. (2021) -- residues within 10 A of the external
aldimine were repackable; large pocket (Trp58, Met54, Leu57, Tyr151, Ala230,
Ile261, Arg417) and small pocket (Phe86) residues were targeted for mutation.
"""
from pathlib import Path

import numpy as np

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl

try:
    from Bio.PDB import PDBParser
except ImportError:
    PDBParser = None


class PocketDetectionStep:
    """
    Detect active-site residues by distance to ligand.

    When large_pocket_residues / small_pocket_residues are provided in state,
    they are used to classify detected residues. Otherwise all detected
    residues are treated as designable.
    """

    name = "pocket_detection"

    def __init__(
        self,
        ligand_resname: str = "PLP",
        cutoff: float = 8.0,
    ):
        self.ligand_resname = ligand_resname
        self.cutoff = cutoff

    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        print(f"[STEP 3/7] {self.name} (cutoff = {self.cutoff} A)")
        print(f"{'='*60}")

        if PDBParser is None:
            raise ImportError("BioPython is required for pocket detection")

        pdb_file = state.prepared_receptor
        if pdb_file is None:
            raise ValueError("prepared_receptor is None -- run Step 1 first")

        output_dir = state.work_dir / "03_pocket_detection"
        output_dir.mkdir(parents=True, exist_ok=True)

        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("enzyme", str(pdb_file))

        # -- collect ligand atom coordinates -----------------
        ligand_atoms = []
        for model in structure:
            for chain in model:
                for residue in chain:
                    if residue.get_resname() == self.ligand_resname:
                        for atom in residue:
                            ligand_atoms.append(atom.coord)
                        print(f"  Found {self.ligand_resname} in chain {chain.id}")

        if not ligand_atoms:
            # try to find any HETATM as potential ligand
            print(f"  WARNING: No {self.ligand_resname} found in structure.")
            print(f"  Trying to detect any HETATM as ligand...")
            for model in structure:
                for chain in model:
                    for residue in chain:
                        if residue.get_id()[0].startswith("H_"):
                            for atom in residue:
                                ligand_atoms.append(atom.coord)
                            print(f"  Using {residue.get_resname()} as ligand")
                            break
                    if ligand_atoms:
                        break
                if ligand_atoms:
                    break

        if not ligand_atoms:
            raise ValueError(
                f"No ligand found. Set ligand_resname (currently '{self.ligand_resname}') "
                "to match the residue name in your PDB file."
            )

        # -- find all residues within cutoff -----------------
        pocket_residues = set()
        for model in structure:
            for chain in model:
                for residue in chain:
                    if residue.get_resname() == self.ligand_resname:
                        continue
                    residue_id = residue.get_id()[1]
                    for atom in residue:
                        for lig_coord in ligand_atoms:
                            dist = np.linalg.norm(atom.coord - lig_coord)
                            if dist <= self.cutoff:
                                pocket_residues.add(residue_id)
                                break

        pocket_residues = sorted(pocket_residues)
        print(f"  Pocket residues ({len(pocket_residues)}): {pocket_residues}")

        # -- classify into large / small pocket --------------
        large = [r for r in pocket_residues
                 if r in (state.large_pocket_residues or [])]
        small = [r for r in pocket_residues
                 if r in (state.small_pocket_residues or [])]

        if large:
            print(f"  Large pocket: {large}")
        if small:
            print(f"  Small pocket: {small}")

        # -- save --------------------------------------------
        pocket_file = output_dir / "pocket_residues.txt"
        with open(pocket_file, "w") as f:
            for r in pocket_residues:
                tag = ""
                if r in large:
                    tag = "\tlarge_pocket"
                elif r in small:
                    tag = "\tsmall_pocket"
                f.write(f"{r}{tag}\n")

        state.pocket_residues = pocket_residues
        state.large_pocket_detected = large if large else pocket_residues
        state.small_pocket_detected = small
        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state
