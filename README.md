# MiniProt Virtual Lab

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**AI-Human collaboration framework for protein & enzyme research.**

> [中文版 (Chinese Version)](README_CN.md)

Combines the **multi-agent meeting architecture** of [Virtual Lab](https://github.com/zou-group/virtual-lab) (Zou Group, *Nature* 2025) with the **bioinformatics tool capabilities** of [MiniProt](https://github.com/SJTU-software-2026/enzyme_update). A human researcher works with a team of specialist LLM agents — each with access to specific protein/enzyme tools — through structured team meetings and individual work sessions.

![Architecture](figures/architecture.png)

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Agent Roles](#agent-roles)
- [Meeting Types](#meeting-types)
- [Logging System](#logging-system)
- [Built-in Demo](#built-in-demo)
- [Project Structure](#project-structure)
- [Comparison with Original Projects](#comparison-with-original-projects)

---

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Agent Meetings** | Team meetings (PI + specialists) and individual meetings (specialist + tools + critic) |
| **40+ Bioinformatics Tools** | UniProt, AlphaFold, AutoDock Vina, HMMER, MAFFT, Foldseek, PyMOL... |
| **YAML Config File** | One `config/settings.yaml` file with comments — no need for complex env vars |
| **Multi-Provider Support** | DeepSeek V4 (default), OpenAI GPT-5.2, SJTU server, custom endpoints |
| **Per-Agent API Config** | Each agent can use a different API key/model for prompt-cache isolation |
| **Structured Logging** | JSONL traces, API call logs, tool execution logs, token/cost tracking |
| **Scientific Critic** | Automatic review of specialist outputs for correctness and completeness |

---

## Quick Start

```bash
# 1. Set your API key (in environment or config file)
export DEEPSEEK_API_KEY="your_key_here"

# 2. (Recommended) Edit config/settings.yaml to customize your setup
#    All settings are documented with comments in the file.

# 3. Run the demo
python run.py --demo

# 4. Interactive mode
python run.py

# 5. Switch provider (optional)
python run.py --provider openai --demo
```

---

## Architecture

![Meeting Flow](figures/meeting_flow.png)

The Virtual Lab organizes research as a series of **meetings**:

1. **Team Meetings**: PI convenes specialists → multi-round discussion → task assignments
2. **Individual Meetings**: Specialist executes tools via MiniProt → Critic reviews → agent revises
3. **Iteration**: Team review → new tasks → execute → repeat

```
Human Researcher
      │
      ▼
┌─────────────────────────────────────────────────────┐
│                 TEAM MEETING                         │
│  PI convenes → Specialists discuss → PI synthesizes  │
│  Output: Research plan + Task assignments           │
└─────────────────────────────────────────────────────┘
      │
      ▼ (parallel individual meetings)
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ INDIVIDUAL   │ │ INDIVIDUAL   │ │ INDIVIDUAL   │
│ MEETING      │ │ MEETING      │ │ MEETING      │
│              │ │              │ │              │
│ Agent calls  │ │ Agent calls  │ │ Agent calls  │
│ MiniProt     │ │ MiniProt     │ │ MiniProt     │
│ tools        │ │ tools        │ │ tools        │
│      │       │ │      │       │ │      │       │
│   Critic     │ │   Critic     │ │   Critic     │
│   reviews    │ │   reviews    │ │   reviews    │
└──────────────┘ └──────────────┘ └──────────────┘
      │               │               │
      ▼               ▼               ▼
┌─────────────────────────────────────────────────────┐
│              TEAM MEETING (Review)                  │
│  PI reviews results → Team discusses → Final report │
└─────────────────────────────────────────────────────┘
```

---

## Installation

### 1. Clone & setup

```bash
git clone https://github.com/SJTU-software-2026/enzyme_update.git
git clone https://github.com/<your-org>/miniprot_virtual_lab.git

cd enzyme_update && pip install -r requirements.txt
cd ../miniprot_virtual_lab && pip install -r requirements.txt
```

### 2. Configure

```bash
# Step 1: Copy the example config to create your own
cp config/settings.example.yaml config/settings.yaml

# Step 2: Edit settings.yaml — fill in your API key(s)
# Every option is documented with comments in both English and Chinese.

# Step 3 (optional): Use environment variables as overrides
export DEEPSEEK_API_KEY="your_key_here"
```

> **Important:** The program looks for `config/settings.yaml` first. If not found, it falls back to `settings.example.yaml` and prints a warning reminding you to rename it. `settings.yaml` is in `.gitignore` — your API keys will never be committed.

### 3. Verify

```bash
python run.py --providers     # List available AI providers
python run.py --demo          # Run demo pipeline
```

---

## Configuration

All settings are managed through **`config/settings.yaml`** — a single file with extensive comments in both English and Chinese. No need to set a dozen environment variables.

### Quick YAML example

```yaml
# config/settings.yaml

global:
  provider: deepseek              # deepseek | openai | sjtu | custom
  api_key: ""                     # Leave "" to use DEEPSEEK_API_KEY env var
  model: deepseek-v4-pro          # Default for all agents

meeting:
  default_team_rounds: 3
  creative_temperature: 0.7
  precise_temperature: 0.2

agents:
  principal_investigator:
    temperature: 0.7              # PI benefits from more creative reasoning

  docking_specialist:
    # api_key: "sk-docking-only"  # Optional: isolate this agent's cache
    # model: gpt-5.2             # Optional: use a different model
    temperature: 0.2

logging:
  log_dir: logs
  save_api_trace: true
  save_tool_trace: true

output:
  meetings_dir: meetings
  data_dir: data/outputs
```

### Configuration priority (highest to lowest)

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Agent attribute in Python code | `Agent(..., api_key="sk-...")` |
| 2 | Environment variable | `MINIPROT_DOCKING_SPECIALIST_API_KEY` |
| 3 | **YAML `agents.<slug>`** | `agents.docking_specialist.api_key` |
| 4 | **YAML `global`** | `global.api_key` |
| 5 | Provider preset env var | `MINIPROT_PROVIDER` |
| 6 | Global env var | `DEEPSEEK_API_KEY` |
| 7 | Hard-coded default | DeepSeek V4 |

### Per-agent API isolation (for prompt-cache performance)

Each agent has a **different system prompt**. Using separate API keys isolates cache namespaces, improving cache hit rates:

```yaml
# config/settings.yaml — cache isolation example
agents:
  principal_investigator:
    api_key: "sk-pi-xxxx"
    model: deepseek-v4-pro
  scientific_critic:
    api_key: "sk-critic-xxxx"
    model: deepseek-chat           # Cheaper model for simple review tasks
  docking_specialist:
    api_key: "sk-docking-xxxx"
    model: gpt-5.2                 # Different provider entirely
```

Or via environment variables:
```bash
export MINIPROT_PROTEIN_SEARCH_SPECIALIST_API_KEY="sk-search"
export MINIPROT_DOCKING_SPECIALIST_MODEL="gpt-5.2"
```

### Switching AI providers

```bash
# Via config file: set global.provider in config/settings.yaml

# Via env var (overrides YAML):
export MINIPROT_PROVIDER=openai

# Via CLI:
python run.py --provider openai --demo
```

---

## Usage

### Interactive Mode

```bash
python run.py
```

```
Virtual Lab> /team Design a pipeline to find novel lipases in bacteria
Virtual Lab> /task search Search UniProt for lipase enzymes, reviewed only
Virtual Lab> /task structure Get AlphaFold structure for P12345
Virtual Lab> /agents                      # List agents + their API config
Virtual Lab> /providers                   # List AI provider presets
Virtual Lab> /workflow                    # Show reference workflow
Virtual Lab> /demo                        # Run demo pipeline
```

### Command-Line Mode

```bash
# Single team meeting
python run.py --agenda "Find insulin proteins and their 3D structures"

# With specific provider
python run.py --provider openai --demo

# Custom log directory
python run.py --log-dir ./my_logs --demo
```

### Python API

```python
from pathlib import Path
from miniprot_virtual_lab import (
    run_meeting, RunLogger,
    PRINCIPAL_INVESTIGATOR, PROTEIN_SEARCH_SPECIALIST,
    DOCKING_SPECIALIST, DEFAULT_TEAM,
)

# With structured logging
rl = RunLogger(Path("./logs/experiment_1"))

# Team meeting
summary = run_meeting(
    meeting_type="team",
    agenda="Identify and characterize novel cellulases...",
    team_lead=PRINCIPAL_INVESTIGATOR,
    team_members=DEFAULT_TEAM,
    save_dir=Path("./meetings"),
    num_rounds=3,
    temperature=0.7,
    return_summary=True,
    run_logger=rl,
)

# Individual meeting with tools
run_meeting(
    meeting_type="individual",
    agenda="Search UniProt for cellulases, download FASTA...",
    team_member=PROTEIN_SEARCH_SPECIALIST,
    save_dir=Path("./meetings"),
    enable_tools=True,
    run_logger=rl,
)

# Finalize logs
summary = rl.finalize()
print(f"Logs: {summary['log_dir']}")
```

---

## Agent Roles

![Agent Roles](figures/agent_roles.png)

| Agent | Tools | Role |
|-------|-------|------|
| **Principal Investigator** | (none) | Lead team, define agenda, synthesize, assign tasks |
| **Protein Search Specialist** | `uniprot_search`, `ncbi_search` | Find proteins in UniProt/NCBI |
| **Structure Specialist** | `alphafold`, `pdb`, `foldseek`, `tmalign`, `esmfold`, `omegafold`, `structure_from_fasta`, `structure_alignment_batch`, `similarity_matrix` | Get/predict 3D structures |
| **Chemistry Specialist** | `smiles` | Look up compounds, prepare ligands |
| **Docking Specialist** | `autodock_vina`, `pocket_picker`, `pocket_box`, `pdb_repair` | Molecular docking |
| **Sequence Analysis Specialist** | `sequence_alignment`, `hmmer`, `mmseqs2`, `cdhit`, `protein_properties`, `pymol`, `ete`, `merger`, `pdb_merge`, `fasta_convert`, `sequence_length_filter`, `sequence_similarity` | MSA, homolog search, phylogenetics |
| **Scientific Critic** | (none) | Review outputs for correctness |

---

## Meeting Types

### Team Meeting (`meeting_type="team"`)

Multi-agent discussion format:

```
Round 1: PI opens → Specialist 1 → Specialist 2 → ... → PI synthesizes
Round 2: Specialist 1 → Specialist 2 → ... → PI synthesizes
...
Final:   PI produces summary + task assignments
```

### Individual Meeting (`meeting_type="individual"`)

Single-agent task execution with tools + critic review:

```
Specialist receives task → calls tools (JSON action blocks) → final answer
    → Critic reviews → Agent revises (if needed)
```

**Tool call format** (the agent outputs this JSON to invoke MiniProt tools):
```json
{"action": "run_tool", "tool": "uniprot_search", "args": {"query": "insulin", "limit": 5}}
```

---

## Logging System

Every run generates comprehensive structured logs in `logs/run_<timestamp>/`:

| File | Content |
|------|---------|
| `run.log` | Human-readable text log (all levels) |
| `run.jsonl` | Structured event stream (one JSON per line) |
| `api_calls.json` | All LLM API calls with tokens, latency, cost |
| `tools.json` | All tool executions with success/failure |
| `discussion.jsonl` | **Every agent response** — full content, agent name, round, purpose |

**Logged events:** `run_start`, `meeting_start`, `api_call`, `tool_call`, `agent_config`, `agent_response`, `phase`, `critic_satisfied`, `error`, `warning`, `run_end`

### Python API

```python
from miniprot_virtual_lab import RunLogger

rl = RunLogger(Path("./logs/my_experiment"))
rl.log_api_call(agent="Search", model="deepseek-v4-pro",
                input_tokens=1500, output_tokens=800, latency_ms=3200)
rl.log_tool_call(agent="Search", tool="uniprot_search",
                 args={"query": "insulin"}, success=True, elapsed_ms=450)
summary = rl.finalize()
```

---

## Continuing from Previous Meetings

Agents can read previous meeting records as context, enabling long-running research projects that span multiple sessions.

### CLI (interactive mode)

```bash
Virtual Lab> /history                    # List all saved meetings
Virtual Lab> /load 01_team_planning      # Load a meeting as context
Virtual Lab> /context                    # Show currently loaded context
Virtual Lab> /team Continue our project  # Agents will reference prior discussions
Virtual Lab> /clearctx                   # Clear loaded context
```

### CLI (command-line)

```bash
python run.py --agenda "Continue the insulin project..." \
     --context meetings/01_team_planning.json meetings/02a_protein_search.json
```

### Python API

```python
from miniprot_virtual_lab import load_meeting_context, list_saved_meetings

# Browse saved meetings
for m in list_saved_meetings("meetings"):
    print(f"{m['name']}: {m['agents']} ({m['turns']} turns)")

# Load as context
ctx = load_meeting_context("meetings/01_team_planning.json")

# Pass to next meeting — agents will reference prior discussions
run_meeting(
    meeting_type="team",
    agenda="Continue our insulin project with the next steps...",
    summaries=ctx["summaries"],     # Last message of each loaded meeting
    contexts=ctx["contexts"],       # Per-agent contributions (capped at 8000 chars)
    ...
)
```

### How it works

- `load_meeting_context()` reads saved meeting JSON files
- Extracts the final summary + each agent's contributions
- Injects them into the new meeting's prompt via `summaries` and `contexts`
- Agents see the prior discussion and can reference decisions, file paths, and results
- `list_saved_meetings()` shows all available meeting files with agent lists and turn counts

---

## Built-in Demo

`python run.py --demo` runs a 3-phase enzyme mining pipeline:

| Phase | Meeting Type | Participants | Purpose |
|-------|-------------|-------------|---------|
| **1. Planning** | Team (2 rounds) | PI + 5 specialists | Design research approach |
| **2a. Search** | Individual | Protein Search Specialist | Find TPH enzymes in UniProt |
| **2b. Structure** | Individual | Structure Specialist | Download TPH2 AlphaFold structure |
| **2c. Docking** | Individual | Docking Specialist | Dock tryptophan to TPH2 |
| **3. Review** | Team (2 rounds) | PI + 5 specialists | Synthesize findings |

---

## Project Structure

```
miniprot_virtual_lab/
├── run.py                             # Entry point (CLI + demo + API)
├── requirements.txt                   # Python dependencies
├── README.md                          # English docs (this file)
├── README_CN.md                       # Chinese docs
├── config/
│   └── settings.yaml                  # ⭐ User configuration file
├── data/outputs/                      # Tool outputs (FASTA, PDB, etc.)
├── meetings/                          # Meeting records (JSON + Markdown)
├── logs/                              # Structured run logs
│   └── run_20260101_120000/
│       ├── run.log                    # Text log
│       ├── run.jsonl                  # JSONL event stream
│       ├── api_calls.json             # API call trace
│       └── tools.json                 # Tool execution trace
├── figures/                           # Architecture diagrams
├── scripts/
│   └── draw_architecture.py           # Diagram generator
└── src/miniprot_virtual_lab/
    ├── __init__.py                    # Package exports
    ├── agent.py          (~70 lines)  # Agent dataclass
    ├── config.py         (~380 lines) # Provider + YAML + per-agent resolver
    ├── constants.py      (~170 lines) # Tool categories, workflows
    ├── logging_config.py (~200 lines) # Structured logging (RunLogger)
    ├── prompts.py        (~350 lines) # Agent roles, meeting templates
    ├── run_meeting.py    (~390 lines) # Core meeting orchestration
    ├── tools.py          (~250 lines) # ToolBridge → MiniProt
    └── utils.py          (~170 lines) # Token counting, I/O, cost
```

---

## Generating Architecture Diagrams

```bash
pip install matplotlib
python scripts/draw_architecture.py --output-dir ./figures
```

Produces: `architecture.png`, `meeting_flow.png`, `agent_roles.png`

---

## Comparison with Original Projects

| Dimension | MiniProt (enzyme_update) | Virtual Lab (zou-group) | **MiniProt Virtual Lab** |
|-----------|--------------------------|------------------------|--------------------------|
| **Interaction** | Single user ↔ Agent chat | Human PI + meetings | Human PI + meetings |
| **Agent model** | Planner→Executor→Summarizer | Agent persona prompt | Agent persona + tool access |
| **Tools** | 40+, LLM plans calls | PubMed search only | 40+ via MiniProt ToolManager |
| **Configuration** | Scattered env vars | Scattered env vars | **One YAML config file** |
| **API config** | Single global key | Single global key | **Per-agent keys/models** |
| **Logging** | stdlib logging | Print statements | **Structured JSONL + trace files** |
| **Provider** | DeepSeek only (LangChain) | OpenAI only | **Multi-provider presets** |
| **Quality** | Tool retry, replan | Scientific Critic + merge | Scientific Critic + tool retry |
| **Memory** | LangGraph MemorySaver | Meeting summaries | Meeting summaries + artifacts |
| **Domain** | Protein/enzyme only | General science | Protein/enzyme only |
| **Codebase** | ~4500-line graph.py | ~270-line run_meeting.py | ~390-line run_meeting.py |

---

## License

MIT License.

## Citation

If you use MiniProt Virtual Lab, please cite:

- Swanson, K., Wu, W., Bulaong, N.L. et al. *The Virtual Lab of AI agents designs new SARS-CoV-2 nanobodies.* Nature (2025). https://doi.org/10.1038/s41586-025-09442-9
- MiniProt — AI Agent for Protein & Enzyme Mining. SJTU Software 2026.
