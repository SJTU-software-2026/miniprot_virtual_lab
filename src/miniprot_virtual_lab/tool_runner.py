"""
MiniProt tool runner: LLM selects tool from natural language, runs it, and reviews output at each step.
Supports session memory: conversation and downloaded artifacts so user can say "use those sequences".
"""
import json
import logging
import os
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from tools.uniprot_tool import UniProtTool
from tools.alphafold_tool import AlphaFoldTool
from tools.autodock_vina_tool import AutoDockVinaTool
from tools.pdb_tool import PDBTool
from tools.smiles_tool import SMILESTool
from tools.pocket_box_tool import PocketBoxTool, PocketPickerTool
from tools.pdb_repair_tool import PDBRepairTool
from tools.pdb_merge_tool import MergerTool, PDBMergeTool
from tools.sequence_alignment_tool import SequenceAlignmentTool
from tools.hmmer_tool import HMMERTool
from tools.mmseqs2_tool import MMseqs2Tool
from tools.similarity_matrix_tool import SimilarityMatrixTool
from tools.cdhit_tool import CDHitTool
from tools.sequence_length_filter_tool import SequenceLengthFilterTool
from tools.foldseek_tool import FoldseekTool
from tools.omegafold_tool import OmegaFoldTool
from tools.esmfold_tool import ESMFoldTool
from tools.tmalign_tool import TMalignTool
from tools.structure_alignment_batch_tool import StructureAlignmentBatchTool
from tools.ete_tool import ETETool
from tools.pymol_tool import PyMOLTool
from tools.structure_from_fasta_tool import StructureFromFastaTool
from tools.sequence_similarity_tool import SequenceSimilarityTool
from tools.protein_properties_tool import ProteinPropertiesTool
from tools.fasta_convert_tool import FastaConvertTool
from tools.ncbi_search_tool import NCBISearchTool
from tools.enzyme_specificity_predict_tool import EnzymeSpecificityPredictTool
from tools.enzymecage_retrieve_tool import EnzymeCAGERetrieveTool
from tools.miniprot_rag_tool import MiniProtRagTool
from tools.enzyme_redesign_tool import EnzymeRedesignTool
from tools.mepam_tool import MepamTool
from tools.base_tools import BaseTool
from tools.echo_tool import EchoTool

if TYPE_CHECKING:
    from session_state import SessionState

logger = logging.getLogger(__name__)


class ToolManager:
    """Registry of tools. Each tool is a script/function that can be updated over time."""
    _tools: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, tool_class: type) -> None:
        cls._tools[name] = tool_class

    @classmethod
    def get_tool(cls, tool_name: str) -> BaseTool:
        if tool_name not in cls._tools:
            raise ValueError(f"Tool '{tool_name}' not found. Available: {list(cls._tools.keys())}")
        return cls._tools[tool_name]()

    @classmethod
    def get_all_schemas(cls) -> List[Dict[str, Any]]:
        """Return list of tool schemas for the LLM to choose from."""
        return [cls._tools[name]().get_schema() for name in cls._tools]

    @classmethod
    def run_tool(cls, tool_name: str, **kwargs) -> Dict[str, Any]:
        if tool_name == "smiles":
            try:
                from tools.smiles_tool import normalize_smiles_tool_arguments
            except ImportError:
                from ..tools.smiles_tool import normalize_smiles_tool_arguments
            kwargs = normalize_smiles_tool_arguments(dict(kwargs))
        tool = cls.get_tool(tool_name)
        return tool.execute(**kwargs)


# Register built-in tools
ToolManager.register("uniprot_search", UniProtTool)
ToolManager.register("alphafold", AlphaFoldTool)
ToolManager.register("autodock_vina", AutoDockVinaTool)
ToolManager.register("pdb", PDBTool)
ToolManager.register("smiles", SMILESTool)
ToolManager.register("pocket_box", PocketBoxTool)
ToolManager.register("pocket_picker", PocketPickerTool)
ToolManager.register("pdb_repair", PDBRepairTool)
ToolManager.register("pdb_merge", PDBMergeTool)
ToolManager.register("merger", MergerTool)
ToolManager.register("sequence_alignment", SequenceAlignmentTool)
ToolManager.register("hmmer", HMMERTool)
ToolManager.register("mmseqs2", MMseqs2Tool)
ToolManager.register("similarity_matrix", SimilarityMatrixTool)
ToolManager.register("cdhit", CDHitTool)
ToolManager.register("sequence_length_filter", SequenceLengthFilterTool)
ToolManager.register("foldseek", FoldseekTool)
ToolManager.register("omegafold", OmegaFoldTool)
ToolManager.register("esmfold", ESMFoldTool)
ToolManager.register("tmalign", TMalignTool)
ToolManager.register("structure_alignment_batch", StructureAlignmentBatchTool)
ToolManager.register("ete", ETETool)
ToolManager.register("pymol", PyMOLTool)
ToolManager.register("structure_from_fasta", StructureFromFastaTool)
ToolManager.register("sequence_similarity", SequenceSimilarityTool)
ToolManager.register("protein_properties", ProteinPropertiesTool)
ToolManager.register("fasta_convert", FastaConvertTool)
ToolManager.register("ncbi_search", NCBISearchTool)
ToolManager.register("enzyme_specificity_predict", EnzymeSpecificityPredictTool)
ToolManager.register("enzymecage_retrieve", EnzymeCAGERetrieveTool)
ToolManager.register("miniprot_rag", MiniProtRagTool)
ToolManager.register("enzyme_redesign", EnzymeRedesignTool)
ToolManager.register("mepam", MepamTool)
ToolManager.register("echo_tool", EchoTool) # For test

def _load_knowledge_graph_hint() -> Optional[str]:
    """Load knowledge graph to hint which tool fits which query type."""
    for base in [os.path.curdir, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]:
        path = os.path.join(base, "knowledge_graph", "graph.json")
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    kg = json.load(f)
                rel = kg.get("relationships", {})
                if rel:
                    return f"Query-type hints: {json.dumps(rel)[:400]}"
            except Exception:
                pass
    return None


# Cached graphs per intent (enzyme_mining vs specific_task use different tool sets)
_run_tool_task_checkpointer = None
_run_tool_task_graphs: dict = {}


def run_tool_task(
    user_input: str,
    max_steps: int = 5,
    use_review: bool = True,
    session: Optional["SessionState"] = None,
    thread_id: str = "default",
) -> Dict[str, Any]:
    """
    MiniProt agent flow via LangGraph (memory and tools). Returns success, data, history, tool_used.
    Graph and checkpointer are cached so repeated calls do not rebuild the agent.
    """
    global _run_tool_task_checkpointer, _run_tool_task_graphs
    try:
        from agent.graph import create_minprot_agent, run_langgraph_agent, get_intent_for_request
        from langgraph.checkpoint.memory import MemorySaver
        if _run_tool_task_checkpointer is None:
            _run_tool_task_checkpointer = MemorySaver()
        # Ensure we have at least one graph for get_state (for "proceed" intent preservation)
        if "specific_task" not in _run_tool_task_graphs:
            _run_tool_task_graphs["specific_task"] = create_minprot_agent(
                checkpointer=_run_tool_task_checkpointer,
                intent="specific_task",
            )
        intent = get_intent_for_request(
            user_input, thread_id, _run_tool_task_checkpointer,
            _run_tool_task_graphs["specific_task"],
        )
        if intent not in _run_tool_task_graphs:
            _run_tool_task_graphs[intent] = create_minprot_agent(
                checkpointer=_run_tool_task_checkpointer,
                intent=intent,
            )
        return run_langgraph_agent(
            user_input,
            thread_id=thread_id,
            graph=_run_tool_task_graphs[intent],
            checkpointer=_run_tool_task_checkpointer,
            intent=intent,
        )
    except Exception as e:
        logger.exception("LangGraph agent failed: %s", e)
        return {"success": False, "error": str(e), "data": {}, "history": []}

    # ----- Legacy path (commented out): LLM select_tool → execute → review_tool_output loop -----
    # from session_state import SessionState as SS
    # llm = LLMClient()
    # schemas = ToolManager.get_all_schemas()
    # if not schemas:
    #     return {"success": False, "error": "No tools registered", "data": {}}
    # session_summary = session.get_session_summary_for_llm() if session else ""
    # current_query = user_input
    # step = 0
    # last_output: Optional[Dict[str, Any]] = None
    # history: List[Dict[str, Any]] = []
    # while step < max_steps:
    #     step += 1
    #     logger.info("Step %s: selecting tool for query: %s", step, current_query[:80])
    #     selection = llm.select_tool(current_query, schemas, session_summary=session_summary)
    #     if selection["type"] == "error":
    #         return {"success": False, "error": selection.get("content", "LLM selection failed"), "data": {}, "history": history}
    #     if selection["type"] == "direct":
    #         return {"success": True, "data": {"answer": selection.get("content", ""), "raw": selection.get("raw")}, "history": history}
    #     tool_name = selection["data"]["tool"]
    #     params = dict(selection["data"].get("parameters") or {})
    #     if "query" not in params and current_query:
    #         params["query"] = current_query
    #     if session and params.get("session_fasta_paths"):
    #         fasta_paths = session.get_recent_fasta_paths(n=20)
    #         if fasta_paths:
    #             params["_session_fasta_paths"] = fasta_paths
    #         params["session_fasta_paths"] = True
    #     try:
    #         result = ToolManager.run_tool(tool_name, **params)
    #     except Exception as e:
    #         logger.exception("Tool execution failed: %s", e)
    #         return {"success": False, "error": f"Tool error: {str(e)}", "data": {}, "history": history}
    #     history.append({"step": step, "tool": tool_name, "params": params, "result_preview": str(result)[:200]})
    #     last_output = result
    #     if not use_review:
    #         return {"success": True, "data": result, "history": history, "tool_used": tool_name}
    #     context = "; ".join([f"Step {h['step']}: {h['tool']}" for h in history[:-1]]) if len(history) > 1 else None
    #     review = llm.review_tool_output(user_input, tool_name, result, context=context)
    #     if not review.get("valid", True):
    #         logger.warning("Review rejected: %s", review.get("reason"))
    #         if step == 1:
    #             current_query = f"{user_input} (previous attempt failed: {review.get('reason', '')})"
    #             step -= 1
    #             continue
    #         return {"success": False, "error": review.get("reason", "Output rejected by reviewer"), "data": result, "history": history}
    #     next_step = review.get("next_step")
    #     if not next_step:
    #         return {"success": True, "data": result, "history": history, "tool_used": tool_name}
    #     current_query = next_step
    #     if session:
    #         session_summary = session.get_session_summary_for_llm()
    #     logger.info("Next step: %s", next_step)
    # return {"success": True, "data": last_output or {}, "history": history, "message": "Max steps reached; returning last result.", "tool_used": history[-1]["tool"] if history else None}
