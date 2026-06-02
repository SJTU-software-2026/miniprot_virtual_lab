"""
ToolBridge — connects Virtual Lab agents to MiniProt's ToolManager.

Lazy-imports enzyme_update's ToolManager, provides safe tool execution
with artifact tracking, and filters tools by agent category.
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import normalize_schema

logger = logging.getLogger(__name__)

# ── Lazy import of MiniProt's ToolManager ──────────────────────────

_TOOL_MANAGER = None
_ENZYME_UPDATE_PATH: Optional[str] = None


def _find_enzyme_update() -> Optional[str]:
    """Locate the enzyme_update directory relative to this project."""
    candidates = []

    # 1. Same parent directory (relative to this project)
    project_root = Path(__file__).resolve().parents[3]  # → miniprot_virtual_lab/
    enzyme_path = project_root.parent / "enzyme_update" / "src"
    candidates.append(str(enzyme_path))

    # 2. Environment variable
    env_path = os.getenv("ENZYME_UPDATE_SRC", "").strip()
    if env_path:
        candidates.append(env_path)

    # 3. Relative sibling
    from_project_root = project_root.parent / "enzyme_update" / "src"
    if from_project_root != enzyme_path:
        candidates.append(str(from_project_root))

    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return None


def _import_tool_manager() -> Any:
    """Lazy-import ToolManager from enzyme_update."""
    global _TOOL_MANAGER, _ENZYME_UPDATE_PATH

    if _TOOL_MANAGER is not None:
        return _TOOL_MANAGER

    src_path = _find_enzyme_update()
    if src_path is None:
        raise ImportError(
            "Cannot find enzyme_update/src. "
            "Set ENZYME_UPDATE_SRC=/path/to/enzyme_update/src "
            "or place enzyme_update next to miniprot_virtual_lab."
        )

    _ENZYME_UPDATE_PATH = src_path
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    try:
        from tool_runner import ToolManager
        _TOOL_MANAGER = ToolManager
        logger.info("ToolManager loaded from %s", src_path)
        return ToolManager
    except ImportError as e:
        raise ImportError(
            f"Found enzyme_update at {src_path} but cannot import ToolManager. "
            f"Ensure all dependencies are installed: {e}"
        ) from e


# ── ToolBridge ─────────────────────────────────────────────────────

class ToolBridge:
    """Bridge between Virtual Lab agents and MiniProt's ToolManager.

    Handles:
      - Tool discovery and schema caching
      - Safe tool execution with error normalization
      - Artifact tracking (which files were produced)
      - Agent-to-tool mapping via category metadata
    """

    def __init__(self) -> None:
        # Load user-configured local tool paths before importing ToolManager
        from .tool_paths import load_tool_paths
        load_tool_paths()

        self._tm = _import_tool_manager()
        self._schemas: Dict[str, Dict[str, Any]] = {}
        self._artifacts: List[Dict[str, Any]] = []
        self._run_log: List[Dict[str, Any]] = []
        self._load_schemas()

    # ── Schema management ──────────────────────────────────────

    def _load_schemas(self) -> None:
        """Load and cache all tool schemas from ToolManager.

        Uses the ToolManager's registered tool names as the source of truth.
        If a schema lacks a 'name' field (JSON Schema format), the registered
        key is used instead.
        """
        try:
            raw_schemas = self._tm.get_all_schemas()
            # Build a registry-key → schema mapping for name resolution
            registered_names = list(self._tm._tools.keys())
            for i, raw in enumerate(raw_schemas):
                normalized = normalize_schema(raw)
                name = normalized["name"]
                # If schema has no name, use the ToolManager registration key
                if name == "unknown" and i < len(registered_names):
                    name = registered_names[i]
                    normalized["name"] = name
                self._schemas[name] = normalized
            logger.info("Loaded %d tool schemas", len(self._schemas))
        except Exception as e:
            logger.warning("Failed to load tool schemas: %s", e)

    def list_tools(self) -> List[str]:
        """Return sorted list of all available tool IDs."""
        return sorted(self._schemas.keys())

    def get_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get the normalized schema for a specific tool."""
        return self._schemas.get(tool_name)

    def tools_for_agent(self, agent) -> List[Dict[str, Any]]:
        """Return schemas for tools the given agent is authorized to use.

        Uses the tool category registry from tools/__init__.py to map
        agent.tool_categories → tool IDs → schemas.
        """
        from . import TOOL_REGISTRY
        from ..constants import TOOL_CATEGORIES

        allowed: set = set()
        for cat in agent.tool_categories:
            allowed.update(TOOL_CATEGORIES.get(cat, []))

        if not allowed:
            return []

        return [
            schema for name, schema in self._schemas.items()
            if name in allowed
        ]

    def tools_summary_for_agent(self, agent) -> str:
        """Human-readable tool summary for including in agent prompts."""
        tool_schemas = self.tools_for_agent(agent)
        if not tool_schemas:
            return "(no tools available)"

        from . import TOOL_REGISTRY

        lines: List[str] = []
        for ts in tool_schemas:
            name = ts["name"]
            desc = ts["description"][:120]
            params = ts.get("parameters_text", "")
            # Add agent hint if available
            meta = TOOL_REGISTRY.get(name, {})
            primary = meta.get("primary_agent", "")
            hint = f"  ← best for: {primary}" if primary else ""
            lines.append(f"  **{name}**: {desc}{hint}")
            if params and params != "    (none)":
                lines.append(f"    Parameters:\n{params}")

        return "\n".join(lines)

    # ── Tool execution ─────────────────────────────────────────

    def run(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Run a MiniProt tool and return a normalized result.

        Args:
            tool_name: Registered tool ID (e.g. 'uniprot_search').
            **kwargs: Tool-specific parameters.

        Returns:
            Dict with success, tool, result, error, elapsed_ms, artifacts.
        """
        t0 = time.perf_counter()

        if tool_name not in self._schemas:
            return {
                "success": False,
                "tool": tool_name,
                "result": {},
                "error": f"Unknown tool: {tool_name}. "
                         f"Available: {self.list_tools()}",
                "elapsed_ms": 0,
                "artifacts": [],
            }

        try:
            raw_result = self._tm.run_tool(tool_name, **kwargs)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("Tool %s failed: %s", tool_name, e)
            return {
                "success": False,
                "tool": tool_name,
                "result": {},
                "error": str(e),
                "elapsed_ms": elapsed_ms,
                "artifacts": [],
            }

        artifacts = self._extract_artifacts(tool_name, raw_result)
        for art in artifacts:
            self._artifacts.append(art)
        self._run_log.append({
            "tool": tool_name,
            "args": kwargs,
            "success": raw_result.get("success", True),
            "elapsed_ms": elapsed_ms,
        })

        normalized_result = raw_result
        if isinstance(raw_result, dict):
            inner_data = raw_result.get("data")
            if isinstance(inner_data, dict) and inner_data.get("success"):
                inner_inner = inner_data.get("data")
                if isinstance(inner_inner, dict):
                    normalized_result = inner_data

        return {
            "success": raw_result.get("success", True) if isinstance(raw_result, dict) else True,
            "tool": tool_name,
            "result": normalized_result,
            "error": raw_result.get("error") if isinstance(raw_result, dict) else None,
            "elapsed_ms": elapsed_ms,
            "artifacts": artifacts,
        }

    def _extract_artifacts(
        self, tool_name: str, result: Any
    ) -> List[Dict[str, Any]]:
        """Pull file paths from a tool result into artifact records."""
        artifacts: List[Dict[str, Any]] = []
        if not isinstance(result, dict):
            return artifacts

        data = result.get("data")
        if isinstance(data, dict) and data.get("success"):
            data = data.get("data", data)
        if not isinstance(data, dict):
            data = result

        downloaded = data.get("downloaded")
        if isinstance(downloaded, dict):
            for fmt, paths in downloaded.items():
                if isinstance(paths, list) and paths:
                    artifacts.append({
                        "format": fmt, "paths": list(paths), "tool": tool_name,
                    })

        gen = data.get("generated_files")
        if isinstance(gen, list) and gen:
            artifacts.append({
                "format": "generated", "paths": [str(p) for p in gen if p],
                "tool": tool_name,
            })
        elif isinstance(gen, dict):
            for fmt, paths in gen.items():
                if isinstance(paths, list) and paths:
                    artifacts.append({
                        "format": fmt, "paths": list(paths), "tool": tool_name,
                    })

        for key in ("output_path", "csv_path", "fasta_path",
                     "aligned_path", "result_path", "merged_path"):
            val = data.get(key)
            if isinstance(val, str) and val:
                artifacts.append({
                    "format": key.replace("_path", ""),
                    "paths": [val], "tool": tool_name,
                })

        return artifacts

    def get_artifacts(self) -> List[Dict[str, Any]]:
        return list(self._artifacts)

    def clear_artifacts(self) -> None:
        self._artifacts.clear()
        self._run_log.clear()

    def get_run_log(self) -> List[Dict[str, Any]]:
        return list(self._run_log)


# ── Singleton ──────────────────────────────────────────────────────

_bridge_instance: Optional[ToolBridge] = None


def get_bridge() -> ToolBridge:
    """Get or create the singleton ToolBridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = ToolBridge()
    return _bridge_instance
