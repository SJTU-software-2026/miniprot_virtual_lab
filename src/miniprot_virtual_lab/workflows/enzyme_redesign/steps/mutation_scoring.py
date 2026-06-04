import csv
from collections import defaultdict

from Bio.PDB import PDBParser
import numpy as np

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl


class MutationScoringStep:
    """
    对 mutants 进行 heuristic scoring
    """

    name = "mutation_scoring"

    # 氨基酸分类
    HYDROPHOBIC = {
        "A", "V", "L", "I",
        "F", "W", "Y", "M"
    }

    POLAR = {
        "S", "T", "N", "Q"
    }

    POSITIVE = {
        "K", "R", "H"
    }

    NEGATIVE = {
        "D", "E"
    }

    SMALL = {
        "G", "A", "S"
    }

    LARGE = {
        "W", "Y", "F", "R"
    }

    def run(
        self,
        state: WorkflowState
    ) -> WorkflowState:

        print(f"\n[STEP] {self.name}")

        mutants = state.generated_mutants

        if not mutants:

            raise ValueError(
                "No generated mutants"
            )

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

        # -------------------------
        # ligand atoms
        # -------------------------

        ligand_atoms = []

        ligand_name = "PLP"

        for model in structure:
            for chain in model:
                for residue in chain:

                    if (
                        residue.get_resname()
                        ==
                        ligand_name
                    ):

                        for atom in residue:

                            ligand_atoms.append(
                                atom.coord
                            )

        if len(ligand_atoms) == 0:

            raise ValueError(
                f"No ligand found: "
                f"{ligand_name}"
            )

        # -------------------------
        # residue minimum distance
        # -------------------------

        residue_distance_map = {}

        for model in structure:
            for chain in model:
                for residue in chain:

                    residue_id = (
                        residue.get_id()[1]
                    )

                    min_distance = 999.0

                    for atom in residue:

                        atom_coord = atom.coord

                        for ligand_coord in ligand_atoms:

                            distance = (
                                np.linalg.norm(
                                    atom_coord
                                    -
                                    ligand_coord
                                )
                            )

                            if (
                                distance
                                <
                                min_distance
                            ):

                                min_distance = (
                                    distance
                                )

                    residue_distance_map[
                        residue_id
                    ] = min_distance

        # -------------------------
        # scoring
        # -------------------------

        mutant_scores = []

        for mutant in mutants:

            wt_aa = mutant[0]

            mut_aa = mutant[-1]

            residue_id = int(
                mutant[1:-1]
            )

            score = 0.0

            # -----------------
            # 距离加权
            # 越靠近 ligand 越重要
            # -----------------

            distance = (
                residue_distance_map.get(
                    residue_id,
                    10.0
                )
            )

            distance_weight = (
                max(0, 10 - distance)
            )

            score += (
                distance_weight * 2.0
            )

            # -----------------
            # hydrophobic reward
            # -----------------

            if (
                wt_aa in self.HYDROPHOBIC
                and
                mut_aa in self.HYDROPHOBIC
            ):

                score += 2.0

            # -----------------
            # polarity compatibility
            # -----------------

            if (
                wt_aa in self.POLAR
                and
                mut_aa in self.POLAR
            ):

                score += 1.5

            # -----------------
            # charge conservation
            # -----------------

            if (
                wt_aa in self.POSITIVE
                and
                mut_aa in self.POSITIVE
            ):

                score += 2.0

            if (
                wt_aa in self.NEGATIVE
                and
                mut_aa in self.NEGATIVE
            ):

                score += 2.0

            # -----------------
            # steric penalty
            # -----------------

            if (
                wt_aa in self.SMALL
                and
                mut_aa in self.LARGE
            ):

                score -= 2.5

            # -----------------
            # aromatic stabilization
            # -----------------

            aromatic = {
                "F", "W", "Y"
            }

            if (
                wt_aa in aromatic
                and
                mut_aa in aromatic
            ):

                score += 1.5

            mutant_scores.append(
                {
                    "mutant": mutant,
                    "score": float(round(score, 3))
                }
            )

        # -------------------------
        # ranking
        # -------------------------

        mutant_scores = sorted(
            mutant_scores,
            key=lambda x: x["score"],
            reverse=True
        )

        # -------------------------
        # save csv
        # -------------------------

        output_dir = (
            state.work_dir /
            "mutation_scoring"
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        csv_file = (
            output_dir /
            "ranked_mutants.csv"
        )

        with open(
            csv_file,
            "w",
            newline=""
        ) as f:

            writer = csv.writer(f)

            writer.writerow([
                "rank",
                "mutant",
                "score"
            ])

            for idx, item in enumerate(
                mutant_scores,
                start=1
            ):

                writer.writerow([
                    idx,
                    item["mutant"],
                    item["score"]
                ])

        print(
            f"Top mutant: "
            f"{mutant_scores[0]}"
        )

        # state update
        state.docking_scores = {
            item["mutant"]: float(item["score"])
            for item in mutant_scores
        }

        state.completed_steps.append(
            self.name
        )

        state.current_step = self.name

        return state
