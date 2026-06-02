# =============================================================================
#  MiniProt Virtual Lab — Docker Image
#  =============================================================================
#
#  Build:
#    docker build -t miniprot-vlab .
#
#  Run (interactive):
#    docker run -it --rm \
#      -e DEEPSEEK_API_KEY="sk-..." \
#      -v $(pwd)/data:/app/data \
#      -v $(pwd)/meetings:/app/meetings \
#      -v $(pwd)/logs:/app/logs \
#      -v $(pwd)/config/settings.yaml:/app/config/settings.yaml \
#      miniprot-vlab
#
#  Run (demo):
#    docker run -it --rm \
#      -e DEEPSEEK_API_KEY="sk-..." \
#      miniprot-vlab python run.py --demo
#
#  docker-compose:
#    docker-compose run --rm miniprot-vlab python run.py --demo
# =============================================================================

FROM continuumio/miniconda3:24.11.1-0

LABEL org.opencontainers.image.title="MiniProt Virtual Lab"
LABEL org.opencontainers.image.description="AI-Human collaboration for protein & enzyme research"
LABEL org.opencontainers.image.url="https://github.com/SJTU-software-2026/miniprot_virtual_lab"

# ── System dependencies ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    build-essential \
    wget \
    curl \
    git \
    # Bioinformatics runtime deps
    libxrender1 \
    libxext6 \
    libgl1-mesa-glx \
    # Clean up
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Conda environment with bioinformatics tools ────────────────────
# Install all conda-installable tools in one layer for efficiency
RUN conda install -y -c conda-forge -c bioconda \
    python=3.12 \
    pip \
    # === Core bioinformatics ===
    mafft \
    mmseqs2 \
    cd-hit \
    blast \
    # === Structure tools ===
    foldseek \
    tmalign \
    # === Docking tools ===
    vina \
    openbabel \
    meeko \
    # === Phylogenetics ===
    fasttree \
    # === Visualization ===
    pymol-open-source \
    # === Python scientific stack ===
    numpy \
    pandas \
    scipy \
    matplotlib \
    seaborn \
    biopython \
    rdkit \
    # === ML / DL ===
    pytorch \
    transformers \
    sentencepiece \
    protobuf \
    # === Web / API ===
    requests \
    pyyaml \
    tqdm \
    && conda clean -afy

# ── Pip-only packages ──────────────────────────────────────────────
RUN pip install --no-cache-dir \
    openai>=1.0.0 \
    python-dotenv>=1.0.0 \
    tiktoken>=0.5.0 \
    langchain-core>=0.3.0 \
    langchain-openai>=0.2.0 \
    langgraph>=0.2.0 \
    fastapi>=0.115.0 \
    uvicorn>=0.32.0 \
    streamlit>=1.28.0 \
    pdbfixer \
    openmm \
    ete3>=3.1.0 \
    sentence-transformers>=2.2.0 \
    chromadb>=0.5.0 \
    langchain-text-splitters>=0.3.0 \
    langchain-community>=0.3.0 \
    langchain-chroma>=0.2.0

# ── P2Rank (ML-based binding site predictor) ───────────────────────
# Installed separately because it needs Java + download
RUN conda install -y -c conda-forge openjdk=17 \
    && conda clean -afy

ENV P2RANK_VERSION=2.5.1
RUN mkdir -p /opt/p2rank && \
    cd /opt/p2rank && \
    wget -q https://github.com/rdk/p2rank/releases/download/${P2RANK_VERSION}/p2rank_${P2RANK_VERSION}.tar.gz && \
    tar -xzf p2rank_${P2RANK_VERSION}.tar.gz && \
    rm p2rank_${P2RANK_VERSION}.tar.gz && \
    chmod +x p2rank_${P2RANK_VERSION}/prank

ENV P2RANK_HOME=/opt/p2rank/p2rank_${P2RANK_VERSION}
ENV PATH="${P2RANK_HOME}:${PATH}"

# ── OmegaFold (de novo structure prediction) ───────────────────────
# NOTE: OmegaFold requires Python 3.8-3.10 (NOT 3.12).
# We create a separate conda environment for it and call via subprocess.
# The enzyme_update omegafold_tool.py will use OMEGAFOLD_PYTHON env var
# to point to this environment's Python.
RUN conda create -y -n omegafold python=3.10 pip \
    && conda run -n omegafold pip install --no-cache-dir \
        biopython \
    && conda run -n omegafold pip install --no-cache-dir \
        git+https://github.com/HeliXonProtein/OmegaFold.git \
    && conda clean -afy \
    || echo "WARNING: OmegaFold sub-environment creation failed"

ENV OMEGAFOLD_ENV=omegafold
ENV OMEGAFOLD_PYTHON=/opt/conda/envs/omegafold/bin/python

# ── App setup ──────────────────────────────────────────────────────
WORKDIR /app

# Copy project files
COPY requirements.txt .
COPY run.py .
COPY README.md README_CN.md ./
COPY src/ src/
COPY scripts/ scripts/
COPY config/settings.example.yaml config/
COPY figures/ figures/

# Create directories for runtime data (mounted as volumes in production)
RUN mkdir -p /app/data/outputs /app/meetings /app/logs

# Default command: interactive mode
# User must provide DEEPSEEK_API_KEY and mount config or set env vars
CMD ["python", "run.py"]

# ── Health check ────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0,'src'); from miniprot_virtual_lab.config import resolve_config; print('healthy')" || exit 1
