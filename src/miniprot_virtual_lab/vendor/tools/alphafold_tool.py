"""
AlphaFold tool: download 3D structures from the AlphaFold Database (EBI) by UniProt accession.
Does not run structure prediction; use structure_from_fasta, OmegaFold, or ESMFold for that.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool
import os
import re
import requests
from typing import Dict, Any, List, Optional, Tuple

try:
    from utils.path_utils import safe_dir, safe_filename, resolve_output_dir
except ImportError:
    from ..utils.path_utils import safe_dir, safe_filename, resolve_output_dir

AF_API = "https://alphafold.ebi.ac.uk/api/prediction"
AF_FILES_BASE = "https://alphafold.ebi.ac.uk/files"
UNIPROT_BASE = "https://rest.uniprot.org/uniprotkb"
UNIPROT_SEARCH = f"{UNIPROT_BASE}/search"
AF_MODEL_VERSIONS = ("v4", "v3", "v2")


class AlphaFoldTool(BaseTool):
    def __init__(self):
        self._name = "alphafold"
        self._description = (
            "Download AlphaFold Database structures (PDB/mmCIF) by UniProt accession. "
            "Pass uniprot_ids, fasta_paths (parses headers for accessions), session_fasta_paths=true after a UniProt FASTA download, "
            "or query (protein name) to resolve to UniProt then fetch. Does not predict structures."
        )
        self.BASE_URL = AF_API

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
                            "enum": ["download", "get_structure"],
                            "description": "Both download structures from AlphaFold DB only. get_structure is an alias that resolves query to UniProt accessions first.",
                            "default": "download",
                        },
                        "query": {
                            "type": "string",
                            "description": "Protein name, gene, or search term; resolved via UniProt search to accessions, then structures downloaded from AlphaFold DB.",
                        },
                        "uniprot_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "UniProt accession(s), e.g. P12345, Q8JUX6.",
                        },
                        "fasta_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "FASTA file paths; accessions are parsed from headers (e.g. sp|P12345|...).",
                        },
                        "session_fasta_paths": {
                            "type": "boolean",
                            "description": "If true, use FASTA paths injected from the session (e.g. after uniprot_search download).",
                            "default": False,
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save downloaded structures.",
                            "default": "data/outputs/alphafold",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max UniProt hits when resolving query (default 1).",
                            "default": 1,
                        },
                        "reviewed_only": {
                            "type": "boolean",
                            "description": "When resolving query, restrict to Swiss-Prot (reviewed).",
                            "default": True,
                        },
                        "formats": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["pdb", "cif", "bcif"]},
                            "description": "Structure formats to download (default pdb).",
                        },
                    },
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or "download").strip().lower()
        if action == "predict":
            return {
                "success": False,
                "error": (
                    "This tool only downloads structures from the AlphaFold Database. "
                    "For prediction from sequence use structure_from_fasta, omegafold, or esmfold."
                ),
                "data": {},
            }
        if action in ("download", "get_structure", ""):
            return self._download_from_database(kwargs)
        return {
            "success": False,
            "error": f"Unknown action: {action}. Use download or get_structure (AlphaFold DB download only).",
            "data": {},
        }

    def _list_from_ids_arg(self, raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return [s.strip() for s in raw.replace(";", ",").split(",") if s.strip()]
        if isinstance(raw, list):
            out = []
            for x in raw:
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return out
        return []

    def _collect_accessions(self, kwargs: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """
        Build ordered unique UniProt accessions from explicit IDs, uniprot_id alias, FASTA paths, session FASTA, then query.
        Returns (accessions, notes_for_message).
        """
        notes: List[str] = []
        seen = set()
        out: List[str] = []

        def add_many(ids: List[str], source: str) -> None:
            for u in ids:
                u = (u or "").strip()
                if not u or u in seen:
                    continue
                seen.add(u)
                out.append(u)
            if ids and source:
                notes.append(f"{source}: {len([i for i in ids if (i or '').strip()])} id(s) considered")

        add_many(self._list_from_ids_arg(kwargs.get("uniprot_ids")), "")
        uid_one = kwargs.get("uniprot_id")
        if uid_one:
            add_many(self._list_from_ids_arg(uid_one if isinstance(uid_one, list) else str(uid_one)), "uniprot_id")

        fasta_paths = list(kwargs.get("fasta_paths") or [])
        if kwargs.get("session_fasta_paths") and kwargs.get("_session_fasta_paths"):
            fasta_paths = list(kwargs["_session_fasta_paths"]) + fasta_paths

        if fasta_paths:
            try:
                from utils.fasta_parser import parse_fasta_paths, extract_uniprot_ids_from_entries
            except ImportError:
                from ..utils.fasta_parser import parse_fasta_paths, extract_uniprot_ids_from_entries
            entries = parse_fasta_paths(fasta_paths)
            from_fasta = extract_uniprot_ids_from_entries(entries)
            if from_fasta:
                add_many(from_fasta, "FASTA")
            elif entries:
                notes.append("FASTA provided but no UniProt accessions parsed from headers.")

        query = (kwargs.get("query") or "").strip()
        if not out and query:
            limit = min(int(kwargs.get("limit") or 1), 20)
            reviewed_only = bool(kwargs.get("reviewed_only", True))
            entries = self._resolve_to_uniprot_entries(query, limit, reviewed_only=reviewed_only)
            add_many([e["accession"] for e in entries], "UniProt search")

        return out, notes

    def _resolve_to_uniprot_entries(self, query: str, limit: int, reviewed_only: bool = True) -> List[Dict[str, Any]]:
        entries = []
        try:
            q = query.strip()
            effective_query = f"({q}) AND (reviewed:true)" if reviewed_only else q
            params = {
                "query": effective_query,
                "size": min(limit, 20),
                "fields": "accession,id,protein_name,sequence",
                "format": "json",
            }
            resp = requests.get(UNIPROT_SEARCH, params=params, timeout=30)
            if resp.status_code != 200:
                return entries
            data = resp.json()
            for item in data.get("results", []):
                acc = item.get("primaryAccession") or item.get("uniProtkbId")
                if not acc:
                    continue
                seq = (item.get("sequence") or {}).get("value", "")
                name = "Unknown"
                try:
                    pd = item.get("proteinDescription") or {}
                    rec = pd.get("recommendedName")
                    if rec:
                        name = (rec.get("fullName") or {}).get("value", "Unknown")
                    elif (pd.get("submissionNames") or [{}]) and isinstance(pd["submissionNames"][0], dict):
                        name = (pd["submissionNames"][0].get("fullName") or {}).get("value", "Unknown")
                except (KeyError, TypeError, IndexError):
                    pass
                entries.append({"accession": acc, "sequence": seq, "protein_name": name})
        except Exception:
            pass
        return entries

    def _try_direct_alphafold_download(
        self, uid: str, output_dir: str, formats: List[str]
    ) -> Dict[str, List[str]]:
        entry_id = f"AF-{uid}-F1"
        result: Dict[str, List[str]] = {fmt: [] for fmt in formats}
        for fmt in formats:
            ext = "pdb" if fmt == "pdb" else ("cif" if fmt == "cif" else "bcif")
            for ver in AF_MODEL_VERSIONS:
                url = f"{AF_FILES_BASE}/{entry_id}-model_{ver}.{ext}"
                try:
                    r = requests.get(url, timeout=60)
                    if r.status_code != 200 or len(r.content) < 500:
                        continue
                    path = os.path.join(output_dir, f"{entry_id}.{ext}")
                    with open(path, "wb") as f:
                        f.write(r.content)
                    if fmt == "pdb":
                        try:
                            from utils.pdb_clean import clean_pdb_for_docking
                        except ImportError:
                            from ..utils.pdb_clean import clean_pdb_for_docking
                        clean_pdb_for_docking(path, remove_hetatm=True, remove_water=True)
                    result[fmt].append(path)
                    break
                except Exception:
                    continue
        return result

    def _entry_id_str(self, entry: Dict[str, Any], uid: str) -> str:
        return (entry.get("entryId") or entry.get("entry_id") or f"AF-{uid}-F1").strip()

    def _pick_single_af_entry(self, entries: List[Any], uid: str) -> Optional[Dict[str, Any]]:
        """
        Choose one AlphaFold DB prediction per UniProt accession.
        Prefers canonical AF-{ACCESSION}-F1, then lowest fragment index F2, F3, ...
        Avoids downloading every fragment when the API returns many entries.
        """
        uid_u = (uid or "").strip().upper()
        if not uid_u:
            return None
        dicts = [e for e in entries if isinstance(e, dict)]
        if not dicts:
            return None

        def eid_of(e: Dict[str, Any]) -> str:
            return self._entry_id_str(e, uid)

        canonical = f"AF-{uid_u}-F1"
        for e in dicts:
            if eid_of(e).upper() == canonical:
                return e

        best: Optional[Dict[str, Any]] = None
        best_n = 10**9
        pat_acc = re.compile(rf"^AF-{re.escape(uid_u)}-F(\d+)$", re.I)
        for e in dicts:
            m = pat_acc.match(eid_of(e))
            if m:
                n = int(m.group(1))
                if n < best_n:
                    best_n = n
                    best = e
        if best is not None:
            return best

        for e in dicts:
            ua = e.get("uniprotAccession") or e.get("uniprot_accession")
            if isinstance(ua, str) and ua.strip().upper() == uid_u:
                return e

        numeric_af = re.compile(r"^AF-\d{6,}$", re.I)
        with_accession_in_id = [e for e in dicts if uid_u in eid_of(e).upper()]
        if with_accession_in_id:
            best = None
            best_n = 10**9
            for e in with_accession_in_id:
                m = re.search(r"-F(\d+)$", eid_of(e), flags=re.I)
                n = int(m.group(1)) if m else 0
                if n < best_n:
                    best_n = n
                    best = e
            return best

        non_numeric = [e for e in dicts if not numeric_af.match(eid_of(e))]
        if non_numeric:
            return non_numeric[0]
        return dicts[0]

    def _download_files_for_af_entry(
        self, entry: Dict[str, Any], uid: str, output_dir: str, formats: List[str]
    ) -> Dict[str, List[str]]:
        downloaded: Dict[str, List[str]] = {fmt: [] for fmt in formats}
        entry_id = self._entry_id_str(entry, uid)
        for fmt in formats:
            url_key = f"{fmt}Url" if fmt != "bcif" else "bcifUrl"
            url = entry.get(url_key) or entry.get(fmt + "_url")
            if not url:
                url = f"{AF_FILES_BASE}/{entry_id}-model_v4.{'pdb' if fmt == 'pdb' else ('cif' if fmt == 'cif' else 'bcif')}"
            try:
                r = requests.get(url, timeout=60)
                if r.status_code != 200:
                    continue
                ext = "pdb" if fmt == "pdb" else ("cif" if fmt == "cif" else "bcif")
                path = os.path.join(output_dir, f"{entry_id}.{ext}")
                with open(path, "wb") as f:
                    f.write(r.content)
                if fmt == "pdb":
                    try:
                        from utils.pdb_clean import clean_pdb_for_docking
                    except ImportError:
                        from ..utils.pdb_clean import clean_pdb_for_docking
                    clean_pdb_for_docking(path, remove_hetatm=True, remove_water=True)
                downloaded[fmt].append(path)
            except Exception:
                continue
        return downloaded

    def _download_one_accession(
        self, uid: str, output_dir: str, formats: List[str]
    ) -> Tuple[bool, Dict[str, List[str]]]:
        """One structure model per accession: canonical URL first, else a single API prediction."""
        downloaded: Dict[str, List[str]] = {fmt: [] for fmt in formats}
        got_any = False

        direct = self._try_direct_alphafold_download(uid, output_dir, formats)
        for fmt in formats:
            if direct.get(fmt):
                downloaded[fmt].extend(direct[fmt])
                got_any = True
        if got_any:
            return True, downloaded

        try:
            resp = requests.get(f"{self.BASE_URL}/{uid}", timeout=30)
            if resp.status_code == 200 and resp.content:
                data = resp.json()
                entries = data if isinstance(data, list) else (
                    data.get("predictions") or [data] if isinstance(data, dict) else []
                )
                entry = self._pick_single_af_entry(entries, uid)
                if entry:
                    chunk = self._download_files_for_af_entry(entry, uid, output_dir, formats)
                    for fmt in formats:
                        if chunk.get(fmt):
                            downloaded[fmt].extend(chunk[fmt])
                            got_any = True
        except Exception:
            pass
        return got_any, downloaded

    def _merge_downloaded(
        self, acc: str, into: Dict[str, List[str]], chunk: Dict[str, List[str]]
    ) -> bool:
        got = False
        for fmt, paths in chunk.items():
            if paths:
                into.setdefault(fmt, []).extend(paths)
                got = True
        return got

    def _download_from_database(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        output_dir = safe_dir(resolve_output_dir((kwargs.get("output_dir") or "data/outputs/alphafold").strip()))
        formats = list(kwargs.get("formats") or ["pdb"])
        if not formats or not any(f in formats for f in ("pdb", "cif", "bcif")):
            formats = ["pdb"]

        accessions, notes = self._collect_accessions(kwargs)
        if not accessions:
            err_parts = [
                "No UniProt accession to download. Provide uniprot_ids, fasta_paths (with sp|ACCESSION| headers), "
                "session_fasta_paths=true after downloading FASTA, or query (protein name) for get_structure."
            ]
            if notes:
                err_parts.append(" " + " ".join(notes))
            return {"success": False, "error": "".join(err_parts), "data": {}}

        downloaded: Dict[str, List[str]] = {fmt: [] for fmt in formats}
        results: List[Dict[str, Any]] = []
        for uid in accessions:
            ok, chunk = self._download_one_accession(uid, output_dir, formats)
            results.append({"accession": uid, "downloaded": ok})
            self._merge_downloaded(uid, downloaded, chunk)

        any_file = any(downloaded.get(fmt) for fmt in formats)
        if not any_file:
            return {
                "success": False,
                "error": (
                    f"No AlphaFold DB files retrieved for: {', '.join(accessions)}. "
                    "Entries may be missing from the database or network failed."
                ),
                "data": {
                    "results": results,
                    "downloaded": downloaded,
                    "accessions_tried": accessions,
                },
            }

        msg = f"Downloaded {sum(len(downloaded.get(f, [])) for f in formats)} structure file(s) to {output_dir}."
        if notes:
            msg += " " + " ".join(notes)
        return {
            "success": True,
            "data": {
                "results": results,
                "downloaded": downloaded,
                "message": msg,
            },
        }
