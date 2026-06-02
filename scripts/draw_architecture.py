#!/usr/bin/env python3
"""
Architecture diagram generator for MiniProt Virtual Lab.

Generates:
  - architecture.png  — System architecture overview
  - meeting_flow.png  — Meeting flow (team + individual)
  - agent_roles.png   — Agent roles and tool assignments

Requires: matplotlib (pip install matplotlib)
Usage: python scripts/draw_architecture.py [--output-dir ./figures]
"""

import argparse
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
except ImportError:
    print("matplotlib is required: pip install matplotlib")
    sys.exit(1)


# ── Color palette ──────────────────────────────────────────────────

C_BG = "#FAFAFA"
C_BOX = "#FFFFFF"
C_BORDER = "#CCCCCC"
C_ACCENT = "#2563EB"       # Blue - PI/coordinator
C_TOOLS = "#059669"        # Green - tool execution
C_SPECIALIST = "#7C3AED"   # Purple - specialists
C_CRITIC = "#DC2626"       # Red - critic
C_USER = "#F59E0B"         # Amber - human user
C_MEETING = "#0891B2"      # Cyan - meetings
C_LOG = "#6B7280"          # Gray - logging
C_FLOW = "#374151"         # Dark gray - flow arrows


def _draw_box(ax, x, y, w, h, text, color=C_BORDER, fontsize=9, fontweight="normal",
              text_color="black", linewidth=1.5, fill=True):
    """Draw a rounded box with centered text."""
    if fill:
        rect = FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.08",
            facecolor=C_BOX, edgecolor=color, linewidth=linewidth,
        )
    else:
        rect = FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.08",
            facecolor="none", edgecolor=color, linewidth=linewidth,
        )
    ax.add_patch(rect)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fontsize, fontweight=fontweight, color=text_color,
            wrap=True)


def _draw_arrow(ax, x1, y1, x2, y2, color=C_FLOW, lw=1.2):
    """Draw an arrow from (x1,y1) to (x2,y2)."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw))


def _draw_section_label(ax, x, y, text, color=C_FLOW):
    """Draw a section label."""
    ax.text(x, y, text, fontsize=11, fontweight="bold", color=color, ha="left", va="center")


# ── Architecture diagram ───────────────────────────────────────────

def draw_architecture(output_dir: Path) -> None:
    """Main system architecture diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 10))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_facecolor(C_BG)

    # Title
    ax.text(8, 9.4, "MiniProt Virtual Lab — System Architecture",
            fontsize=16, fontweight="bold", ha="center", color=C_FLOW)

    # ── Layer 1: Human User ──────────────────────────────────
    _draw_section_label(ax, 0.5, 8.5, "Human Researcher", C_USER)
    _draw_box(ax, 0.3, 7.8, 3.0, 0.6, "Natural Language\nResearch Agenda", C_USER, fontsize=8)

    # ── Layer 2: Entry Point ─────────────────────────────────
    _draw_section_label(ax, 4.5, 8.5, "Entry Point", C_MEETING)
    _draw_box(ax, 4.3, 7.8, 2.5, 0.6, "run.py\n(CLI / API / Demo)", C_MEETING, fontsize=8)

    _draw_arrow(ax, 3.3, 8.1, 4.3, 8.1)

    # ── Layer 3: Config & Logging ────────────────────────────
    _draw_section_label(ax, 7.5, 8.5, "Config & Logging", C_LOG)
    _draw_box(ax, 7.3, 7.8, 2.2, 0.6, "config.py\nlogging_config.py", C_LOG, fontsize=8)
    _draw_arrow(ax, 6.8, 8.1, 7.3, 8.1)

    # ── Layer 4: Meeting Orchestration ───────────────────────
    _draw_section_label(ax, 10.2, 8.5, "Meeting Orchestration", C_MEETING)
    _draw_box(ax, 10.0, 7.8, 2.8, 0.6, "run_meeting.py\n(Team + Individual)", C_MEETING, fontsize=8)
    _draw_arrow(ax, 9.5, 8.1, 10.0, 8.1)

    # ── Main flow area ───────────────────────────────────────
    # Left column: Team Meeting
    _draw_section_label(ax, 0.5, 6.8, "Team Meeting Flow", C_MEETING)

    _draw_box(ax, 0.3, 6.0, 3.5, 0.7, "PI convenes meeting\nwith research agenda", C_ACCENT, fontsize=8)
    _draw_arrow(ax, 2.05, 6.0, 2.05, 5.3)

    _draw_box(ax, 0.3, 4.6, 3.5, 0.7, "Round 1..N: Specialists\ndiscuss from their domain", C_SPECIALIST, fontsize=8)
    _draw_arrow(ax, 2.05, 4.6, 2.05, 3.9)

    _draw_box(ax, 0.3, 3.2, 3.5, 0.7, "PI synthesises → assigns\nconcrete tasks to specialists", C_ACCENT, fontsize=8)

    # Middle column: Individual Meeting
    _draw_section_label(ax, 5.0, 6.8, "Individual Meeting Flow", C_TOOLS)

    _draw_box(ax, 4.8, 6.0, 3.5, 0.7, "Specialist receives\ntask + tool access", C_SPECIALIST, fontsize=8)
    _draw_arrow(ax, 6.55, 6.0, 6.55, 5.3)

    _draw_box(ax, 4.8, 4.6, 3.5, 0.7, "Agent ↔ Tool Loop:\nJSON action blocks →\nMiniProt ToolManager", C_TOOLS, fontsize=8)
    _draw_arrow(ax, 6.55, 4.6, 6.55, 3.9)

    _draw_box(ax, 4.8, 3.5, 1.6, 0.5, "Critic reviews", C_CRITIC, fontsize=8)
    _draw_arrow(ax, 6.4, 3.5, 5.6, 3.75)
    _draw_arrow(ax, 5.6, 3.75, 6.4, 3.25)
    # Revision loop arrow back
    ax.annotate("revise", xy=(7.2, 4.95), xytext=(7.9, 4.95),
                arrowprops=dict(arrowstyle="->", color=C_CRITIC, lw=1.0,
                               connectionstyle="arc3,rad=0.3"),
                fontsize=7, color=C_CRITIC)

    _draw_box(ax, 4.8, 2.8, 3.5, 0.5, "Final answer with file paths", C_SPECIALIST, fontsize=8)

    # Right column: Agent Pool
    _draw_section_label(ax, 9.5, 6.8, "Agent Pool (6 specialists)", C_SPECIALIST)

    agents_info = [
        ("PI", C_ACCENT, "(coordinates)"),
        ("Protein Search", C_SPECIALIST, "uniprot, ncbi"),
        ("Structure", C_SPECIALIST, "alphafold, pdb, foldseek..."),
        ("Chemistry", C_SPECIALIST, "smiles"),
        ("Docking", C_SPECIALIST, "vina, pocket_picker..."),
        ("Seq Analysis", C_SPECIALIST, "mafft, hmmer, mmseqs..."),
        ("Scientific Critic", C_CRITIC, "(reviews only)"),
    ]
    for i, (name, color, tools) in enumerate(agents_info):
        y = 5.8 - i * 0.55
        _draw_box(ax, 9.5, y, 2.8, 0.45, f"{name}\n{tools}", color, fontsize=6.5)

    # Right side: Multi-Provider API
    _draw_section_label(ax, 9.5, 2.2, "Multi-Provider API Layer", C_LOG)
    providers = "DeepSeek V4 | GPT-5.2 | SJTU | Custom"
    _draw_box(ax, 9.5, 1.4, 2.8, 0.7, f"Per-agent API keys\n{providers}", C_LOG, fontsize=7)

    # Bottom: Output
    _draw_section_label(ax, 0.5, 2.2, "Outputs", C_FLOW)

    _draw_box(ax, 0.3, 0.5, 3.5, 1.5,
              "Meeting Records:\n  JSON + Markdown\n\nTool Artifacts:\n  FASTA, PDB, CSV...\n\nStructured Logs:\n  run.log / .jsonl / api_calls.json",
              C_FLOW, fontsize=7)

    _draw_box(ax, 4.8, 0.5, 3.5, 1.5,
              "Per-agent Cache Isolation:\n  Different system prompts\n  → different cache keys\n  → higher hit rates\n\nToken tracking & cost\nestimation per call",
              C_LOG, fontsize=7)

    _draw_box(ax, 9.5, 0.5, 3.5, 1.5,
              "Pluggable Providers:\n  MINIPROT_PROVIDER=deepseek\n  MINIPROT_PROVIDER=openai\n\nPer-agent overrides:\n  MINIPROT_PI_MODEL=...\n  MINIPROT_DOCKING_API_KEY=...",
              C_LOG, fontsize=7)

    # Save
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "architecture.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  Saved: {path}")


# ── Meeting flow diagram ───────────────────────────────────────────

def draw_meeting_flow(output_dir: Path) -> None:
    """Meeting flow diagram showing team and individual meeting sequences."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
    fig.patch.set_facecolor(C_BG)

    # Left: Team Meeting
    ax1.set_xlim(0, 7)
    ax1.set_ylim(0, 7)
    ax1.axis("off")
    ax1.set_facecolor(C_BG)
    ax1.text(3.5, 6.7, "Team Meeting", fontsize=14, fontweight="bold",
             ha="center", color=C_MEETING)

    steps_team = [
        (0, "Human Researcher\nposes agenda"),
        (1, "System injects prior\nsummaries + contexts"),
        (2, "PI: Opening statement\n+ guiding questions"),
        (3, "Round 1..N:\nEach specialist\ncontributes"),
        (4, "PI: Synthesis after\neach round"),
        (5, "PI: Final summary\n+ task assignments"),
    ]
    for i, (step_num, text) in enumerate(steps_team):
        y = 5.8 - i * 0.9
        color = C_USER if i == 0 else (C_ACCENT if step_num in (2, 4, 5) else C_SPECIALIST)
        _draw_box(ax1, 0.8, y, 5.2, 0.7, f"{step_num}. {text}", color, fontsize=8)
        if i < len(steps_team) - 1:
            _draw_arrow(ax1, 3.4, y, 3.4, y - 0.2)

    # Right: Individual Meeting
    ax2.set_xlim(0, 7)
    ax2.set_ylim(0, 7)
    ax2.axis("off")
    ax2.set_facecolor(C_BG)
    ax2.text(3.5, 6.7, "Individual Meeting", fontsize=14, fontweight="bold",
             ha="center", color=C_TOOLS)

    steps_indiv = [
        (0, "Specialist receives\ntask + tool descriptions"),
        (1, "Agent calls tool:\nJSON action block"),
        (2, "ToolBridge executes:\nMiniProt ToolManager"),
        (3, "Tool result returns\nto conversation"),
        (4, "More tools? → loop\nDone? → final answer"),
        (5, "Scientific Critic:\nreview → approve/revise"),
        (6, "Agent revises if needed\n(run tools again)"),
    ]
    for i, (step_num, text) in enumerate(steps_indiv):
        y = 5.8 - i * 0.8
        if step_num in (1, 2, 3):
            color = C_TOOLS
        elif step_num == 5:
            color = C_CRITIC
        else:
            color = C_SPECIALIST
        _draw_box(ax2, 0.8, y, 5.2, 0.65, f"{step_num}. {text}", color, fontsize=8)
        if i < len(steps_indiv) - 1:
            _draw_arrow(ax2, 3.4, y, 3.4, y - 0.15)

    path = output_dir / "meeting_flow.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  Saved: {path}")


# ── Agent roles diagram ────────────────────────────────────────────

def draw_agent_roles(output_dir: Path) -> None:
    """Agent roles and tool assignments diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(14, 8))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_facecolor(C_BG)

    ax.text(7, 7.5, "Agent Roles & Tool Assignments",
            fontsize=14, fontweight="bold", ha="center", color=C_FLOW)

    agents_data = [
        ("PI", C_ACCENT, "Lead, plan, synthesize", ["(no tools)"]),
        ("Protein Search\nSpecialist", C_SPECIALIST, "UniProt & NCBI",
         ["uniprot_search", "ncbi_search"]),
        ("Structure\nSpecialist", C_SPECIALIST, "3D structures",
         ["alphafold", "pdb", "structure_from_fasta",
          "omegafold", "esmfold", "foldseek",
          "tmalign", "structure_alignment_batch",
          "similarity_matrix"]),
        ("Chemistry\nSpecialist", C_SPECIALIST, "Small molecules",
         ["smiles"]),
        ("Docking\nSpecialist", C_SPECIALIST, "Molecular docking",
         ["autodock_vina", "pocket_picker",
          "pocket_box", "pdb_repair"]),
        ("Sequence Analysis\nSpecialist", C_SPECIALIST, "MSA, HMMER, trees",
         ["sequence_alignment", "hmmer", "mmseqs2",
          "cdhit", "protein_properties", "pymol",
          "ete", "merger", "pdb_merge"]),
        ("Scientific Critic", C_CRITIC, "Review & validate",
         ["(no tools)"]),
    ]

    for i, (name, color, role, tools) in enumerate(agents_data):
        x = 0.3 + (i % 4) * 3.4
        y = 5.5 - (i // 4) * 3.8

        # Agent card
        _draw_box(ax, x, y, 3.0, 0.7, name, color, fontsize=8, fontweight="bold")
        ax.text(x + 1.5, y + 0.6, role, fontsize=7, ha="center", color=C_LOG, style="italic")

        # Tool list
        for j, tool in enumerate(tools):
            ty = y - 0.5 - j * 0.35
            _draw_box(ax, x + 0.15, ty, 2.7, 0.30, tool, C_TOOLS if tool != "(no tools)" else C_LOG,
                      fontsize=6.5, text_color="white" if tool != "(no tools)" else C_LOG,
                      fill=(tool != "(no tools)"))

    path = output_dir / "agent_roles.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  Saved: {path}")


# ── Main ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate architecture diagrams for MiniProt Virtual Lab",
    )
    parser.add_argument("--output-dir", "-o", type=str, default="./figures",
                        help="Output directory for figures (default: ./figures)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Generating architecture diagrams...")
    draw_architecture(output_dir)
    draw_meeting_flow(output_dir)
    draw_agent_roles(output_dir)
    print(f"\nDone! Figures saved to: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
