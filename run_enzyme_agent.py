#!/usr/bin/env python
"""
Enzyme Redesign Agent — Intelligent multi-round iterative design.

Unlike the pipeline CLI (which runs once and stops), the agent:
  1. Runs Round 1: broad scan of all target positions
  2. Analyzes score distribution
  3. Decides: refine top positions? expand search? report?
  4. Runs Round 2 (and possibly 3) with adjusted parameters
  5. Generates a comprehensive report with recommendations

Usage:
  python run_enzyme_agent.py -e 6TB1.pdb -l substrate.sdf -w output/ \\
      --large-pocket 54 57 58 151 230 261 417 --small-pocket 86

  # Single-round mode (no iteration)
  python run_enzyme_agent.py -e enzyme.pdb -l ligand.sdf -w output/ --no-iterate
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src" / "miniprot_virtual_lab"))

from workflows.enzyme_redesign.agent import (
    AgentConfig,
    EnzymeRedesignAgent,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enzyme Redesign Agent — Intelligent iterative computational design",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Paper mode with auto-iteration
  python run_enzyme_agent.py -e 6TB1.pdb -l substrate.sdf -w output/ \\
      --large-pocket 54 57 58 151 230 261 417 --small-pocket 86

  # Single round (no iteration, same as pipeline)
  python run_enzyme_agent.py -e enzyme.pdb -l ligand.sdf -w output/ --no-iterate
        """,
    )

    parser.add_argument("-e", "--enzyme", required=True, help="Enzyme PDB file")
    parser.add_argument("-l", "--ligand", required=True, help="Ligand SDF file")
    parser.add_argument("-w", "--work-dir", required=True, help="Output directory")
    parser.add_argument("--plp", default="PLP", help="PLP residue name (default: PLP)")

    # Pocket
    parser.add_argument("--large-pocket", type=int, nargs="*", help="Large pocket residues")
    parser.add_argument("--small-pocket", type=int, nargs="*", help="Small pocket residues")
    parser.add_argument("--pocket-cutoff", type=float, default=8.0, help="Pocket cutoff (A)")

    # Strategy
    parser.add_argument("--strategy", choices=["smart", "full"], default="smart")
    parser.add_argument("--target-positions", type=int, nargs="*", help="Target residue IDs")

    # Agent behavior
    parser.add_argument("--max-rounds", type=int, default=3, help="Max design rounds (default: 3)")
    parser.add_argument("--no-iterate", action="store_true", help="Single round only")
    parser.add_argument("--round1-top-n", type=int, default=20)
    parser.add_argument("--round1-exhaustiveness", type=int, default=4)
    parser.add_argument("--round2-exhaustiveness", type=int, default=8)

    return parser.parse_args()


def main():
    args = parse_args()

    enzyme_pdb = Path(args.enzyme)
    ligand_sdf = Path(args.ligand)
    work_dir = Path(args.work_dir)

    for f, name in [(enzyme_pdb, "enzyme"), (ligand_sdf, "ligand")]:
        if not f.exists():
            print(f"[ERROR] {name} file not found: {f}")
            sys.exit(1)

    config = AgentConfig(
        max_rounds=args.max_rounds,
        auto_iterate=not args.no_iterate,
        round1_strategy=args.strategy,
        round1_positions=args.target_positions or [],
        round1_top_n=args.round1_top_n,
        round1_exhaustiveness=args.round1_exhaustiveness,
        round2_exhaustiveness=args.round2_exhaustiveness,
        paper_mode=bool(args.large_pocket),
        large_pocket=args.large_pocket or [],
        small_pocket=args.small_pocket or [],
        plp_resname=args.plp,
    )

    agent = EnzymeRedesignAgent(
        enzyme_pdb=enzyme_pdb,
        ligand_sdf=ligand_sdf,
        work_dir=work_dir,
        config=config,
    )

    report = agent.run()

    print(f"\nAgent completed {report.rounds_completed} round(s).")
    print(f"Top recommendation: {report.top_recommendations[0] if report.top_recommendations else 'none'}")


if __name__ == "__main__":
    main()
