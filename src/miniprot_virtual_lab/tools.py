"""
Tool integration layer for MiniProt Virtual Lab.

Wraps MiniProt's ToolManager from enzyme_update, providing:
  - Lazy import with clear error messages
  - Tool discovery and schema retrieval
  - Safe execution with timeout and error handling
  - Artifact tracking (files produced by each tool call)
  - Tool-to-category mapping for agent assignment

Usage:
    from miniprot_virtual_lab.tools import ToolBridge

    bridge = ToolBridge()
    result = bridge.run("uniprot_search", query="insulin", limit=5)
    print(bridge.list_tools())          # all available tools
    print(bridge.tools_for_agent(sa))   # tools for Sequence Analysis Specialist
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Lazy import of MiniProt's ToolManager ──────────────────────────

_TOOL_MANAGER = None
_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {}
_ENZYME_UPDATE_PATH: Optional[str] = None


def _find_enzyme_update() -> Optional[str]:
    """Locate the enzyme_update directory relative to this project."""
    candidates = []

    # 1. Same parent directory (relative to this project)
    project_root = Path(__file__).resolve().parents[3]  # → miniprot_virtual_lab/
    enzyme_path = project_root.parent / "enzyme_update" / "src"
    candidates.append(str(enzyme_path))

    # 2. Environment variable (user can set if enzyme_update is elsewhere)
    env_path = os.getenv("ENZYME_UPDATE_SRC", "").strip()
    if env_path:
        candidates.append(env_path)

    # 3. Relative sibling: ../../enzyme_update/src from project root
    from_project_root = project_root.parent / "enzyme_update" / "src"
    if from_project_root != enzyme_path:
        candidates.append(str(from_project_root))

    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return None


def _import_tool_manager() -> Any:
    """Lazy-import ToolManager from enzyme_update.

    Returns:
        ToolManager class from enzyme_update's tool_runner module.

    Raises:
        ImportError: If enzyme_update cannot be found or imported.
    """
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


# ── Schema normalization ───────────────────────────────────────────

def _normalize_schema(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a MiniProt tool schema to a flatter JSON Schema-style dict.

    MiniProt tools use two different schema formats:
      1. Flat:   {name, description, parameters: {prop: {type, description}}}
      2. OpenAI: {type: "function", function: {name, description, parameters}}

    We normalize both to a consistent structure for the LLM.
    """
    # Handle OpenAI function-calling wrapper format
    if "function" in raw and isinstance(raw.get("function"), dict):
        inner = raw["function"]
        name = inner.get("name", "unknown")
        desc = inner.get("description", "")
        params = inner.get("parameters", {})
    else:
        name = raw.get("name", "unknown")
        desc = raw.get("description", "")
        params = raw.get("parameters", {})

    # Handle JSON Schema wrapper in parameters (OpenAI format):
    #   {type: "object", properties: {param: {type, desc}}, required: [...]}
    # vs flat format:
    #   {param_name: {type, description}, ...}
    if isinstance(params, dict) and "properties" in params:
        props = params.get("properties", {})
        required = params.get("required", [])
    else:
        props = params if isinstance(params, dict) else {}
        required = []

    # Build a flat parameter description
    param_lines: List[str] = []
    for pname, pinfo in props.items():
        if not isinstance(pinfo, dict):
            param_lines.append(f"    {pname}")
            continue
        ptype = pinfo.get("type", "string")
        pdesc = pinfo.get("description", "")
        pdefault = pinfo.get("default")
        dflt = f" (default: {pdefault})" if pdefault is not None else ""
        req = " [required]" if pname in required else ""
        param_lines.append(f"    {pname}: {ptype} — {pdesc}{dflt}{req}")

    params_text = "\n".join(param_lines) if param_lines else "    (none)"

    return {
        "name": name,
        "description": desc,
        "parameters_text": params_text,
        "parameters_raw": params,
    }


# ── ToolBridge ─────────────────────────────────────────────────────

class ToolBridge:
    """Bridge between Virtual Lab agents and MiniProt's ToolManager.

    Handles:
      - Tool discovery and schema caching
      - Safe tool execution with error normalization
      - Artifact tracking (which files were produced)
      - Agent-to-tool mapping
    """

    def __init__(self) -> None:
        self._tm = _import_tool_manager()
        self._schemas: Dict[str, Dict[str, Any]] = {}
        self._artifacts: List[Dict[str, Any]] = []
        self._run_log: List[Dict[str, Any]] = []
        self._load_schemas()

    # ── Schema management ──────────────────────────────────────

    def _load_schemas(self) -> None:
        """Load and cache all tool schemas from ToolManager."""
        try:
            raw_schemas = self._tm.get_all_schemas()
            for raw in raw_schemas:
                normalized = _normalize_schema(raw)
                name = normalized["name"]
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

        Args:
            agent: An Agent instance with tool_categories attribute.

        Returns:
            List of normalized tool schemas.
        """
        from .constants import TOOL_CATEGORIES

        allowed: set = set()
        for cat in agent.tool_categories:
            allowed.update(TOOL_CATEGORIES.get(cat, []))

        if not allowed:
            # Agent with no categories gets no tools
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

        lines: List[str] = []
        for ts in tool_schemas:
            name = ts["name"]
            desc = ts["description"][:120]
            params = ts.get("parameters_text", "")
            lines.append(f"  **{name}**: {desc}")
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
            Dict with keys:
              - success: bool
              - tool: tool name
              - result: the raw result from the tool
              - error: error message (if success=False)
              - elapsed_ms: execution time
              - artifacts: list of {format, paths} dicts for new files
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

        # Extract artifact paths from the result
        artifacts = self._extract_artifacts(tool_name, raw_result)

        # Track globally
        for art in artifacts:
            self._artifacts.append(art)
        self._run_log.append({
            "tool": tool_name,
            "args": kwargs,
            "success": raw_result.get("success", True),
            "elapsed_ms": elapsed_ms,
        })

        # Normalize: handle nested {success, data: {success, data: ...}}
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
        """Pull file paths from a tool result into artifact records.

        Each artifact: {format, paths, tool}.
        """
        artifacts: List[Dict[str, Any]] = []
        if not isinstance(result, dict):
            return artifacts

        data = result.get("data")
        if isinstance(data, dict) and data.get("success"):
            data = data.get("data", data)

        if not isinstance(data, dict):
            data = result

        # Standard 'downloaded' key
        downloaded = data.get("downloaded")
        if isinstance(downloaded, dict):
            for fmt, paths in downloaded.items():
                if isinstance(paths, list) and paths:
                    artifacts.append({
                        "format": fmt,
                        "paths": list(paths),
                        "tool": tool_name,
                    })

        # 'generated_files' (list or dict)
        gen = data.get("generated_files")
        if isinstance(gen, list) and gen:
            artifacts.append({
                "format": "generated",
                "paths": [str(p) for p in gen if p],
                "tool": tool_name,
            })
        elif isinstance(gen, dict):
            for fmt, paths in gen.items():
                if isinstance(paths, list) and paths:
                    artifacts.append({
                        "format": fmt,
                        "paths": list(paths),
                        "tool": tool_name,
                    })

        # Single-file outputs
        for key in ("output_path", "csv_path", "fasta_path",
                     "aligned_path", "result_path", "merged_path"):
            val = data.get(key)
            if isinstance(val, str) and val:
                fmt = key.replace("_path", "")
                artifacts.append({
                    "format": fmt,
                    "paths": [val],
                    "tool": tool_name,
                })

        return artifacts

    # ── Artifact management ────────────────────────────────────

    def get_artifacts(self) -> List[Dict[str, Any]]:
        """Return all accumulated artifacts from this session."""
        return list(self._artifacts)

    def get_artifacts_by_format(self, fmt: str) -> List[str]:
        """Get all file paths of a given format (e.g. 'fasta', 'pdb')."""
        paths: List[str] = []
        for art in self._artifacts:
            if art.get("format") == fmt:
                paths.extend(art.get("paths", []))
        return paths

    def get_recent_paths(self, fmt: str, n: int = 5) -> List[str]:
        """Return the n most recent paths of a given format."""
        all_paths = self.get_artifacts_by_format(fmt)
        return all_paths[-n:] if len(all_paths) > n else all_paths

    def clear_artifacts(self) -> None:
        """Reset artifact tracking."""
        self._artifacts.clear()
        self._run_log.clear()

    def get_run_log(self) -> List[Dict[str, Any]]:
        """Return the tool execution log."""
        return list(self._run_log)


# ── Singleton convenience ──────────────────────────────────────────

_bridge_instance: Optional[ToolBridge] = None


def get_bridge() -> ToolBridge:
    """Get or create the singleton ToolBridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = ToolBridge()
    return _bridge_instance
