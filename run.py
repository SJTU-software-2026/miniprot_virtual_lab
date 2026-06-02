#!/usr/bin/env python3
"""
MiniProt Virtual Lab — Entry Point.

Interactive CLI that lets a human researcher collaborate with a team of
LLM agents to solve protein and enzyme bioinformatics tasks.

Usage:
    python run.py                      # Interactive mode
    python run.py --agenda "..."       # Run a single research session
    python run.py --demo               # Run the built-in demo workflow
    python run.py --providers          # List available AI providers

Environment:
    DEEPSEEK_API_KEY      — Required. Your API key.
    MINIPROT_PROVIDER     — Provider preset: deepseek, openai, sjtu, etc.
    MINIPROT_<AGENT>_API_KEY — Per-agent API key override.
    MINIPROT_<AGENT>_MODEL  — Per-agent model override.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from miniprot_virtual_lab import (
    Agent,
    run_meeting,
    load_meeting_context,
    list_saved_meetings,
    load_checkpoint,
    RunLogger,
    resolve_config,
    list_providers,
    print_config_summary,
    PRINCIPAL_INVESTIGATOR,
    SCIENTIFIC_CRITIC,
    PROTEIN_SEARCH_SPECIALIST,
    STRUCTURE_SPECIALIST,
    DOCKING_SPECIALIST,
    CHEMISTRY_SPECIALIST,
    SEQUENCE_ANALYSIS_SPECIALIST,
    DEFAULT_TEAM,
    SEARCH_TEAM,
    ENZYME_MINING_REFERENCE_WORKFLOW,
)
from miniprot_virtual_lab.utils import load_env, check_api_key, timestamp_str

# ── Run logger ─────────────────────────────────────────────────────

_run_logger: RunLogger | None = None


def _get_logger() -> RunLogger:
    global _run_logger
    if _run_logger is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_dir = Path("logs") / f"run_{ts}"
        _run_logger = RunLogger(log_dir)
    return _run_logger


# ── Session ────────────────────────────────────────────────────────

class ResearchSession:
    """Manages a multi-meeting research project."""

    def __init__(self, save_dir: Path) -> None:
        self.save_dir = save_dir
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.phase: str = "init"
        self.meeting_counter: int = 0
        self.summaries: list[str] = []

    def next_meeting_name(self, prefix: str = "discussion") -> str:
        self.meeting_counter += 1
        return f"{self.meeting_counter:02d}_{prefix}"


# ── Demo ───────────────────────────────────────────────────────────

def run_enzyme_mining_demo(session: ResearchSession) -> None:
    """Run the complete enzyme mining pipeline as a series of meetings."""
    rl = _get_logger()
    rl.log_phase("demo_start", "Enzyme mining demonstration pipeline")

    print("\n" + "=" * 60)
    print("  MiniProt Virtual Lab — Enzyme Mining Demo")
    print("=" * 60)

    # Phase 1: Team Planning
    print("\n" + "─" * 50)
    print("  PHASE 1: Research Planning (Team Meeting)")
    print("─" * 50)
    rl.log_phase("phase1", "Team planning meeting")

    planning_agenda = (
        "We need to identify enzymes or proteins that can catalyze the "
        "hydroxylation of tryptophan (converting tryptophan to 5-HTP). "
        "Known enzymes include tryptophan hydroxylase (TPH/TPH2) in humans "
        "and other organisms. Our goal is to:\n\n"
        "1. Search for known tryptophan hydroxylases across organisms\n"
        "2. Find homologous sequences that may have similar activity\n"
        "3. Get 3D structures for the most promising candidates\n"
        "4. Perform molecular docking with tryptophan as the ligand\n"
        "5. Analyze sequence similarity and evolutionary relationships\n\n"
        "Please discuss the overall approach: what order of operations, "
        "which tools to use at each step, how to select the best candidates, "
        "and any potential pitfalls to watch for."
    )

    planning_questions = (
        "What is the recommended order of operations for this project?",
        "Which specialist should handle each phase of the work?",
        "How should we filter candidates to ensure we find novel enzymes?",
        "What quality checks should be performed after each step?",
    )

    run_meeting(
        meeting_type="team",
        agenda=planning_agenda,
        agenda_questions=planning_questions,
        team_lead=PRINCIPAL_INVESTIGATOR,
        team_members=DEFAULT_TEAM,
        save_dir=session.save_dir,
        save_name=session.next_meeting_name("planning"),
        num_rounds=2,
        temperature=0.7,
        run_logger=rl,
    )

    # Phase 2: Individual Tasks
    print("\n" + "─" * 50)
    print("  PHASE 2: Task Execution (Individual Meetings)")
    print("─" * 50)
    rl.log_phase("phase2", "Individual task execution")

    # 2a: Search
    search_agenda = (
        "Search UniProt for tryptophan hydroxylase enzymes across all "
        "organisms. Your tasks:\n"
        "1. Use uniprot_search with query='tryptophan hydroxylase', "
        "   reviewed_only=True, download_formats=['fasta'], limit=20\n"
        "2. Also search for 'tph' gene name\n"
        "3. Report how many unique accessions, what organisms, "
        "   and the path to the downloaded FASTA"
    )
    run_meeting(
        meeting_type="individual",
        agenda=search_agenda,
        team_member=PROTEIN_SEARCH_SPECIALIST,
        save_dir=session.save_dir,
        save_name=session.next_meeting_name("search"),
        num_rounds=1,
        enable_tools=True,
        run_logger=rl,
    )

    # 2b: Structure
    structure_agenda = (
        "Get the 3D structure for human tryptophan hydroxylase 2 (TPH2). "
        "UniProt accession: Q8IWU9. Your tasks:\n"
        "1. Use alphafold to download the AlphaFold-predicted structure "
        "   for TPH2 (uniprot_id='Q8IWU9')\n"
        "2. Report the PDB file path and any quality notes"
    )
    run_meeting(
        meeting_type="individual",
        agenda=structure_agenda,
        team_member=STRUCTURE_SPECIALIST,
        save_dir=session.save_dir,
        save_name=session.next_meeting_name("structure"),
        num_rounds=1,
        enable_tools=True,
        run_logger=rl,
    )

    # 2c: Docking
    docking_agenda = (
        "Prepare tryptophan and set up docking for TPH2. Your tasks:\n"
        "1. Use smiles with query='tryptophan', output_sdf=true\n"
        "2. Use pocket_picker on the TPH2 receptor PDB\n"
        "3. Use autodock_vina with the receptor and ligand\n"
        "4. Report binding energies and output file locations"
    )
    run_meeting(
        meeting_type="individual",
        agenda=docking_agenda,
        team_member=DOCKING_SPECIALIST,
        save_dir=session.save_dir,
        save_name=session.next_meeting_name("docking"),
        num_rounds=1,
        enable_tools=True,
        run_logger=rl,
    )

    # Phase 3: Review
    print("\n" + "─" * 50)
    print("  PHASE 3: Review and Synthesis (Team Meeting)")
    print("─" * 50)
    rl.log_phase("phase3", "Team review meeting")

    review_agenda = (
        "We have completed the initial phases of the enzyme mining project. "
        "Please review the results and discuss:\n"
        "1. What was found in the UniProt search?\n"
        "2. Was the TPH2 structure successfully retrieved?\n"
        "3. What did the docking reveal?\n"
        "4. What are the next steps?\n\n"
        "The PI should synthesize findings and produce a final research summary."
    )

    run_meeting(
        meeting_type="team",
        agenda=review_agenda,
        team_lead=PRINCIPAL_INVESTIGATOR,
        team_members=DEFAULT_TEAM,
        save_dir=session.save_dir,
        save_name=session.next_meeting_name("review"),
        num_rounds=2,
        temperature=0.7,
        run_logger=rl,
    )

    summary = rl.finalize()
    print("\n" + "=" * 60)
    print("  Demo Complete!")
    print(f"  Logs saved to: {summary['log_dir']}")
    print(f"  Meetings saved to: {session.save_dir}")
    print("=" * 60)


# ── Interactive mode ───────────────────────────────────────────────

def interactive_mode(session: ResearchSession) -> None:
    """Run an interactive research session."""
    rl = _get_logger()

    print("\n" + "=" * 60)
    print("  MiniProt Virtual Lab — Interactive Mode")
    print("=" * 60)
    print()
    print("  Commands:")
    print("    /team <agenda>      Start a team meeting")
    print("    /task <name> <task> Assign to: search|structure|docking|chemistry|sequence")
    print("    /history            List saved meetings that can be loaded as context")
    print("    /load <name>        Load previous meeting(s) as context for next meeting")
    print("    /context            Show currently loaded meeting context")
    print("    /clearctx           Clear loaded meeting context")
    print("    /agents             List available agents")
    print("    /providers          List available AI providers")
    print("    /workflow           Show reference enzyme mining workflow")
    print("    /demo               Run the built-in demo")
    print("    /help               Show this help")
    print("    /quit               Exit")
    print()

    agent_map = {
        "pi": PRINCIPAL_INVESTIGATOR,
        "critic": SCIENTIFIC_CRITIC,
        "search": PROTEIN_SEARCH_SPECIALIST,
        "structure": STRUCTURE_SPECIALIST,
        "chemistry": CHEMISTRY_SPECIALIST,
        "docking": DOCKING_SPECIALIST,
        "sequence": SEQUENCE_ANALYSIS_SPECIALIST,
    }

    # Loaded meeting context (persists across commands)
    loaded_summaries: tuple = ()
    loaded_contexts: tuple = ()
    loaded_names: list = []

    while True:
        try:
            cmd = input("Virtual Lab> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break

        if not cmd:
            continue

        if cmd.lower() in ("/quit", "/exit", "/q"):
            rl.finalize()
            print(f"Exiting. Logs: {rl.log_dir}, Meetings: {session.save_dir}")
            break

        if cmd.lower() == "/help":
            print("Commands:")
            print("  /team <agenda>         Start a team meeting with all specialists")
            print("  /task <name> <task>    Assign to: search|structure|docking|chemistry|sequence")
            print("  /agents                 List agents and their tool access")
            print("  /providers              List AI provider presets")
            print("  /workflow               Show reference enzyme mining workflow")
            print("  /demo                   Run the demo pipeline")
            print("  /quit                   Exit")
            continue

        if cmd.lower() == "/agents":
            print("\nAvailable Agents:")
            all_agents = [PRINCIPAL_INVESTIGATOR, PROTEIN_SEARCH_SPECIALIST,
                          STRUCTURE_SPECIALIST, CHEMISTRY_SPECIALIST,
                          DOCKING_SPECIALIST, SEQUENCE_ANALYSIS_SPECIALIST,
                          SCIENTIFIC_CRITIC]
            for agent in all_agents:
                cfg = resolve_config(agent)
                tools = ", ".join(agent.tool_categories) if agent.tool_categories else "(coordination only)"
                print(f"  [{agent.title}]")
                print(f"     Model:  {cfg.model}")
                print(f"     API:    {cfg.base_url}")
                print(f"     Tools:  {tools}")
                print()
            continue

        if cmd.lower() == "/providers":
            print()
            print(list_providers())
            print(f"\n  Current provider: {os.getenv('MINIPROT_PROVIDER', 'deepseek')}")
            print(f"  Set via: export MINIPROT_PROVIDER=<name>")
            print()
            continue

        if cmd.lower() == "/workflow":
            print("\nReference Enzyme Mining Workflow:")
            for step in ENZYME_MINING_REFERENCE_WORKFLOW:
                print(f"  [{step['phase']}] {step['description']}")
                print(f"       Tools: {', '.join(step['tools'])}")
            print()
            continue

        if cmd.lower() == "/demo":
            run_enzyme_mining_demo(session)
            continue

        if cmd.lower().startswith("/team "):
            agenda = cmd[6:].strip()
            if not agenda:
                print("Usage: /team <agenda>")
                continue
            print(f"\nStarting team meeting...")
            print(f"Team: {', '.join(a.title for a in DEFAULT_TEAM)}")
            if loaded_names:
                print(f"Context: {', '.join(loaded_names)}")
            print()

            summary = run_meeting(
                meeting_type="team",
                agenda=agenda,
                team_lead=PRINCIPAL_INVESTIGATOR,
                team_members=DEFAULT_TEAM,
                save_dir=session.save_dir,
                save_name=session.next_meeting_name("team"),
                num_rounds=3,
                temperature=0.7,
                summaries=loaded_summaries,
                contexts=loaded_contexts,
                return_summary=True,
                run_logger=rl,
            )
            if summary:
                session.summaries.append(summary)
            continue

        if cmd.lower().startswith("/task "):
            parts = cmd[6:].strip().split(maxsplit=1)
            if len(parts) < 2:
                print("Usage: /task <name> <task>")
                print("  Names: search, structure, docking, chemistry, sequence")
                continue

            agent_key, task = parts[0].lower(), parts[1]
            agent = agent_map.get(agent_key)
            if agent is None:
                print(f"Unknown specialist '{agent_key}'. Options: {', '.join(agent_map.keys())}")
                continue

            print(f"\nAssigning to {agent.title}...")
            if loaded_names:
                print(f"Context: {', '.join(loaded_names)}")
            print()
            run_meeting(
                meeting_type="individual",
                agenda=task,
                team_member=agent,
                save_dir=session.save_dir,
                save_name=session.next_meeting_name(f"task_{agent_key}"),
                num_rounds=2,
                enable_tools=True,
                summaries=loaded_summaries,
                contexts=loaded_contexts,
                run_logger=rl,
            )
            continue

        if cmd.lower() == "/history":
            meetings = list_saved_meetings(str(session.save_dir))
            if not meetings:
                print("No saved meetings found in:", session.save_dir)
            else:
                print(f"\nSaved meetings ({len(meetings)}):")
                for m in meetings:
                    agents_str = ", ".join(m["agents"][:4])
                    if len(m["agents"]) > 4:
                        agents_str += f" +{len(m['agents']) - 4}"
                    print(f"  {m['name']:<30} {m['turns']:>3} turns  "
                          f"{m['size_kb']:>6} KB  [{agents_str}]")
                print("\nUse /load <name> to load one as context.")
            continue

        if cmd.lower().startswith("/load "):
            name = cmd[6:].strip()
            path = session.save_dir / f"{name}.json"
            if not path.exists():
                # Try exact path
                path = Path(name)
                if not path.exists():
                    print(f"Meeting not found: {name}")
                    print(f"  Looked in: {session.save_dir / (name + '.json')}")
                    print(f"  Use /history to see available meetings.")
                    continue

            ctx = load_meeting_context(str(path))
            loaded_summaries = loaded_summaries + ctx["summaries"]
            loaded_contexts = loaded_contexts + ctx["contexts"]
            loaded_names.append(path.stem)

            print(f"Loaded: {path.stem}")
            print(f"  Agents: {', '.join(ctx['agent_contributions'].keys())}")
            print(f"  Context size: {sum(len(c) for c in ctx['contexts'])} chars")
            print(f"  Total loaded meetings: {len(loaded_names)}")
            continue

        if cmd.lower() == "/context":
            if not loaded_names:
                print("No meeting context loaded. Use /load <name> first.")
            else:
                print(f"Loaded {len(loaded_names)} meeting(s):")
                for n, s in zip(loaded_names, loaded_summaries):
                    print(f"  [{n}] summary: {s[:200]}...")
            continue

        if cmd.lower() == "/clearctx":
            loaded_summaries = ()
            loaded_contexts = ()
            loaded_names = []
            print("Meeting context cleared.")
            continue

        print("Use /team, /task, /history, /load, /context, /agents, "
              "/providers, /demo, /help, or /quit")


# ── Main ───────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiniProt Virtual Lab — AI-Human Collaboration for Protein Research",
    )
    parser.add_argument("--agenda", "-a", type=str, default=None,
                        help="Run a single team meeting with the given agenda.")
    parser.add_argument("--demo", action="store_true",
                        help="Run the built-in enzyme mining demo pipeline.")
    parser.add_argument("--providers", action="store_true",
                        help="List available AI provider presets and exit.")
    parser.add_argument("--provider", "-p", type=str, default=None,
                        help="Provider preset to use (deepseek, openai, sjtu, etc.).")
    parser.add_argument("--save-dir", "-s", type=str, default=None,
                        help="Directory to save meeting files (default: ./meetings).")
    parser.add_argument("--log-dir", "-l", type=str, default=None,
                        help="Directory for structured logs (default: ./logs/run_<timestamp>).")
    parser.add_argument("--context", "-c", type=str, default=None, nargs="*",
                        help="Load saved meeting JSON file(s) as context for the meeting.")
    parser.add_argument("--resume", "-r", type=str, default=None,
                        help="Resume an interrupted meeting from a checkpoint file.")
    parser.add_argument("--model", "-m", type=str, default=None,
                        help="Global model name override.")
    args = parser.parse_args()

    env = load_env()
    if args.model:
        os.environ["DEEPSEEK_MODEL"] = args.model
    if args.provider:
        os.environ["MINIPROT_PROVIDER"] = args.provider

    # Just list providers?
    if args.providers:
        print(list_providers())
        return 0

    check_api_key()

    # Print config summary
    all_agents = [PRINCIPAL_INVESTIGATOR, PROTEIN_SEARCH_SPECIALIST,
                  STRUCTURE_SPECIALIST, CHEMISTRY_SPECIALIST,
                  DOCKING_SPECIALIST, SEQUENCE_ANALYSIS_SPECIALIST,
                  SCIENTIFIC_CRITIC]
    print_config_summary(all_agents)

    # Override log dir if specified
    global _run_logger
    if args.log_dir:
        _run_logger = RunLogger(Path(args.log_dir))

    save_dir = Path(args.save_dir) if args.save_dir else Path("meetings")
    session = ResearchSession(save_dir)

    print(f"\nSaving meetings to: {save_dir.resolve()}")
    print(f"Logging to:         {_get_logger().log_dir}")

    if args.resume:
        rl = _get_logger()
        cp = load_checkpoint(args.resume)
        if cp is None:
            print(f"ERROR: Cannot load checkpoint: {args.resume}")
            return 1
        print(f"Resuming meeting from checkpoint: {args.resume}")
        print(f"  Meeting type: {cp['meeting_type']}")
        print(f"  Discussion turns so far: {len(cp['discussion'])}")
        print()

        summary_text = "\n".join(
            f"{t['agent']}: {t['message'][:200]}" for t in cp['discussion'][-6:]
        )
        ctx_text = (
            f"[Previous progress — {len(cp['discussion'])} turns completed "
            f"before network interruption]\n{summary_text}"
        )

        if cp['meeting_type'] == 'team':
            run_meeting(
                meeting_type='team',
                agenda=f"CONTINUING INTERRUPTED MEETING:\n\n{cp['agenda']}",
                team_lead=PRINCIPAL_INVESTIGATOR,
                team_members=DEFAULT_TEAM,
                save_dir=session.save_dir,
                save_name=session.next_meeting_name("resumed"),
                num_rounds=cp.get('num_rounds', 3),
                summaries=(summary_text,),
                contexts=(ctx_text,),
                run_logger=rl,
            )
        else:
            run_meeting(
                meeting_type='individual',
                agenda=f"CONTINUING INTERRUPTED TASK:\n\n{cp['agenda']}",
                team_member=PROTEIN_SEARCH_SPECIALIST,
                save_dir=session.save_dir,
                save_name=session.next_meeting_name("resumed"),
                num_rounds=cp.get('num_rounds', 1),
                summaries=(summary_text,),
                contexts=(ctx_text,),
                enable_tools=True,
                run_logger=rl,
            )
        rl.finalize()
        return 0

    if args.demo:
        run_enzyme_mining_demo(session)
        return 0

    if args.agenda:
        rl = _get_logger()

        # Load context if specified
        ctx_summaries = ()
        ctx_contexts = ()
        if args.context:
            for p in args.context:
                ctx = load_meeting_context(p)
                ctx_summaries = ctx_summaries + ctx["summaries"]
                ctx_contexts = ctx_contexts + ctx["contexts"]
                print(f"Loaded context: {ctx['meeting_names']}")

        print(f"\nRunning team meeting for: {args.agenda[:120]}...")
        run_meeting(
            meeting_type="team",
            agenda=args.agenda,
            team_lead=PRINCIPAL_INVESTIGATOR,
            team_members=DEFAULT_TEAM,
            save_dir=session.save_dir,
            save_name=session.next_meeting_name("cli"),
            num_rounds=3,
            temperature=0.7,
            summaries=ctx_summaries,
            contexts=ctx_contexts,
            run_logger=rl,
        )
        rl.finalize()
        return 0

    # Interactive mode
    interactive_mode(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
