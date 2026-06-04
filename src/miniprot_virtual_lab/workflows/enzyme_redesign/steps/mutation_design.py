"""
Step 4: Mutation Design
  - Generate targeted mutations at identified pocket positions
  - Support focused design (user-specified positions) or broad scanning
  - Each position is mutated to a set of amino acids

Paper reference: Meng et al. (2021) -- 7 residues in the large binding pocket
(Met54, Leu57, Trp58, Tyr151, Ala230, Ile261, Arg417) and 1 in the small
pocket (Phe86) were targeted. Small focused libraries of 7-18 variants each
were designed per substrate.
"""
from pathlib import Path

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl


class MutationDesignStep:
    """
    Generate a focused mutation library at pocket positions.

    Two strategies:
      - "smart":  mutate each position to a curated subset (hydrophobic,
                  polar, charged, small, large) -- fewer combos, more efficient
      - "full":   mutate each position to all 19 other AAs (systematic scan)
    """

    name = "mutation_design"

    # Amino acid classification for smart design
    AA_GROUPS = {
        "hydrophobic": ["A", "V", "L", "I", "F", "W", "Y", "M"],
        "polar":       ["S", "T", "N", "Q"],
        "positive":    ["K", "R", "H"],
        "negative":    ["D", "E"],
        "small":       ["G", "A", "S"],
        "aromatic":    ["F", "W", "Y", "H"],
    }

    ALL_AA = [
        "A", "C", "D", "E", "F", "G", "H", "I", "K", "L",
        "M", "N", "P", "Q", "R", "S", "T", "V", "W", "Y",
    ]

    THREE_TO_ONE = {
        "ALA": "A", "VAL": "V", "LEU": "L", "ILE": "I",
        "PHE": "F", "TYR": "Y", "TRP": "W", "SER": "S",
        "THR": "T", "ASN": "N", "GLN": "Q", "ASP": "D",
        "GLU": "E", "LYS": "K", "ARG": "R", "HIS": "H",
        "GLY": "G", "CYS": "C", "MET": "M", "PRO": "P",
    }

    def __init__(
        self,
        strategy: str = "smart",
        target_positions: list = None,
    ):
        """
        Parameters
        ----------
        strategy : str
            "smart" -- curated AA subsets per property
            "full"  -- all 19 substitutions per position
        target_positions : list[int] or None
            Specific residue IDs to mutate. If None, uses all pocket residues.
        """
        self.strategy = strategy
        self.target_positions = target_positions or []

    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        print(f"[STEP 4/7] {self.name} (strategy = '{self.strategy}')")
        print(f"{'='*60}")

        # Determine which positions to mutate
        positions = self.target_positions or state.pocket_residues
        if not positions:
            raise ValueError("No pocket residues specified for mutation design")

        # Get the WT amino acid for each position from the structure
        wt_map = self._get_wt_residues(state.prepared_receptor, positions)
        print(f"  Target positions ({len(positions)}): {positions}")

        # Generate mutation list
        mutants = []
        design_list = []

        for pos in positions:
            wt_aa = wt_map.get(pos, "X")
            if wt_aa == "X":
                print(f"  WARNING: Could not determine WT residue at position {pos}")
                continue

            if self.strategy == "smart":
                # Generate from each AA group (excluding WT)
                seen = set()
                for group_name, group_aas in self.AA_GROUPS.items():
                    for mut_aa in group_aas:
                        if mut_aa != wt_aa and mut_aa not in seen:
                            seen.add(mut_aa)
                            mutant_name = f"{wt_aa}{pos}{mut_aa}"
                            mutants.append(mutant_name)
                            design_list.append({
                                "position": pos, "wt": wt_aa,
                                "mut": mut_aa, "group": group_name,
                                "mutant": mutant_name,
                            })
                # Small -> Large and Large -> Small steric swaps
                if wt_aa in self.AA_GROUPS["small"]:
                    for aa in ["W", "Y", "F", "R"]:
                        if aa not in seen:
                            mutant_name = f"{wt_aa}{pos}{aa}"
                            mutants.append(mutant_name)
                            design_list.append({
                                "position": pos, "wt": wt_aa,
                                "mut": aa, "group": "large_insertion",
                                "mutant": mutant_name,
                            })

            else:  # "full" -- all 19 substitutions
                for mut_aa in self.ALL_AA:
                    if mut_aa != wt_aa:
                        mutant_name = f"{wt_aa}{pos}{mut_aa}"
                        mutants.append(mutant_name)
                        design_list.append({
                            "position": pos, "wt": wt_aa,
                            "mut": mut_aa, "group": "full_scan",
                            "mutant": mutant_name,
                        })

        print(f"  Generated {len(mutants)} single-point mutation designs")

        # -- save --------------------------------------------
        output_dir = state.work_dir / "04_mutation_design"
        output_dir.mkdir(parents=True, exist_ok=True)
        mutant_file = output_dir / "mutant_designs.txt"
        with open(mutant_file, "w") as f:
            f.write("# position\twt\tmut\tgroup\tmutant\n")
            for d in design_list:
                f.write(f"{d['position']}\t{d['wt']}\t{d['mut']}"
                        f"\t{d['group']}\t{d['mutant']}\n")

        state.generated_mutants = mutants
        state.mutant_design_list = design_list
        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state

    # -- helpers ---------------------------------------------

    def _get_wt_residues(self, pdb_path: Path, positions: list) -> dict:
        """Extract WT amino acid identities from PDB file."""
        from Bio.PDB import PDBParser

        if pdb_path is None:
            return {}
        parser = PDBParser(QUIET=True)
        structure = parser.get_structure("enzyme", str(pdb_path))
        wt_map = {}
        for model in structure:
            for chain in model:
                for residue in chain:
                    rid = residue.get_id()[1]
                    if rid in positions:
                        resname = residue.get_resname()
                        wt_map[rid] = self.THREE_TO_ONE.get(resname, "X")
        return wt_map
