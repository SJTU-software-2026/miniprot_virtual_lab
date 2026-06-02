# This tool is used just for test

try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

from typing import Any, Dict

class EchoTool(BaseTool):
    name = "echo_tool"
    description = "Echo back the user's input text. Useful for testing tool integration."

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to echo back"
                }
            },
            "required": ["text"]
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        text = kwargs.get("text", "")

        return {
            "success": True,
            "data": {
                "echo": text
            }
        }