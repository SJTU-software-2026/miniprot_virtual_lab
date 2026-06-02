"""
Sequence analysis tools — alignment, search, clustering, phylogenetics.

Primary agent: **Sequence Analysis Specialist**
"""

CATEGORY = "sequence_analysis"
PRIMARY_AGENT = "Sequence Analysis Specialist"
DESCRIPTION = "Multiple sequence alignment, HMMER, MMseqs2, CD-HIT, phylogenetic trees, protein properties."

TOOLS = {
    "sequence_alignment": {
        "description": "MSA via MAFFT (default), Clustal Omega, or NCBI BLAST.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Align sequences before tree building; BLAST for remote database search.",
        "key_params": "method (mafft|clustalo|blast), fasta_path, query, program",
    },
    "hmmer": {
        "description": "Profile HMM search via EBI HMMER API (phmmer, hmmscan, hmmsearch, jackhmmer).",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Find homologs via phmmer. Poll for results. Fetch hits as FASTA.",
        "key_params": "action, input, fasta_path, database, job_id, wait, max_wait_seconds",
    },
    "mmseqs2": {
        "description": "Ultra-fast sequence search and clustering (MMseqs2).",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "All-vs-all sequence search, convert to .m8 for similarity matrix.",
        "key_params": "action, query_fasta, target_fasta, sensitivity",
    },
    "cdhit": {
        "description": "CD-HIT: cluster or compare protein/nucleotide sequences by identity.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Remove redundancy after HMMER search (identity=0.9). Compare two datasets.",
        "key_params": "action (cluster|compare), input_fasta, identity",
    },
    "sequence_length_filter": {
        "description": "Filter FASTA by sequence length (min/max or reference ± range).",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Remove unusually short/long sequences after CD-HIT. Use reference_fasta + length_range=30.",
        "key_params": "input_fasta, min_length, max_length, reference_fasta, length_range",
    },
    "sequence_similarity": {
        "description": "Compute pairwise sequence similarity from aligned FASTA using BLOSUM62.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Quantify sequence similarity without clustering.",
        "key_params": "fasta_path (aligned MSA)",
    },
    "protein_properties": {
        "description": "Compute physicochemical properties: GRAVY, MW, pI, aromaticity, instability index, charge.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Generate property CSV for candidate ranking.",
        "key_params": "input_fasta, sequence, properties, ph, output_csv",
    },
    "fasta_convert": {
        "description": "Convert between CSV and FASTA sequence file formats.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "csv_to_fasta (table→sequences) or fasta_to_csv (sequences→table).",
        "key_params": "action, input_path, sequence_column, delimiter",
    },
    "ete": {
        "description": "ETE Toolkit: build, render, and compare phylogenetic trees.",
        "primary_agent": "Sequence Analysis Specialist",
        "typical_use": "Build tree (build_nj/build_fasttree) from alignment, set outgroup, render PNG.",
        "key_params": "action, newick_path, alignment_path, outgroup",
    },
}
