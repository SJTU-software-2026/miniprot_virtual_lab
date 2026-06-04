from Bio.PDB import PDBParser

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl


class MutationGenerationStep:
    """
    生成 active-site mutants
    """

    name = "mutation_generation"

    # 常见 redesign amino acids
    DEFAULT_MUTATIONS = [
        "A",
        "V",
        "L",
        "I",
        "F",
        "Y",
        "W",
        "S",
        "T",
        "N",
        "Q",
        "D",
        "E",
        "K",
        "R",
        "H",
        "G"
    ]

    THREE_TO_ONE = {
        "ALA": "A",
        "VAL": "V",
        "LEU": "L",
        "ILE": "I",
        "PHE": "F",
        "TYR": "Y",
        "TRP": "W",
        "SER": "S",
        "THR": "T",
        "ASN": "N",
        "GLN": "Q",
        "ASP": "D",
        "GLU": "E",
        "LYS": "K",
        "ARG": "R",
        "HIS": "H",
        "GLY": "G",
        "CYS": "C",
        "MET": "M",
        "PRO": "P"
    }

    def run(
        self,
        state: WorkflowState
    ) -> WorkflowState:

        print(f"\n[STEP] {self.name}")

        pdb_file = state.prepared_pdb

        if pdb_file is None:
            raise ValueError(
                "prepared_pdb is None"
            )

        parser = PDBParser(QUIET=True)

        structure = parser.get_structure(
            "enzyme",
            str(pdb_file)
        )

        pocket_residues = (
            state.pocket_residues
        )

        if not pocket_residues:
            raise ValueError(
                "No pocket residues found"
            )

        generated_mutants = []

        for model in structure:
            for chain in model:
                for residue in chain:

                    residue_id = (
                        residue.get_id()[1]
                    )

                    if (
                        residue_id
                        not in pocket_residues
                    ):
                        continue

                    residue_name = (
                        residue.get_resname()
                    )

                    if (
                        residue_name
                        not in self.THREE_TO_ONE
                    ):
                        continue

                    wt_aa = (
                        self.THREE_TO_ONE[
                            residue_name
                        ]
                    )

                    for mut_aa in (
                        self.DEFAULT_MUTATIONS
                    ):

                        if mut_aa == wt_aa:
                            continue

                        mutant_name = (
                            f"{wt_aa}"
                            f"{residue_id}"
                            f"{mut_aa}"
                        )

                        generated_mutants.append(
                            mutant_name
                        )

        print(
            f"Generated "
            f"{len(generated_mutants)} "
            f"mutants"
        )

        output_dir = (
            state.work_dir /
            "mutation_generation"
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        mutant_file = (
            output_dir /
            "mutants.txt"
        )

        with open(mutant_file, "w", encoding="utf-8") as f:
            for mutant in generated_mutants:
                f.write(f"{mutant}\n")

        state.generated_mutants = (
            generated_mutants
        )

        state.completed_steps.append(
            self.name
        )

        state.current_step = self.name

        return state
