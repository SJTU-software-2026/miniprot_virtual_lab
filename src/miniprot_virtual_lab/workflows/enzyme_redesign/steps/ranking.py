"""
Step 7: Ranking & Summary Output
  - Combine scores from all previous steps
  - Rank mutants by Vina binding affinity
  - Generate final CSV report with all relevant metrics
  - Produce a human-readable summary

Paper reference: Meng et al. (2021) selected top 10-20 variants per library
by Rosetta interface energy and validated them experimentally.
"""
import csv
from pathlib import Path

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl


class RankingStep:
    """
    Final ranking step. Produces a comprehensive CSV with:
      - rank, mutant name, Vina score
      - original residue positions and pocket classification
      - recommended variants for experimental testing
    """

    name = "ranking"

    def __init__(self, top_n: int = 20):
        self.top_n = top_n

    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        print(f"[STEP 7/7] {self.name} -- Final Report")
        print(f"{'='*60}")

        output_dir = state.work_dir / "07_results"
        output_dir.mkdir(parents=True, exist_ok=True)

        vina_scores = state.vina_scores
        if not vina_scores:
            print("  WARNING: No Vina scores available.")
            print("  Ranking by heuristic: position in mutant list.")
            # Fallback: assign placeholder scores
            vina_scores = {
                m: 0.0 for m in state.generated_mutants[:self.top_n]
            }

        # -- sort by Vina score (lower = better binding) -----
        ranked = sorted(vina_scores.items(), key=lambda x: x[1])

        # -- enrich with design metadata ---------------------
        design_map = {}
        for d in state.mutant_design_list:
            design_map[d["mutant"]] = d

        results = []
        for rank, (mutant, score) in enumerate(ranked[:self.top_n], start=1):
            info = design_map.get(mutant, {})
            results.append({
                "rank": rank,
                "mutant": mutant,
                "vina_score": round(score, 3),
                "position": info.get("position", ""),
                "wt_aa": info.get("wt", ""),
                "mut_aa": info.get("mut", ""),
                "design_group": info.get("group", ""),
            })

        state.ranked_results = results

        # -- write CSV ---------------------------------------
        csv_path = output_dir / "ranked_mutants_final.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "rank", "mutant", "vina_score_kcal_per_mol",
                "position", "wt_aa", "mut_aa", "design_group",
            ])
            for r in results:
                writer.writerow([
                    r["rank"], r["mutant"], r["vina_score"],
                    r["position"], r["wt_aa"], r["mut_aa"],
                    r["design_group"],
                ])

        state.summary_csv = csv_path

        # -- write human-readable summary --------------------
        summary_path = output_dir / "summary.txt"
        with open(summary_path, "w") as f:
            f.write("=" * 60 + "\n")
            f.write("Enzyme Redesign Pipeline -- Results Summary\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Input structure: {state.enzyme_pdb}\n")
            f.write(f"Input ligand:    {state.ligand_sdf}\n")
            f.write(f"Pocket residues: {state.pocket_residues}\n")
            f.write(f"Total mutants designed: {len(state.generated_mutants)}\n")
            f.write(f"Total mutants docked:   {len(state.vina_scores)}\n\n")
            f.write("-" * 60 + "\n")
            f.write("Top Recommended Variants for Experimental Testing\n")
            f.write("-" * 60 + "\n\n")
            f.write(f"{'#':<4} {'Mutant':<12} {'Score':<10} {'Notes'}\n")
            f.write("-" * 50 + "\n")
            for r in results[:10]:
                notes = self._suggest_notes(r)
                f.write(f"{r['rank']:<4} {r['mutant']:<12} "
                        f"{r['vina_score']:<10.2f} {notes}\n")
            f.write("\nFull results: " + str(csv_path) + "\n")

        # -- print to console --------------------------------
        print(f"\n  {'-'*50}")
        print(f"  TOP {min(10, len(results))} RECOMMENDED VARIANTS")
        print(f"  {'-'*50}")
        print(f"  {'#':<4} {'Mutant':<12} {'Score (kcal/mol)':<18} {'Suggestion'}")
        print(f"  {'-'*50}")
        for r in results[:10]:
            notes = self._suggest_notes(r)
            print(f"  {r['rank']:<4} {r['mutant']:<12} "
                  f"{r['vina_score']:<18.2f} {notes}")
        print(f"  {'-'*50}")
        print(f"\n  Full report saved to: {csv_path}")

        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state

    @staticmethod
    def _suggest_notes(result: dict) -> str:
        """Generate a brief note about the mutation type."""
        group = result.get("design_group", "")
        pos = result.get("position", "")
        if group == "large_insertion":
            return f"steric expansion at position {pos}"
        elif group == "small":
            return f"reduce steric bulk at position {pos}"
        elif group == "hydrophobic":
            return f"hydrophobic packing at position {pos}"
        elif group == "aromatic":
            return f"pi-stacking at position {pos}"
        elif group == "positive" or group == "negative":
            return f"charge modification at position {pos}"
        return f"general mutation at position {pos}"
