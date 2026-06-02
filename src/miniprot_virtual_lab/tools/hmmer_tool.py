"""
HMMER tool: EBI HMMER Web API for profile HMM and sequence search.

User guide: http://eddylab.org/software/hmmer/Userguide.pdf
API docs: https://hmmer-web-docs.readthedocs.io/en/latest/api.html
         https://www.ebi.ac.uk/Tools/hmmer/api/v1/docs#/

Actions:
- phmmer: protein sequence vs sequence DB (pdb, uniprot, etc.)
- hmmscan: protein sequence vs profile DB (Pfam)
- hmmsearch: profile or multiple alignment vs sequence DB (builds HMM from alignment on server)
- jackhmmer: iterative protein search
- get_result: fetch job result by ID (poll until SUCCESS or failed)
- taxonomy: taxonomy tree for a result ID
- architecture: domain architecture for a result ID

Result format follows EBI/appendices: result has stats and hits (array of sequence hashes with name, acc, score, evalue, metadata). When downloading hit sequences we use (1) EBI GET /download/{id}/full_length_fasta when a job_id is available, or (2) NCBI EFetch (db=protein, rettype=fasta) for accessions—no UniProt, no filters.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

HMMER_API_BASE = "https://www.ebi.ac.uk/Tools/hmmer/api/v1"
HMMER_RESULTS_WEB_BASE = "https://www.ebi.ac.uk/Tools/hmmer/results"
DEFAULT_OUTPUT_DIR = "data/outputs/hmmer"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions


def _read_fasta_input(path_or_content: str) -> Optional[str]:
    """If path exists and is file, read and return contents; else return path_or_content as FASTA string."""
    if not path_or_content or not path_or_content.strip():
        return None
    s = path_or_content.strip()
    if os.path.isfile(s):
        try:
            with open(s, "r") as f:
                return f.read()
        except Exception as e:
            logger.warning("hmmer: failed to read file %s: %s", s, e)
            return None
    return s


def _submit_search(action: str, input_data: str, database: str, **extra: Any) -> Dict[str, Any]:
    """POST to EBI HMMER API. Returns response JSON or error. For phmmer: database (default uniprot), include_taxonomy / exclude_taxonomy (list of NCBI taxon IDs) from extra."""
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}
    if action == "phmmer":
        url = f"{HMMER_API_BASE}/search/phmmer"
    elif action == "hmmscan":
        url = f"{HMMER_API_BASE}/search/hmmscan"
    elif action == "hmmsearch":
        url = f"{HMMER_API_BASE}/search/hmmsearch"
    elif action == "jackhmmer":
        url = f"{HMMER_API_BASE}/search/jackhmmer"
    else:
        return {"success": False, "error": f"Unknown search action: {action}"}
    payload = {"database": database, "input": input_data}
    if extra.get("email"):
        payload["email_address"] = str(extra["email"]).strip()
    def _normalize_taxonomy_ids(val: Any, db: str) -> List[int]:
        if val is None:
            return []
        if isinstance(val, (list, tuple)):
            out = []
            for x in val:
                out.extend(_normalize_taxonomy_ids(x, db))
            return list(dict.fromkeys(out))
        s = str(val).strip()
        if s.isdigit():
            return [int(s)]
        resolved = _taxonomy_search(s, db)
        return resolved if resolved else []

    inc = _normalize_taxonomy_ids(extra.get("include_taxonomy"), database)
    if inc:
        payload["include_taxonomy"] = inc
    exc = _normalize_taxonomy_ids(extra.get("exclude_taxonomy"), database)
    if exc:
        payload["exclude_taxonomy"] = exc
    payload.update({k: v for k, v in extra.items() if k not in ("email", "include_taxonomy", "exclude_taxonomy") and v is not None})
    try:
        r = requests.post(url, json=payload, headers={"Accept": "application/json"}, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {"success": False, "error": str(e), "response_text": getattr(e, "response", None) and getattr(e.response, "text", "")[:500]}


def _job_status_nl_summary(job_results: List[Dict[str, Any]]) -> str:
    """Build a short natural language summary from job_results (status, SUCCESS, PENDING, RUNNING, EXPIRED, FAILED)."""
    if not job_results:
        return ""
    statuses = [str((j.get("status") or "")).upper() for j in job_results]
    n_success = sum(1 for s in statuses if s == "SUCCESS")
    n_running = sum(1 for s in statuses if s in ("PENDING", "RUNNING"))
    n_expired = sum(1 for s in statuses if s in ("EXPIRED", "NOT FOUND", "GONE"))
    n_failed = sum(1 for s in statuses if s == "FAILED") - n_expired
    parts = []
    if n_success == len(job_results):
        parts.append("All results are available. Proceeding with download of results.")
    elif n_success > 0:
        parts.append(f"Results are available for {n_success} job(s); download completed for these.")
    if n_running > 0:
        parts.append("Some jobs are still running. You can re-run fetch_results or summarize later to retrieve results.")
    if n_expired > 0:
        parts.append("One or more jobs have expired (EBI removes old results after some time). Resubmit the search if you need the data.")
    if n_failed > 0:
        parts.append(f"{n_failed} job(s) failed (check job_results for errors).")
    return " ".join(parts) if parts else "Job status summary unavailable."


def _taxonomy_search(query: str, database: str = "uniprot") -> List[int]:
    """Resolve taxonomy name (e.g. 'Archaea') to NCBI taxon IDs via EBI GET /taxonomy/search. Returns list of IDs."""
    try:
        import requests
    except ImportError:
        return []
    url = f"{HMMER_API_BASE}/taxonomy/search"
    try:
        r = requests.get(url, params={"q": query.strip(), "database": database}, headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return [int(x["id"]) for x in data if isinstance(x, dict) and x.get("id") is not None]
        return []
    except (requests.RequestException, (ValueError, TypeError, KeyError)) as e:
        logger.warning("hmmer taxonomy search failed for %r: %s", query, e)
        return []


def _get_result(job_id: str, page: int = 1, page_size: int = 500) -> Dict[str, Any]:
    """GET one page of job result from EBI HMMER API. Returns dict with status, result (with hits or list of per-query results), page_count."""
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}
    url = f"{HMMER_API_BASE}/result/{job_id}"
    try:
        r = requests.get(
            url,
            params={"page": page, "page_size": page_size},
            headers={"Accept": "application/json"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return {"status": "SUCCESS", "result": data, "page_count": 1}
        if isinstance(data, dict):
            return data
        return {"status": "SUCCESS", "result": data, "page_count": 1}
    except requests.RequestException as e:
        return {"success": False, "error": str(e)}


def _get_result_all_pages(job_id: str, page_size: int = 500) -> Dict[str, Any]:
    """Fetch all pages of result for a job; returns same shape as _get_result with merged hits."""
    all_hits: List[Dict[str, Any]] = []
    page = 1
    page_count = 1
    while page <= page_count:
        res = _get_result(job_id, page=page, page_size=page_size)
        if res.get("success") is False:
            return res
        status = (res.get("status") or "").upper()
        if status != "SUCCESS":
            return res
        inner = res.get("result") or res
        stats_here = None
        if isinstance(inner, dict):
            hits = inner.get("hits") or []
            all_hits.extend(hits)
            stats_here = inner.get("stats")
            page_count = res.get("page_count") or 1
            if not hits or len(hits) < page_size:
                break
        elif isinstance(inner, list):
            for item in inner:
                if isinstance(item, dict):
                    all_hits.extend(item.get("hits") or [])
                    if stats_here is None and item.get("stats") is not None:
                        stats_here = item.get("stats")
            break
        else:
            break
        page += 1
    last_result = res.get("result") if isinstance(res, dict) else None
    if stats_here is None and isinstance(last_result, dict):
        stats_here = last_result.get("stats")
    return {
        "status": "SUCCESS",
        "result": {"hits": all_hits, "stats": stats_here},
        "success": True,
    }


def _download_fasta_ebi(job_id: str, fmt: str = "full_length_fasta") -> Optional[str]:
    """Download FASTA (or full_length_fasta) for a result from EBI. Returns FASTA string or None."""
    try:
        import requests
    except ImportError:
        return None
    url = f"{HMMER_API_BASE}/download/{job_id}/{fmt}"
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        return r.text if r.text and r.text.strip() else None
    except requests.RequestException as e:
        logger.warning("hmmer EBI download %s failed: %s", fmt, e)
        return None


def _get_taxonomy(job_id: str) -> Dict[str, Any]:
    """GET taxonomy tree for a result ID."""
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}
    url = f"{HMMER_API_BASE}/taxonomy/{job_id}/tree"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {"success": False, "error": str(e)}


def _get_architecture(job_id: str) -> Dict[str, Any]:
    """GET domain architecture for a result ID."""
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests not installed"}
    url = f"{HMMER_API_BASE}/architecture/{job_id}"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        return {"success": False, "error": str(e)}


NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
NCBI_CHUNK_SIZE = 200  # NCBI recommends batching; stay under URL/response limits
NCBI_DELAY_SEC = 0.35   # Stay under 3 requests/sec

def _fetch_fasta_ncbi(accessions: List[str]) -> str:
    """Fetch multi-FASTA from NCBI protein database by accession. No filters. Chunks requests and rate-limits. Returns concatenated FASTA string (may be empty if none found)."""
    if not accessions:
        return ""
    try:
        import requests
    except ImportError:
        logger.warning("hmmer: requests not installed")
        return ""
    out: List[str] = []
    for i in range(0, len(accessions), NCBI_CHUNK_SIZE):
        chunk = accessions[i : i + NCBI_CHUNK_SIZE]
        ids = ",".join(chunk)
        try:
            r = requests.get(
                NCBI_EFETCH_URL,
                params={"db": "protein", "id": ids, "rettype": "fasta", "retmode": "text", "tool": "miniprot", "email": "miniprot@local"},
                timeout=60,
            )
            r.raise_for_status()
            if r.text and r.text.strip():
                out.append(r.text.strip())
        except requests.RequestException as e:
            logger.warning("hmmer NCBI efetch chunk failed: %s", e)
        if i + NCBI_CHUNK_SIZE < len(accessions):
            time.sleep(NCBI_DELAY_SEC)
    return "\n".join(out) if out else ""


class HMMERTool(BaseTool):
    """Run HMMER via EBI Web API: phmmer, hmmscan, hmmsearch, jackhmmer; get results; taxonomy and domain architecture."""

    @property
    def name(self) -> str:
        return "hmmer"

    @property
    def description(self) -> str:
        return (
            "HMMER: sequence and profile search via EBI API. When user says 'HMM search' or 'phmmer' with a .fasta file: submit phmmer to EBI, always use UniProt database, optional taxonomy filter (include_taxonomy / exclude_taxonomy). After results: download ALL hits (no limits) to FASTA. "
            "Actions: phmmer (FASTA vs UniProt; download all hits), hmmscan, hmmsearch, get_result, fetch_results, taxonomy, architecture, summarize. "
            "Pass input or fasta_path; database defaults to uniprot for phmmer. Optional include_taxonomy (NCBI taxon IDs) if user mentions taxonomy. Result URLs: https://www.ebi.ac.uk/Tools/hmmer/results/<id>/"
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "hmmer",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: phmmer, hmmscan, hmmsearch, jackhmmer, get_result, fetch_results (collect results from all ids in a result JSON), taxonomy, architecture, summarize. No hmmbuild.",
                        "enum": ["phmmer", "hmmscan", "hmmsearch", "jackhmmer", "get_result", "fetch_results", "taxonomy", "architecture", "summarize"],
                    },
                    "result_path": {
                        "type": "string",
                        "description": "Path to a saved HMMER result JSON file. Required for summarize and fetch_results. For fetch_results: accepts (1) already-fetched JSON with result.hits (get sequences and CSV from this file) or (2) JSON with list of job 'id's to fetch from EBI.",
                    },
                    "email": {
                        "type": "string",
                        "description": "Optional. Email address for job-completion notification (if supported by EBI HMMER service). Pass when submitting phmmer/hmmscan/hmmsearch/jackhmmer.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional output path for FASTA or other output files when applicable.",
                    },
                    "input": {
                        "type": "string",
                        "description": "FASTA sequence(s) or path to FASTA file. For hmmsearch can be alignment or HMM. Required for phmmer, hmmscan, hmmsearch, jackhmmer.",
                    },
                    "fasta_path": {
                        "type": "string",
                        "description": "Path to FASTA file (alternative to input). Content is read and sent as input.",
                    },
                    "database": {
                        "type": "string",
                        "description": "Target database for phmmer: always use uniprot (default). For hmmsearch: uniprot, pdb; for hmmscan: pfam. EBI phmmer databases: uniprot, swissprot, pdb, reference proteomes, etc.",
                        "default": "uniprot",
                    },
                    "include_taxonomy": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "NCBI taxonomy IDs to restrict phmmer search to (only hits in these taxa). Optional; use when user mentions taxonomy filter.",
                    },
                    "exclude_taxonomy": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "NCBI taxonomy IDs to exclude from phmmer search. Optional.",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Job ID (UUID) from a previous submit. Required for get_result, taxonomy, architecture.",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "If true, after submit poll get_result until SUCCESS or failure (default true for submit actions).",
                        "default": True,
                    },
                    "max_wait_seconds": {
                        "type": "integer",
                        "description": "Max seconds to poll when wait=true (default 300).",
                        "default": 300,
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Directory to save result JSON (optional).",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "max_evalue": {
                        "type": "number",
                        "description": "After hmmsearch: filter hits by e-value <= this (optional).",
                    },
                    "min_score": {
                        "type": "number",
                        "description": "After hmmsearch: filter hits by score >= this (optional).",
                    },
                    "fetch_sequences": {
                        "type": "boolean",
                        "description": "After hmmsearch: if true (default), fetch hit accessions and their sequences (FASTA from EBI or NCBI). If false, only return result JSON.",
                        "default": True,
                    },
                    "save_csv": {
                        "type": "boolean",
                        "description": "For fetch_results: if true, write a CSV of accession, identifier, organism, description to output_dir. Use when user asks to save accession IDs and organism to CSV.",
                        "default": False,
                    },
                    "save_fasta": {
                        "type": "boolean",
                        "description": "For fetch_results: if true, download all hit sequences (EBI direct FASTA when job_id available; else NCBI EFetch for all accessions, no filters) and write one FASTA file to output_dir.",
                        "default": False,
                    },
                },
                "required": ["action"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or kwargs.get("method") or "").strip().lower()
        # Fallback: if no action, infer from result_path (summarize) or from FASTA path in query/input (phmmer)
        if not action:
            raw_rp = kwargs.get("result_path")
            rp = (raw_rp[0] if isinstance(raw_rp, list) and raw_rp else raw_rp) if raw_rp else ""
            rp = (rp or "").strip() if isinstance(rp, str) else ""
            if rp and os.path.isfile(rp) and (rp.endswith(".json") or "hmmer" in rp.lower()):
                kwargs["result_path"] = rp
                action = "summarize"
            if not action and kwargs.get("query"):
                q = (kwargs.get("query") or "").strip()
                for m in re.finditer(r"(/[^\s]+\.json\b)", q):
                    cand = m.group(1).strip()
                    if os.path.isfile(cand) and ("hmmer" in cand.lower() or "phmmer" in cand or "hmmsearch" in cand):
                        kwargs["result_path"] = cand
                        if "get result" in q.lower() or "use the id" in q.lower() or "use the ids" in q.lower() or "get the results" in q.lower():
                            action = "fetch_results"
                        else:
                            action = "summarize"
                        break
            if not action:
                path = (kwargs.get("input") or kwargs.get("fasta_path") or "").strip()
                if not path and kwargs.get("query"):
                    q = (kwargs.get("query") or "").strip()
                    if os.path.isfile(q):
                        path = q
                    else:
                        for m in re.finditer(r"(/[^\s]+\.fasta\b)", q):
                            cand = m.group(1).strip()
                            if os.path.isfile(cand):
                                path = cand
                                break
                if path and os.path.isfile(path):
                    kwargs["input"] = path
                    action = "phmmer"
            if not action:
                return {"success": False, "error": "action is required (phmmer, hmmscan, hmmsearch, jackhmmer, get_result, fetch_results, taxonomy, architecture, summarize). Pass action and input/fasta_path or result_path.", "data": {}}

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        safe_dir(output_dir)

        raw_input = kwargs.get("input") or kwargs.get("fasta_path")
        input_data = _read_fasta_input(raw_input) if raw_input else None
        # For phmmer always use UniProt unless user explicitly chooses another database (see EBI phmmer page).
        database = (kwargs.get("database") or "uniprot").strip().lower() or "uniprot"
        job_id = (kwargs.get("job_id") or "").strip()
        wait = kwargs.get("wait", True)
        max_wait = max(10, min(3600, int(kwargs.get("max_wait_seconds") or 300)))

        # get_result, taxonomy, architecture only need job_id
        if action == "get_result":
            if not job_id:
                return {"success": False, "error": "job_id required for get_result.", "data": {}}
            data = _get_result_all_pages(job_id)
            if data.get("success") is False:
                return {"success": False, "error": data.get("error", "get_result failed"), "data": {"job_id": job_id}}
            return {"success": True, "data": {"job_id": job_id, "result": data}}

        if action == "taxonomy":
            if not job_id:
                return {"success": False, "error": "job_id required for taxonomy.", "data": {}}
            data = _get_taxonomy(job_id)
            if data.get("success") is False:
                return {"success": False, "error": data.get("error", "taxonomy failed"), "data": {"job_id": job_id}}
            return {"success": True, "data": {"job_id": job_id, "taxonomy": data}}

        if action == "architecture":
            if not job_id:
                return {"success": False, "error": "job_id required for architecture.", "data": {}}
            data = _get_architecture(job_id)
            if data.get("success") is False:
                return {"success": False, "error": data.get("error", "architecture failed"), "data": {"job_id": job_id}}
            return {"success": True, "data": {"job_id": job_id, "architecture": data}}

        if action == "fetch_results":
            raw_rp = kwargs.get("result_path")
            rp = (raw_rp[0] if isinstance(raw_rp, list) and raw_rp else raw_rp) if raw_rp else ""
            rp = (rp or "").strip() if isinstance(rp, str) else ""
            if not rp:
                return {"success": False, "error": "result_path is required for fetch_results (path to HMMER result JSON, either with result.hits or with list of job ids).", "data": {}}
            if not os.path.isfile(rp):
                return {"success": False, "error": f"result_path is not a file: {rp}", "data": {}}
            try:
                import json
                with open(rp, "r") as f:
                    data = json.load(f)
            except Exception as e:
                return {"success": False, "error": f"Failed to read result JSON: {e}", "data": {}}
            raw_result = data.get("result") or data
            all_hits: List[Dict[str, Any]] = []
            job_results: List[Dict[str, Any]] = []
            num_jobs = 0

            # Format A: already-fetched result with result.hits (e.g. saved hmmer_fetched_*.json)
            if isinstance(raw_result, dict) and "hits" in raw_result:
                hits_list = raw_result.get("hits") or []
                if isinstance(hits_list, list):
                    all_hits = [h for h in hits_list if isinstance(h, dict)]
                if not all_hits:
                    return {"success": False, "error": "Result has no hits (result.hits empty or missing).", "data": {}}
                job_results.append({"source": "result_path", "status": "SUCCESS", "hits_count": len(all_hits)})
            else:
                # Format B: list of items with 'id' (job IDs) to fetch from EBI
                if not isinstance(raw_result, list):
                    return {"success": False, "error": "fetch_results expects either (1) result with 'hits' (already-fetched JSON) or (2) a list of items with 'id' (job IDs to fetch from EBI).", "data": {}}
                items_with_id = [x for x in raw_result if isinstance(x, dict) and (x.get("id") or "").strip()]
                if not items_with_id:
                    return {"success": False, "error": "No items with 'id' found in the result list.", "data": {}}
                num_jobs = len(items_with_id)
                for item in items_with_id:
                    jid = (item.get("id") or "").strip()
                    qname = (item.get("query_name") or "").strip()
                    result_url = f"{HMMER_RESULTS_WEB_BASE}/{jid}/" if jid else ""
                    res = _get_result_all_pages(jid)
                    if res.get("success") is False:
                        err = (res.get("error") or "").lower()
                        if "404" in err or "410" in err or "not found" in err or "expired" in err or "gone" in err:
                            job_results.append({"id": jid, "query_name": qname, "result_url": result_url, "status": "EXPIRED", "error": res.get("error")})
                        else:
                            job_results.append({"id": jid, "query_name": qname, "result_url": result_url, "status": "FAILED", "error": res.get("error")})
                        continue
                    s = (res.get("status") or "").upper()
                    inner = res.get("result") or res
                    if s == "SUCCESS" and isinstance(inner, dict):
                        hits = inner.get("hits") or []
                        all_hits.extend(hits)
                        job_results.append({"id": jid, "query_name": qname, "result_url": result_url, "status": "SUCCESS", "hits_count": len(hits)})
                    else:
                        job_results.append({"id": jid, "query_name": qname, "result_url": result_url, "status": s or "PENDING"})

            accessions: List[str] = []
            for h in all_hits:
                meta = (h or {}).get("metadata") or {}
                acc = (meta.get("uniprot_accession") or meta.get("accession") or "").strip()
                if acc and acc not in accessions:
                    accessions.append(acc)
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            used_existing_hits = isinstance(raw_result, dict) and "hits" in raw_result
            out_path = ""
            if not used_existing_hits:
                out_path = os.path.join(output_dir, f"hmmer_fetched_{run_id}.json")
                try:
                    with open(out_path, "w") as f:
                        json.dump({"status": "SUCCESS", "result": {"hits": all_hits}, "job_results": job_results, "result_urls": [jr.get("result_url") for jr in job_results if jr.get("result_url")]}, f, indent=2)
                    ensure_file_permissions(out_path)
                except Exception as e:
                    logger.warning("hmmer fetch_results: could not write JSON: %s", e)
                    out_path = ""
            csv_path = ""
            if kwargs.get("save_csv") and all_hits:
                import csv
                csv_path = os.path.join(output_dir, f"hmmer_hits_{run_id}.csv")
                try:
                    with open(csv_path, "w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(["accession", "identifier", "organism", "description"])
                        seen = set()
                        for h in all_hits:
                            meta = (h or {}).get("metadata") or {}
                            acc = (meta.get("uniprot_accession") or meta.get("accession") or "").strip()
                            if not acc or acc in seen:
                                continue
                            seen.add(acc)
                            ident = (meta.get("uniprot_identifier") or meta.get("identifier") or "").strip()
                            organism = (meta.get("species") or meta.get("organism") or "").strip()
                            desc = (meta.get("description") or "").strip()
                            w.writerow([acc, ident, organism, desc])
                    ensure_file_permissions(csv_path)
                except Exception as e:
                    logger.warning("hmmer fetch_results: could not write CSV: %s", e)
                    csv_path = ""
            fasta_path = ""
            if kwargs.get("save_fasta"):
                fasta_path = os.path.join(output_dir, f"hmmer_sequences_{run_id}.fasta")
                fasta_parts: List[str] = []
                if not used_existing_hits and num_jobs > 0 and job_results:
                    for jr in job_results:
                        if (jr.get("status") or "").upper() != "SUCCESS" or not jr.get("id"):
                            continue
                        ebi_fasta = _download_fasta_ebi(jr["id"], "full_length_fasta")
                        if ebi_fasta and ebi_fasta.strip():
                            fasta_parts.append(ebi_fasta.strip())
                    if fasta_parts:
                        fasta_content = "\n".join(fasta_parts)
                        if not fasta_content.endswith("\n"):
                            fasta_content += "\n"
                    else:
                        fasta_content = None
                else:
                    fasta_content = None
                if (not fasta_content or not fasta_content.strip()) and accessions:
                    fasta_content = _fetch_fasta_ncbi(accessions)
                if fasta_content and fasta_content.strip():
                    try:
                        with open(fasta_path, "w", encoding="utf-8") as f:
                            f.write(fasta_content)
                        ensure_file_permissions(fasta_path)
                    except Exception as e:
                        logger.warning("hmmer fetch_results: could not write FASTA: %s", e)
                        fasta_path = ""
                else:
                    if not fasta_parts and accessions:
                        logger.warning("hmmer fetch_results: no FASTA from EBI or NCBI for %d accessions", len(accessions))
                    fasta_path = ""
            if used_existing_hits:
                msg = f"Used hits from result file. Total hits: {len(all_hits)}; unique accessions: {len(accessions)}."
            else:
                msg = f"Fetched results for {num_jobs} job(s) from EBI (all pages). Total hits: {len(all_hits)}; unique accessions: {len(accessions)}. Web result links: {HMMER_RESULTS_WEB_BASE}/<id>/"
            if out_path:
                msg += f" JSON saved to {out_path}"
            elif used_existing_hits:
                msg += f" Result file: {rp}"
            if csv_path:
                msg += f". CSV saved to {csv_path}"
            if fasta_path:
                msg += f". FASTA ({len(accessions)} sequences) saved to {fasta_path}"
            nl = _job_status_nl_summary(job_results) if not used_existing_hits else ""
            if nl:
                msg += f" — {nl}"
            return {
                "success": True,
                "data": {
                    "message": msg,
                    "status_summary": nl,
                    "result_path": os.path.abspath(rp),
                    "fetched_path": os.path.abspath(out_path) if out_path else os.path.abspath(rp),
                    "csv_path": os.path.abspath(csv_path) if csv_path else None,
                    "fasta_path": os.path.abspath(fasta_path) if fasta_path else None,
                    "jobs_fetched": num_jobs,
                    "total_hits": len(all_hits),
                    "unique_accessions": len(accessions),
                    "job_results": job_results,
                    "result_urls": [jr.get("result_url") for jr in job_results if jr.get("result_url")],
                },
            }

        if action == "summarize":
            raw_rp = kwargs.get("result_path")
            result_path = (raw_rp[0] if isinstance(raw_rp, list) and raw_rp else raw_rp) if raw_rp else ""
            result_path = (result_path or "").strip() if isinstance(result_path, str) else ""
            if not result_path:
                return {"success": False, "error": "result_path is required for action summarize (path to hmmer result JSON).", "data": {}}
            if not os.path.isfile(result_path):
                return {"success": False, "error": f"result_path is not a file: {result_path}", "data": {}}
            try:
                import json
                with open(result_path, "r") as f:
                    data = json.load(f)
            except Exception as e:
                return {"success": False, "error": f"Failed to read result JSON: {e}", "data": {}}
            status = data.get("status", "unknown")
            raw_result = data.get("result") or data
            if isinstance(raw_result, list):
                result_type = "phmmer"
                count = len(raw_result)
                pending = [item for item in raw_result if isinstance(item, dict) and (item.get("status") or "").upper() in ("PENDING", "RUNNING") and item.get("id")]
                if pending:
                    # Recheck: fetch final results for each pending job id
                    all_hits: List[Dict[str, Any]] = []
                    job_results: List[Dict[str, Any]] = []
                    for item in pending:
                        jid = (item.get("id") or "").strip()
                        if not jid:
                            continue
                        res = _get_result_all_pages(jid)
                        if res.get("success") is False:
                            err = (res.get("error") or "").lower()
                            if "404" in err or "410" in err or "not found" in err or "expired" in err or "gone" in err:
                                job_results.append({"id": jid, "query_name": item.get("query_name"), "status": "EXPIRED", "error": res.get("error")})
                            else:
                                job_results.append({"id": jid, "query_name": item.get("query_name"), "status": "FAILED", "error": res.get("error")})
                            continue
                        s = (res.get("status") or "").upper()
                        inner = res.get("result") or res
                        if s == "SUCCESS" and isinstance(inner, dict):
                            hits = inner.get("hits") or []
                            all_hits.extend(hits)
                            job_results.append({"id": jid, "query_name": item.get("query_name"), "status": "SUCCESS", "hits_count": len(hits)})
                        else:
                            job_results.append({"id": jid, "query_name": item.get("query_name"), "status": s or "PENDING"})
                    accessions: List[str] = []
                    for h in all_hits:
                        meta = (h or {}).get("metadata") or {}
                        acc = (meta.get("uniprot_accession") or meta.get("accession") or "").strip()
                        if acc and acc not in accessions:
                            accessions.append(acc)
                    sample = accessions[: 15]
                    run_id_s = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = os.path.join(output_dir, f"hmmer_phmmer_fetched_{run_id_s}.json")
                    try:
                        with open(out_path, "w") as f:
                            json.dump({"status": "SUCCESS", "result": {"hits": all_hits}, "job_results": job_results}, f, indent=2)
                        ensure_file_permissions(out_path)
                    except Exception as e:
                        logger.warning("hmmer summarize: could not write fetched result JSON: %s", e)
                        out_path = ""
                    csv_path_s = ""
                    if kwargs.get("save_csv") and all_hits:
                        import csv
                        csv_path_s = os.path.join(output_dir, f"hmmer_hits_{run_id_s}.csv")
                        try:
                            with open(csv_path_s, "w", newline="", encoding="utf-8") as f:
                                w = csv.writer(f)
                                w.writerow(["accession", "identifier", "organism", "description"])
                                seen = set()
                                for h in all_hits:
                                    meta = (h or {}).get("metadata") or {}
                                    acc = (meta.get("uniprot_accession") or meta.get("accession") or "").strip()
                                    if not acc or acc in seen:
                                        continue
                                    seen.add(acc)
                                    ident = (meta.get("uniprot_identifier") or meta.get("identifier") or "").strip()
                                    organism = (meta.get("species") or meta.get("organism") or "").strip()
                                    desc = (meta.get("description") or "").strip()
                                    w.writerow([acc, ident, organism, desc])
                            ensure_file_permissions(csv_path_s)
                        except Exception as e:
                            logger.warning("hmmer summarize: could not write CSV: %s", e)
                            csv_path_s = ""
                    msg = f"Fetched final results for {len(pending)} pending job(s). Total hits: {len(all_hits)}; unique accessions: {len(accessions)}."
                    if out_path:
                        msg += f" JSON saved to {out_path}"
                    if csv_path_s:
                        msg += f". CSV saved to {csv_path_s}"
                    nl = _job_status_nl_summary(job_results)
                    if nl:
                        msg += f" — {nl}"
                    return {
                        "success": True,
                        "data": {
                            "message": msg,
                            "status_summary": nl,
                            "result_path": os.path.abspath(result_path),
                            "fetched_path": os.path.abspath(out_path) if out_path else None,
                            "csv_path": os.path.abspath(csv_path_s) if csv_path_s else None,
                            "status": "SUCCESS",
                            "result_type": result_type,
                            "jobs_fetched": len(pending),
                            "total_hits": len(all_hits),
                            "unique_accessions": len(accessions),
                            "sample": sample,
                            "job_results": job_results,
                        },
                    }
                sample = []
                for item in raw_result[: 10]:
                    if isinstance(item, dict):
                        qname = (item.get("query_name") or "").strip()
                        if qname:
                            sample.append(qname)
                return {
                    "success": True,
                    "data": {
                        "message": f"HMMER result summary: {result_type}, status={status}, count={count} (no pending jobs to fetch).",
                        "result_path": os.path.abspath(result_path),
                        "status": status,
                        "result_type": result_type,
                        "count": count,
                        "sample": sample,
                    },
                }
            else:
                result_type = "hmmsearch"
                hits = (raw_result.get("hits") or []) if isinstance(raw_result, dict) else []
                count = len(hits)
                sample = []
                for h in hits[: 10]:
                    meta = (h or {}).get("metadata") or {}
                    acc = (meta.get("uniprot_accession") or meta.get("accession") or "").strip()
                    if acc:
                        sample.append(acc)
                return {
                    "success": True,
                    "data": {
                        "message": f"HMMER result summary: {result_type}, status={status}, count={count}.",
                        "result_path": os.path.abspath(result_path),
                        "status": status,
                        "result_type": result_type,
                        "count": count,
                        "sample": sample,
                    },
                }

        # Submit actions: phmmer, hmmscan, hmmsearch, jackhmmer
        if action not in ("phmmer", "hmmscan", "hmmsearch", "jackhmmer"):
            return {"success": False, "error": f"Unknown action: {action}. Use phmmer, hmmscan, hmmsearch, jackhmmer, get_result, fetch_results, taxonomy, architecture, or summarize.", "data": {}}
        if not input_data:
            return {"success": False, "error": "input or fasta_path required for search (FASTA sequence or path to file).", "data": {}}

        extra = {"email": kwargs.get("email")}
        if action == "phmmer":
            if kwargs.get("include_taxonomy") is not None:
                extra["include_taxonomy"] = kwargs["include_taxonomy"]
            if kwargs.get("exclude_taxonomy") is not None:
                extra["exclude_taxonomy"] = kwargs["exclude_taxonomy"]
        resp = _submit_search(action, input_data, database, **extra)
        if resp.get("success") is False:
            return {"success": False, "error": resp.get("error", "submit failed"), "data": {}}

        # EBI returns e.g. {"id": "uuid", ...}
        submitted_id = resp.get("id") or resp.get("job_id") or resp.get("jobId")
        if not submitted_id:
            return {"success": True, "data": {"message": "Search submitted but no job id in response.", "response": resp}}

        if not wait:
            return {"success": True, "data": {"message": f"{action} submitted.", "job_id": submitted_id, "result_url": f"{HMMER_API_BASE}/result/{submitted_id}"}}

        # Poll until SUCCESS or FAIL
        poll_interval = 5
        start = time.time()
        while time.time() - start < max_wait:
            result = _get_result(submitted_id, page=1, page_size=50)
            if not isinstance(result, dict):
                result = {"status": "SUCCESS", "result": result}
            if result.get("success") is False:
                return {"success": False, "error": result.get("error", "get_result failed"), "data": {"job_id": submitted_id}}
            status = (result.get("status") or "").upper()
            if status == "SUCCESS":
                result = _get_result_all_pages(submitted_id)
                if result.get("success") is False:
                    return {"success": False, "error": result.get("error", "get_result_all_pages failed"), "data": {"job_id": submitted_id}}
                out_path = os.path.join(output_dir, f"hmmer_{action}_{submitted_id[:8]}.json")
                try:
                    import json
                    with open(out_path, "w") as f:
                        json.dump(result, f, indent=2)
                    ensure_file_permissions(out_path)
                except Exception as e:
                    logger.warning("hmmer: could not write result JSON: %s", e)
                data_payload = {
                    "message": f"{action} completed.",
                    "job_id": submitted_id,
                    "status": status,
                    "result": result,
                    "result_path": out_path,
                    "downloaded": {"hmmer_result": [os.path.abspath(out_path)]},
                }
                # phmmer: download ALL hits (no limits). EBI full_length_fasta then NCBI fallback.
                if action == "phmmer":
                    inner = result.get("result") or result
                    hits = list(inner.get("hits") or [])
                    accessions = []
                    for h in hits:
                        meta = (h or {}).get("metadata") or {}
                        acc = (meta.get("uniprot_accession") or meta.get("accession") or (h or {}).get("acc") or "").strip()
                        if acc and acc not in accessions:
                            accessions.append(acc)
                    fasta_content = _download_fasta_ebi(submitted_id, "full_length_fasta")
                    if not fasta_content or not fasta_content.strip():
                        if accessions:
                            fasta_content = _fetch_fasta_ncbi(accessions)
                    if fasta_content and fasta_content.strip():
                        seq_path = os.path.join(output_dir, f"phmmer_hits_{submitted_id[:8]}.fasta")
                        try:
                            with open(seq_path, "w") as f:
                                f.write(fasta_content)
                            ensure_file_permissions(seq_path)
                            data_payload["downloaded"]["fasta"] = [os.path.abspath(seq_path)]
                            n_seqs = fasta_content.count(">")
                            data_payload["message"] = f"{action} completed. All {n_seqs} hit sequences saved to {seq_path} (no limits)."
                        except Exception as e:
                            logger.warning("hmmer: could not write phmmer FASTA: %s", e)
                    elif accessions:
                        logger.warning("hmmer phmmer: no FASTA from EBI or NCBI for %d accessions", len(accessions))
                elif action == "hmmsearch":
                    max_evalue = kwargs.get("max_evalue")
                    min_score = kwargs.get("min_score")
                    fetch_sequences = kwargs.get("fetch_sequences", True)
                    inner = result.get("result") or result
                    hits = list(inner.get("hits") or [])
                    if max_evalue is not None:
                        try:
                            hits = [h for h in hits if (h or {}).get("evalue") is not None and float((h or {}).get("evalue", 1)) <= float(max_evalue)]
                        except (TypeError, ValueError):
                            pass
                    if min_score is not None:
                        try:
                            hits = [h for h in hits if (h or {}).get("score") is not None and float((h or {}).get("score", 0)) >= float(min_score)]
                        except (TypeError, ValueError):
                            pass
                    data_payload["filtered_hits_count"] = len(hits)
                    if fetch_sequences and hits:
                        fasta_content = _download_fasta_ebi(submitted_id, "full_length_fasta")
                        if not fasta_content or not fasta_content.strip():
                            accessions = []
                            for h in hits:
                                meta = (h or {}).get("metadata") or {}
                                acc = (meta.get("uniprot_accession") or meta.get("accession") or "").strip()
                                if acc and acc not in accessions:
                                    accessions.append(acc)
                            if accessions:
                                fasta_content = _fetch_fasta_ncbi(accessions)
                        if fasta_content and fasta_content.strip():
                            seq_path = os.path.join(output_dir, f"hmmsearch_sequences_{submitted_id[:8]}.fasta")
                            try:
                                with open(seq_path, "w") as f:
                                    f.write(fasta_content)
                                ensure_file_permissions(seq_path)
                                data_payload["downloaded"]["fasta"] = [os.path.abspath(seq_path)]
                                n_seqs = fasta_content.count(">")
                                data_payload["message"] = f"{action} completed. FASTA ({n_seqs} sequences) saved to {seq_path}."
                            except Exception as e:
                                logger.warning("hmmer: could not write fetched FASTA: %s", e)
                return {"success": True, "data": data_payload}
            if status == "FAILURE" or status == "ERROR":
                return {"success": False, "error": result.get("message") or result.get("error") or f"Job {status}", "data": {"job_id": submitted_id, "result": result}}
            time.sleep(poll_interval)

        return {"success": False, "error": f"Timed out after {max_wait}s. Check job_id later.", "data": {"job_id": submitted_id, "result_url": f"{HMMER_API_BASE}/result/{submitted_id}"}}
