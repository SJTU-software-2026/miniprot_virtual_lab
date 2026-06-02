"""
Schema normalization for MiniProt tools.

MiniProt tools use two different schema formats:
  1. Flat:   {name, description, parameters: {prop: {type, description}}}
  2. OpenAI: {type: "function", function: {name, description, parameters}}

This module normalizes both to a consistent structure for the LLM.
"""

from typing import Any, Dict, List


def normalize_schema(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a MiniProt tool schema to a flatter JSON Schema-style dict.

    MiniProt tools use THREE different schema formats:
      1. OpenAI:  {type:"function", function:{name, description, parameters}}
      2. Flat:    {name, description, parameters: {prop: {type, desc}}}
      3. JSON:    {type:"object", properties: {prop: {type, desc}}, required:[...]}
                  (no name field — tool ID comes from ToolManager registration key)

    We normalize all to a consistent structure.
    """
    name = "unknown"
    desc = ""
    params: Dict[str, Any] = {}

    # Format 1: OpenAI function-calling wrapper
    if "function" in raw and isinstance(raw.get("function"), dict):
        inner = raw["function"]
        name = inner.get("name", "unknown")
        desc = inner.get("description", "")
        params = inner.get("parameters", {})

    # Format 2: Flat with name key
    elif "name" in raw:
        name = raw.get("name", "unknown")
        desc = raw.get("description", "")
        params = raw.get("parameters", {})

    # Format 3: JSON Schema without name (enzyme_redesign, echo_tool, etc.)
    elif "properties" in raw and "type" in raw:
        # Name is not in the schema — will be supplemented from
        # the ToolManager registration key when available
        desc = raw.get("description", "")
        params = raw

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
