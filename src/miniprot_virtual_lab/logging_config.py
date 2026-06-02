"""
Structured logging for MiniProt Virtual Lab.

Generates comprehensive logs for each run:
  - run.log        — Human-readable text log
  - run.jsonl      — Machine-readable structured log (one JSON per line)
  - api_calls.json — API call trace (model, tokens, latency, cost)
  - tools.json     — Tool execution trace

Usage:
    from miniprot_virtual_lab.logging_config import RunLogger

    logger = RunLogger(log_dir=Path("./logs"))
    logger.log_api_call(agent="PI", model="deepseek-v4-pro", ...)
    logger.log_tool_call(agent="Docking", tool="autodock_vina", ...)
    logger.log_meeting_start(meeting_type="team", agenda="...")
    logger.log_meeting_end(summary="...")
    logger.finalize()
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Standard Python logger ─────────────────────────────────────────

def _setup_stdlib_logger(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """Configure the standard library logger with file + console handlers."""
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("miniprot_virtual_lab")
    logger.setLevel(level)
    logger.handlers.clear()

    # File handler: full detail
    file_handler = logging.FileHandler(
        log_dir / "run.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(file_handler)

    # Console handler: info+ only
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(
        "[%(levelname)s] %(message)s"
    ))
    logger.addHandler(console_handler)

    return logger


# ── Structured run logger ──────────────────────────────────────────

class RunLogger:
    """Structured logger for a single Virtual Lab run.

    Produces:
      - Text log (run.log) via stdlib logging
      - JSONL trace (run.jsonl) with one event per line
      - API call summary (api_calls.json)
      - Tool execution summary (tools.json)

    Usage:
        rl = RunLogger(log_dir=Path("./logs/run_20260101_120000"))
        rl.log_meeting_start("team", "Design a pipeline...")
        rl.log_api_call(agent="PI", model="deepseek-v4-pro",
                        input_tokens=1500, output_tokens=800,
                        latency_ms=3200)
        rl.log_tool_call(agent="Search", tool="uniprot_search",
                         args={"query": "insulin"}, success=True,
                         elapsed_ms=450)
        rl.finalize()
    """

    def __init__(self, log_dir: Path, run_id: Optional[str] = None) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.start_time = time.time()

        # Standard logger
        self._logger = _setup_stdlib_logger(log_dir)

        # JSONL event stream
        self._jsonl_path = log_dir / "run.jsonl"
        self._jsonl_file = open(self._jsonl_path, "w", encoding="utf-8")

        # Discussion stream: full agent responses, one JSON per line
        self._discussion_path = log_dir / "discussion.jsonl"
        self._discussion_file = open(self._discussion_path, "w", encoding="utf-8")

        # Accumulators
        self._api_calls: List[Dict[str, Any]] = []
        self._tool_calls: List[Dict[str, Any]] = []
        self._meetings: List[Dict[str, Any]] = []
        self._token_totals: Dict[str, int] = {"input": 0, "output": 0, "tool": 0}
        self._event_count = 0

        self._emit("run_start", {
            "run_id": self.run_id,
            "start_time": self.start_time,
        })
        self._logger.info("Run started: %s", self.run_id)

    # ── Event emission ─────────────────────────────────────────

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Write a structured event to the JSONL stream."""
        self._event_count += 1
        record = {
            "seq": self._event_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.time() - self.start_time, 3),
            "event": event_type,
            **data,
        }
        self._jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._jsonl_file.flush()

    # ── High-level log methods ─────────────────────────────────

    def log_meeting_start(
        self,
        meeting_type: str,
        agenda: str,
        save_name: str = "",
        agents: Optional[List[str]] = None,
    ) -> None:
        """Log the start of a meeting."""
        preview = agenda[:200].replace("\n", " ")
        self._logger.info(
            "Meeting START [%s] %s — %s",
            meeting_type, save_name, preview,
        )
        self._emit("meeting_start", {
            "meeting_type": meeting_type,
            "save_name": save_name,
            "agenda_preview": preview,
            "agents": agents or [],
        })

    def log_meeting_end(
        self,
        save_name: str = "",
        summary_preview: str = "",
        num_rounds: int = 0,
        elapsed_s: float = 0,
    ) -> None:
        """Log the end of a meeting."""
        self._logger.info(
            "Meeting END [%s] — %d rounds, %.1fs",
            save_name, num_rounds, elapsed_s,
        )
        self._emit("meeting_end", {
            "save_name": save_name,
            "summary_preview": summary_preview[:200] if summary_preview else "",
            "num_rounds": num_rounds,
            "elapsed_s": round(elapsed_s, 1),
        })

    def log_api_call(
        self,
        agent: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        purpose: str = "discussion",
        cost_usd: float = 0.0,
    ) -> None:
        """Log an LLM API call."""
        self._token_totals["input"] += input_tokens
        self._token_totals["output"] += output_tokens

        call_record = {
            "agent": agent,
            "model": model,
            "purpose": purpose,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "cost_usd": round(cost_usd, 6),
        }
        self._api_calls.append(call_record)

        self._emit("api_call", call_record)
        self._logger.debug(
            "API call [%s] %s: %d→%d tokens, %dms, $%.4f",
            agent, model, input_tokens, output_tokens, latency_ms, cost_usd,
        )

    def log_tool_call(
        self,
        agent: str,
        tool: str,
        args: Dict[str, Any],
        success: bool,
        elapsed_ms: int = 0,
        error: str = "",
    ) -> None:
        """Log a tool execution."""
        # Sanitize args: truncate long values
        safe_args = {}
        for k, v in args.items():
            s = str(v)
            safe_args[k] = s[:200] + "..." if len(s) > 200 else s

        call_record = {
            "agent": agent,
            "tool": tool,
            "args": safe_args,
            "success": success,
            "elapsed_ms": elapsed_ms,
        }
        if error:
            call_record["error"] = error

        self._tool_calls.append(call_record)
        self._emit("tool_call", call_record)

        status = "OK" if success else "FAIL"
        self._logger.info(
            "Tool [%s] %s → %s (%dms)%s",
            agent, tool, status, elapsed_ms,
            f" — {error[:100]}" if error else "",
        )

    def log_phase(self, phase_name: str, description: str = "") -> None:
        """Log a research phase transition."""
        self._logger.info("Phase: %s — %s", phase_name, description)
        self._emit("phase", {"phase": phase_name, "description": description})

    def log_error(self, source: str, message: str) -> None:
        """Log an error."""
        self._logger.error("[%s] %s", source, message)
        self._emit("error", {"source": source, "message": message})

    def log_warning(self, source: str, message: str) -> None:
        """Log a warning."""
        self._logger.warning("[%s] %s", source, message)
        self._emit("warning", {"source": source, "message": message})

    def log_agent_response(
        self,
        agent: str,
        content: str,
        meeting_name: str = "",
        round_num: int = 0,
        purpose: str = "discussion",
    ) -> None:
        """Log an agent's speech/response content.

        Saves the full response to discussion.jsonl and emits a summary
        event to run.jsonl. This makes every agent utterance searchable
        and reviewable after the run.
        """
        preview = content[:300].replace("\n", " ")
        self._logger.info(
            "Agent [%s] round=%d purpose=%s: %s",
            agent, round_num, purpose, preview,
        )
        # Full content saved to discussion stream
        self._discussion_file.write(json.dumps({
            "agent": agent,
            "meeting": meeting_name,
            "round": round_num,
            "purpose": purpose,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": content,
        }, ensure_ascii=False) + "\n")
        self._discussion_file.flush()

        # Summary event in main JSONL
        self._emit("agent_response", {
            "agent": agent,
            "meeting": meeting_name,
            "round": round_num,
            "purpose": purpose,
            "chars": len(content),
            "preview": preview,
        })

    # ── Finalize ───────────────────────────────────────────────

    def finalize(self) -> Dict[str, Any]:
        """Close log files and write summary files. Returns run summary dict."""
        elapsed_s = time.time() - self.start_time

        # Write API calls summary
        api_summary = {
            "run_id": self.run_id,
            "total_calls": len(self._api_calls),
            "token_totals": self._token_totals,
            "calls": self._api_calls,
        }
        api_path = self.log_dir / "api_calls.json"
        with open(api_path, "w", encoding="utf-8") as f:
            json.dump(api_summary, f, indent=2, ensure_ascii=False)

        # Write tool calls summary
        tool_summary = {
            "run_id": self.run_id,
            "total_calls": len(self._tool_calls),
            "success_rate": (
                sum(1 for t in self._tool_calls if t["success"]) / len(self._tool_calls)
                if self._tool_calls else 1.0
            ),
            "calls": self._tool_calls,
        }
        tool_path = self.log_dir / "tools.json"
        with open(tool_path, "w", encoding="utf-8") as f:
            json.dump(tool_summary, f, indent=2, ensure_ascii=False)

        # Emit final event
        self._emit("run_end", {
            "elapsed_s": round(elapsed_s, 1),
            "total_api_calls": len(self._api_calls),
            "total_tool_calls": len(self._tool_calls),
            "token_totals": self._token_totals,
            "tool_success_rate": tool_summary["success_rate"],
        })

        # Close files
        self._jsonl_file.close()
        self._discussion_file.close()

        # Log summary
        self._logger.info(
            "Run complete [%s] — %.1fs, %d API calls (%d input / %d output tokens), "
            "%d tool calls (%.0f%% success)",
            self.run_id, elapsed_s,
            len(self._api_calls),
            self._token_totals["input"],
            self._token_totals["output"],
            len(self._tool_calls),
            tool_summary["success_rate"] * 100,
        )
        self._logger.info("Logs saved to: %s", self.log_dir)

        return {
            "run_id": self.run_id,
            "elapsed_s": round(elapsed_s, 1),
            "api_calls": len(self._api_calls),
            "tool_calls": len(self._tool_calls),
            "token_totals": self._token_totals,
            "tool_success_rate": tool_summary["success_rate"],
            "log_dir": str(self.log_dir),
        }
