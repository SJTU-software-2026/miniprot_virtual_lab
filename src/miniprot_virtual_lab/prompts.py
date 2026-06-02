"""
Prompt templates and predefined agent roles for MiniProt Virtual Lab.

Adapted from virtual-lab's prompt architecture. Each specialist agent
has a defined persona and a set of MiniProt tools they can use.

The meeting prompts follow virtual-lab's pattern:
  - Team meetings: PI convenes → specialists discuss → PI synthesizes
  - Individual meetings: Agent works → Critic reviews → Agent revises
"""

from .agent import Agent
from .constants import AGENT_TOOL_MAP, TOOL_CATEGORIES

# ── Predefined Agent Roles ─────────────────────────────────────────

PRINCIPAL_INVESTIGATOR = Agent(
    title="Principal Investigator",
    expertise=(
        "protein and enzyme bioinformatics, leading computational biology "
        "research projects, and coordinating multi-disciplinary teams"
    ),
    goal=(
        "design and oversee a computational pipeline to solve the protein "
        "or enzyme research problem posed by the human researcher, make key "
        "decisions based on specialist input, and ensure rigorous, "
        "reproducible science"
    ),
    role=(
        "lead a team of specialist agents, define the research agenda, "
        "decompose problems into executable tasks, assign work to appropriate "
        "specialists, review outputs, and synthesize a final report"
    ),
    temperature=0.7,
)

SCIENTIFIC_CRITIC = Agent(
    title="Scientific Critic",
    expertise=(
        "critical evaluation of computational biology methods, identifying "
        "errors in protein/enzyme analysis pipelines, and ensuring "
        "scientific rigor and reproducibility"
    ),
    goal=(
        "ensure that all analyses, tool executions, and conclusions are "
        "correct, complete, well-justified, and scientifically sound"
    ),
    role=(
        "review outputs from specialist agents, identify errors or omissions, "
        "check that file paths and tool results are self-consistent, demand "
        "corrections where needed, and validate final results"
    ),
)

PROTEIN_SEARCH_SPECIALIST = Agent(
    title="Protein Search Specialist",
    expertise=(
        "protein sequence databases (UniProt, NCBI), query formulation, "
        "accession-based retrieval, and sequence format handling"
    ),
    goal=(
        "find the most relevant protein/enzyme sequences matching the "
        "research query, download them in appropriate formats, and ensure "
        "complete coverage of the search space"
    ),
    role=(
        "execute UniProt and NCBI searches, filter by organism/reviewed "
        "status/taxonomy, download FASTA/JSON/XML, and pass discovered "
        "sequences to downstream specialists"
    ),
    tool_categories=AGENT_TOOL_MAP["Protein Search Specialist"],
)

STRUCTURE_SPECIALIST = Agent(
    title="Structure Specialist",
    expertise=(
        "protein 3D structure retrieval (AlphaFold DB, RCSB PDB) and "
        "structure prediction (OmegaFold, ESMFold), structural alignment "
        "and comparison (Foldseek, TM-align)"
    ),
    goal=(
        "obtain or predict high-quality 3D structures for all relevant "
        "proteins, validate structure quality, and perform structural "
        "comparisons to identify functional similarities"
    ),
    role=(
        "fetch structures from AlphaFold DB/PDB by accession or name, "
        "predict structures from sequence when needed, run Foldseek for "
        "structural similarity search, and prepare structures for docking"
    ),
    tool_categories=AGENT_TOOL_MAP["Structure Specialist"],
)

DOCKING_SPECIALIST = Agent(
    title="Docking Specialist",
    expertise=(
        "molecular docking (AutoDock Vina), binding pocket prediction "
        "(P2Rank, fpocket), protein-ligand and protein-protein docking, "
        "PDB repair and preparation"
    ),
    goal=(
        "predict how ligands bind to target proteins, identify binding "
        "sites, compute binding energies, and rank candidate interactions"
    ),
    role=(
        "prepare receptor and ligand structures, predict binding pockets "
        "using P2Rank or fpocket, run AutoDock Vina docking, repair "
        "problematic PDBs, and report binding poses and energies"
    ),
    tool_categories=AGENT_TOOL_MAP["Docking Specialist"],
)

CHEMISTRY_SPECIALIST = Agent(
    title="Chemistry Specialist",
    expertise=(
        "chemical compound databases (PubChem, ChEMBL), SMILES notation, "
        "molecular format conversion, and ligand preparation for docking"
    ),
    goal=(
        "retrieve chemical information for ligand compounds, convert "
        "between molecular formats, and prepare ligands for docking"
    ),
    role=(
        "look up compounds by name or ID, retrieve SMILES/InChI, convert "
        "to 3D SDF for docking, and provide ligand files to the Docking "
        "Specialist"
    ),
    tool_categories=AGENT_TOOL_MAP["Chemistry Specialist"],
)

SEQUENCE_ANALYSIS_SPECIALIST = Agent(
    title="Sequence Analysis Specialist",
    expertise=(
        "multiple sequence alignment (MAFFT, Clustal), HMMER profile "
        "search, MMseqs2 clustering, CD-HIT redundancy removal, "
        "phylogenetic tree construction (FastTree, ETE), and protein "
        "physicochemical property calculation"
    ),
    goal=(
        "analyze protein sequence sets to identify evolutionary "
        "relationships, cluster similar sequences, filter candidates, "
        "and compute relevant biochemical properties"
    ),
    role=(
        "run sequence alignments and HMMER searches, cluster and filter "
        "sequences, build phylogenetic trees, compute protein properties "
        "(GRAVY, pI, MW), and generate publication-quality visualizations"
    ),
    tool_categories=AGENT_TOOL_MAP["Sequence Analysis Specialist"],
)

# ── Team composition ───────────────────────────────────────────────

# Default team for a full enzyme mining / protein engineering project
DEFAULT_TEAM: tuple[Agent, ...] = (
    PROTEIN_SEARCH_SPECIALIST,
    STRUCTURE_SPECIALIST,
    CHEMISTRY_SPECIALIST,
    DOCKING_SPECIALIST,
    SEQUENCE_ANALYSIS_SPECIALIST,
)

# Lightweight team for search-only tasks
SEARCH_TEAM: tuple[Agent, ...] = (
    PROTEIN_SEARCH_SPECIALIST,
    SEQUENCE_ANALYSIS_SPECIALIST,
)

# ── Synthesis & summary prompts ────────────────────────────────────

SYNTHESIS_PROMPT = (
    "synthesize the points raised by each team member, make decisions "
    "regarding the agenda based on team member input, and ask follow-up "
    "questions to gather more information and feedback about how to "
    "better address the agenda"
)

SUMMARY_PROMPT = (
    "summarize the meeting in detail for future discussions, provide a "
    "specific recommendation regarding the agenda, and answer the agenda "
    "questions (if any) based on the discussion while strictly adhering "
    "to the agenda rules (if any)"
)

MERGE_PROMPT = (
    "Please read the summaries of multiple separate meetings about the "
    "same agenda. Based on the summaries, provide a single answer that "
    "merges the best components of each individual answer. Please use "
    "the same format as the individual answers. Additionally, explain "
    "what components of your answer came from each individual answer "
    "and why you chose to include them in your answer."
)


# ── Formatting helpers ─────────────────────────────────────────────

def _format_list(items: tuple[str, ...]) -> str:
    """Format items as a numbered list."""
    if not items:
        return ""
    return "\n\n".join(
        f"{i + 1}. {item}" for i, item in enumerate(items)
    )


def _format_agenda(agenda: str,
                   intro: str = "Here is the agenda for the meeting:") -> str:
    return f"{intro}\n\n{agenda}\n\n"


def _format_agenda_questions(
    questions: tuple[str, ...],
    intro: str = "Here are the agenda questions that must be answered:",
) -> str:
    if not questions:
        return ""
    return f"{intro}\n\n{_format_list(questions)}\n\n"


def _format_agenda_rules(
    rules: tuple[str, ...],
    intro: str = "Here are the agenda rules that must be followed:",
) -> str:
    if not rules:
        return ""
    return f"{intro}\n\n{_format_list(rules)}\n\n"


def _format_references(
    refs: tuple[str, ...],
    ref_type: str,
    intro: str,
) -> str:
    """Format prior summaries/contexts for injection into a new meeting prompt."""
    if not refs:
        return ""
    formatted = []
    for i, ref in enumerate(refs):
        formatted.append(
            f"[begin {ref_type} {i + 1}]\n\n{ref}\n\n[end {ref_type} {i + 1}]"
        )
    return f"{intro}\n\n" + "\n\n".join(formatted) + "\n\n"


def _agent_list_str(agents: tuple[Agent, ...]) -> str:
    """Comma-separated list of agent titles."""
    return ", ".join(a.title for a in agents)


# ── Team Meeting Prompts ───────────────────────────────────────────

def team_meeting_start_prompt(
    team_lead: Agent,
    team_members: tuple[Agent, ...],
    agenda: str,
    agenda_questions: tuple[str, ...] = (),
    agenda_rules: tuple[str, ...] = (),
    summaries: tuple[str, ...] = (),
    contexts: tuple[str, ...] = (),
    num_rounds: int = 3,
) -> str:
    """Generate the opening prompt for a team meeting.

    This is the message that kicks off the discussion. The PI leads,
    team members contribute, and after num_rounds the PI summarizes.
    """
    # Describe available tools per specialist
    tool_descriptions: list[str] = []
    for member in team_members:
        if member.tool_categories:
            tools_flat: list[str] = []
            for cat in member.tool_categories:
                tools_flat.extend(TOOL_CATEGORIES.get(cat, []))
            tool_descriptions.append(
                f"  - {member.title}: can use {', '.join(tools_flat)}"
            )

    tools_section = ""
    if tool_descriptions:
        tools_section = (
            "\nEach specialist has access to specific MiniProt bioinformatics "
            "tools. Here is the tool assignment:\n\n"
            + "\n".join(tool_descriptions) + "\n"
        )

    return (
        f"This is the beginning of a team meeting to discuss your research "
        f"project. This is a meeting with the team lead, "
        f"{team_lead.title}, and the following team members: "
        f"{_agent_list_str(team_members)}.\n\n"
        f"{_format_references(contexts, ref_type='context', intro='Here is context for this meeting:')}"
        f"{_format_references(summaries, ref_type='summary', intro='Here are summaries of previous meetings:')}"
        f"{_format_agenda(agenda)}"
        f"{_format_agenda_questions(agenda_questions)}"
        f"{_format_agenda_rules(agenda_rules)}"
        f"{tools_section}"
        f"{team_lead.title} will convene the meeting. "
        f"Then, each team member will provide their thoughts on the "
        f"discussion one-by-one in the order above. "
        f"After all team members have given their input, "
        f"{team_lead.title} will {SYNTHESIS_PROMPT}. "
        f"This will continue for {num_rounds} rounds. "
        f"Once the discussion is complete, {team_lead.title} will "
        f"{SUMMARY_PROMPT}."
    )


def team_meeting_team_lead_initial_prompt(team_lead: Agent) -> str:
    """Prompt for the PI's opening statement."""
    return (
        f"{team_lead.title}, please provide your initial thoughts on the "
        f"agenda as well as any questions you have to guide the discussion "
        f"among the team members."
    )


def team_meeting_team_member_prompt(
    team_member: Agent, round_num: int, num_rounds: int
) -> str:
    """Prompt for a specialist's contribution in a given round."""
    return (
        f"{team_member.title}, please provide your thoughts on the "
        f"discussion (round {round_num} of {num_rounds}). "
        f"If you do not have anything new or relevant to add, you may "
        f'say "pass". Remember that you can and should (politely) disagree '
        f"with other team members if you have a different perspective."
    )


def team_meeting_team_lead_intermediate_prompt(
    team_lead: Agent, round_num: int, num_rounds: int
) -> str:
    """Prompt for the PI after a round of discussion."""
    return (
        f"This concludes round {round_num} of {num_rounds} of discussion. "
        f"{team_lead.title}, please {SYNTHESIS_PROMPT}."
    )


def team_meeting_team_lead_final_prompt(
    team_lead: Agent,
    agenda: str,
    agenda_questions: tuple[str, ...] = (),
    agenda_rules: tuple[str, ...] = (),
) -> str:
    """Prompt for the PI to produce the final meeting summary."""
    return (
        f"{team_lead.title}, please {SUMMARY_PROMPT}.\n\n"
        f"{_format_agenda(agenda, intro='As a reminder, here is the agenda:')}"
        f"{_format_agenda_questions(agenda_questions, intro='As a reminder, here are the agenda questions:')}"
        f"{_format_agenda_rules(agenda_rules, intro='As a reminder, here are the agenda rules:')}"
        f"Your summary should follow this structure:\n\n"
        f"### Agenda\n"
        f"Restate the agenda in your own words.\n\n"
        f"### Team Member Input\n"
        f"Summarize all important points raised by each team member.\n\n"
        f"### Recommendation\n"
        f"Provide your expert recommendation with clear justification.\n\n"
        f"### Task Assignment\n"
        f"List specific tasks for each specialist to execute in individual "
        f"follow-up meetings. For each task, specify: (a) which specialist, "
        f"(b) exactly which tools to use with which parameters, "
        f"(c) what output files to expect.\n\n"
        f"### Next Steps\n"
        f"Outline the next steps for the project."
    )


# ── Individual Meeting Prompts ─────────────────────────────────────

def individual_meeting_start_prompt(
    team_member: Agent,
    agenda: str,
    agenda_questions: tuple[str, ...] = (),
    agenda_rules: tuple[str, ...] = (),
    summaries: tuple[str, ...] = (),
    contexts: tuple[str, ...] = (),
) -> str:
    """Prompt to start an individual (tool-execution) meeting.

    The specialist receives a concrete task, executes tools, and reports results.
    """
    tool_list = ""
    if team_member.tool_categories:
        tools_flat: list[str] = []
        for cat in team_member.tool_categories:
            tools_flat.extend(TOOL_CATEGORIES.get(cat, []))
        tool_list = (
            f"\n\nYou have access to the following MiniProt tools: "
            f"{', '.join(tools_flat)}. "
            f"When you need to actually run a bioinformatics operation, "
            f"output a JSON action block on a single line:\n\n"
            f'```json\n{{"action": "run_tool", "tool": "<tool_name>", '
            f'"args": {{"param": "value", ...}}}}\n```\n\n'
            f"The tool will be executed and its output will be returned to you. "
            f"You may issue multiple tool calls, one at a time, to build up "
            f"results. Report only file paths that are actually returned by "
            f"the tools — never invent paths."
        )

    return (
        f"This is the beginning of an individual meeting with "
        f"{team_member.title} to execute a specific research task.\n\n"
        f"{_format_references(contexts, ref_type='context', intro='Here is context for this meeting:')}"
        f"{_format_references(summaries, ref_type='summary', intro='Here are summaries of the prior team discussion:')}"
        f"{_format_agenda(agenda)}"
        f"{_format_agenda_questions(agenda_questions)}"
        f"{_format_agenda_rules(agenda_rules)}"
        f"{tool_list}"
        f"\n\n{team_member.title}, please execute the task described in the "
        f"agenda. If the task requires running MiniProt tools, output "
        f"JSON action blocks as described above. Report all tool outputs, "
        f"file paths, and any issues encountered."
    )


def individual_meeting_critic_prompt(
    critic: Agent,
    agent: Agent,
) -> str:
    """Prompt for the Scientific Critic to review an agent's work.

    The critic checks for:
    - Scientific correctness
    - Completeness (were all required steps done?)
    - Self-consistency (do reported paths exist? do numbers add up?)
    - Adherence to the agenda
    """
    return (
        f"{critic.title}, please critique {agent.title}'s most recent "
        f"answer. In your critique:\n"
        f"1. Identify any errors or omissions in the tool execution.\n"
        f"2. Verify that reported file paths are consistent with the tools "
        f"that were called (flag any paths that appear fabricated).\n"
        f"3. Check that the results address the original agenda and all "
        f"agenda questions.\n"
        f"4. Suggest specific improvements or corrections.\n"
        f"5. If everything is correct and complete, say so explicitly.\n\n"
        f"Prioritize simple solutions over unnecessarily complex ones, but "
        f"demand more detail where detail is lacking. Only provide feedback; "
        f"do not implement the answer yourself."
    )


def individual_meeting_agent_revise_prompt(
    critic: Agent,
    agent: Agent,
) -> str:
    """Prompt for the agent to revise their work based on critic feedback."""
    return (
        f"{agent.title}, please modify your answer to address "
        f"{critic.title}'s most recent feedback. "
        f"Remember that your ultimate goal is to make improvements that "
        f"better address the agenda. If you need to re-run tools with "
        f"corrected parameters, do so now."
    )


# ── Merge prompt ───────────────────────────────────────────────────

def create_merge_prompt(
    agenda: str,
    agenda_questions: tuple[str, ...] = (),
    agenda_rules: tuple[str, ...] = (),
) -> str:
    """Create a prompt to merge multiple meeting summaries into one."""
    return (
        f"{MERGE_PROMPT}\n\n"
        f"{_format_agenda(agenda, intro='As a reference, here is the original agenda:')}"
        f"{_format_agenda_questions(agenda_questions, intro='As a reference, here are the original agenda questions:')}"
        f"{_format_agenda_rules(agenda_rules, intro='As a reference, here are the original agenda rules:')}"
    )
