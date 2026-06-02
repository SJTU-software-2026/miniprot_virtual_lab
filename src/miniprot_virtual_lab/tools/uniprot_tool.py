try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool
import logging
import os
import re
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter, Retry
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

UNIPROT_PAGE_SIZE = 500  # Recommended by UniProt for fast performance (https://www.uniprot.org/help/api_queries)
# Pagination: next page URL is in the Link header: <url>; rel="next"
_RE_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"', re.IGNORECASE)

try:
    from utils.path_utils import safe_dir, safe_run_id, resolve_output_dir, workspace_root
except ImportError:
    from ..utils.path_utils import safe_dir, safe_run_id, resolve_output_dir, workspace_root

UNIPROT_BASE = "https://rest.uniprot.org/uniprotkb"
UNIPROT_SEARCH = f"{UNIPROT_BASE}/search"
UNIPROT_STREAM = f"{UNIPROT_BASE}/stream"
RCSB_PDB_URL = "https://files.rcsb.org/download"


class UniProtTool(BaseTool):
    def __init__(self):
        self._name = "uniprot_search"
        self._description = (
            "Search UniProt; download FASTA. Default for protein/enzyme *discovery* searches: Swiss-Prot (reviewed) only. "
            "For fetching sequences by UniProt accessions or IDs from HMMER/phmmer hit lists, use from_hmmer_hit_ids=true "
            "(searches full UniProtKB including TrEMBL). For 3D by name use alphafold."
        )
        self.BASE_URL = UNIPROT_SEARCH

    @property
    def name(self):
        return self._name

    @property
    def description(self):
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
                        "query": {"type": "string", "description": "UniProt search query (e.g. GTPase AND lineage:Archaea, or gene:BRCA1 AND organism_name:\\\"Homo sapiens\\\"). Use lineage: for taxon (Archaea/Bacteria), organism_name: for species; avoid unsupported 'taxonomy:'."},
                        "limit": {
                            "type": "integer",
                            "description": "Max number of results. If user says 'all' or 'download all' use a large number (e.g. 100000) or pass 'all' via string; tool will fetch all via pagination. If user wants 'example(s)' use 5. If user gives an explicit number, use that. Default 10.",
                            "default": 10,
                        },
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fields to return (e.g. accession, protein_name, sequence). Leave empty for defaults.",
                        },
                        "download_formats": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["fasta", "pdb", "json", "xml"]},
                            "description": "Required to write files to disk. When user says 'download sequence' or 'get FASTA' set to ['fasta']; otherwise no FASTA file is created and only in-memory results are returned.",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["fasta", "pdb", "json", "xml"],
                            "description": "Alias for download_formats. Use format='fasta' to save FASTA files.",
                        },
                        "download_format": {
                            "type": "string",
                            "enum": ["fasta", "pdb", "json", "xml"],
                            "description": "Alias for download_formats.",
                        },
                        "formats": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["fasta", "pdb", "json", "xml"]},
                            "description": "Alias for download_formats.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Directory to save downloaded files (default: 'data/outputs/uniprot').",
                        },
                        "single_fasta": {
                            "type": "boolean",
                            "description": "If true and download_formats includes fasta, write all sequences to one FASTA file.",
                            "default": False,
                        },
                        "reviewed_only": {
                            "type": "boolean",
                            "description": "If true (default), restrict to Swiss-Prot for *text* searches (gene/protein name). Ignored when from_hmmer_hit_ids=true or query is a list of accessions.",
                            "default": True,
                        },
                        "from_hmmer_hit_ids": {
                            "type": "boolean",
                            "description": "Set true when fetching FASTA for UniProt accessions from HMMER/phmmer (or similar) hit tables—uses full UniProtKB, not Swiss-Prot only.",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    @staticmethod
    def _organism_synonym_to_canonical(token: str) -> Optional[str]:
        t = (token or "").strip().strip("\"'").strip()
        if not t:
            return None
        tl = re.sub(r"[\s_]+", " ", t).strip().lower()
        if not tl:
            return None
        for alias, name in UniProtTool._ORGANISM_ALIASES:
            if alias.lower() == tl:
                return name
        return None

    @staticmethod
    def _expand_organism_name_aliases(q: str) -> str:
        """Map organism_name:human-style tokens to organism_name:\"Homo sapiens\"."""

        def _quoted(match) -> str:
            inner = (match.group(1) or "").strip()
            canon = UniProtTool._organism_synonym_to_canonical(inner)
            if canon:
                return f'organism_name:"{canon}"'
            return match.group(0)

        q2 = re.sub(r'\borganism_name:\s*"([^"]*)"', _quoted, q, flags=re.IGNORECASE)

        def _bare(match) -> str:
            tok = (match.group(1) or "").strip()
            if re.match(r"^[A-Za-z][0-9][A-Za-z0-9]{3,}[0-9]$", tok):
                # Accession-ish token; skip
                return match.group(0)
            canon = UniProtTool._organism_synonym_to_canonical(tok.strip("'\""))
            if canon:
                return f'organism_name:"{canon}"'
            return match.group(0)

        return re.sub(r"\borganism_name:\s*([^\s\)\]]+)", _bare, q2, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Map unsupported field names to valid UniProt API fields only. Query content is set by the caller (agent)."""
        q = (query or "").strip()
        # reviewed:yes -> reviewed:true (UniProt expects true/false)
        q = re.sub(r"\breviewed\s*:\s*yes\b", "reviewed:true", q, flags=re.IGNORECASE)
        q = re.sub(r"\breviewed\s*:\s*no\b", "reviewed:false", q, flags=re.IGNORECASE)
        # organism:"Homo sapiens" -> organism_name:"Homo sapiens" (UniProt search field)
        q = re.sub(r"\borganism\s*:\s*\"([^\"]+)\"", r'organism_name:"\1"', q, flags=re.IGNORECASE)
        q = re.sub(r"\borganism\s*:\s*(\S+)", r"organism_name:\1", q, flags=re.IGNORECASE)
        q = UniProtTool._expand_organism_name_aliases(q)
        # taxonomy:"X" or taxonomy:X -> lineage:X (Archaea, Bacteria, etc.)
        q = re.sub(r"\btaxonomy\s*:\s*([^\s\)]+)", r"lineage:\1", q, flags=re.IGNORECASE)
        # gene:<value> with no wildcard -> gene:<value>*
        q = re.sub(r"\bgene\s*:\s*([^\s*?]+)(?!\*)", r"gene:\1*", q, flags=re.IGNORECASE)
        has_search_fields = bool(
            re.search(
                r"\b(?:gene|protein_name|organism_name|organism_id|lineage|ec:|go:|keyword|taxonomy_id):",
                q,
                re.IGNORECASE,
            )
        )
        reviewed_clause = bool(re.search(r"\breviewed\s*:\s*(?:true|false)\b", q, re.IGNORECASE))
        structured = bool(re.search(r"\bAND\b", q)) and (has_search_fields or reviewed_clause)
        # Avoid destroying structured REST queries via natural-language simplify.
        if (
            not structured
            and (
                (len(q) > 50 and re.search(r"\b(i want|don't|do not|not |download|sequence)\b", q, re.IGNORECASE))
                or len(q) > 120
            )
        ):
            q = UniProtTool._simplify_natural_language_query(q)
        return q.strip() or (query or "").strip()

    # Organism aliases: lowercase key → UniProt organism_name value.
    # Ordered longest-first so greedy matching picks multi-word names before substrings.
    _ORGANISM_ALIASES: List[tuple] = sorted([
        # Common model organisms
        ("human", "Homo sapiens"),
        ("homo sapiens", "Homo sapiens"),
        ("mouse", "Mus musculus"),
        ("mus musculus", "Mus musculus"),
        ("rat", "Rattus norvegicus"),
        ("rattus norvegicus", "Rattus norvegicus"),
        ("bovine", "Bos taurus"),
        ("bos taurus", "Bos taurus"),
        ("pig", "Sus scrofa"),
        ("sus scrofa", "Sus scrofa"),
        ("chicken", "Gallus gallus"),
        ("gallus gallus", "Gallus gallus"),
        ("rabbit", "Oryctolagus cuniculus"),
        ("sheep", "Ovis aries"),
        ("dog", "Canis lupus familiaris"),
        ("cat", "Felis catus"),
        ("horse", "Equus caballus"),
        ("zebrafish", "Danio rerio"),
        ("danio rerio", "Danio rerio"),
        ("xenopus", "Xenopus laevis"),
        ("drosophila", "Drosophila melanogaster"),
        ("drosophila melanogaster", "Drosophila melanogaster"),
        ("fruit fly", "Drosophila melanogaster"),
        ("c. elegans", "Caenorhabditis elegans"),
        ("caenorhabditis elegans", "Caenorhabditis elegans"),
        ("nematode", "Caenorhabditis elegans"),
        # Microorganisms
        ("e. coli", "Escherichia coli"),
        ("escherichia coli", "Escherichia coli"),
        ("e.coli", "Escherichia coli"),
        ("bacillus subtilis", "Bacillus subtilis"),
        ("b. subtilis", "Bacillus subtilis"),
        ("pseudomonas aeruginosa", "Pseudomonas aeruginosa"),
        ("p. aeruginosa", "Pseudomonas aeruginosa"),
        ("staphylococcus aureus", "Staphylococcus aureus"),
        ("s. aureus", "Staphylococcus aureus"),
        ("mycobacterium tuberculosis", "Mycobacterium tuberculosis"),
        ("m. tuberculosis", "Mycobacterium tuberculosis"),
        ("salmonella", "Salmonella typhimurium"),
        ("streptococcus", "Streptococcus"),
        ("helicobacter pylori", "Helicobacter pylori"),
        ("h. pylori", "Helicobacter pylori"),
        ("vibrio cholerae", "Vibrio cholerae"),
        ("clostridium", "Clostridium"),
        # Yeasts / fungi
        ("yeast", "Saccharomyces cerevisiae"),
        ("saccharomyces cerevisiae", "Saccharomyces cerevisiae"),
        ("s. cerevisiae", "Saccharomyces cerevisiae"),
        ("schizosaccharomyces pombe", "Schizosaccharomyces pombe"),
        ("s. pombe", "Schizosaccharomyces pombe"),
        ("candida albicans", "Candida albicans"),
        ("aspergillus niger", "Aspergillus niger"),
        ("aspergillus", "Aspergillus"),
        # Plants
        ("arabidopsis", "Arabidopsis thaliana"),
        ("arabidopsis thaliana", "Arabidopsis thaliana"),
        ("rice", "Oryza sativa"),
        ("oryza sativa", "Oryza sativa"),
        ("maize", "Zea mays"),
        ("zea mays", "Zea mays"),
        ("tobacco", "Nicotiana tabacum"),
        ("soybean", "Glycine max"),
        # Viruses (multi-word first so they match greedily before short substrings)
        ("human respiratory syncytial virus", "Human respiratory syncytial virus"),
        ("respiratory syncytial virus", "Human respiratory syncytial virus"),
        ("rsv", "Human respiratory syncytial virus"),
        ("sars-cov-2", "Severe acute respiratory syndrome coronavirus 2"),
        ("sars-cov2", "Severe acute respiratory syndrome coronavirus 2"),
        ("sars cov 2", "Severe acute respiratory syndrome coronavirus 2"),
        ("covid-19", "Severe acute respiratory syndrome coronavirus 2"),
        ("covid", "Severe acute respiratory syndrome coronavirus 2"),
        ("sars", "Severe acute respiratory syndrome-related coronavirus"),
        ("mers", "Middle East respiratory syndrome-related coronavirus"),
        ("hiv-1", "Human immunodeficiency virus 1"),
        ("hiv 1", "Human immunodeficiency virus 1"),
        ("hiv1", "Human immunodeficiency virus 1"),
        ("hiv-2", "Human immunodeficiency virus 2"),
        ("hiv", "Human immunodeficiency virus 1"),
        ("hepatitis c virus", "Hepatitis C virus"),
        ("hepatitis c", "Hepacivirus C"),
        ("hepatitis b virus", "Hepatitis B virus"),
        ("hepatitis b", "Hepatitis B virus"),
        ("hepatitis a", "Hepatovirus A"),
        ("ebola virus", "Zaire ebolavirus"),
        ("ebola", "Zaire ebolavirus"),
        ("zika virus", "Zika virus"),
        ("zika", "Zika virus"),
        ("dengue virus", "Dengue virus"),
        ("dengue", "Dengue virus"),
        ("influenza a", "Influenza A virus"),
        ("influenza b", "Influenza B virus"),
        ("influenza", "Influenza A virus"),
        ("measles virus", "Measles morbillivirus"),
        ("measles", "Measles morbillivirus"),
        ("rabies virus", "Rabies lyssavirus"),
        ("rabies", "Rabies lyssavirus"),
        ("herpes simplex virus 1", "Human alphaherpesvirus 1"),
        ("herpes simplex virus 2", "Human alphaherpesvirus 2"),
        ("herpes simplex", "Human alphaherpesvirus 1"),
        ("herpes", "Human alphaherpesvirus 1"),
        ("epstein-barr virus", "Human gammaherpesvirus 4"),
        ("epstein barr", "Human gammaherpesvirus 4"),
        ("ebv", "Human gammaherpesvirus 4"),
        ("human papillomavirus", "Human papillomavirus"),
        ("hpv", "Human papillomavirus"),
        ("adenovirus", "Human adenovirus"),
        ("rotavirus", "Rotavirus A"),
        ("norovirus", "Norwalk virus"),
        ("west nile virus", "West Nile virus"),
        ("chikungunya virus", "Chikungunya virus"),
        ("chikungunya", "Chikungunya virus"),
        ("nipah virus", "Nipah henipavirus"),
        ("nipah", "Nipah henipavirus"),
        ("marburg virus", "Marburg marburgvirus"),
        ("marburg", "Marburg marburgvirus"),
        ("smallpox", "Variola virus"),
        ("variola", "Variola virus"),
        ("vaccinia", "Vaccinia virus"),
        ("tobacco mosaic virus", "Tobacco mosaic virus"),
        ("tmv", "Tobacco mosaic virus"),
        ("bacteriophage t4", "Escherichia virus T4"),
        ("phage t4", "Escherichia virus T4"),
        ("lambda phage", "Escherichia virus Lambda"),
    ], key=lambda x: -len(x[0]))

    @staticmethod
    def _extract_organism(text: str) -> tuple:
        """Extract an organism/species/virus name from natural-language text.

        Returns (organism_name_for_uniprot, remaining_text_with_organism_removed).
        organism_name is None when no known organism is found.
        Matches are greedy (longest alias first) and case-insensitive.
        """
        lower = text.lower()
        for alias, uniprot_name in UniProtTool._ORGANISM_ALIASES:
            idx = lower.find(alias)
            if idx == -1:
                continue
            end = idx + len(alias)
            if idx > 0 and lower[idx - 1].isalnum():
                continue
            if end < len(lower) and lower[end].isalnum():
                continue
            remainder = (text[:idx] + " " + text[end:]).strip()
            return uniprot_name, remainder
        return None, text

    @staticmethod
    def _simplify_natural_language_query(q: str) -> str:
        """Reduce instruction-like query to a short UniProt query.

        Extracts organism/species/virus from the text (if mentioned) and adds it as an
        organism_name filter. Extracts the protein/gene term from the remaining text.
        """
        q = q.strip()
        # Remove common instruction phrases to get the substantive part
        for phrase in [
            r"i want to (download|get|find|search for)\s*",
            r"download (one|a|all|every|some|the)?\s*",
            r"one reviewed?\s*",
            r"reviewed\s*",
            r"not conotoxin[^\s]*",
            r"not (conotoxin|like)[^,.]*",
            r"\b(sequence|sequences|protein|proteins)\b",
            r"organism\s*:\s*[\"\']?[^\"\'\s)]+[\"\']?",
        ]:
            q = re.sub(phrase, " ", q, flags=re.IGNORECASE)
        q = re.sub(r"\s+", " ", q).strip()

        organism, remainder = UniProtTool._extract_organism(q)
        remainder = re.sub(r"\s+", " ", remainder).strip()

        # Extract the protein/gene term from the remainder.
        # Use all remaining meaningful tokens (not just the first) so multi-word protein
        # names like "fusion glycoprotein" stay intact.
        skip = {"and", "the", "for", "of", "from", "in", "a", "an", "one", "all",
                "yes", "true", "false", "reviewed", "sequence", "download", "get",
                "find", "search", "with", "its", "this", "that", "some"}
        tokens = re.findall(r"[a-zA-Z0-9_\-]+", remainder)
        meaningful = [t for t in tokens if t.lower() not in skip and len(t) > 1]

        if not meaningful:
            if organism:
                return f'organism_name:"{organism}"'
            return q[:200]

        # Single gene-symbol-like token (e.g. TPH2, SLC6A4) → use gene: field
        if len(meaningful) == 1 and re.match(r"^[A-Za-z]{2,10}[0-9][A-Za-z0-9]{0,4}$", meaningful[0]):
            gene_q = f"gene:{meaningful[0]}"
            if organism:
                return f'{gene_q} AND organism_name:"{organism}"'
            return gene_q

        # Multi-word protein name: join tokens into a quoted phrase for exact matching,
        # or use protein_name with wildcard for single words.
        if len(meaningful) == 1:
            protein_q = f"protein_name:{meaningful[0]}*"
        else:
            protein_phrase = " ".join(meaningful)
            protein_q = f'"{protein_phrase}"'

        if organism:
            return f'{protein_q} AND organism_name:"{organism}"'
        return protein_q

    @staticmethod
    def _looks_like_accession_id_fetch(query: str) -> bool:
        """
        True if query is one or more UniProt accessions (OR/comma-separated), not a text discovery search.
        Then search full UniProtKB so TrEMBL-only hits (e.g. from HMMER) resolve.
        """
        q = (query or "").strip()
        if not q:
            return False
        if re.search(
            r"\b(gene|protein_name|organism_name|organism_id|lineage|taxonomy|ec:|go:|reviewed)\s*:",
            q,
            re.I,
        ):
            return False
        parts = re.split(r"\s+OR\s+", q, flags=re.I)
        if len(parts) == 1 and "," in parts[0]:
            parts = re.split(r"\s*,\s*", parts[0])
        parts = [p.strip() for p in parts if p.strip()]
        if not parts:
            return False

        def _one_acc(t: str) -> bool:
            t = re.sub(r"^accession:\s*", "", t, flags=re.I).strip()
            if re.match(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$", t, re.I):
                return True
            if re.match(r"^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$", t, re.I):
                return True
            if 6 <= len(t) <= 15 and re.match(r"^[A-NR-Z][0-9][A-Z0-9]{3,}[0-9]$", t, re.I):
                return True
            return False

        return all(_one_acc(p) for p in parts)

    @staticmethod
    def _extract_accessions_from_file(path: str, max_ids: int = 5000) -> List[str]:
        """Extract accession-like IDs from FASTA/text files."""
        out: List[str] = []
        seen = set()
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".fasta", ".fa", ".faa", ".fna"):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if not line.startswith(">"):
                            continue
                        hdr = line[1:].strip()
                        # Expected headers: sp|P12345|... or tr|Q9XYZ1|...
                        hl = hdr.lower()
                        token = ""
                        if hl.startswith("sp|") or hl.startswith("tr|"):
                            parts = hdr.split("|")
                            if len(parts) >= 2 and parts[1]:
                                token = parts[1].strip()
                        else:
                            # Take first token before '|', '/', or whitespace.
                            token = re.split(r"[|/\\s]+", hdr, maxsplit=1)[0].strip()
                        # Validate token as a UniProt accession-like string.
                        if token and token not in seen and UniProtTool._looks_like_accession_id_fetch(token):
                            seen.add(token)
                            out.append(token)
                            if len(out) >= max_ids:
                                break
            else:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                for m in re.finditer(r"\b([A-NR-Z][0-9][A-Z0-9]{3,}[0-9]|[OPQ][0-9][A-Z0-9]{3}[0-9])\b", text, re.I):
                    cand = (m.group(1) or "").strip()
                    if cand and cand not in seen:
                        seen.add(cand)
                        out.append(cand)
                        if len(out) >= max_ids:
                            break
        except Exception:
            return []
        return out

    @staticmethod
    def _resolve_possible_file_path(raw: str) -> Optional[str]:
        """Resolve a likely file path, including case-insensitive basename fallback."""
        p = (raw or "").strip()
        if not p:
            return None
        if os.path.isfile(p):
            return os.path.abspath(p)
        if not ("/" in p or p.lower().endswith((".fasta", ".fa", ".faa", ".fna", ".txt", ".csv", ".json"))):
            return None
        d = os.path.dirname(p) or "."
        b = os.path.basename(p)
        if not os.path.isdir(d):
            return None
        try:
            for name in os.listdir(d):
                if name.lower() == b.lower():
                    cand = os.path.join(d, name)
                    if os.path.isfile(cand):
                        return os.path.abspath(cand)
        except Exception:
            return None
        return None

    @staticmethod
    def _get_next_link(headers: Any) -> Optional[str]:
        """Extract next page URL from Link header (UniProt API pagination)."""
        link = headers.get("Link") if hasattr(headers, "get") else None
        if not link:
            return None
        m = _RE_NEXT_LINK.search(link)
        return m.group(1).strip() if m else None

    @staticmethod
    def _coerce_download_formats(raw: Any) -> List[str]:
        """
        UniProt expects download_formats like ['fasta']. A string 'fasta' must not become list(str)
        which would be ['f','a','s','t','a'] and write no files.
        """
        if raw is None:
            return []
        if isinstance(raw, str):
            parts = [p.strip().lower() for p in re.split(r"[,;\s]+", raw) if p.strip()]
            allowed = {"fasta", "pdb", "json", "xml"}
            return [p for p in parts if p in allowed] or ([raw.strip().lower()] if raw.strip().lower() in allowed else [])
        if isinstance(raw, (list, tuple)):
            out: List[str] = []
            for x in raw:
                if not x:
                    continue
                if isinstance(x, str):
                    s = x.strip().lower()
                    if s in ("fasta", "pdb", "json", "xml"):
                        out.append(s)
            # Recover planner bug: download_formats passed as str -> list() -> ['f','a','s','t','a']
            if (
                len(out) == 0
                and len(raw) >= 5
                and all(isinstance(x, str) and len(x) == 1 for x in raw)
            ):
                joined = "".join(str(x) for x in raw).lower()
                if joined == "fasta":
                    return ["fasta"]
                if joined in ("json", "xml", "pdb"):
                    return [joined]
            return out
        return []

    def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            query = (kwargs.get("query") or "").strip()
            if not query:
                return {"success": False, "error": "UniProt query is empty; a non-empty 'query' string is required."}
            # Accept prefixes like "ids:/path/to/file.txt", "file:/path/to/file.txt", "accession:/path/to/file.txt".
            # These are NOT UniProt query fields; they indicate a local file containing IDs.
            query = re.sub(r"^(?:file|path|ids?|accessions?|accs?|accession|acc)\s*:\s*", "", query, flags=re.IGNORECASE).strip()
            # If query is a local file path, extract accession IDs and convert to OR-list query.
            query_file = self._resolve_possible_file_path(query)
            extracted_ids_from_file: List[str] = []
            extracted_ids_path: Optional[str] = None
            if query_file:
                extracted_ids_from_file = self._extract_accessions_from_file(query_file)
                if extracted_ids_from_file:
                    # Avoid extremely long query strings; cap OR-list size.
                    MAX_QUERY_IDS_FROM_FILE = 2000
                    query = " OR ".join(extracted_ids_from_file[:MAX_QUERY_IDS_FROM_FILE])
            raw_limit = kwargs.get("limit")
            raw_download_formats = kwargs.get("download_formats")
            if raw_download_formats is None:
                raw_download_formats = kwargs.get("format")
            if raw_download_formats is None:
                raw_download_formats = kwargs.get("download_format")
            if raw_download_formats is None:
                raw_download_formats = kwargs.get("formats")
            download_formats: List[str] = self._coerce_download_formats(raw_download_formats)
            if not download_formats and (kwargs.get("output_fasta") or kwargs.get("save_fasta")):
                download_formats = ["fasta"]
            # Treat explicit 'all' OR omitted limit with FASTA download as 'fetch all'
            fetch_all = False
            if raw_limit is not None and str(raw_limit).strip().lower() == "all":
                fetch_all = True
            elif raw_limit is None and "fasta" in download_formats:
                fetch_all = True
            if fetch_all:
                limit_num: Optional[int] = None  # no cap; paginate until no next
            else:
                try:
                    limit_num = int(raw_limit) if raw_limit is not None else 10
                except (TypeError, ValueError):
                    limit_num = 10
                limit_num = max(1, min(limit_num, 50000))
            raw_fields = kwargs.get("fields")
            if isinstance(raw_fields, str):
                fields_list = [f.strip() for f in raw_fields.split(",") if f.strip()]
            else:
                fields_list = list(raw_fields or [])
            output_dir = (kwargs.get("output_dir") or "data/outputs/uniprot").strip()
            output_dir = resolve_output_dir(output_dir)
            if not os.path.isabs(output_dir):
                output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
            single_fasta = bool(kwargs.get("single_fasta", False))
            reviewed_only = bool(kwargs.get("reviewed_only", True))
            from_hmmer = bool(kwargs.get("from_hmmer_hit_ids", False))

            query = self._normalize_query(query)
            id_fetch = from_hmmer or self._looks_like_accession_id_fetch(query)
            if id_fetch:
                reviewed_only = False
            # Swiss-Prot default for protein/enzyme *discovery*; full UniProtKB for accession lists / HMMER hits
            if id_fetch:
                effective_query = query
            elif reviewed_only:
                if re.search(r"\breviewed\s*:\s*(?:true|false)\b", query, re.IGNORECASE):
                    effective_query = query
                else:
                    effective_query = f"({query}) AND (reviewed:true)"
            else:
                effective_query = query

            base_fields = "accession,id,protein_name,gene_names,organism_name,sequence"
            if download_formats and "pdb" in download_formats:
                base_fields += ",uniProtKBCrossReferences"
            fields = ",".join(fields_list) if fields_list else base_fields

            # Pagination per UniProt docs: size=500, follow Link header rel="next" (https://www.uniprot.org/help/api_queries)
            retries = Retry(total=5, backoff_factor=0.25, status_forcelist=[500, 502, 503, 504])
            session = requests.Session()
            session.mount("https://", HTTPAdapter(max_retries=retries))
            params: Dict[str, Any] = {"query": effective_query, "size": UNIPROT_PAGE_SIZE, "fields": fields, "format": "json"}

            def process_batch(data: Dict[str, Any], acc: List[Dict[str, Any]]) -> None:
                for item in data.get("results", []):
                    acc.append({
                        "accession": item.get("primaryAccession", "N/A"),
                        "id": item.get("uniProtkbId", "N/A"),
                        "protein_name": self._protein_name(item),
                        "gene_names": self._gene_names(item),
                        "organism": item.get("organism", {}).get("scientificName", "N/A"),
                        "sequence": item.get("sequence", {}).get("value", ""),
                    })

            results: List[Dict[str, Any]] = []
            batch_url: Optional[str] = self.BASE_URL
            first_params = dict(params)
            if limit_num and limit_num < UNIPROT_PAGE_SIZE:
                first_params["size"] = limit_num
            total_header = ""
            max_batches = 2000

            while batch_url and len(results) < max_batches * UNIPROT_PAGE_SIZE:
                if batch_url == self.BASE_URL:
                    resp = session.get(batch_url, params=first_params, timeout=60)
                else:
                    resp = session.get(batch_url, timeout=60)
                if resp.status_code == 400 and batch_url == self.BASE_URL:
                    simple = re.sub(r"\s*AND\s+\(?lineage:(?:\"[^\"]*\"|\S+)\)?", "", query, flags=re.IGNORECASE).strip()
                    simple = re.sub(r"\s*AND\s+\(?organism_name:(?:\"[^\"]*\"|\S+)\)?", "", simple, flags=re.IGNORECASE).strip()
                    simple = re.sub(r"\s*AND\s+\(?taxonomy_name:(?:\"[^\"]*\"|\S+)\)?", "", simple, flags=re.IGNORECASE).strip()
                    simple = simple.strip() or (query.split(" AND ")[0].strip() if " AND " in query else query)
                    if id_fetch:
                        fallback_query = simple
                    else:
                        fallback_query = f"({simple}) AND (reviewed:true)" if reviewed_only else simple
                    if fallback_query != effective_query:
                        logger.warning("UniProt 400; retrying with simplified query: %r", fallback_query[:80])
                    first_params["query"] = fallback_query
                    resp = session.get(self.BASE_URL, params=first_params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                total_header = resp.headers.get("x-total-results", "") or total_header
                process_batch(data, results)
                if total_header.isdigit():
                    logger.debug("UniProt batch: %d / %s", len(results), total_header)
                if limit_num and len(results) >= limit_num:
                    results = results[:limit_num]
                    break
                # When user asked for "all" and FASTA, stream endpoint will provide the full file; skip pagination
                if fetch_all and "fasta" in download_formats:
                    break
                batch_url = self._get_next_link(resp.headers)
                if not batch_url or not data.get("results"):
                    break
            if total_header.isdigit() and len(results) < int(total_header) and not (limit_num and len(results) >= limit_num) and not (fetch_all and "fasta" in download_formats):
                logger.warning("UniProt pagination stopped before fetching all %s results (safety cap).", total_header)

            if not results:
                payload = {
                    "tool": self.name,
                    "query": query,
                    "effective_query": effective_query,
                    "results": [],
                    "count": 0,
                    "downloaded": {},
                    "output_dir": output_dir,
                    "generated_files": [],
                    "artifact_refs": [],
                    "artifacts": [],
                }
                return {
                    "success": False,
                    "error": f"No UniProt hits for query: {effective_query}",
                    **payload,
                    "data": payload,
                }

            out_paths: Dict[str, List[str]] = {}
            if download_formats:
                out_paths = self._download(
                    results=results,
                    query=query,
                    effective_query=effective_query,
                    download_formats=download_formats,
                    output_dir=output_dir,
                    single_fasta=single_fasta,
                    fetch_all=fetch_all,
                )
                if "fasta" in download_formats and not out_paths.get("fasta"):
                    fallback_fasta = self._write_fasta_from_results(
                        results=results,
                        output_dir=output_dir,
                        single_fasta=single_fasta,
                    )
                    if fallback_fasta:
                        out_paths["fasta"] = fallback_fasta

            # When user gives a FASTA of IDs, also write the extracted IDs to a sidecar file.
            if extracted_ids_from_file:
                try:
                    base = os.path.splitext(os.path.basename(query_file or query))[0] or "ids"
                    extracted_ids_path = os.path.join(output_dir, f"uniprot_ids_from_{base}.txt")
                    with open(extracted_ids_path, "w", encoding="utf-8") as f:
                        for acc in extracted_ids_from_file:
                            f.write(str(acc).strip() + "\n")
                    # Expose as downloadable artifact for downstream steps / user inspection.
                    out_paths["ids"] = [os.path.abspath(extracted_ids_path)]
                except Exception:
                    pass

            generated_files = [
                os.path.abspath(path)
                for paths in out_paths.values()
                for path in (paths or [])
                if isinstance(path, str) and path and os.path.isfile(path)
            ]
            artifact_refs = list(generated_files)
            artifacts = [
                {"type": fmt, "path": os.path.abspath(path), "source": self.name}
                for fmt, paths in out_paths.items()
                for path in (paths or [])
                if isinstance(path, str) and path and os.path.isfile(path)
            ]
            payload = {
                "tool": self.name,
                "query": query,
                "effective_query": effective_query,
                "results": results,
                "count": len(results),
                "downloaded": out_paths,
                "output_dir": output_dir,
                "generated_files": generated_files,
                "artifact_refs": artifact_refs,
                "artifacts": artifacts,
            }

            # If user asked for FASTA but we wrote no FASTA file (empty stream + no results), report failure
            if "fasta" in download_formats and not (out_paths.get("fasta")):
                return {
                    "success": False,
                    "error": "No sequences found or UniProt returned no FASTA data. Try a simpler query (e.g. 'insulin' or 'INS gene') or set reviewed_only=false.",
                    **payload,
                    "data": payload,
                }

            return {
                "success": True,
                **payload,
                "data": payload,
            }
        except requests.exceptions.HTTPError as e:
            response = getattr(e, "response", None)
            status_code = getattr(response, "status_code", None)
            return {
                "success": False,
                "error": f"UniProt API HTTP error: {str(e)}",
                "status_code": status_code,
                "output_dir": locals().get("output_dir"),
                "generated_files": [],
                "artifact_refs": [],
                "artifacts": [],
                "count": 0,
            }
        except requests.exceptions.RequestException as e:
            response = getattr(e, "response", None)
            status_code = getattr(response, "status_code", None)
            return {
                "success": False,
                "error": f"UniProt API error: {str(e)}",
                "status_code": status_code,
                "output_dir": locals().get("output_dir"),
                "generated_files": [],
                "artifact_refs": [],
                "artifacts": [],
                "count": 0,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "output_dir": locals().get("output_dir"),
                "generated_files": [],
                "artifact_refs": [],
                "artifacts": [],
                "count": 0,
            }

    def _protein_name(self, item: Dict) -> str:
        try:
            pd = item.get("proteinDescription") or {}
            rec = pd.get("recommendedName")
            if rec:
                fn = rec.get("fullName") or {}
                return (fn.get("value") or "Unknown").strip()
            subs = pd.get("submissionNames") or []
            if subs and isinstance(subs[0], dict):
                fn = (subs[0].get("fullName") or {})
                return (fn.get("value") or "Unknown").strip()
        except (KeyError, TypeError, IndexError):
            pass
        return "Unknown"

    def _gene_names(self, item: Dict) -> str:
        try:
            genes = item.get("genes") or []
            names = []
            for g in genes:
                gn = g.get("geneName") or {}
                if isinstance(gn.get("value"), list):
                    names.extend(gn["value"])
                elif isinstance(gn.get("value"), str):
                    names.append(gn["value"])
            return ", ".join(names) if names else "Unknown"
        except (KeyError, TypeError):
            return "Unknown"

    def _stream_fasta_to_file(self, effective_query: str, output_dir: str) -> Optional[str]:
        """Stream all FASTA results from UniProt stream endpoint to one file. Returns path or None on failure."""
        try:
            url = f"{UNIPROT_STREAM}?{urlencode({'format': 'fasta', 'query': effective_query})}"
            retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
            session = requests.Session()
            session.mount("https://", HTTPAdapter(max_retries=retries))
            resp = session.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            path = os.path.join(output_dir, "uniprot_search.fasta")
            written = 0
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
            if written == 0:
                try:
                    os.remove(path)
                except OSError:
                    pass
                logger.warning("UniProt stream FASTA returned empty response; will fall back to building from results.")
                return None
            return path
        except Exception as e:
            logger.warning("UniProt stream FASTA failed: %s; will fall back to building from results.", e)
            return None

    def _write_fasta_from_results(
        self,
        results: List[Dict[str, Any]],
        output_dir: str,
        single_fasta: bool = False,
    ) -> List[str]:
        """Write FASTA files from search result sequence fields as a fallback."""
        paths: List[str] = []
        if not results:
            return paths
        if single_fasta:
            path = os.path.join(output_dir, "uniprot_search.fasta")
            count = 0
            with open(path, "w", encoding="utf-8") as f:
                for r in results:
                    acc = str(r.get("accession") or "N_A").strip() or "N_A"
                    seq = (r.get("sequence") or "").strip()
                    if not seq:
                        continue
                    name = str(r.get("protein_name") or "Unknown").strip()
                    f.write(f">sp|{acc}| {name}\n")
                    for i in range(0, len(seq), 80):
                        f.write(seq[i : i + 80] + "\n")
                    count += 1
            if count > 0:
                paths.append(path)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return paths

        for r in results:
            acc = str(r.get("accession") or "N_A").strip() or "N_A"
            seq = (r.get("sequence") or "").strip()
            if not seq:
                continue
            safe_acc = re.sub(r"[^A-Za-z0-9_.-]+", "_", acc)
            path = os.path.join(output_dir, f"{safe_acc}.fasta")
            name = str(r.get("protein_name") or "Unknown").strip()
            with open(path, "w", encoding="utf-8") as f:
                f.write(f">sp|{acc}| {name}\n")
                for i in range(0, len(seq), 80):
                    f.write(seq[i : i + 80] + "\n")
            paths.append(path)
        return paths

    def _download(
        self,
        results: List[Dict[str, Any]],
        query: str,
        effective_query: str,
        download_formats: List[str],
        output_dir: str,
        single_fasta: bool,
        fetch_all: bool = False,
    ) -> Dict[str, List[str]]:
        out_paths: Dict[str, List[str]] = {fmt: [] for fmt in download_formats}
        # Use streaming endpoint when user asked for "all" or requested a single FASTA file
        use_stream_fasta = ("fasta" in download_formats) and (single_fasta or fetch_all)

        if "fasta" in download_formats:
            if use_stream_fasta:
                path = self._stream_fasta_to_file(effective_query, output_dir)
                if path:
                    out_paths["fasta"].append(path)
                else:
                    # Fallback: build single FASTA from in-memory results (only if we have sequences)
                    path = os.path.join(output_dir, "uniprot_search.fasta")
                    count = 0
                    with open(path, "w") as f:
                        for r in results:
                            acc = r.get("accession", "N/A")
                            name = r.get("protein_name", "Unknown")
                            seq = (r.get("sequence") or "").strip()
                            if acc and seq:
                                f.write(f">sp|{acc}| {name}\n")
                                for i in range(0, len(seq), 80):
                                    f.write(seq[i : i + 80] + "\n")
                                count += 1
                    if count > 0:
                        out_paths["fasta"].append(path)
                    else:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        logger.warning("UniProt: no sequences to write for FASTA; search returned no sequence data.")
            else:
                for r in results:
                    acc = r.get("accession", "N/A")
                    if acc == "N/A":
                        continue
                    url = f"{UNIPROT_BASE}/{acc}.fasta"
                    try:
                        resp = requests.get(url, timeout=30)
                        resp.raise_for_status()
                        content = resp.content or b""
                        if not content.strip() or not content.lstrip().startswith(b">"):
                            raise ValueError(f"UniProt FASTA response for {acc} was empty or invalid")
                        path = os.path.join(output_dir, f"{acc}.fasta")
                        with open(path, "wb") as f:
                            f.write(content)
                        out_paths["fasta"].append(path)
                    except Exception:
                        seq = (r.get("sequence") or "").strip()
                        if seq:
                            fasta_path = os.path.join(output_dir, f"{acc}.fasta")
                            with open(fasta_path, "w") as f:
                                f.write(f">sp|{acc}| {r.get('protein_name', 'Unknown')}\n")
                                for i in range(0, len(seq), 80):
                                    f.write(seq[i : i + 80] + "\n")
                            out_paths["fasta"].append(fasta_path)

        if "pdb" in download_formats:
            for r in results:
                acc = r.get("accession", "N/A")
                if acc == "N/A":
                    continue
                try:
                    entry_url = f"{UNIPROT_BASE}/{acc}.json"
                    entry_resp = requests.get(entry_url, timeout=30)
                    entry_resp.raise_for_status()
                    entry = entry_resp.json()
                    xrefs = entry.get("uniProtKBCrossReferences") or []
                    pdb_ids = [
                        x.get("id")
                        for x in xrefs
                        if isinstance(x, dict) and (x.get("database") or "").upper() == "PDB" and x.get("id")
                    ]
                    for pdb_id in pdb_ids:
                        pdb_id = str(pdb_id).strip().upper()
                        if not pdb_id:
                            continue
                        try:
                            url = f"{RCSB_PDB_URL}/{pdb_id}.pdb"
                            resp = requests.get(url, timeout=30)
                            resp.raise_for_status()
                            path = os.path.join(output_dir, f"{pdb_id}.pdb")
                            with open(path, "wb") as f:
                                f.write(resp.content)
                            out_paths["pdb"].append(path)
                        except Exception:
                            continue
                except Exception:
                    continue

        if "json" in download_formats:
            for r in results:
                acc = r.get("accession", "N/A")
                if acc == "N/A":
                    continue
                try:
                    url = f"{UNIPROT_BASE}/{acc}.json"
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    path = os.path.join(output_dir, f"{acc}.json")
                    with open(path, "wb") as f:
                        f.write(resp.content)
                    out_paths["json"].append(path)
                except Exception:
                    continue

        if "xml" in download_formats:
            for r in results:
                acc = r.get("accession", "N/A")
                if acc == "N/A":
                    continue
                try:
                    url = f"{UNIPROT_BASE}/{acc}.xml"
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    path = os.path.join(output_dir, f"{acc}.xml")
                    with open(path, "wb") as f:
                        f.write(resp.content)
                    out_paths["xml"].append(path)
                except Exception:
                    continue

        return out_paths
