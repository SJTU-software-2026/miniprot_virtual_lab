"""
Parse FASTA files for use in structure prediction (AlphaFold, ChromaFold).
Returns list of {id, sequence, description} for uploading or submitting.
"""
import os
from typing import List, Dict, Any, Optional


def parse_fasta(path: str) -> List[Dict[str, Any]]:
    """
    Parse a single FASTA file. Returns list of {"id": str, "sequence": str, "description": str}.
    """
    entries = []
    current_id = None
    current_desc = None
    current_seq = []
    with open(path, "r") as f:
        for line in f:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith(">"):
                if current_id is not None:
                    entries.append({
                        "id": current_id,
                        "sequence": "".join(current_seq).replace(" ", ""),
                        "description": current_desc or "",
                    })
                parts = line[1:].split(None, 1)
                current_id = parts[0] if parts else "unknown"
                current_desc = parts[1] if len(parts) > 1 else ""
                current_seq = []
            else:
                current_seq.append(line)
        if current_id is not None:
            entries.append({
                "id": current_id,
                "sequence": "".join(current_seq).replace(" ", ""),
                "description": current_desc or "",
            })
    return entries


def parse_fasta_paths(paths: List[str]) -> List[Dict[str, Any]]:
    """Parse multiple FASTA files; returns combined list of entries."""
    entries = []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            entries.extend(parse_fasta(p))
        except Exception:
            continue
    return entries


def extract_uniprot_ids_from_entries(entries: List[Dict[str, Any]]) -> List[str]:
    """
    Extract UniProt accessions from FASTA entries (id or description).
    Handles: sp|P12345|..., tr|Q9Y6K1|..., P12345, UniProtKB:P12345.
    Returns list of unique accessions (order preserved).
    """
    seen = set()
    out: List[str] = []
    for e in entries:
        sid = (e.get("id") or "").strip()
        if not sid or sid.lower() in ("unknown", "sequence"):
            continue
        acc = None
        if "|" in sid:
            parts = sid.split("|")
            if len(parts) >= 2 and len(parts[1]) >= 2:
                acc = parts[1].strip()
        elif sid.startswith("UniProtKB:"):
            acc = sid.replace("UniProtKB:", "").strip()
        else:
            # Assume whole id is accession if it looks like one (P/Q/O + alphanumeric, 6-10 chars)
            if len(sid) >= 4 and len(sid) <= 12 and sid[0:1].upper() in ("P", "Q", "O", "A") and sid[1:].replace("-", "").isalnum():
                acc = sid
        if acc and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def merge_fasta_files(paths: List[str], out_path: str) -> bool:
    """
    Concatenate multiple FASTA files into one. Returns True if out_path was written successfully.
    """
    if not paths or not out_path:
        return False
    entries = parse_fasta_paths(paths)
    if not entries:
        return False
    try:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True, mode=0o755)
        write_fasta(entries, out_path)
        from .path_utils import ensure_file_permissions
        ensure_file_permissions(out_path)
        return os.path.isfile(out_path)
    except Exception:
        return False


def write_fasta(entries: List[Dict[str, Any]], path: str) -> None:
    """Write entries to a single FASTA file."""
    with open(path, "w") as f:
        for e in entries:
            seq = (e.get("sequence") or "").strip()
            sid = (e.get("id") or "unknown").replace("\n", " ")
            desc = (e.get("description") or "").replace("\n", " ").strip()
            header = f">{sid}"
            if desc:
                header += f" {desc}"
            f.write(header + "\n")
            for i in range(0, len(seq), 80):
                f.write(seq[i : i + 80] + "\n")
