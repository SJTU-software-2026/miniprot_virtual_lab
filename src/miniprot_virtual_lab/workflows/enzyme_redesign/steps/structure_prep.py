"""
Step 1: Structure Preparation
  - Extract chain A (or first chain) from multi-chain structures
  - Remove water molecules
  - Keep PLP and other cofactors
  - Uses Biopython for robust PDB handling (OpenBabel struggles with large PDBs)

Adapted from: Meng et al. (2021) ACS Catalysis 11, 10733-10747.
"""
from pathlib import Path

from Bio.PDB import PDBParser, PDBIO, Select

from ..state import WorkflowState


class ChainAWatersRemovedSelect(Select):
    """Select only chain A, remove water, keep everything else."""
    def __init__(self, target_chain: str = "A"):
        self.target_chain = target_chain

    def accept_chain(self, chain):
        return chain.get_id() == self.target_chain

    def accept_residue(self, residue):
        return residue.get_resname() != "HOH"


class StructurePrepStep:
    """Clean and prepare the enzyme structure for downstream modelling."""

    name = "structure_preparation"

    def __init__(self, target_chain: str = "A"):
        self.target_chain = target_chain

    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        print(f"[STEP 1/7] {self.name} (chain {self.target_chain})")
        print(f"{'='*60}")

        output_dir = state.work_dir / "01_structure_prep"
        output_dir.mkdir(parents=True, exist_ok=True)
        prepared_pdb = output_dir / "prepared_receptor.pdb"

        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("enzyme", str(state.enzyme_pdb))

        # Auto-detect available chains
        chains = [ch.get_id() for ch in structure.get_chains()]
        print(f"  Available chains: {chains}")

        io = PDBIO()
        io.set_structure(structure)

        # If target chain exists, use it; otherwise use first chain
        use_chain = self.target_chain if self.target_chain in chains else chains[0]
        print(f"  Using chain: {use_chain}")

        select = ChainAWatersRemovedSelect(target_chain=use_chain)
        io.save(str(prepared_pdb), select)

        # Fix END position (Biopython sometimes places END incorrectly)
        self._fix_end(prepared_pdb)

        atom_count = self._count_atoms(prepared_pdb)
        print(f"  Prepared receptor -> {prepared_pdb}")
        print(f"  Total atoms: {atom_count}")

        state.prepared_receptor = prepared_pdb
        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state

    @staticmethod
    def _fix_end(pdb_path: Path):
        """Ensure END is at the very end of the file."""
        lines = []
        with open(pdb_path) as f:
            for line in f:
                if line.startswith("END"):
                    continue  # remove all END lines
                lines.append(line)
        with open(pdb_path, "w") as f:
            f.writelines(lines)
            f.write("END\n")

    @staticmethod
    def _count_atoms(pdb: Path) -> int:
        with open(pdb) as f:
            return sum(
                1 for line in f
                if line.startswith("ATOM") or line.startswith("HETATM")
            )
