"""
MiniProt Virtual Lab — AI-Human Collaboration for Protein & Enzyme Research.

A multi-agent scientific collaboration framework that combines the meeting-based
architecture of Virtual Lab (zou-group/virtual-lab) with the bioinformatics tool
capabilities of MiniProt (enzyme_update).

Usage:
    from miniprot_virtual_lab import (
        Agent, run_meeting,
        PRINCIPAL_INVESTIGATOR, SCIENTIFIC_CRITIC,
        PROTEIN_SEARCH_SPECIALIST, STRUCTURE_SPECIALIST,
        DOCKING_SPECIALIST, SEQUENCE_ANALYSIS_SPECIALIST,
        DEFAULT_TEAM,
    )

    # Team meeting
    run_meeting(
        meeting_type="team",
        agenda="Identify enzymes that catalyze tryptophan hydroxylation...",
        team_lead=PRINCIPAL_INVESTIGATOR,
        team_members=DEFAULT_TEAM,
        save_dir=Path("./meetings"),
        num_rounds=3,
    )

    # Individual meeting with tools
    run_meeting(
        meeting_type="individual",
        agenda="Search UniProt for tryptophan hydroxylase, download FASTA...",
        team_member=PROTEIN_SEARCH_SPECIALIST,
        save_dir=Path("./meetings"),
        enable_tools=True,
    )
"""

from .agent import Agent
from .config import (
    resolve_config,
    ResolvedConfig,
    ProviderPreset,
    PROVIDER_PRESETS,
    list_providers,
    print_config_summary,
)
from .logging_config import RunLogger
from .prompts import (
    PRINCIPAL_INVESTIGATOR,
    SCIENTIFIC_CRITIC,
    PROTEIN_SEARCH_SPECIALIST,
    STRUCTURE_SPECIALIST,
    DOCKING_SPECIALIST,
    CHEMISTRY_SPECIALIST,
    SEQUENCE_ANALYSIS_SPECIALIST,
    DEFAULT_TEAM,
    SEARCH_TEAM,
)
from .run_meeting import run_meeting, load_meeting_context, list_saved_meetings
from .constants import ENZYME_MINING_REFERENCE_WORKFLOW
from .tools import ToolBridge, get_bridge

__all__ = [
    # Core
    "Agent",
    "run_meeting",
    "load_meeting_context",
    "list_saved_meetings",
    "RunLogger",
    # Config
    "resolve_config",
    "ResolvedConfig",
    "ProviderPreset",
    "PROVIDER_PRESETS",
    "list_providers",
    "print_config_summary",
    # Agents
    "PRINCIPAL_INVESTIGATOR",
    "SCIENTIFIC_CRITIC",
    "PROTEIN_SEARCH_SPECIALIST",
    "STRUCTURE_SPECIALIST",
    "DOCKING_SPECIALIST",
    "CHEMISTRY_SPECIALIST",
    "SEQUENCE_ANALYSIS_SPECIALIST",
    "DEFAULT_TEAM",
    "SEARCH_TEAM",
    # Tools
    "ToolBridge",
    "get_bridge",
    # Reference
    "ENZYME_MINING_REFERENCE_WORKFLOW",
]

__version__ = "0.2.0"
