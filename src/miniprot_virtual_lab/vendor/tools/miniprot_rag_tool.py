try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

from typing import Any, Dict


class MiniProtRagTool(BaseTool):
    """
    Local Chroma RAG over Markdown (CASPIA-RAG-style index: header split + recursive split).
    Use after building the index with ``scripts/build_miniprot_rag_index.py``.
    """

    def __init__(self) -> None:
        self._name = "miniprot_rag"
        self._description = (
            "Search the MiniProt local knowledge base (Chroma vector store built from Markdown). "
            "Use when the user asks how MiniProt works, for workflow/tooling documentation indexed under "
            "docs/rag_corpus, or for internal playbook text. Returns retrieved passages with source metadata; "
            "does not replace UniProt/HMMER/docking tools for factual database operations. "
            "Requires OPENAI_API_KEY for embeddings and a built index at MINIPROT_RAG_PERSIST_DIR "
            "(default data/miniprot_rag_chroma)."
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language question for similarity search over the indexed corpus.",
                        },
                        "k": {
                            "type": "integer",
                            "description": "Number of chunks to return (default 5).",
                            "default": 5,
                        },
                        "expert_mode": {
                            "type": "boolean",
                            "description": (
                                "If true, retrieve more candidates then re-rank (DashScope TextReRank when "
                                "DASHSCOPE_API_KEY is set; otherwise distance sort). Default false."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "query is required.", "data": {}}

        k = kwargs.get("k", 5)
        try:
            k_int = int(k)
        except (TypeError, ValueError):
            k_int = 5
        k_int = max(1, min(k_int, 30))

        expert = bool(kwargs.get("expert_mode", False))

        try:
            from miniprot_rag.retrieval import retrieve_chunks
        except ImportError as exc:
            return {
                "success": False,
                "error": f"RAG dependencies not installed: {exc}",
                "data": {},
            }

        try:
            raw = retrieve_chunks(query, k=k_int, expert_mode=expert)
        except Exception as exc:
            return {"success": False, "error": str(exc), "data": {}}

        if not raw.get("success"):
            return {"success": False, "error": raw.get("error", "retrieval failed"), "data": raw}

        chunks = raw.get("chunks") or []
        joined = "\n\n---\n\n".join(
            f"(score={c.get('score')}, source={c.get('metadata', {}).get('source', '')})\n{c.get('text', '')}"
            for c in chunks
        )
        return {
            "success": True,
            "data": {
                "chunks": chunks,
                "context_for_llm": joined,
                "chunk_count": len(chunks),
                "persist_dir": raw.get("persist_dir"),
                "expert_mode": raw.get("expert_mode", expert),
            },
        }
