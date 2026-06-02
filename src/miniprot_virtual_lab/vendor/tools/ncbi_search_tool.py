"""
NCBI E-utilities search and FASTA retrieval.

Actions:
- search / protein_search: ESearch + ESummary for protein, nuccore, or gene.
- fetch_summary: ESummary for explicit IDs or a query.
- fetch_fasta: EFetch FASTA for protein or nuccore IDs.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/ncbi"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
VALID_DBS = {"protein", "nuccore", "gene"}

try:
    from utils.path_utils import ensure_file_permissions, resolve_output_dir, safe_dir, safe_filename, safe_run_id, workspace_root
except ImportError:
    from ..utils.path_utils import ensure_file_permissions, resolve_output_dir, safe_dir, safe_filename, safe_run_id, workspace_root


def _normalize_ids(ids: Any) -> List[str]:
    """Normalize ID inputs from lists, comma-separated strings, or whitespace-separated text."""
    if ids is None:
        return []
    raw_items: List[str] = []
    if isinstance(ids, (list, tuple, set)):
        for item in ids:
            raw_items.extend(_normalize_ids(item))
        return list(dict.fromkeys(raw_items))
    text = str(ids).strip()
    if not text:
        return []
    for part in re.split(r"[\s,;]+", text):
        part = part.strip()
        if part:
            raw_items.append(part)
    return list(dict.fromkeys(raw_items))


def _output_dir(path: Optional[str]) -> str:
    out_dir = resolve_output_dir((path or DEFAULT_OUTPUT_DIR).strip())
    if not os.path.isabs(out_dir):
        out_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), out_dir)))
    return safe_dir(out_dir)


class NCBISearchTool(BaseTool):
    """Search NCBI databases and fetch FASTA records via E-utilities."""

    def __init__(self) -> None:
        self._name = "ncbi_search"
        self._description = (
            "Search NCBI E-utilities databases and fetch FASTA. "
            "Use for NCBI protein/nuccore/gene keyword searches, accession/UID summaries, "
            "or batch FASTA retrieval from NCBI. This complements UniProt; it is not a BLAST tool."
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
                        "action": {
                            "type": "string",
                            "enum": ["search", "protein_search", "fetch_fasta", "fetch_summary"],
                            "description": "search/protein_search uses ESearch plus ESummary; fetch_summary summarizes IDs or query hits; fetch_fasta downloads FASTA for protein or nuccore IDs.",
                            "default": "search",
                        },
                        "query": {
                            "type": "string",
                            "description": "NCBI search term, e.g. 'insulin Homo sapiens[Organism]' or 'tryptophan synthase bacteria'.",
                        },
                        "db": {
                            "type": "string",
                            "enum": ["protein", "nuccore", "gene"],
                            "description": "NCBI database. Default protein.",
                            "default": "protein",
                        },
                        "retmax": {
                            "type": "integer",
                            "description": "Maximum number of search hits to return.",
                            "default": 20,
                        },
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "NCBI UIDs or accession.version values for fetch_summary/fetch_fasta. May also be a comma-separated string.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save FASTA output. Default: data/outputs/ncbi.",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "email": {
                            "type": "string",
                            "description": "Optional email parameter recommended by NCBI for E-utilities usage.",
                        },
                        "api_key": {
                            "type": "string",
                            "description": "Optional NCBI API key for higher rate limits.",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    def _session(self):
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
        except ImportError as e:
            raise RuntimeError(f"requests is required for ncbi_search: {e}") from e

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _base_params(self, kwargs: Dict[str, Any]) -> Dict[str, str]:
        params = {"retmode": "json", "tool": "MiniProt"}
        email = (kwargs.get("email") or "").strip()
        api_key = (kwargs.get("api_key") or "").strip()
        if email:
            params["email"] = email
        if api_key:
            params["api_key"] = api_key
        return params

    def _get_json(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._session()
        url = f"{EUTILS_BASE}/{endpoint}"
        try:
            response = session.get(url, params=params, timeout=45)
            if response.status_code == 429:
                return {"success": False, "error": "NCBI rate limited the request (HTTP 429). Retry later or provide an api_key."}
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": f"NCBI request failed for {endpoint}: {e}"}

    def _esearch(self, query: str, db: str, retmax: int, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        params = self._base_params(kwargs)
        params.update({"db": db, "term": query, "retmax": max(0, int(retmax or 20)), "retmode": "json"})
        result = self._get_json("esearch.fcgi", params)
        if not result.get("success"):
            return result
        data = result.get("data") or {}
        search = data.get("esearchresult") or {}
        ids = [str(x) for x in (search.get("idlist") or [])]
        try:
            count = int(search.get("count") or len(ids))
        except (TypeError, ValueError):
            count = len(ids)
        return {"success": True, "ids": ids, "count": count, "query_translation": search.get("querytranslation")}

    def _esummary(self, ids: List[str], db: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if not ids:
            return {"success": True, "summaries": []}
        params = self._base_params(kwargs)
        params.update({"db": db, "id": ",".join(ids), "retmode": "json"})
        result = self._get_json("esummary.fcgi", params)
        if not result.get("success"):
            return result
        data = result.get("data") or {}
        summary_result = data.get("result") or {}
        uids = summary_result.get("uids") or ids
        summaries: List[Dict[str, Any]] = []
        for uid in uids:
            doc = summary_result.get(str(uid)) or {}
            if not isinstance(doc, dict):
                continue
            title = doc.get("title") or doc.get("caption") or doc.get("name") or ""
            accession = doc.get("accessionversion") or doc.get("accession") or doc.get("caption")
            summary = {
                "uid": str(uid),
                "accession": accession,
                "title": title,
                "organism": doc.get("organism") or doc.get("taxname") or doc.get("organismname"),
                "taxid": doc.get("taxid"),
                "length": doc.get("slen") or doc.get("length"),
                "extra": doc,
            }
            summaries.append(summary)
        return {"success": True, "summaries": summaries}

    def _efetch_fasta(self, ids: List[str], db: str, output_dir: str, basename: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if db == "gene":
            return {"success": False, "error": "fetch_fasta supports db=protein or db=nuccore. Use fetch_summary for db=gene.", "data": {}}
        if not ids:
            return {"success": False, "error": "ids or query is required for fetch_fasta.", "data": {}}
        params = self._base_params(kwargs)
        params.update({"db": db, "id": ",".join(ids), "rettype": "fasta", "retmode": "text"})
        session = self._session()
        try:
            response = session.get(f"{EUTILS_BASE}/efetch.fcgi", params=params, timeout=90)
            if response.status_code == 429:
                return {"success": False, "error": "NCBI rate limited the FASTA request (HTTP 429). Retry later or provide an api_key.", "data": {}}
            response.raise_for_status()
            text = response.text.strip()
            if not text or not text.startswith(">"):
                return {"success": False, "error": f"NCBI returned no FASTA records for {len(ids)} id(s).", "data": {"ids": ids}}
        except Exception as e:
            return {"success": False, "error": f"NCBI efetch failed: {e}", "data": {}}

        safe_dir(output_dir)
        safe_base = safe_filename(basename or f"ncbi_{db}") or f"ncbi_{db}"
        fasta_path = os.path.join(output_dir, f"{safe_base}_{safe_run_id()}.fasta")
        try:
            with open(fasta_path, "w", encoding="utf-8") as f:
                f.write(text)
                f.write("\n")
            ensure_file_permissions(fasta_path)
        except Exception as e:
            return {"success": False, "error": f"Failed to write FASTA: {e}", "data": {}}

        record_count = sum(1 for line in text.splitlines() if line.startswith(">"))
        abs_path = os.path.abspath(fasta_path)
        return {
            "success": True,
            "data": {
                "message": f"Fetched {record_count} FASTA record(s) from NCBI {db}.",
                "ids": ids,
                "count": record_count,
                "fasta_path": abs_path,
                "downloaded": {"fasta": [abs_path]},
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or "search").strip().lower()
        if action == "protein_search":
            action = "search"
            kwargs["db"] = kwargs.get("db") or "protein"
        if action not in {"search", "fetch_summary", "fetch_fasta"}:
            return {"success": False, "error": "action must be one of: search, protein_search, fetch_summary, fetch_fasta.", "data": {}}

        db = (kwargs.get("db") or "protein").strip().lower()
        if db not in VALID_DBS:
            return {"success": False, "error": "db must be one of: protein, nuccore, gene.", "data": {}}

        query = (kwargs.get("query") or "").strip()
        retmax = int(kwargs.get("retmax") or 20)
        ids = _normalize_ids(kwargs.get("ids"))
        out_dir = _output_dir(kwargs.get("output_dir"))

        try:
            if action == "search":
                if not query:
                    return {"success": False, "error": "query is required for search.", "data": {}}
                search = self._esearch(query, db, retmax, kwargs)
                if not search.get("success"):
                    return {"success": False, "error": search.get("error", "NCBI search failed."), "data": {}}
                summary = self._esummary(search["ids"], db, kwargs)
                if not summary.get("success"):
                    return {"success": False, "error": summary.get("error", "NCBI summary failed."), "data": {"ids": search["ids"], "count": search["count"]}}
                return {
                    "success": True,
                    "error": "",
                    "data": {
                        "message": f"Found {len(search['ids'])} NCBI {db} hit(s) out of {search['count']} total.",
                        "ids": search["ids"],
                        "count": search["count"],
                        "returned_count": len(search["ids"]),
                        "query_translation": search.get("query_translation"),
                        "summaries": summary.get("summaries", []),
                    },
                }

            if not ids:
                if not query:
                    return {"success": False, "error": "Provide ids or query.", "data": {}}
                search = self._esearch(query, db, retmax, kwargs)
                if not search.get("success"):
                    return {"success": False, "error": search.get("error", "NCBI search failed."), "data": {}}
                ids = search["ids"]

            if action == "fetch_summary":
                summary = self._esummary(ids, db, kwargs)
                if not summary.get("success"):
                    return {"success": False, "error": summary.get("error", "NCBI summary failed."), "data": {}}
                return {
                    "success": True,
                    "error": "",
                    "data": {
                        "message": f"Fetched {len(summary.get('summaries', []))} NCBI {db} summary record(s).",
                        "ids": ids,
                        "count": len(ids),
                        "summaries": summary.get("summaries", []),
                    },
                }

            basename = safe_filename(query[:60]) if query else f"ncbi_{db}_{len(ids)}"
            return self._efetch_fasta(ids, db, out_dir, basename, kwargs)
        except RuntimeError as e:
            return {"success": False, "error": str(e), "data": {}}
        except Exception as e:
            logger.exception("ncbi_search failed: %s", e)
            return {"success": False, "error": str(e), "data": {}}
