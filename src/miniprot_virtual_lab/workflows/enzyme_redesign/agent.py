"""
Enzyme Redesign Agent

Upgrades the linear 7-step pipeline into an intelligent agent that can:
  1. Analyze intermediate results and make decisions
  2. Run multiple design rounds with adaptive parameter tuning
  3. Self-diagnose problems (stuck scores, poor convergence) and fix them
  4. Generate human-readable analysis reports

Architecture:
  Round 1: Broad scan → score distribution analysis
    ↓
  Decision Node: refine? expand? report?
    ↓
  Round 2 (if needed): Focused refinement on top positions
    ↓
  Report: Final ranking + experimental recommendations + comparison to literature

Usage:
  from workflows.enzyme_redesign.agent import EnzymeRedesignAgent

  agent = EnzymeRedesignAgent(
      enzyme_pdb=Path("6TB1.pdb"),
      ligand_sdf=Path("substrate.sdf"),
      work_dir=Path("output"),
  )
  report = agent.run()
"""
from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .pipeline import EnzymeRedesignPipeline
from .state import WorkflowState


# ── Agent-specific data structures ──────────────────────


@dataclass
class RoundResult:
    """Results from one design round."""
    round_number: int
    positions: List[int]
    num_mutants: int
    scores: Dict[str, float]
    top_score: float
    bottom_score: float
    score_spread: float  # max - min
    score_stdev: float
    top_mutants: List[Tuple[str, float]]
    decision: str = ""  # "expand", "refine", "report"


@dataclass
class AgentConfig:
    """Configuration for the enzyme redesign agent."""
    # General
    max_rounds: int = 3
    auto_iterate: bool = True

    # Score analysis thresholds
    min_score_spread: float = 0.5   # kcal/mol — if spread < this, scores are stuck
    min_meaningful_score: float = -5.0  # if all scores > this, binding is too weak

    # Round 1: broad scan
    round1_strategy: str = "smart"
    round1_positions: List[int] = field(default_factory=list)
    round1_top_n: int = 20
    round1_exhaustiveness: int = 4
    round1_box_padding: float = 10.0

    # Round 2: targeted refinement
    round2_exhaustiveness: int = 8
    round2_box_padding: float = 12.0
    round2_top_n: int = 10
    round2_double_mutants: bool = False

    # Paper-specific
    paper_mode: bool = False
    large_pocket: List[int] = field(default_factory=list)
    small_pocket: List[int] = field(default_factory=list)
    plp_resname: str = "PLP"


@dataclass
class AgentReport:
    """Final report produced by the agent."""
    rounds_completed: int
    total_mutants_tested: int
    top_recommendations: List[Dict[str, Any]]
    score_analysis: str
    suggested_next_steps: str
    raw_rounds: List[RoundResult] = field(default_factory=list)


# ── Agent ───────────────────────────────────────────────


class EnzymeRedesignAgent:
    """
    Intelligent agent for computational enzyme redesign.

    The agent wraps the 7-step pipeline and adds:
      - Multi-round iterative design
      - Score distribution analysis
      - Automatic parameter adjustment
      - Natural language reporting
    """

    def __init__(
        self,
        enzyme_pdb: Path,
        ligand_sdf: Path,
        work_dir: Path,
        config: AgentConfig = None,
    ):
        self.enzyme_pdb = enzyme_pdb
        self.ligand_sdf = ligand_sdf
        self.work_dir = work_dir
        self.config = config or AgentConfig()
        self.rounds: List[RoundResult] = []

    # ── public API ───────────────────────────────────────

    def run(self) -> AgentReport:
        """Run the full agent workflow: round(s) + analysis + report."""
        print("\n" + "=" * 70)
        print("  ENZYME REDESIGN AGENT")
        print("  Multi-round iterative design with intelligent decision-making")
        print("=" * 70)

        # ── Round 1: Broad scan ──────────────────────────
        r1 = self._run_round(
            round_number=1,
            positions=self.config.round1_positions,
            strategy=self.config.round1_strategy,
            top_n=self.config.round1_top_n,
            exhaustiveness=self.config.round1_exhaustiveness,
            box_padding=self.config.round1_box_padding,
        )
        self.rounds.append(r1)

        # ── Decision: what to do next? ───────────────────
        decision = self._analyze_and_decide(r1)
        r1.decision = decision

        if not self.config.auto_iterate:
            return self._build_report(self.rounds)

        # ── Round 2: Targeted refinement ────────────────
        if decision in ("refine", "expand") and self.config.max_rounds >= 2:
            r2_positions = self._select_refinement_positions(r1)

            r2 = self._run_round(
                round_number=2,
                positions=r2_positions,
                strategy="smart",
                top_n=self.config.round2_top_n,
                exhaustiveness=self.config.round2_exhaustiveness,
                box_padding=self.config.round2_box_padding,
            )
            self.rounds.append(r2)

            decision2 = self._analyze_and_decide(r2, previous=r1)
            r2.decision = decision2

            # ── Round 3: Deep dive ───────────────────────
            if decision2 == "refine" and self.config.max_rounds >= 3:
                r3_positions = self._select_refinement_positions(r2)
                r3 = self._run_round(
                    round_number=3,
                    positions=r3_positions,
                    strategy="smart",
                    top_n=self.config.round2_top_n,
                    exhaustiveness=16,  # maximum precision
                    box_padding=self.config.round2_box_padding,
                )
                self.rounds.append(r3)

        return self._build_report(self.rounds)

    # ── round execution ──────────────────────────────────

    def _run_round(
        self,
        round_number: int,
        positions: List[int],
        strategy: str,
        top_n: int,
        exhaustiveness: int,
        box_padding: float,
    ) -> RoundResult:
        """Execute one full 7-step pipeline round."""
        print(f"\n{'─' * 60}")
        print(f"  ROUND {round_number}")
        print(f"  Positions: {positions or 'all pocket'}, strategy={strategy}")
        print(f"  top_n={top_n}, exhaustiveness={exhaustiveness}")
        print(f"{'─' * 60}")

        round_dir = self.work_dir / f"round{round_number}"

        pipeline = EnzymeRedesignPipeline(
            enzyme_pdb=self.enzyme_pdb,
            ligand_sdf=self.ligand_sdf,
            work_dir=round_dir,
            plp_resname=self.config.plp_resname,
            large_pocket_residues=self.config.large_pocket or None,
            small_pocket_residues=self.config.small_pocket or None,
            mutation_strategy=strategy,
            target_positions=[p for p in positions if p > 0],
            top_n=top_n,
            exhaustiveness=exhaustiveness,
            box_padding=box_padding,
        )

        state = pipeline.run()

        # Extract scores
        scores = state.vina_scores
        if not scores:
            print("  WARNING: No docking scores — using fallback scoring")
            scores = {
                m: 0.0 for m in state.generated_mutants[:top_n]
            }

        score_values = list(scores.values())
        ranked = sorted(scores.items(), key=lambda x: x[1])

        result = RoundResult(
            round_number=round_number,
            positions=positions or state.pocket_residues,
            num_mutants=len(state.generated_mutants),
            scores=scores,
            top_score=min(score_values) if score_values else 0,
            bottom_score=max(score_values) if score_values else 0,
            score_spread=(
                max(score_values) - min(score_values)
                if len(score_values) > 1 else 0
            ),
            score_stdev=(
                statistics.stdev(score_values)
                if len(score_values) > 1 else 0
            ),
            top_mutants=ranked[:10],
        )

        # Save round results
        self._save_round_result(round_dir, result, ranked)

        return result

    # ── decision engine ──────────────────────────────────

    def _analyze_and_decide(
        self,
        current: RoundResult,
        previous: RoundResult = None,
    ) -> str:
        """
        Analyze scores and decide the next action.

        Returns one of:
          - "report"  — results are good, stop and report
          - "refine"  — focus on top positions with higher precision
          - "expand"  — broaden the search (more positions, bigger box)
        """
        print(f"\n  [DECISION] Analyzing Round {current.round_number} scores...")
        print(f"    Score range: {current.top_score:.2f} ~ {current.bottom_score:.2f}")
        print(f"    Score spread: {current.score_spread:.2f}")
        print(f"    Score stdev:  {current.score_stdev:.3f}")

        # ── heuristic 1: scores too clustered → bad signal ──
        if current.score_spread < self.config.min_score_spread:
            print(f"    -> Scores too clustered (< {self.config.min_score_spread} kcal/mol spread)")
            if current.round_number == 1:
                print(f"    -> EXPAND: increase search space and precision")
                return "expand"
            else:
                print(f"    -> REFINE failed, stopping")
                return "report"

        # ── heuristic 2: all scores weak → binding unlikely ──
        if current.top_score > self.config.min_meaningful_score:
            print(f"    -> All scores > {self.config.min_meaningful_score} (weak binding)")
            if current.round_number == 1:
                print(f"    -> EXPAND: try larger box or alternative positions")
                return "expand"
            else:
                return "report"

        # ── heuristic 3: improvement over previous round? ──
        if previous and current.top_score >= previous.top_score:
            print("    -> No improvement over previous round, stopping")
            return "report"

        if previous and current.top_score < previous.top_score:
            improvement = previous.top_score - current.top_score
            print(f"    -> Improved by {improvement:.2f} kcal/mol over Round {previous.round_number}")
            if improvement > 0.5:
                print("    -> Significant improvement! Deep refinement warranted.")
                return "refine"

        # ── default: good results, can stop ──
        if current.score_spread >= 1.0:
            print("    -> Good score spread, results are meaningful")
            return "report"

        return "refine"

    # ── position selection ───────────────────────────────

    def _select_refinement_positions(self, result: RoundResult) -> List[int]:
        """
        From round results, pick the best positions to focus on in the next round.

        Strategy:
          - Extract position number from mutant name (e.g. W58G → 58)
          - Count frequency in top 10
          - Return positions that appear most often
        """
        position_counts: Dict[int, int] = {}
        for mutant, score in result.top_mutants:
            # Parse position from "W58G" format
            pos_str = mutant[1:-1]  # strip first and last char (AA codes)
            try:
                pos = int(pos_str)
                position_counts[pos] = position_counts.get(pos, 0) + 1
            except ValueError:
                continue

        # Sort by frequency, take top positions
        sorted_pos = sorted(position_counts.items(), key=lambda x: -x[1])
        top_positions = [p for p, _ in sorted_pos[:5]]
        print(f"\n  [REFINE] Focusing on positions: {top_positions}")
        return top_positions

    # ── reporting ────────────────────────────────────────

    def _build_report(self, rounds: List[RoundResult]) -> AgentReport:
        """Generate the final agent report."""
        all_scores = {}
        for r in rounds:
            all_scores.update(r.scores)

        ranked_all = sorted(all_scores.items(), key=lambda x: x[1])

        recommendations = []
        for rank, (mutant, score) in enumerate(ranked_all[:10], start=1):
            recommendations.append({
                "rank": rank,
                "mutant": mutant,
                "score": round(score, 3),
            })

        # Score analysis
        score_values = list(all_scores.values())
        analysis = (
            f"Tested {len(all_scores)} variants across {len(rounds)} rounds. "
            f"Score range: {min(score_values):.2f} to {max(score_values):.2f} kcal/mol. "
            f"Mean: {statistics.mean(score_values):.3f}, "
            f"StDev: {statistics.stdev(score_values):.3f}."
        )

        # Suggestions
        if max(score_values) - min(score_values) < 0.5:
            suggestion = (
                "Score spread is narrow — consider: "
                "(1) using external aldimine intermediate instead of free ligand, "
                "(2) running MD for better scoring, "
                "(3) testing double or triple mutants."
            )
        elif ranked_all and ranked_all[0][1] < -5.0:
            suggestion = (
                f"Top candidate {ranked_all[0][0]} shows strong predicted binding "
                f"({ranked_all[0][1]:.2f} kcal/mol). Recommended for experimental validation. "
                "Consider testing at 0.5-5 mM substrate, 56°C, with 1 M isopropylamine."
            )
        else:
            suggestion = (
                f"Top candidates show moderate binding. Proceed with experimental "
                f"testing of top 5 variants. Use chiral HPLC to measure ee."
            )

        report = AgentReport(
            rounds_completed=len(rounds),
            total_mutants_tested=len(all_scores),
            top_recommendations=recommendations,
            score_analysis=analysis,
            suggested_next_steps=suggestion,
            raw_rounds=rounds,
        )

        self._print_report(report)
        self._save_report(report)

        return report

    def _print_report(self, report: AgentReport):
        """Print a formatted report to console."""
        print("\n")
        print("=" * 70)
        print("  AGENT REPORT — Enzyme Redesign Results")
        print("=" * 70)
        print(f"  Rounds completed:     {report.rounds_completed}")
        print(f"  Total variants tested:{report.total_mutants_tested}")
        print(f"  Score analysis:       {report.score_analysis}")
        print(f"\n  TOP RECOMMENDATIONS:")
        print(f"  {'#':<4} {'Mutant':<12} {'Score (kcal/mol)'}")
        print(f"  {'-'*30}")
        for rec in report.top_recommendations[:5]:
            print(f"  {rec['rank']:<4} {rec['mutant']:<12} {rec['score']:.2f}")
        print(f"\n  NEXT STEPS: {report.suggested_next_steps}")
        print("=" * 70)

    def _save_report(self, report: AgentReport):
        """Save report as JSON."""
        report_path = self.work_dir / "agent_report.json"
        data = {
            "rounds_completed": report.rounds_completed,
            "total_mutants_tested": report.total_mutants_tested,
            "top_recommendations": report.top_recommendations,
            "score_analysis": report.score_analysis,
            "suggested_next_steps": report.suggested_next_steps,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n  Report saved: {report_path}")

    # ── helpers ──────────────────────────────────────────

    @staticmethod
    def _save_round_result(
        round_dir: Path,
        result: RoundResult,
        ranked: List[Tuple[str, float]],
    ):
        """Save per-round results as CSV."""
        csv_path = round_dir / "07_results" / "ranked_mutants_final.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["rank", "mutant", "score", "round"])
            for idx, (mutant, score) in enumerate(ranked, start=1):
                writer.writerow([idx, mutant, f"{score:.3f}", result.round_number])
