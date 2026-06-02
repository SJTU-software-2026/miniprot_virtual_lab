"""
Agent class for MiniProt Virtual Lab.

Each Agent has a title, expertise, goal, role, a set of tools they are
authorized to use, and optional per-agent API configuration (model, api_key,
base_url). Per-agent API config improves prompt-cache hit rates because each
agent's system prompt is different — using separate API keys isolates their
cache namespaces.

Adapted from virtual-lab's Agent but extended with:
  - Tool category support for MiniProt integration
  - Per-agent API configuration for cache isolation
  - Provider preset for easy switching between AI providers
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Agent:
    """A scientist agent in the Virtual Lab.

    Each agent can optionally override the global API configuration.
    When api_key / base_url / model are None, the global defaults are used.
    When set, they isolate that agent's API calls — improving prompt-cache
    hit rates since each agent has a different system prompt.

    Attributes:
        title: Short professional title (e.g. "Structure Specialist").
        expertise: Domain expertise description.
        goal: What this agent aims to accomplish.
        role: How this agent contributes to the team.
        model: LLM model name. None = use global default.
        api_key: Per-agent API key override. None = use global.
        base_url: Per-agent API base URL override. None = use global.
        tool_categories: Names of tool categories this agent can use.
        temperature: Sampling temperature override (None = use default).
        extra_body: Optional extra body params for the API (e.g. thinking).
    """

    title: str
    expertise: str
    goal: str
    role: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    tool_categories: List[str] = field(default_factory=list)
    temperature: Optional[float] = None
    extra_body: Optional[Dict[str, Any]] = None

    @property
    def prompt(self) -> str:
        """Natural-language prompt describing this agent's identity."""
        return (
            f"You are a {self.title}. "
            f"Your expertise is in {self.expertise}. "
            f"Your goal is to {self.goal}. "
            f"Your role is to {self.role}."
        )

    @property
    def system_message(self) -> Dict[str, str]:
        """OpenAI-compatible system message dict."""
        return {"role": "system", "content": self.prompt}

    def __hash__(self) -> int:
        return hash(self.title)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Agent):
            return False
        return self.title == other.title

    def __str__(self) -> str:
        return self.title

    def __repr__(self) -> str:
        return f"Agent(title={self.title!r})"
