"""
Utility functions for MiniProt Virtual Lab.

Token counting, cost estimation, file I/O, and discussion management.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Try tiktoken; fall back to character-based approximation.
try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
    _ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _TIKTOKEN_AVAILABLE = False


# ── Token counting ─────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """Count tokens in a string (uses tiktoken if available, else char/4 estimate)."""
    if not text:
        return 0
    if _TIKTOKEN_AVAILABLE:
        return len(_ENCODING.encode(text))
    # Rough approximation: ~4 chars per token for English text
    return max(1, len(text) // 4)


def count_discussion_tokens(discussion: List[Dict[str, str]]) -> Dict[str, int]:
    """Count input/output tokens across a full discussion.

    Returns dict with 'input', 'output', 'max' keys.
    """
    token_counts: Dict[str, int] = {"input": 0, "output": 0, "max": 0}
    for index, turn in enumerate(discussion):
        if turn["agent"] != "User":
            # Output = this agent's response
            output_tokens = count_tokens(turn["message"])
            token_counts["output"] += output_tokens
            # Input = all prior messages
            prior_text = " ".join(t["message"] for t in discussion[:index])
            input_tokens = count_tokens(prior_text)
            token_counts["input"] += input_tokens
            token_counts["max"] = max(
                token_counts["max"], input_tokens + output_tokens
            )
    return token_counts


# ── Cost estimation ────────────────────────────────────────────────

def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    price_dicts: Tuple[Dict[str, float], Dict[str, float]],
) -> float:
    """Compute API cost given token counts and pricing tables.

    Args:
        model: Model name string.
        input_tokens: Number of input/prompt tokens.
        output_tokens: Number of output/completion tokens.
        price_dicts: (input_prices, output_prices) dicts mapping model → $/token.

    Returns:
        Estimated cost in USD.
    """
    input_prices, output_prices = price_dicts

    def _find_key(name: str, d: Dict[str, float]) -> Optional[str]:
        if name in d:
            return name
        # Longest prefix match
        matches = [k for k in d if name.startswith(k)]
        return max(matches, key=len) if matches else None

    in_key = _find_key(model, input_prices)
    out_key = _find_key(model, output_prices)

    if in_key is None or out_key is None:
        return 0.0  # Unknown model — can't estimate

    return (
        input_tokens * input_prices[in_key]
        + output_tokens * output_prices[out_key]
    )


def print_cost_and_time(
    token_counts: Dict[str, int],
    model: str,
    elapsed_time: float,
    input_prices: Dict[str, float],
    output_prices: Dict[str, float],
) -> None:
    """Print token usage, cost, and elapsed time for a meeting."""
    total_input = token_counts.get("input", 0) + token_counts.get("tool", 0)
    total_output = token_counts.get("output", 0)

    print(f"  Input tokens:   {total_input:>10,}")
    print(f"  Output tokens:  {total_output:>10,}")
    print(f"  Max context:    {token_counts.get('max', 0):>10,}")

    cost = compute_cost(model, total_input, total_output,
                        (input_prices, output_prices))
    if cost > 0:
        print(f"  Est. cost:      ${cost:>10.4f}")

    mins = int(elapsed_time // 60)
    secs = int(elapsed_time % 60)
    print(f"  Elapsed:        {mins:>7}:{secs:02d}")


# ── File I/O ───────────────────────────────────────────────────────

def save_meeting(
    save_dir: Path,
    save_name: str,
    discussion: List[Dict[str, str]],
) -> None:
    """Save a meeting discussion to JSON and Markdown files.

    Args:
        save_dir: Directory to save files.
        save_name: Base filename (without extension).
        discussion: List of {agent, message} dicts.
    """
    save_dir.mkdir(parents=True, exist_ok=True)

    # JSON (machine-readable, full fidelity)
    json_path = save_dir / f"{save_name}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(discussion, f, indent=2, ensure_ascii=False)

    # Markdown (human-readable)
    md_path = save_dir / f"{save_name}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Meeting: {save_name}\n\n")
        f.write(f"*Saved: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n\n---\n\n")
        for turn in discussion:
            f.write(f"## {turn['agent']}\n\n{turn['message']}\n\n---\n\n")

    print(f"  Meeting saved → {json_path}")


def load_summaries(discussion_paths: List[Path]) -> Tuple[str, ...]:
    """Load summaries (last message) from a list of discussion JSON files.

    Args:
        discussion_paths: Paths to discussion_*.json files.

    Returns:
        Tuple of summary strings.
    """
    summaries: List[str] = []
    for path in discussion_paths:
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            discussion = json.load(f)
        if discussion:
            summaries.append(discussion[-1]["message"])
    return tuple(summaries)


def get_summary(discussion: List[Dict[str, str]]) -> str:
    """Extract the summary (last message) from a discussion."""
    if not discussion:
        return ""
    return discussion[-1]["message"]


# ── Timestamp helpers ──────────────────────────────────────────────

def timestamp_str() -> str:
    """Return a short timestamp string for run naming."""
    return time.strftime("%Y%m%d_%H%M%S")


# ── Environment helpers ────────────────────────────────────────────

def load_env() -> Dict[str, str]:
    """Load environment variables. Uses python-dotenv if available."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL",
                              "https://models.sjtu.edu.cn/api/v1").strip(),
    }


def check_api_key() -> str:
    """Check that DEEPSEEK_API_KEY is set; return it or exit."""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("ERROR: DEEPSEEK_API_KEY not set in environment or .env file.")
        print("  export DEEPSEEK_API_KEY=your_key_here")
        exit(1)
    return key
