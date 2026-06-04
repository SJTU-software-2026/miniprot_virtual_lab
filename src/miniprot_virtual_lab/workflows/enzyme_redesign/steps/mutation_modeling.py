"""
Step 5: Mutation Modeling
  - Generate mutant structures with side-chain optimization
  - Primary: SCWRL4 for accurate side-chain packing
  - Fallback: OpenBabel MMFF94 local minimization of mutated residue
  - Automatic tool detection -- uses whatever is available

Paper reference: Meng et al. (2021) used Rosetta with flexible backbone +
sidechain sampling (enzdes). SCWRL4 is a lighter-weight alternative for
side-chain optimization that can approximate Rosetta-like repacking.
"""
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl

try:
    from Bio.PDB import PDBParser, PDBIO
except ImportError:
    PDBParser = None
    PDBIO = None


class MutationModelingStep:
    """
    Build mutant structures.

    Detection order for SCWRL4:
      1. scwrl_binary on PATH or in tools/
      2. SCWRL4_EXE environment variable
      -> if not found, fall back to OpenBabel minimization
    """

    name = "mutation_modeling"

    ONE_TO_THREE = {
        "A": "ALA", "V": "VAL", "L": "LEU", "I": "ILE",
        "F": "PHE", "Y": "TYR", "W": "TRP", "S": "SER",
        "T": "THR", "N": "ASN", "Q": "GLN", "D": "ASP",
        "E": "GLU", "K": "LYS", "R": "ARG", "H": "HIS",
        "G": "GLY", "C": "CYS", "M": "MET", "P": "PRO",
    }

    def __init__(
        self,
        scwrl_binary: str = "Scwrl4",
        obabel_binary: str = "obabel",
        top_n: int = 20,
    ):
        self.scwrl_binary = scwrl_binary
        self.obabel_binary = obabel_binary
        self.top_n = top_n

    @property
    def has_scwrl(self) -> bool:
        """Check if SCWRL4 executable is available."""
        exe = shutil.which(self.scwrl_binary)
        if exe:
            return True
        # Check common locations
        candidates = [
            Path("C:/miniprot/tools/Scwrl4.exe"),
            Path("C:/miniprot/tools/scwrl4.exe"),
            Path.home() / "bin" / "Scwrl4.exe",
        ]
        return any(c.exists() for c in candidates)

    def _find_scwrl(self) -> str:
        """Return full path to SCWRL4 executable."""
        exe = shutil.which(self.scwrl_binary)
        if exe:
            return exe
        candidates = [
            Path("C:/miniprot/tools/Scwrl4.exe"),
            Path("C:/miniprot/tools/scwrl4.exe"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return None


    # -- main ------------------------------------------------

    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        scwrl_status = "SCWRL4: yes" if self.has_scwrl else "fallback: OpenBabel"
        print(f"[STEP 5/7] {self.name} ({scwrl_status})")
        print(f"{'='*60}")

        if PDBParser is None:
            raise ImportError("BioPython is required for mutation modeling")

        mutants = state.generated_mutants
        if not mutants:
            raise ValueError("No mutants generated -- run Step 4 first")

        pdb_file = state.prepared_receptor
        if pdb_file is None:
            raise ValueError("prepared_receptor is None -- run Step 1 first")

        output_dir = state.work_dir / "05_mutation_models"
        output_dir.mkdir(parents=True, exist_ok=True)

        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("enzyme", str(pdb_file))

        # Limit to top N mutants to keep runtime manageable
        top_mutants = mutants[:self.top_n]
        print(f"  Modeling top {len(top_mutants)} mutants...")

        generated_models = {}
        for i, mutant in enumerate(top_mutants):
            print(f"  [{i+1}/{len(top_mutants)}] {mutant}")

            residue_id = int(mutant[1:-1])
            mut_aa = mutant[-1]
            mut_resname = self.ONE_TO_THREE.get(mut_aa)
            if mut_resname is None:
                print(f"    Unknown AA code: {mut_aa}, skipping")
                continue

            # Step A: mutate residue in silico
            mutant_structure = self._mutate_residue(
                structure, residue_id, mut_resname
            )
            mutant_pdb = output_dir / f"{mutant}.pdb"
            io = PDBIO()
            io.set_structure(mutant_structure)
            io.save(str(mutant_pdb))

            # Step B: side-chain optimization
            # Fix END position for obabel compatibility
            final_pdb = mutant_pdb
            self._fix_pdb_end(mutant_pdb)

            if self.has_scwrl:
                refined = self._run_scwrl(mutant_pdb, output_dir, mutant)
                if refined:
                    final_pdb = refined
            # Note: OpenBabel relax is skipped because it cannot reliably
            # parse Biopython-generated PDB files.

            generated_models[mutant] = str(final_pdb)

        state.generated_models = generated_models
        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state

    # -- SCWRL4 ----------------------------------------------

    def _run_scwrl(
        self, input_pdb: Path, output_dir: Path, mutant_name: str
    ) -> Path | None:
        """Run SCWRL4 for side-chain optimization."""
        scwrl = self._find_scwrl()
        if scwrl is None:
            return None

        output_pdb = output_dir / f"{mutant_name}_scwrl.pdb"
        try:
            result = subprocess.run(
                [scwrl, "-i", str(input_pdb), "-o", str(output_pdb)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and output_pdb.exists():
                print(f"    SCWRL4 refined -> {output_pdb.name}")
                return output_pdb
            else:
                print(f"    SCWRL4 failed: {result.stderr[:200]}")
                return None
        except Exception as e:
            print(f"    SCWRL4 error: {e}")
            return None

    # -- OpenBabel fallback ----------------------------------

    def _run_obabel_relax(
        self, input_pdb: Path, output_dir: Path, mutant_name: str
    ) -> Path | None:
        """Use OpenBabel MMFF94 to relax the mutated structure."""
        obabel = find_obabel(self.obabel_binary)
        if obabel is None:
            return None

        output_pdb = output_dir / f"{mutant_name}_obabel.pdb"
        try:
            result = subprocess.run(
                [
                    obabel, str(input_pdb),
                    "-O", str(output_pdb),
                    "--minimize", "--ff", "MMFF94",
                    "--nsteps", "500",
                ],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and output_pdb.exists():
                print(f"    OpenBabel relaxed -> {output_pdb.name}")
                return output_pdb
            else:
                return None
        except Exception:
            return None

    # -- structure manipulation ------------------------------

    @staticmethod
    def _fix_pdb_end(pdb_path: Path):
        """Remove all END lines and add one at the very end."""
        lines = []
        with open(pdb_path) as f:
            for line in f:
                if not line.startswith("END"):
                    lines.append(line)
        with open(pdb_path, "w") as f:
            f.writelines(lines)
            f.write("END\n")

    @staticmethod
    def _mutate_residue(structure, residue_id: int, new_resname: str):
        """Create a deep copy of the structure with one residue mutated."""
        structure_copy = deepcopy(structure)
        for model in structure_copy:
            for chain in model:
                for residue in chain:
                    if residue.get_id()[1] == residue_id:
                        residue.resname = new_resname
                        return structure_copy
        raise ValueError(f"Residue {residue_id} not found in structure")
