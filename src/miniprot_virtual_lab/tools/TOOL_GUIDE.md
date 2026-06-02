# MiniProt Virtual Lab — Tool Guide

Complete reference for all 33 bioinformatics tools available to Virtual Lab agents.

> **How to read this guide:** Each tool lists its primary agent, what it does,
> when to use it, key parameters, and which downstream tools it feeds.
>
> **Docker users:** All tools marked with ✅ below are pre-installed in the Docker image.
> Manual install users: tools needing external binaries are marked with 🖥️.

---

## Availability Summary

| Tool | Docker | Manual | Needs |
|------|--------|--------|-------|
| uniprot_search, ncbi_search | ✅ | ✅ API | — |
| alphafold, pdb | ✅ | ✅ API | — |
| structure_from_fasta | ✅ | ✅ | OmegaFold/ESMFold optional |
| smiles | ✅ | ✅ API | — |
| protein_properties | ✅ | ✅ | Biopython |
| sequence_length_filter, sequence_similarity | ✅ | ✅ | — |
| fasta_convert, merger, pdb_merge | ✅ | ✅ | — |
| similarity_matrix | ✅ | ✅ | NumPy/Matplotlib |
| hmmer | ✅ | ✅ API | EBI network access |
| **sequence_alignment (MAFFT)** | ✅ | 🖥️ | `conda install -c bioconda mafft` |
| **mmseqs2** | ✅ | 🖥️ | `conda install -c bioconda mmseqs2` |
| **cdhit** | ✅ | 🖥️ | `conda install -c bioconda cd-hit` |
| **foldseek** | ✅ | 🖥️ | `conda install -c bioconda foldseek` |
| **tmalign** | ✅ | 🖥️ | `conda install -c bioconda tmalign` |
| **autodock_vina** | ✅ | 🖥️ | `conda install -c conda-forge vina` |
| **pocket_box** | ✅ | 🖥️ | `conda install -c conda-forge openbabel` |
| **pocket_picker (P2Rank)** | ✅ | 🖥️ | Java 17 + P2Rank download |
| **pdb_repair** | ✅ | 🖥️ | `pip install pdbfixer` |
| **ete** | ✅ | 🖥️ | `pip install ete3` |
| **pymol** | ✅ | 🖥️ | `conda install pymol-open-source` |
| **omegafold** | ✅ | 🖥️ | `pip install git+...OmegaFold` |
| **esmfold** | ✅ | 🖥️ | `pip install transformers torch` |
| enzyme_specificity_predict | ❌ | ❌ | Trained .ckpt model |
| enzymecage_retrieve | ❌ | ❌ | Separate conda env |
| enzyme_redesign | ❌ | ❌ | SCWRL4 license |
| echo_tool | ✅ | ✅ | — |
| miniprot_rag | ✅ | 🖥️ | `pip install chromadb` + build index |

---

## Tool Categories

| Category | Primary Agent | Tool Count |
|----------|--------------|------------|
| [search](#search) | Protein Search Specialist | 2 |
| [structure](#structure) | Structure Specialist | 9 |
| [chemistry](#chemistry) | Chemistry Specialist | 1 |
| [docking](#docking) | Docking Specialist | 4 |
| [sequence_analysis](#sequence) | Sequence Analysis Specialist | 9 |
| [visualization](#visualization) | Sequence Analysis Specialist | 1 |
| [utility](#utility) | Sequence Analysis / Docking | 2 |

---

## search

**Primary agent:** Protein Search Specialist

### uniprot_search

| Field | Value |
|-------|-------|
| **What it does** | Search the UniProt database for proteins by name, gene, organism, taxonomy, or accession. Can download sequences as FASTA, metadata as JSON/XML. |
| **When to use** | User asks to find, search, or download protein sequences. "Find insulin", "Download FASTA for P01308", "Search reviewed human kinases". |
| **Key parameters** | `query` (search term), `limit` (max results, default 5), `download_formats` (['fasta'] to write files), `reviewed_only` (Swiss-Prot only, default True), `organism_name` (species filter) |
| **Outputs** | FASTA files → `data/outputs/uniprot/<timestamp>/<ACCESSION>.fasta` |
| **Feeds into** | alphafold, structure_from_fasta, sequence_alignment, hmmer, cdhit |
| **Tool chain example** | uniprot_search → alphafold (get structure) → pocket_picker → autodock_vina |

### ncbi_search

| Field | Value |
|-------|-------|
| **What it does** | Search NCBI Entrez databases (protein, nuccore, gene). Fetch summaries or FASTA sequences. |
| **When to use** | User explicitly asks for NCBI, Entrez, RefSeq, or GenBank. "Search NCBI for BRCA1", "Get NCBI protein FASTA". |
| **Key parameters** | `action` (search|fetch_summary|fetch_fasta), `query`, `db` (protein|nuccore|gene), `ids` |
| **Outputs** | FASTA files → `data/outputs/ncbi/` |
| **Note** | Complements uniprot_search. Not for BLAST — use sequence_alignment method=blast for that. |

---

## structure

**Primary agent:** Structure Specialist

### alphafold

| Field | Value |
|-------|-------|
| **What it does** | **Download** predicted structures from the AlphaFold Database (EBI). Does NOT run AlphaFold prediction. |
| **When to use** | "Get structure for P01308", "Download AlphaFold model for human TPH2". For de novo prediction use structure_from_fasta instead. |
| **Key parameters** | `action` (download|get_structure), `uniprot_id` or `uniprot_ids`, `query` + `action=get_structure`, `fasta_paths`, `session_fasta_paths` |
| **Outputs** | PDB/CIF files → `data/outputs/alphafold/AF-<ACCESSION>-F1.pdb` |
| **Feeds into** | pocket_picker, autodock_vina, foldseek, pymol, pdb_repair |

### pdb

| Field | Value |
|-------|-------|
| **What it does** | Fetch experimental structures from RCSB Protein Data Bank. |
| **When to use** | User provides a 4-character PDB ID. "Download 1ADA". ONLY for 4-char IDs (longer IDs are UniProt accessions). |
| **Key parameters** | `pdb_ids` (e.g. "1ADA" or "1ADA, 2XYZ"), `formats` ([pdb, cif]) |
| **Outputs** | PDB/CIF files → `data/outputs/pdb/` |

### structure_from_fasta

| Field | Value |
|-------|-------|
| **What it does** | Get structures from FASTA files. Tries AlphaFold DB download first, falls back to OmegaFold → ESMFold prediction. |
| **When to use** | "Get structures for these sequences", batch convert FASTA→PDB. Pass ALL fasta_paths in ONE call. |
| **Key parameters** | `fasta_paths` (list of paths), `prefer_prediction` (set True only if user says "predict structure") |
| **Outputs** | PDB files → `data/outputs/structure_from_fasta/` |

### foldseek

| Field | Value |
|-------|-------|
| **What it does** | Ultra-fast protein structure search and clustering. Default for batch structure alignment. |
| **When to use** | "Find structurally similar proteins", "Cluster these structures", batch all-vs-all structure comparison. |
| **Key parameters** | `action` (easy_search|easy_cluster|createdb|databases), `query_path`, `target_path`, `sensitivity` |
| **Outputs** | Search results → `data/outputs/foldseek/` |

### tmalign

| Field | Value |
|-------|-------|
| **What it does** | Pairwise protein structure alignment. Returns TM-score, RMSD, superposed coordinates. |
| **When to use** | "Compare these two structures", "What is the TM-score between X and Y?". Only when user explicitly requests TM-align. |
| **Key parameters** | `structure1`, `structure2` (PDB/mmCIF paths) |

### structure_alignment_batch

| Field | Value |
|-------|-------|
| **What it does** | Batch all-vs-all structure alignment over a directory of PDB files. Default: Foldseek. |
| **When to use** | "Compare all structures in this directory", after structure_from_fasta produces many PDBs. |
| **Key parameters** | `input_dir` (directory of PDBs), `use_tmalign` (only when user explicitly asks) |

### similarity_matrix

| Field | Value |
|-------|-------|
| **What it does** | Build similarity matrix and clustermap heatmap from alignment results. |
| **When to use** | After mmseqs2 (matrix_from_m8) or structure_alignment_batch (clustermap_from_alignments). |
| **Key parameters** | `action` (matrix_from_m8|clustermap|clustermap_from_alignments), `m8_path`, `alignment_json_path`, `query_ids` |
| **Outputs** | Similarity matrix (.npz) + clustermap figure (.png) |

### omegafold

| Field | Value |
|-------|-------|
| **What it does** | Predict 3D protein structure from sequence using OmegaFold. |
| **When to use** | De novo prediction when no AlphaFold DB entry exists. First choice predictor. |
| **Key parameters** | `fasta_path`, `timeout` |
| **Requires** | `pip install git+https://github.com/HeliXonProtein/OmegaFold.git` |

### esmfold

| Field | Value |
|-------|-------|
| **What it does** | Predict 3D structure from sequence using ESMFold (HuggingFace). Max 1024 residues. |
| **When to use** | Fallback predictor when OmegaFold fails. GPU recommended. |
| **Key parameters** | `fasta_path`, `device` (cuda:0 or cpu) |
| **Requires** | `pip install transformers torch` |

---

## chemistry

**Primary agent:** Chemistry Specialist

### smiles

| Field | Value |
|-------|-------|
| **What it does** | Look up chemical compounds by name, PubChem CID, or ChEMBL ID. Returns SMILES, formula, InChI key. |
| **When to use** | "Get SMILES for aspirin", "Prepare ligand for docking". Set `output_sdf=true` to generate 3D structure for docking. |
| **Key parameters** | `query` (compound name), `source` (auto|pubchem|chembl), `output_sdf` (True for docking), `save_smi` |
| **Outputs** | .smi file + optional .sdf file → `data/outputs/smiles/` |
| **Feeds into** | autodock_vina (as ligand_pdb_path) |
| **Tool chain example** | smiles (aspirin, output_sdf=true) → autodock_vina (ligand_pdb_path=<sdf path>) |

---

## docking

**Primary agent:** Docking Specialist

### pocket_picker

| Field | Value |
|-------|-------|
| **What it does** | Predict ligand-binding sites on a receptor protein. **MANDATORY before autodock_vina.** |
| **When to use** | ALWAYS run before docking (unless user provides explicit box coordinates). |
| **Key parameters** | `receptor_pdb_path`, `method` (p2rank|fpocket|geometry|dogsite_api). Default: p2rank (ML, highest accuracy). |
| **Outputs** | Box coordinates (center_x/y/z, size_x/y/z) — auto-injected into next autodock_vina call. Active residue list. |
| **Requires** | p2rank (Java 17+, set P2RANK_HOME) or fpocket |

### autodock_vina

| Field | Value |
|-------|-------|
| **What it does** | Run AutoDock Vina molecular docking. Predicts how a ligand binds to a receptor. |
| **When to use** | "Dock aspirin to insulin", "Run docking with these PDBs". Run pocket_picker first! |
| **Key parameters** | `receptor_pdb_path`, `ligand_pdb_path`, `exhaustiveness` (default 32), `num_poses` (default 9) |
| **Outputs** | Docked poses (PDBQT/PDB), energies CSV → `data/outputs/docking/vina_run_<timestamp>/` |
| **Requires** | AutoDock Vina + Open Babel/Meeko (conda install) |
| **Important** | For protein-protein docking: pass TWO protein PDBs as receptor+ligand. Do NOT merge them first. |

### pocket_box

| Field | Value |
|-------|-------|
| **What it does** | Compute Vina search box from receptor coordinates. Simpler than pocket_picker (no ML). |
| **When to use** | When you know the approximate binding site location (from co-crystallized ligand). |
| **Key parameters** | `receptor_pdb_path`, `ligand_resname`, `padding` |

### pdb_repair

| Field | Value |
|-------|-------|
| **What it does** | Repair problematic PDB structures: missing residues, chain breaks, non-standard residues. |
| **When to use** | When docking fails due to structure issues. "Fix this PDB before docking". |
| **Key parameters** | `pdb_path` or `pdb_paths` (list for batch), `force_repair`, `add_hydrogens` |
| **Outputs** | Repaired PDBs → `data/outputs/pdb_repair/` |
| **Feeds into** | pocket_picker → autodock_vina |

---

## sequence_analysis

**Primary agent:** Sequence Analysis Specialist

### sequence_alignment

| Field | Value |
|-------|-------|
| **What it does** | Multiple sequence alignment via MAFFT (default), Clustal Omega, or NCBI BLAST. |
| **When to use** | "Align these sequences", "Run BLAST on this protein", before phylogenetic tree building. |
| **Key parameters** | `method` (mafft|clustalo|blast), `fasta_path`, `query` (for BLAST), `program` (blastp|blastn) |
| **Outputs** | Aligned FASTA → `data/outputs/sequence_alignment/` |
| **Feeds into** | ete (build tree from alignment_path) |
| **Requires** | MAFFT binary on PATH |

### hmmer

| Field | Value |
|-------|-------|
| **What it does** | Profile HMM search via EBI HMMER API. Submit phmmer/hmmsearch jobs, poll for results, download hit sequences. |
| **When to use** | "Find homologs of this protein", "Run phmmer on query.fasta". |
| **Key parameters** | `action` (phmmer|hmmscan|hmmsearch|get_result|fetch_results), `input`/`fasta_path`, `database`, `job_id` |
| **Outputs** | Result JSON + hit FASTA → `data/outputs/hmmer/` |
| **Feeds into** | cdhit (cluster hits), merger (merge with query) |
| **Note** | Jobs take minutes to hours. Suggest other analyses (structure/docking) while waiting. |

### mmseqs2

| Field | Value |
|-------|-------|
| **What it does** | Ultra-fast sequence search and clustering. All-vs-all comparison with .m8 output. |
| **When to use** | "Compare all sequences to each other", before similarity_matrix matrix_from_m8. |
| **Key parameters** | `action` (run_search|createdb|search|convertalis), `query_fasta`, `target_fasta`, `sensitivity` |
| **Feeds into** | similarity_matrix (matrix_from_m8) |
| **Requires** | MMseqs2 binary (conda install -c bioconda mmseqs2) |

### cdhit

| Field | Value |
|-------|-------|
| **What it does** | CD-HIT: cluster or compare protein/nucleotide sequences by sequence identity. |
| **When to use** | "Remove redundant sequences", "Cluster these hits at 90% identity", after HMMER search. |
| **Key parameters** | `action` (cluster|cluster_est|compare|compare_est), `input_fasta`, `identity` (default 0.9) |
| **Requires** | CD-HIT binary (conda install -c bioconda cd-hit) |

### sequence_length_filter

| Field | Value |
|-------|-------|
| **What it does** | Filter FASTA file by sequence length. Remove sequences too short/long vs reference. |
| **When to use** | After CD-HIT in enzyme mining: filter outliers with reference_fasta + length_range=30. |
| **Key parameters** | `input_fasta`, `min_length`/`max_length`, or `reference_fasta` + `length_range` |

### sequence_similarity

| Field | Value |
|-------|-------|
| **What it does** | Compute pairwise sequence similarity scores from aligned FASTA using BLOSUM62. |
| **When to use** | "How similar are these sequences?", when you have an aligned MSA. |

### protein_properties

| Field | Value |
|-------|-------|
| **What it does** | Compute physicochemical properties: molecular weight, pI, GRAVY (hydrophobicity), aromaticity, instability index, charge. |
| **When to use** | "Calculate GRAVY for these proteins", "Generate property CSV for candidate ranking". |
| **Key parameters** | `input_fasta` or `sequence`, `properties` (subset list), `ph`, `output_csv` |
| **Outputs** | CSV file → `data/outputs/protein_properties/` |

### fasta_convert

| Field | Value |
|-------|-------|
| **What it does** | Convert between CSV and FASTA sequence formats. |
| **When to use** | "Convert this CSV to FASTA", "Export sequences as CSV table". |
| **Key parameters** | `action` (csv_to_fasta|fasta_to_csv), `input_path`, `sequence_column`, `delimiter` |

### ete

| Field | Value |
|-------|-------|
| **What it does** | ETE Toolkit: build phylogenetic trees (NJ, FastTree), render images, prune, compare trees. |
| **When to use** | "Build a phylogenetic tree", "Render tree as PNG", after sequence_alignment. |
| **Key parameters** | `action` (build_nj|build_fasttree|render|prune|set_outgroup|ladderize|robinson_foulds), `alignment_path`, `newick_path` |
| **Outputs** | Tree images → `data/outputs/ete/` |
| **Tool chain** | sequence_alignment (mafft) → ete (build_fasttree) → ete (render) |

---

## visualization

**Primary agent:** Sequence Analysis Specialist

### pymol

| Field | Value |
|-------|-------|
| **What it does** | Render protein structures as publication-quality images via PyMOL API (headless). |
| **When to use** | "Show me the docked complex", "Render the insulin structure", "Generate figure for paper". |
| **Key parameters** | `action` (load|render_image), `pdb_path`, `docked_complex` (True for receptor+ligand+interactions), `background` (white), `ray` (True for quality) |
| **Outputs** | PNG/PDF images → `data/outputs/pymol/` |
| **Requires** | PyMOL (conda install -c schrodinger pymol-bundle) |

---

## utility

**Primary agent:** Sequence Analysis Specialist / Docking Specialist

### merger / pdb_merge

| Field | Value |
|-------|-------|
| **What it does** | Merge (concatenate) multiple PDB or FASTA files into one. `merger` is the preferred alias. |
| **When to use** | (a) Concatenate FASTA files after filtering/clustering. (b) Combine multiple small-molecule ligands into one file for multi-ligand docking. |
| **When NOT to use** | NEVER for receptor+ligand pair before docking. NEVER for two proteins in PPI docking. |
| **Key parameters** | `pdb_paths` or `fasta_paths` (list of paths), `output_path` |

---

## Common Tool Chains

### Enzyme Mining Pipeline
```
uniprot_search → sequence_length_filter → hmmer (phmmer)
  → cdhit (cluster) → merger (query+clustered)
  → sequence_alignment (mafft) → ete (tree)
  → mmseqs2 → similarity_matrix (clustermap)
```

### Structure → Docking Pipeline
```
uniprot_search (FASTA) → alphafold (PDB) → pdb_repair (if needed)
  → pocket_picker (binding site) → autodock_vina (docking)
  → pymol (render docked complex)
```

### Ligand-Based Docking
```
smiles (output_sdf=true) → autodock_vina (ligand_pdb_path=<sdf>)
```

### Homology Search → Analysis
```
hmmer (phmmer) → hmmer (fetch_results, save_fasta)
  → cdhit (cluster, identity=0.9) → sequence_alignment (mafft)
  → ete (build tree + render) → protein_properties (CSV)
```
