"""
MEPAM Tool — 酶生产条件推荐

Calls MEPAM API to recommend:
  - Best expression host for an enzyme
  - Culture medium and fermentation conditions
  - Carbon/nitrogen sources

MEPAM server must be running: docker run -p 8000:8000 mepam
"""
from __future__ import annotations

import os
from typing import Any, Dict

import requests

try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool  # type: ignore

MEPAM_URL = os.getenv("MEPAM_API_URL", "http://localhost:8000")


class MepamTool(BaseTool):
    """Recommend production host and fermentation conditions for an enzyme."""

    name = "mepam"
    description = (
        "Query MEPAM knowledge base to recommend optimal expression host, "
        "culture medium, and fermentation conditions for a given enzyme and substrate. "
        "Use this when the user wants to know how to produce an enzyme, what host "
        "to use, or what medium/conditions are recommended. "
        "Database: 12K+ nodes, 35K+ edges, 2K+ media formulations."
    )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "enzyme": {
                    "type": "string",
                    "description": "Enzyme name or type (e.g. 'lipase', 'cellulase', 'transaminase').",
                },
                "substrate": {
                    "type": "string",
                    "description": "Optional substrate for the enzyme (e.g. 'glucose', 'cellulose').",
                },
                "action": {
                    "type": "string",
                    "enum": ["host", "media", "query"],
                    "description": "What to query: 'host' (recommend expression host), "
                    "'media' (recommend medium), 'query' (combined host+media). Default: 'query'.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default: 5).",
                    "default": 5,
                },
            },
            "required": ["enzyme"],
        }

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        enzyme = kwargs.get("enzyme", "").strip()
        if not enzyme:
            return {"success": False, "error": "enzyme is required."}

        action = kwargs.get("action", "query")
        substrate = kwargs.get("substrate", "")
        top_k = kwargs.get("top_k", 5)

        try:
            if action == "media":
                resp = requests.post(
                    f"{MEPAM_URL}/api/media",
                    json={"enzyme": enzyme, "host": substrate},
                    timeout=10,
                )
            elif action == "host":
                resp = requests.post(
                    f"{MEPAM_URL}/api/host",
                    json={"enzyme": enzyme, "substrate": substrate},
                    timeout=10,
                )
            else:
                resp = requests.post(
                    f"{MEPAM_URL}/api/query",
                    json={"enzyme": enzyme, "substrate": substrate, "top_k": top_k},
                    timeout=10,
                )

            resp.raise_for_status()
            data = resp.json()

            # Format for Agent readability
            hosts = data.get("hosts", [])
            summary_parts = [f"## {enzyme} 生产条件推荐\n"]

            for i, h in enumerate(hosts[:top_k], 1):
                summary_parts.append(
                    f"### {i}. {h.get('host', 'Unknown')}\n"
                    f"- 温度: {h.get('temperature', 'N/A')}\n"
                    f"- pH: {h.get('ph', 'N/A')}\n"
                    f"- 培养基: {h.get('medium', 'N/A')}\n"
                    f"- 碳源: {h.get('carbon_source', 'N/A')}\n"
                    f"- 诱导: {h.get('induction', 'N/A')}\n"
                    f"- 底物: {h.get('substrate', 'N/A')}\n"
                    f"- 效果: {h.get('effect', 'N/A')}\n"
                    f"- 数据来源: {h.get('source', 'N/A')}\n"
                )

            return {
                "success": True,
                "data": {
                    "enzyme": enzyme,
                    "substrate": substrate,
                    "hosts": hosts[:top_k],
                    "media_available": data.get("media_available"),
                    "summary": "\n".join(summary_parts),
                },
            }

        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": (
                    f"MEPAM server not reachable at {MEPAM_URL}. "
                    "Start it with: docker run -p 8000:8000 mepam"
                ),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
