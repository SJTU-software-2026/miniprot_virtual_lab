"""
Meeting orchestration for MiniProt Virtual Lab.

Combines virtual-lab's round-based meeting structure with MiniProt's
bioinformatics tools. Supports two meeting types:

  - team:     PI + specialists discuss and plan research
  - individual: Specialist executes tools, Critic reviews output

Also provides load_meeting_context() to let new meetings read prior
meeting records — agents can pick up where they left off.

Each agent can use a different API key/model/base_url — this isolates
prompt-cache namespaces and improves cache hit rates since each agent
has a different system prompt.

Usage:
    from miniprot_virtual_lab.run_meeting import run_meeting

    run_meeting(
        meeting_type="team",
        agenda="Design a pipeline to find tryptophan-hydroxylase homologs...",
        team_lead=PI,
        team_members=(search_spec, struct_spec, dock_spec),
        save_dir=Path("./meetings"),
        num_rounds=3,
    )
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from openai import OpenAI, APIConnectionError, APITimeoutError, InternalServerError

from .agent import Agent
from .config import resolve_config, ResolvedConfig

logger = logging.getLogger(__name__)

# ── Network retry config ────────────────────────────────────────────

MAX_RETRIES = 10
RETRY_DELAY_S = 5       # seconds between retries
RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError, InternalServerError,
                    ConnectionError, TimeoutError, OSError)

# ── Per-agent OpenAI client cache ───────────────────────────────────

_CLIENT_CACHE: Dict[Tuple[int, str], OpenAI] = {}


def _get_client_for(config: ResolvedConfig) -> OpenAI:
    """Get or create an OpenAI-compatible client for a resolved config.

    Clients are cached by (api_key_hash, base_url) to avoid recreating
    connections for agents that share the same API endpoint.
    """
    cache_key = (hash(config.api_key), config.base_url)
    if cache_key not in _CLIENT_CACHE:
        _CLIENT_CACHE[cache_key] = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
    return _CLIENT_CACHE[cache_key]


def _call_llm(
    config: ResolvedConfig,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2048,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Tuple[str, int, int, int]:
    """Call the LLM API with retry on network failure.

    Retries up to MAX_RETRIES times with RETRY_DELAY_S between attempts.
    On transient errors (connection, timeout, server), waits and retries.
    On permanent errors (auth, bad request), fails immediately.

    Returns:
        (response_text, input_token_count, output_token_count, latency_ms)

    Raises:
        NetworkExhaustedError: After MAX_RETRIES consecutive network failures.
    """
    last_error = None
    client = _get_client_for(config)

    kwargs: Dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    effective_extra = extra_body or config.extra_body
    if effective_extra:
        kwargs["extra_body"] = effective_extra

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            latency_ms = int((time.perf_counter() - t0) * 1000)

            content = response.choices[0].message.content or ""
            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            if attempt > 1:
                logger.info("API call succeeded on attempt %d after %d retries",
                            attempt, attempt - 1)
            return content, input_tokens, output_tokens, latency_ms

        except RETRYABLE_ERRORS as e:
            last_error = e
            if attempt < MAX_RETRIES:
                logger.warning(
                    "API call failed (attempt %d/%d): %s — retrying in %ds...",
                    attempt, MAX_RETRIES, _error_summary(e), RETRY_DELAY_S,
                )
                time.sleep(RETRY_DELAY_S)
            else:
                logger.error(
                    "API call exhausted all %d retries: %s",
                    MAX_RETRIES, _error_summary(e),
                )

        except Exception as e:
            # Non-retryable error (auth, bad request, etc.) — fail immediately
            raise RuntimeError(
                f"API call failed with non-retryable error: {_error_summary(e)}"
            ) from e

    # All retries exhausted
    raise NetworkExhaustedError(
        f"Network unreachable after {MAX_RETRIES} attempts "
        f"(last error: {_error_summary(last_error)})"
    )


def _error_summary(e: BaseException) -> str:
    """Short error description for logging."""
    msg = str(e).replace("\n", " ")
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return f"{type(e).__name__}: {msg}"


class NetworkExhaustedError(RuntimeError):
    """Raised when all API retries are exhausted due to network issues."""
    pass


# ── Tool execution helpers ─────────────────────────────────────────

_TOOL_CALL_RE = re.compile(
    r'\{\s*"action"\s*:\s*"run_tool"\s*,'
    r'\s*"tool"\s*:\s*"(\w+)"\s*,'
    r'\s*"args"\s*:\s*(\{.*?\})\s*\}',
    re.DOTALL,
)


def _parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Extract a tool-call action block from agent text."""
    for pattern in [
        r'```json\s*(\{.*?\})\s*```',
        r'```\s*(\{.*?\})\s*```',
    ]:
        for match in re.finditer(pattern, text, re.DOTALL):
            try:
                obj = json.loads(match.group(1))
                if obj.get("action") == "run_tool" and "tool" in obj:
                    return (obj["tool"], obj.get("args", {}))
            except json.JSONDecodeError:
                continue

    match = _TOOL_CALL_RE.search(text)
    if match:
        try:
            return (match.group(1), json.loads(match.group(2)))
        except json.JSONDecodeError:
            return None
    return None


def _execute_tool_loop(
    agent: Agent,
    agent_config: ResolvedConfig,
    messages: List[Dict[str, str]],
    bridge,
    run_logger=None,
    max_tool_calls: int = 15,
    temperature: float = 0.2,
) -> Tuple[str, List[Dict[str, Any]], int]:
    """Agent ↔ tool execution loop.

    Returns (final_response, tool_log, total_tool_tokens).
    """
    tool_log: List[Dict[str, Any]] = []
    total_tool_tokens = 0

    tool_summary = bridge.tools_summary_for_agent(agent)
    system_content = (
        f"{agent.prompt}\n\n"
        f"You are now in an individual work session. You have access "
        f"to the following MiniProt bioinformatics tools:\n\n"
        f"{tool_summary}\n\n"
        f"To use a tool, output a JSON action block:\n"
        f'```json\n{{"action": "run_tool", "tool": "<name>", '
        f'"args": {{"param": "value", ...}}}}\n```\n\n'
        f"The tool will execute and you will see its output. Then you "
        f"can decide whether to call another tool or provide your final "
        f"answer. Call tools one at a time. When you are done, provide "
        f"a complete natural-language summary of what was done and what "
        f"files were produced. Only report file paths that the tools "
        f"actually returned — never invent paths."
    )

    for iteration in range(max_tool_calls):
        api_messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]
        api_messages.extend(messages)

        content, in_tok, out_tok, lat_ms = _call_llm(
            agent_config, api_messages, temperature=temperature, max_tokens=2048,
        )

        if run_logger:
            run_logger.log_api_call(
                agent=agent.title, model=agent_config.model,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=lat_ms, purpose="tool_loop",
            )

        parsed = _parse_tool_call(content)
        if parsed is None:
            messages.append({"role": "assistant", "content": content})
            return content, tool_log, total_tool_tokens

        tool_name, args = parsed
        logger.info("Agent %s calling tool: %s", agent.title, tool_name)

        messages.append({"role": "assistant", "content": content})

        result = bridge.run(tool_name, **args)
        tool_log.append({
            "tool": tool_name,
            "args": args,
            "success": result["success"],
            "elapsed_ms": result.get("elapsed_ms", 0),
        })

        if run_logger:
            run_logger.log_tool_call(
                agent=agent.title, tool=tool_name, args=args,
                success=result["success"],
                elapsed_ms=result.get("elapsed_ms", 0),
                error=result.get("error", ""),
            )

        if result["success"]:
            result_text = json.dumps(result["result"], indent=2, ensure_ascii=False)
            if len(result_text) > 4000:
                result_text = result_text[:4000] + "\n... (truncated)"
            tool_msg = (
                f"[Tool Result: {tool_name} — SUCCESS in "
                f"{result.get('elapsed_ms', '?')}ms]\n\n{result_text}"
            )
        else:
            tool_msg = (
                f"[Tool Result: {tool_name} — FAILED]\n\n"
                f"Error: {result.get('error', 'Unknown error')}"
            )
        total_tool_tokens += 500  # rough estimate
        messages.append({"role": "user", "content": tool_msg})

    # Max iterations
    final_msg = (
        f"[System: Maximum tool calls ({max_tool_calls}) reached. "
        f"Please provide your final answer now.]"
    )
    messages.append({"role": "user", "content": final_msg})
    content, in_tok, out_tok, lat_ms = _call_llm(
        agent_config,
        [{"role": "system", "content": system_content}] + messages,
        temperature=temperature, max_tokens=2048,
    )
    if run_logger:
        run_logger.log_api_call(
            agent=agent.title, model=agent_config.model,
            input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=lat_ms, purpose="tool_loop_final",
        )
    messages.append({"role": "assistant", "content": content})
    return content, tool_log, total_tool_tokens


# ── Checkpoint save / resume ────────────────────────────────────────

def _save_checkpoint(
    save_dir: Path,
    save_name: str,
    meeting_type: str,
    discussion: List[Dict[str, str]],
    messages: List[Dict[str, str]],
    num_rounds: int,
    agenda: str,
) -> Path:
    """Save the current meeting state so it can be resumed after network recovery.

    Returns the path to the checkpoint file.
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    cp_path = save_dir / f"{save_name}.checkpoint.json"

    cp = {
        "meeting_type": meeting_type,
        "save_name": save_name,
        "agenda": agenda,
        "num_rounds": num_rounds,
        "discussion": discussion,
        "messages": messages,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(cp_path, "w", encoding="utf-8") as f:
        json.dump(cp, f, indent=2, ensure_ascii=False)

    logger.info("Checkpoint saved: %s", cp_path)
    return cp_path


def load_checkpoint(checkpoint_path: str) -> Optional[dict]:
    """Load a saved meeting checkpoint.

    Returns dict with meeting_type, save_name, agenda, num_rounds,
    discussion, messages, or None if the file doesn't exist.
    """
    p = Path(checkpoint_path)
    if not p.exists():
        return None

    with open(p, "r", encoding="utf-8") as f:
        cp = json.load(f)

    required = {"meeting_type", "discussion", "messages"}
    if not required.issubset(cp.keys()):
        logger.warning("Checkpoint %s missing required keys", checkpoint_path)
        return None

    logger.info("Checkpoint loaded: %s (%d discussion turns, %d messages)",
                checkpoint_path, len(cp["discussion"]), len(cp["messages"]))
    return cp


# ── Main meeting function ──────────────────────────────────────────

def run_meeting(
    meeting_type: Literal["team", "individual"],
    agenda: str,
    save_dir: Path,
    save_name: str = "discussion",
    *,
    team_lead: Optional[Agent] = None,
    team_members: Optional[Tuple[Agent, ...]] = None,
    team_member: Optional[Agent] = None,
    agenda_questions: Tuple[str, ...] = (),
    agenda_rules: Tuple[str, ...] = (),
    summaries: Tuple[str, ...] = (),
    contexts: Tuple[str, ...] = (),
    num_rounds: int = 3,
    temperature: float = 0.2,
    enable_tools: bool = True,
    return_summary: bool = False,
    run_logger=None,
    provider: Optional[str] = None,
) -> Optional[str]:
    """Run a team or individual meeting with LLM agents.

    Each agent's API config is resolved independently, enabling
    per-agent API keys/models for cache isolation.

    Args:
        meeting_type: 'team' or 'individual'.
        agenda: The meeting agenda (natural language).
        save_dir: Directory to save discussion files.
        save_name: Base name for saved files.
        team_lead: PI agent (team meetings).
        team_members: Specialist agents (team meetings).
        team_member: Single specialist (individual meetings).
        agenda_questions: Specific questions to answer.
        agenda_rules: Rules/constraints.
        summaries: Prior meeting summaries.
        contexts: Additional context.
        num_rounds: Discussion/critique rounds.
        temperature: Default LLM temperature.
        enable_tools: Allow MiniProt tool execution.
        return_summary: Return final summary string.
        run_logger: Optional RunLogger for structured logging.
        provider: Provider preset name override.

    Returns:
        Final summary string if return_summary=True, else None.
    """
    from .prompts import (
        SCIENTIFIC_CRITIC,
        team_meeting_start_prompt,
        team_meeting_team_lead_initial_prompt,
        team_meeting_team_member_prompt,
        team_meeting_team_lead_intermediate_prompt,
        team_meeting_team_lead_final_prompt,
        individual_meeting_start_prompt,
        individual_meeting_critic_prompt,
        individual_meeting_agent_revise_prompt,
    )

    # ── Validate ───────────────────────────────────────────────
    if meeting_type == "team":
        if team_lead is None or team_members is None or len(team_members) == 0:
            raise ValueError("Team meeting requires team_lead and team_members")
        if team_lead in team_members:
            raise ValueError("Team lead must not also be a team member")
    elif meeting_type == "individual":
        if team_member is None:
            raise ValueError("Individual meeting requires team_member")
    else:
        raise ValueError(f"Invalid meeting_type: {meeting_type}")

    # ── Resolve agent API configs ──────────────────────────────
    provider_name = provider or os.getenv("MINIPROT_PROVIDER", "").strip() or None

    all_agents: List[Agent] = []
    if meeting_type == "team":
        assert team_lead is not None and team_members is not None
        all_agents = [team_lead] + list(team_members)
    else:
        assert team_member is not None
        all_agents = [team_member]

    # Build agent → config map
    agent_configs: Dict[str, ResolvedConfig] = {}
    for ag in all_agents:
        agent_configs[ag.title] = resolve_config(ag, provider=provider_name)
    # Critic always resolved too
    critic_config = resolve_config(SCIENTIFIC_CRITIC, provider=provider_name)

    # Log config if logger provided
    if run_logger:
        for ag in all_agents:
            cfg = agent_configs[ag.title]
            run_logger._emit("agent_config", {
                "agent": ag.title,
                "model": cfg.model,
                "base_url": cfg.base_url,
                "provider": cfg.provider_name,
                "has_own_key": bool(ag.api_key),
            })

    # ── Setup ─────────────────────────────────────────────────
    start_time = time.time()

    # Tool bridge
    bridge = None
    if enable_tools and meeting_type == "individual":
        try:
            from .tools import ToolBridge
            bridge = ToolBridge()
            logger.info("ToolBridge initialized")
        except ImportError as e:
            logger.warning("ToolBridge unavailable: %s", e)
            if run_logger:
                run_logger.log_warning("setup", f"ToolBridge unavailable: {e}")

    discussion: List[Dict[str, str]] = []
    messages: List[Dict[str, str]] = []
    tool_token_count = 0

    if run_logger:
        agent_titles = [a.title for a in all_agents]
        run_logger.log_meeting_start(
            meeting_type=meeting_type,
            agenda=agenda,
            save_name=save_name,
            agents=agent_titles,
        )

    # ── Team meeting ──────────────────────────────────────────
    if meeting_type == "team":
        assert team_lead is not None and team_members is not None
        team: List[Agent] = [team_lead] + list(team_members)

        start_content = team_meeting_start_prompt(
            team_lead=team_lead,
            team_members=team_members,
            agenda=agenda,
            agenda_questions=agenda_questions,
            agenda_rules=agenda_rules,
            summaries=summaries,
            contexts=contexts,
            num_rounds=num_rounds,
        )
        messages.append({"role": "user", "content": start_content})
        discussion.append({"agent": "User", "message": start_content})

        for round_idx in range(num_rounds + 1):
            round_num = round_idx + 1

            for agent in team:
                if agent == team_lead:
                    if round_idx == 0:
                        prompt = team_meeting_team_lead_initial_prompt(team_lead)
                    elif round_idx == num_rounds:
                        prompt = team_meeting_team_lead_final_prompt(
                            team_lead=team_lead,
                            agenda=agenda,
                            agenda_questions=agenda_questions,
                            agenda_rules=agenda_rules,
                        )
                    else:
                        prompt = team_meeting_team_lead_intermediate_prompt(
                            team_lead=team_lead,
                            round_num=round_num - 1,
                            num_rounds=num_rounds,
                        )
                else:
                    prompt = team_meeting_team_member_prompt(
                        team_member=agent,
                        round_num=round_num,
                        num_rounds=num_rounds,
                    )

                messages.append({"role": "user", "content": prompt})
                discussion.append({"agent": "User", "message": prompt})

                agent_messages = [agent.system_message] + messages
                cfg = agent_configs[agent.title]
                temp = agent.temperature if agent.temperature is not None else temperature

                content, in_tok, out_tok, lat_ms = _call_llm(
                    cfg, agent_messages, temperature=temp, max_tokens=2048,
                )

                if run_logger:
                    run_logger.log_api_call(
                        agent=agent.title, model=cfg.model,
                        input_tokens=in_tok, output_tokens=out_tok,
                        latency_ms=lat_ms,
                        purpose=f"team_round{round_num}",
                    )

                messages.append({"role": "assistant", "content": content})
                discussion.append({"agent": agent.title, "message": content})

                if run_logger:
                    run_logger.log_agent_response(
                        agent=agent.title, content=content,
                        meeting_name=save_name, round_num=round_num,
                        purpose="team_discussion",
                    )

                if round_idx == num_rounds:
                    break

    # ── Individual meeting ────────────────────────────────────
    else:
        assert team_member is not None
        agent = team_member
        cfg = agent_configs[agent.title]
        temp = agent.temperature if agent.temperature is not None else temperature

        start_content = individual_meeting_start_prompt(
            team_member=agent,
            agenda=agenda,
            agenda_questions=agenda_questions,
            agenda_rules=agenda_rules,
            summaries=summaries,
            contexts=contexts,
        )
        messages.append({"role": "user", "content": start_content})
        discussion.append({"agent": "User", "message": start_content})

        if bridge is not None:
            response_content, tool_log, tt_tokens = _execute_tool_loop(
                agent=agent, agent_config=cfg,
                messages=messages, bridge=bridge,
                run_logger=run_logger, temperature=temp,
            )
            tool_token_count += tt_tokens
        else:
            agent_messages = [agent.system_message] + messages
            content, in_tok, out_tok, lat_ms = _call_llm(
                cfg, agent_messages, temperature=temp, max_tokens=2048,
            )
            if run_logger:
                run_logger.log_api_call(
                    agent=agent.title, model=cfg.model,
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=lat_ms, purpose="individual_task",
                )
            response_content = content
            messages.append({"role": "assistant", "content": response_content})

        discussion.append({"agent": agent.title, "message": response_content})

        if run_logger:
            run_logger.log_agent_response(
                agent=agent.title, content=response_content,
                meeting_name=save_name, round_num=0,
                purpose="individual_task",
            )

        # ── Critic review rounds ─────────────────────────────
        critic = SCIENTIFIC_CRITIC
        critic_cfg = critic_config

        for round_num in range(1, num_rounds + 1):
            critic_prompt = individual_meeting_critic_prompt(
                critic=critic, agent=agent
            )
            messages.append({"role": "user", "content": critic_prompt})
            discussion.append({"agent": "User", "message": critic_prompt})

            critic_messages = [critic.system_message] + messages
            critic_temp = critic.temperature if critic.temperature is not None else temperature
            critic_content, in_tok, out_tok, lat_ms = _call_llm(
                critic_cfg, critic_messages, temperature=critic_temp, max_tokens=2048,
            )
            if run_logger:
                run_logger.log_api_call(
                    agent=critic.title, model=critic_cfg.model,
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=lat_ms, purpose=f"critic_round{round_num}",
                )

            messages.append({"role": "assistant", "content": critic_content})
            discussion.append({"agent": critic.title, "message": critic_content})

            if run_logger:
                run_logger.log_agent_response(
                    agent=critic.title, content=critic_content,
                    meeting_name=save_name, round_num=round_num,
                    purpose="critic_review",
                )

            # Check critic satisfaction
            satisfied = [
                "no issues", "correct", "looks good", "well done",
                "no errors", "all good", "satisfied", "complete",
                "没有问题", "正确", "没问题", "完成得很好",
            ]
            if any(ind in critic_content.lower() for ind in satisfied):
                if not re.search(
                    r'\b(but|however|although|except|除了|但是|然而|不过)\b',
                    critic_content, re.IGNORECASE,
                ):
                    logger.info("Critic satisfied after round %d", round_num)
                    if run_logger:
                        run_logger._emit("critic_satisfied", {"round": round_num})
                    break

            # Agent revises
            revise_prompt = individual_meeting_agent_revise_prompt(
                critic=critic, agent=agent
            )
            messages.append({"role": "user", "content": revise_prompt})
            discussion.append({"agent": "User", "message": revise_prompt})

            if bridge is not None:
                revised, tool_log2, tt_tokens2 = _execute_tool_loop(
                    agent=agent, agent_config=cfg,
                    messages=messages, bridge=bridge,
                    run_logger=run_logger, temperature=temp,
                )
                tool_token_count += tt_tokens2
            else:
                agent_messages = [agent.system_message] + messages
                revised, in_tok, out_tok, lat_ms = _call_llm(
                    cfg, agent_messages, temperature=temp, max_tokens=2048,
                )
                if run_logger:
                    run_logger.log_api_call(
                        agent=agent.title, model=cfg.model,
                        input_tokens=in_tok, output_tokens=out_tok,
                        latency_ms=lat_ms, purpose=f"revise_round{round_num}",
                    )

            messages.append({"role": "assistant", "content": revised})
            discussion.append({"agent": agent.title, "message": revised})

            if run_logger:
                run_logger.log_agent_response(
                    agent=agent.title, content=revised,
                    meeting_name=save_name, round_num=round_num,
                    purpose="agent_revision",
                )

    # ── Execute meeting body (with network error recovery) ──────
    try:
        _run_meeting_body(
            meeting_type, agenda, save_dir, save_name,
            team_lead, team_members, team_member,
            agenda_questions, agenda_rules, summaries, contexts,
            num_rounds, temperature, enable_tools,
            run_logger, provider_name,
            discussion, messages, agent_configs,
        )
    except NetworkExhaustedError as e:
        # Save checkpoint so user can resume after network recovery
        cp_path = _save_checkpoint(
            save_dir, save_name, meeting_type,
            discussion, messages, num_rounds, agenda,
        )
        elapsed_s = time.time() - start_time
        print(f"\n{'!' * 60}")
        print(f"  NETWORK ERROR — meeting interrupted after {elapsed_s:.0f}s")
        print(f"  {e}")
        print()
        print(f"  Your progress has been saved to:")
        print(f"    {cp_path}")
        print()
        print(f"  To RESUME after network recovery:")
        print(f"    python run.py --resume {cp_path}")
        print()
        print(f"  Or in interactive mode:")
        print(f"    Virtual Lab> /resume {cp_path}")
        print(f"{'!' * 60}\n")

        if run_logger:
            run_logger.log_error("network", str(e))
            run_logger._emit("checkpoint_saved", {"path": str(cp_path)})
            run_logger.finalize()

        # Also save whatever discussion we have as partial meeting record
        from .utils import get_summary
        partial_name = f"{save_name}_INTERRUPTED"
        try:
            from .utils import save_meeting
            save_meeting(save_dir, partial_name, discussion)
        except Exception:
            pass

        if return_summary:
            return get_summary(discussion) if discussion else "Meeting interrupted by network error."
        return None

    # ── Finalize ───────────────────────────────────────────────
    elapsed_s = time.time() - start_time

    from .utils import count_discussion_tokens, print_cost_and_time, save_meeting, get_summary
    from .constants import MODEL_TO_INPUT_PRICE_PER_TOKEN, MODEL_TO_OUTPUT_PRICE_PER_TOKEN

    token_counts = count_discussion_tokens(discussion)
    token_counts["tool"] = tool_token_count

    display_model = list(agent_configs.values())[0].model if agent_configs else "unknown"

    print(f"\n{'─' * 50}")
    print(f"  Meeting: {save_name}")
    print(f"  Type:    {meeting_type}")
    print(f"  Rounds:  {num_rounds}")
    print_cost_and_time(
        token_counts, display_model, elapsed_s,
        MODEL_TO_INPUT_PRICE_PER_TOKEN, MODEL_TO_OUTPUT_PRICE_PER_TOKEN,
    )
    print(f"{'─' * 50}")

    save_meeting(save_dir, save_name, discussion)

    if run_logger:
        summary_preview = get_summary(discussion)[:200] if discussion else ""
        run_logger.log_meeting_end(
            save_name=save_name,
            summary_preview=summary_preview,
            num_rounds=num_rounds,
            elapsed_s=elapsed_s,
        )

    if return_summary:
        return get_summary(discussion)

    return None


def _run_meeting_body(
    meeting_type, agenda, save_dir, save_name,
    team_lead, team_members, team_member,
    agenda_questions, agenda_rules, summaries, contexts,
    num_rounds, temperature, enable_tools,
    run_logger, provider_name,
    discussion, messages, agent_configs,
) -> None:
    """Inner function containing the actual meeting logic.

    Separated so NetworkExhaustedError can be caught and checkpointed
    without losing the meeting state accumulated so far.
    """
    from .prompts import (
        SCIENTIFIC_CRITIC,
        team_meeting_start_prompt,
        team_meeting_team_lead_initial_prompt,
        team_meeting_team_member_prompt,
        team_meeting_team_lead_intermediate_prompt,
        team_meeting_team_lead_final_prompt,
        individual_meeting_start_prompt,
        individual_meeting_critic_prompt,
        individual_meeting_agent_revise_prompt,
    )

    tool_token_count = 0
    bridge = None
    if enable_tools and meeting_type == "individual":
        try:
            from .tools import ToolBridge
            bridge = ToolBridge()
            logger.info("ToolBridge initialized")
        except ImportError as e:
            logger.warning("ToolBridge unavailable: %s", e)
            if run_logger:
                run_logger.log_warning("setup", f"ToolBridge unavailable: {e}")

    # ── Team meeting ──────────────────────────────────────────
    if meeting_type == "team":
        assert team_lead is not None and team_members is not None
        team: List[Agent] = [team_lead] + list(team_members)

        start_content = team_meeting_start_prompt(
            team_lead=team_lead, team_members=team_members,
            agenda=agenda, agenda_questions=agenda_questions,
            agenda_rules=agenda_rules, summaries=summaries,
            contexts=contexts, num_rounds=num_rounds,
        )
        messages.append({"role": "user", "content": start_content})
        discussion.append({"agent": "User", "message": start_content})

        for round_idx in range(num_rounds + 1):
            round_num = round_idx + 1
            for agent in team:
                if agent == team_lead:
                    if round_idx == 0:
                        prompt = team_meeting_team_lead_initial_prompt(team_lead)
                    elif round_idx == num_rounds:
                        prompt = team_meeting_team_lead_final_prompt(
                            team_lead=team_lead, agenda=agenda,
                            agenda_questions=agenda_questions,
                            agenda_rules=agenda_rules,
                        )
                    else:
                        prompt = team_meeting_team_lead_intermediate_prompt(
                            team_lead=team_lead, round_num=round_num - 1,
                            num_rounds=num_rounds,
                        )
                else:
                    prompt = team_meeting_team_member_prompt(
                        team_member=agent, round_num=round_num,
                        num_rounds=num_rounds,
                    )

                messages.append({"role": "user", "content": prompt})
                discussion.append({"agent": "User", "message": prompt})

                agent_messages = [agent.system_message] + messages
                cfg = agent_configs[agent.title]
                temp = agent.temperature if agent.temperature is not None else temperature

                content, in_tok, out_tok, lat_ms = _call_llm(
                    cfg, agent_messages, temperature=temp, max_tokens=2048,
                )
                if run_logger:
                    run_logger.log_api_call(
                        agent=agent.title, model=cfg.model,
                        input_tokens=in_tok, output_tokens=out_tok,
                        latency_ms=lat_ms, purpose=f"team_round{round_num}",
                    )
                messages.append({"role": "assistant", "content": content})
                discussion.append({"agent": agent.title, "message": content})

                if run_logger:
                    run_logger.log_agent_response(
                        agent=agent.title, content=content,
                        meeting_name=save_name, round_num=round_num,
                        purpose="team_discussion",
                    )
                if round_idx == num_rounds:
                    break

    # ── Individual meeting ────────────────────────────────────
    else:
        assert team_member is not None
        agent = team_member
        cfg = agent_configs[agent.title]
        temp = agent.temperature if agent.temperature is not None else temperature

        start_content = individual_meeting_start_prompt(
            team_member=agent, agenda=agenda,
            agenda_questions=agenda_questions,
            agenda_rules=agenda_rules,
            summaries=summaries, contexts=contexts,
        )
        messages.append({"role": "user", "content": start_content})
        discussion.append({"agent": "User", "message": start_content})

        if bridge is not None:
            response_content, tool_log, tt_tokens = _execute_tool_loop(
                agent=agent, agent_config=cfg,
                messages=messages, bridge=bridge,
                run_logger=run_logger, temperature=temp,
            )
            tool_token_count += tt_tokens
        else:
            agent_messages = [agent.system_message] + messages
            content, in_tok, out_tok, lat_ms = _call_llm(
                cfg, agent_messages, temperature=temp, max_tokens=2048,
            )
            if run_logger:
                run_logger.log_api_call(
                    agent=agent.title, model=cfg.model,
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=lat_ms, purpose="individual_task",
                )
            response_content = content
            messages.append({"role": "assistant", "content": response_content})

        discussion.append({"agent": agent.title, "message": response_content})
        if run_logger:
            run_logger.log_agent_response(
                agent=agent.title, content=response_content,
                meeting_name=save_name, round_num=0,
                purpose="individual_task",
            )

        # ── Critic review rounds ─────────────────────────────
        critic = SCIENTIFIC_CRITIC
        critic_cfg = resolve_config(critic, provider=provider_name)

        for round_num in range(1, num_rounds + 1):
            critic_prompt = individual_meeting_critic_prompt(
                critic=critic, agent=agent,
            )
            messages.append({"role": "user", "content": critic_prompt})
            discussion.append({"agent": "User", "message": critic_prompt})

            critic_messages = [critic.system_message] + messages
            critic_temp = critic.temperature if critic.temperature is not None else temperature
            critic_content, in_tok, out_tok, lat_ms = _call_llm(
                critic_cfg, critic_messages, temperature=critic_temp, max_tokens=2048,
            )
            if run_logger:
                run_logger.log_api_call(
                    agent=critic.title, model=critic_cfg.model,
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=lat_ms, purpose=f"critic_round{round_num}",
                )
            messages.append({"role": "assistant", "content": critic_content})
            discussion.append({"agent": critic.title, "message": critic_content})
            if run_logger:
                run_logger.log_agent_response(
                    agent=critic.title, content=critic_content,
                    meeting_name=save_name, round_num=round_num,
                    purpose="critic_review",
                )

            satisfied = [
                "no issues", "correct", "looks good", "well done",
                "no errors", "all good", "satisfied", "complete",
                "没有问题", "正确", "没问题", "完成得很好",
            ]
            if any(ind in critic_content.lower() for ind in satisfied):
                if not re.search(
                    r'\b(but|however|although|except|除了|但是|然而|不过)\b',
                    critic_content, re.IGNORECASE,
                ):
                    logger.info("Critic satisfied after round %d", round_num)
                    if run_logger:
                        run_logger._emit("critic_satisfied", {"round": round_num})
                    break

            revise_prompt = individual_meeting_agent_revise_prompt(
                critic=critic, agent=agent,
            )
            messages.append({"role": "user", "content": revise_prompt})
            discussion.append({"agent": "User", "message": revise_prompt})

            if bridge is not None:
                revised, tool_log2, tt_tokens2 = _execute_tool_loop(
                    agent=agent, agent_config=cfg,
                    messages=messages, bridge=bridge,
                    run_logger=run_logger, temperature=temp,
                )
                tool_token_count += tt_tokens2
            else:
                agent_messages = [agent.system_message] + messages
                revised, in_tok, out_tok, lat_ms = _call_llm(
                    cfg, agent_messages, temperature=temp, max_tokens=2048,
                )
                if run_logger:
                    run_logger.log_api_call(
                        agent=agent.title, model=cfg.model,
                        input_tokens=in_tok, output_tokens=out_tok,
                        latency_ms=lat_ms, purpose=f"revise_round{round_num}",
                    )
            messages.append({"role": "assistant", "content": revised})
            discussion.append({"agent": agent.title, "message": revised})
            if run_logger:
                run_logger.log_agent_response(
                    agent=agent.title, content=revised,
                    meeting_name=save_name, round_num=round_num,
                    purpose="agent_revision",
                )


# ── Meeting history loading ────────────────────────────────────────

def load_meeting_context(
    *meeting_paths: str,
) -> dict:
    """Load saved meeting records as context for a new meeting.

    Reads saved JSON discussions and extracts summaries + full content
    that can be passed to run_meeting() via the `summaries` and
    `contexts` parameters.

    Args:
        *meeting_paths: One or more paths to saved meeting JSON files.

    Returns:
        Dict with:
          - summaries: Tuple of summary strings (last message of each meeting)
          - contexts:  Tuple of agent-discussion summaries for context injection
          - meeting_names: List of meeting file stems
          - agent_contributions: Dict[agent_title, List[str]] of what each agent said

    Usage:
        ctx = load_meeting_context("./meetings/01_team_planning.json")
        run_meeting(
            meeting_type="team",
            agenda="Continue our insulin project...",
            summaries=ctx["summaries"],
            contexts=ctx["contexts"],
            ...
        )
    """
    import json as _json

    summaries: list = []
    contexts: list = []
    meeting_names: list = []
    agent_contribs: dict = {}

    for path_str in meeting_paths:
        p = Path(path_str)
        if not p.exists():
            continue

        meeting_names.append(p.stem)

        try:
            with open(p, "r", encoding="utf-8") as f:
                discussion = _json.load(f)
        except Exception:
            continue

        if not discussion:
            continue

        # Summary = last message
        summaries.append(discussion[-1].get("message", ""))

        # Per-agent contributions
        agent_texts: dict = {}
        for turn in discussion:
            ag = turn.get("agent", "User")
            msg = turn.get("message", "")
            if ag != "User":
                agent_texts.setdefault(ag, []).append(msg)

        # Build a structured context for this meeting
        parts = [f"=== Meeting: {p.stem} ==="]
        for ag, texts in agent_texts.items():
            combined = "\n\n".join(texts)
            parts.append(f"\n--- {ag} ---\n{combined[:2000]}")
            agent_contribs.setdefault(ag, []).append(combined[:2000])

        contexts.append("\n".join(parts)[:8000])

    return {
        "summaries": tuple(summaries),
        "contexts": tuple(contexts),
        "meeting_names": meeting_names,
        "agent_contributions": agent_contribs,
    }


def list_saved_meetings(meetings_dir: str = "meetings") -> list:
    """List all saved meeting files with brief info.

    Returns:
        List of {name, path, agents, turns, size_kb} dicts.
    """
    import json as _json

    d = Path(meetings_dir)
    if not d.is_dir():
        return []

    results = []
    for f in sorted(d.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                discussion = _json.load(fh)
        except Exception:
            continue

        agents_seen = set()
        for turn in discussion:
            ag = turn.get("agent", "")
            if ag and ag != "User":
                agents_seen.add(ag)

        size_kb = round(f.stat().st_size / 1024, 1)
        results.append({
            "name": f.stem,
            "path": str(f),
            "agents": sorted(agents_seen),
            "turns": len(discussion),
            "size_kb": size_kb,
        })

    return results
