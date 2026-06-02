"""
Provider configuration resolver for MiniProt Virtual Lab.

Supports:
  - YAML config file  (config/settings.yaml — the PRIMARY user-facing config)
  - Global env vars   (DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL)
  - Per-agent overrides in YAML (agents.<slug>.api_key / .model / .base_url)
  - Per-agent env vars (MINIPROT_<TITLE>_API_KEY, etc.)
  - Agent attributes   (set in Python code)
  - Provider presets   (deepseek, openai, sjtu, custom)

Resolution order (for each agent — first match wins):
  1. Agent attribute    (Agent.api_key / .model / .base_url set in code)
  2. Agent env var      (MINIPROT_<AGENT_SLUG>_API_KEY / _MODEL / _BASE_URL)
  3. YAML agents.<slug> (api_key / model / base_url in settings.yaml)
  4. YAML global        (global.api_key / global.model / global.base_url)
  5. Provider env var   (MINIPROT_PROVIDER → preset)
  6. Global env var     (DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL)
  7. Hard-coded default (deepseek-v4-pro @ api.deepseek.com)

The per-agent API isolation is critical for prompt-cache performance:
since each agent has a different system prompt, using separate API keys
(or model strings) isolates their cache namespaces, improving cache hit rates.

Usage:
    from miniprot_virtual_lab.config import resolve_config, load_yaml_config

    cfg = resolve_config(some_agent)
    print(cfg.model, cfg.base_url)
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent import Agent

# ── YAML support (optional but recommended) ───────────────────────

_yaml_available = False
try:
    import yaml  # pyyaml
    _yaml_available = True
except ImportError:
    pass

# ── Cached YAML config ────────────────────────────────────────────

_yaml_config: Optional[Dict[str, Any]] = None
_yaml_config_path: Optional[Path] = None


def _find_yaml_config() -> Optional[Path]:
    """Locate the YAML config file.

    Checks (in order):
      1. MINIPROT_CONFIG_PATH env var
      2. ./config/settings.yaml (relative to CWD)
      3. ../config/settings.yaml (relative to this file)
      4. ./config/settings.example.yaml (fallback — warns user to rename)
    """
    env_path = os.getenv("MINIPROT_CONFIG_PATH", "").strip()
    if env_path and os.path.isfile(env_path):
        return Path(env_path)

    cwd_candidate = Path.cwd() / "config" / "settings.yaml"
    if cwd_candidate.is_file():
        return cwd_candidate

    src_candidate = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
    if src_candidate.is_file():
        return src_candidate

    # Fallback: try example file — warn user to rename it
    for base in [Path.cwd(), Path(__file__).resolve().parents[2]]:
        example = base / "config" / "settings.example.yaml"
        if example.is_file():
            import sys as _sys
            print(
                "\n" + "!" * 65 + "\n"
                "  WARNING: Using settings.example.yaml as config.\n"
                "  Copy it to settings.yaml and edit your API keys:\n"
                f"    cp {example} {example.parent / 'settings.yaml'}\n"
                "  Then edit settings.yaml with your real API keys.\n"
                "!" * 65 + "\n",
                file=_sys.stderr,
            )
            return example

    return None


def load_yaml_config(reload: bool = False) -> Optional[Dict[str, Any]]:
    """Load the YAML configuration file.

    The result is cached; pass reload=True to force re-read.

    Returns:
        Parsed YAML dict, or None if no config file found or pyyaml not installed.
    """
    global _yaml_config, _yaml_config_path

    if _yaml_config is not None and not reload:
        return _yaml_config

    if not _yaml_available:
        return None

    path = _find_yaml_config()
    if path is None:
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            _yaml_config = yaml.safe_load(f) or {}
        _yaml_config_path = path
        return _yaml_config
    except Exception:
        return None


def get_yaml_config_path() -> Optional[Path]:
    """Return the path to the loaded YAML config, or None."""
    if _yaml_config_path is not None:
        return _yaml_config_path
    load_yaml_config()
    return _yaml_config_path


def _yaml_get(*keys: str, default: Any = None) -> Any:
    """Walk nested keys into the loaded YAML config.

    _yaml_get("agents", "docking_specialist", "model") →
      yaml["agents"]["docking_specialist"]["model"]
    """
    cfg = load_yaml_config()
    if cfg is None:
        return default
    node: Any = cfg
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return default
        if node is None:
            return default
    return node


# ── Provider presets ───────────────────────────────────────────────

@dataclass
class ProviderPreset:
    """Pre-configured AI provider settings."""
    name: str
    api_key_env: str
    model: str
    base_url: str
    description: str = ""


PROVIDER_PRESETS: Dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        description="DeepSeek official API (V4 Pro)",
    ),
    "deepseek-v3": ProviderPreset(
        name="deepseek-v3",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        description="DeepSeek official API (V3/Chat)",
    ),
    "sjtu": ProviderPreset(
        name="sjtu",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
        base_url="https://models.sjtu.edu.cn/api/v1",
        description="SJTU models server (DeepSeek-compatible)",
    ),
    "openai": ProviderPreset(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        model="gpt-5.2",
        base_url="https://api.openai.com/v1",
        description="OpenAI official API (GPT-5.2)",
    ),
    "openai-mini": ProviderPreset(
        name="openai-mini",
        api_key_env="OPENAI_API_KEY",
        model="gpt-5-mini",
        base_url="https://api.openai.com/v1",
        description="OpenAI official API (GPT-5 Mini, cheaper)",
    ),
    "custom": ProviderPreset(
        name="custom",
        api_key_env="MINIPROT_CUSTOM_API_KEY",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        description="Custom OpenAI-compatible endpoint",
    ),
}

DEFAULT_PROVIDER = "deepseek"

# ── Agent slug helper ──────────────────────────────────────────────

def _agent_slug(title: str) -> str:
    """Convert agent title to env-var / YAML-safe slug.

    "Protein Search Specialist" → "protein_search_specialist"
    "Principal Investigator"   → "principal_investigator"
    """
    return re.sub(r"[^a-z0-9]", "_", title.lower().replace(" ", "_")).strip("_")


def _agent_env_slug(title: str) -> str:
    """Convert agent title to UPPER env-var slug.

    "Protein Search Specialist" → "PROTEIN_SEARCH_SPECIALIST"
    """
    return re.sub(r"[^A-Z0-9]", "_", title.upper().replace(" ", "_"))


# ── Effective config resolver ──────────────────────────────────────

@dataclass
class ResolvedConfig:
    """Resolved API config for a specific agent."""
    api_key: str
    model: str
    base_url: str
    agent_slug: str
    provider_name: str
    extra_body: Optional[Dict[str, Any]] = None
    temperature: Optional[float] = None
    source: str = ""  # Which layer provided the config (for debugging)


def resolve_config(
    agent: Optional[Agent] = None,
    provider: Optional[str] = None,
) -> ResolvedConfig:
    """Resolve the effective API configuration for an agent.

    Resolution order (first non-empty value wins):
      1. Agent attribute (set in Python code)
      2. Agent-specific env var (MINIPROT_<SLUG>_API_KEY etc.)
      3. YAML config — agents.<slug> section
      4. YAML config — global section
      5. Provider preset (MINIPROT_PROVIDER env or YAML global.provider)
      6. Global env var (DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL)
      7. Hard-coded default

    Args:
        agent: The Agent instance, or None for global-only config.
        provider: Provider preset name override.

    Returns:
        ResolvedConfig with api_key, model, base_url, temperature.
    """
    slug = _agent_slug(agent.title) if agent else "GLOBAL"
    env_slug = _agent_env_slug(agent.title) if agent else "GLOBAL"

    # ── Determine provider preset ───────────────────────────────
    provider_name = (
        provider
        or os.getenv("MINIPROT_PROVIDER", "").strip()
        or _yaml_get("global", "provider", default="").strip()
        or DEFAULT_PROVIDER
    )
    preset = PROVIDER_PRESETS.get(provider_name, PROVIDER_PRESETS[DEFAULT_PROVIDER])

    # ── Helper: resolve a single value through the chain ────────
    def _resolve(
        yaml_global_key: str,
        yaml_agent_key: str,
        env_global_key: str,
        env_agent_key: str,
        preset_value: str,
    ) -> tuple[Optional[str], str]:
        """Walk the resolution chain for one config value.

        Returns (resolved_value, source_description).
        """
        val: Optional[str] = None

        # 1. Agent attribute (Python code)
        if agent is not None:
            attr_val = getattr(agent, yaml_agent_key, None)
            if attr_val:
                return (attr_val, "agent_attribute")

        # 2. Agent-specific env var
        val = os.getenv(env_agent_key, "").strip() or None
        if val:
            return (val, f"env:{env_agent_key}")

        # 3. YAML agents.<slug>
        if slug != "GLOBAL":
            val = _yaml_get("agents", slug, yaml_agent_key)
            if isinstance(val, str) and val.strip():
                return (val.strip(), f"yaml:agents.{slug}.{yaml_agent_key}")

        # 4. YAML global
        val = _yaml_get("global", yaml_global_key)
        if isinstance(val, str) and val.strip():
            return (val.strip(), f"yaml:global.{yaml_global_key}")

        # 5 & 6. Env vars (provider-specific + global)
        val = os.getenv(env_global_key, "").strip() or None
        if val:
            return (val, f"env:{env_global_key}")

        # For api_key, also try the preset's env var
        if yaml_global_key == "api_key" and preset.api_key_env != env_global_key:
            val = os.getenv(preset.api_key_env, "").strip() or None
            if val:
                return (val, f"env:{preset.api_key_env}")

        # For api_key, also try fallback env vars
        if yaml_global_key == "api_key":
            for fallback in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
                if fallback != env_global_key and fallback != preset.api_key_env:
                    val = os.getenv(fallback, "").strip() or None
                    if val:
                        return (val, f"env:{fallback}")

        # 7. Preset default
        return (preset_value, "preset_default")

    # ── Resolve each field ──────────────────────────────────────
    api_key, api_key_src = _resolve(
        "api_key", "api_key",
        "DEEPSEEK_API_KEY", f"MINIPROT_{env_slug}_API_KEY",
        "",  # No preset default for api_key
    )
    # If still no api_key after all that, try the preset env as a last resort
    if not api_key:
        api_key = os.getenv(preset.api_key_env, "").strip() or None
        if api_key:
            api_key_src = f"env:{preset.api_key_env}"
    if not api_key:
        raise RuntimeError(
            f"No API key found for agent '{slug}'.\n"
            f"  Configure it in config/settings.yaml (global.api_key or "
            f"agents.{slug}.api_key)\n"
            f"  Or set the {preset.api_key_env} environment variable.\n"
            f"  Run 'python run.py --providers' to see available providers."
        )

    model, model_src = _resolve(
        "model", "model",
        "DEEPSEEK_MODEL", f"MINIPROT_{env_slug}_MODEL",
        preset.model,
    )

    base_url, base_url_src = _resolve(
        "base_url", "base_url",
        "DEEPSEEK_BASE_URL", f"MINIPROT_{env_slug}_BASE_URL",
        preset.base_url,
    )

    # Normalize base_url: strip trailing junk, ensure /v1 path is present
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    # OpenAI SDK needs the /v1 path. Only add if not already present anywhere.
    if "/v1" not in base_url and "api.openai.com" not in base_url:
        base_url = base_url + "/v1"

    # ── Resolve extra_body ──────────────────────────────────────
    extra_body: Optional[Dict[str, Any]] = None
    if agent and agent.extra_body:
        extra_body = agent.extra_body
    elif os.getenv("DEEPSEEK_THINKING_ENABLED", "").strip().lower() in ("true", "1", "yes"):
        extra_body = {"thinking": {"type": "enabled"}}
    else:
        yaml_extra = _yaml_get("global", "extra_body")
        if isinstance(yaml_extra, dict) and yaml_extra:
            extra_body = yaml_extra

    # ── Resolve temperature ─────────────────────────────────────
    temperature: Optional[float] = None
    if agent and agent.temperature is not None:
        temperature = agent.temperature
    else:
        yaml_temp = _yaml_get("agents", slug, "temperature") if slug != "GLOBAL" else None
        if yaml_temp is None and agent:
            # Try meeting-specific default
            pass
        if isinstance(yaml_temp, (int, float)):
            temperature = float(yaml_temp)

    return ResolvedConfig(
        api_key=api_key,
        model=model,
        base_url=base_url,
        agent_slug=slug,
        provider_name=provider_name,
        extra_body=extra_body,
        temperature=temperature,
        source=f"model={model_src}, key={api_key_src}, url={base_url_src}",
    )


# ── Public helpers ─────────────────────────────────────────────────

def list_providers() -> str:
    """Human-readable list of available provider presets."""
    lines = ["Available provider presets (set in config/settings.yaml → global.provider):"]
    for name, preset in PROVIDER_PRESETS.items():
        lines.append(
            f"  {name:<18} → {preset.model:<22} @ {preset.base_url}"
            f"  ({preset.description})"
        )
    yaml_path = get_yaml_config_path()
    if yaml_path:
        provider = _yaml_get("global", "provider", default=DEFAULT_PROVIDER)
        lines.append(f"\n  Current from YAML: {provider}  ({yaml_path})")
    else:
        provider = os.getenv("MINIPROT_PROVIDER", "").strip() or DEFAULT_PROVIDER
        lines.append(f"\n  Current (no YAML found): {provider}")
        lines.append("  Tip: copy config/settings.yaml and edit it for persistent config.")
    return "\n".join(lines)


def print_config_summary(agents: List[Agent]) -> None:
    """Print a summary of resolved configs for all agents."""
    print("\n" + "=" * 70)
    print("  API Configuration Summary")
    print("=" * 70)

    yaml_path = get_yaml_config_path()
    provider = (
        os.getenv("MINIPROT_PROVIDER", "").strip()
        or _yaml_get("global", "provider", default="").strip()
        or DEFAULT_PROVIDER
    )
    print(f"  Config source: {yaml_path if yaml_path else 'env vars + defaults'}")
    print(f"  Provider:      {provider}")
    print()

    global_cfg = resolve_config(agent=None, provider=provider)
    print(f"  {'GLOBAL':<30} {global_cfg.model:<22} @ {global_cfg.base_url}")

    for agent in agents:
        cfg = resolve_config(agent=agent, provider=provider)
        marker = ""
        if cfg.api_key != global_cfg.api_key:
            marker = " [ISOLATED KEY]"
        if cfg.model != global_cfg.model:
            marker += f" (model: {cfg.model})"
        if cfg.base_url != global_cfg.base_url:
            marker += f" (url: {cfg.base_url})"
        temp_str = f" T={cfg.temperature}" if cfg.temperature is not None else ""
        print(f"  {cfg.agent_slug:<30} {cfg.model:<22} @ {cfg.base_url}{marker}{temp_str}")

    print("=" * 70)


def reload_config() -> None:
    """Force reload of the YAML config file (useful after editing)."""
    load_yaml_config(reload=True)
