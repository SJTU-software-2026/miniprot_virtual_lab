# tools_src — External Tool Dependencies

This directory contains optional external tools used by MiniProt Virtual Lab.

## Git Submodules (auto-download with git)

| Tool | Type | Source |
|------|------|--------|
| `omegafold/` | git submodule | https://github.com/HeliXonProtein/OmegaFold |

```bash
# Get OmegaFold (if not cloned with --recurse-submodules)
git submodule update --init tools_src/omegafold
```

## Manual Downloads (run setup script)

These tools cannot be submodules (binary releases, license restrictions):

| Tool | Size | Download |
|------|------|---------|
| P2Rank 2.5.1 | ~260 MB | https://github.com/rdk/p2rank/releases/download/2.5.1/p2rank_2.5.1.tar.gz |
| OpenJDK 17 | ~180 MB | https://adoptium.net/download/ |

### Automatic setup

```bash
# Linux / macOS
bash scripts/setup_tools.sh

# Windows (PowerShell)
powershell -File scripts/setup_tools.ps1
```

### Manual setup

```bash
# P2Rank
mkdir -p tools_src/p2rank
cd tools_src/p2rank
wget https://github.com/rdk/p2rank/releases/download/2.5.1/p2rank_2.5.1.tar.gz
tar -xzf p2rank_2.5.1.tar.gz

# Java 17 (choose one)
# Option A: Download from https://adoptium.net/download/
# Option B: Use your system's Java 17+
# Option C: conda install -c conda-forge openjdk=17
```

### After downloading

Edit `config/tool_paths.yaml` to point to your installations:

```yaml
structure:
  omega_fold_python: ""              # Not needed if using submodule

docking:
  p2rank_home: tools_src/p2rank/p2rank_2.5.1
  java_home: /path/to/jdk-17         # or let system PATH handle it
```

> **Note:** You can also install these tools anywhere on your system and reference them via `tool_paths.yaml`. The `tools_src/` directory is just a convenient default location.
