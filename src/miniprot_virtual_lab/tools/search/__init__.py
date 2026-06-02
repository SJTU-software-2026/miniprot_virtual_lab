"""
Search tools — UniProt and NCBI protein sequence lookup.

Primary agent: **Protein Search Specialist**
"""

CATEGORY = "search"
PRIMARY_AGENT = "Protein Search Specialist"
DESCRIPTION = "Protein and sequence database search tools."

TOOLS = {
    "uniprot_search": {
        "description": "Search UniProt for proteins. Download FASTA/JSON/XML.",
        "primary_agent": "Protein Search Specialist",
        "typical_use": "Find proteins by name, gene, organism, or accession.",
        "key_params": "query, limit, download_formats, reviewed_only, organism_name",
    },
    "ncbi_search": {
        "description": "Search NCBI E-utilities. Fetch summaries, FASTA from GenBank/RefSeq.",
        "primary_agent": "Protein Search Specialist",
        "typical_use": "NCBI/Entrez keyword search, fetch NCBI protein/nuccore sequences.",
        "key_params": "action (search|fetch_summary|fetch_fasta), query, db, ids",
    },
}
