"""
Local tool path resolver.

Loads config/tool_paths.yaml and sets environment variables so
MiniProt tools find user-installed binaries. Users who already have
tools like MAFFT, Vina, etc. installed locally can set paths here
instead of using Docker's built-in copies.

Resolution order for each tool binary:
  1. tool_paths.yaml entry (if non-empty)
  2. Environment variable (e.g. VINA_BIN, MAFFT_BIN)
  3. System PATH (default lookup)

Usage:
    from .tool_paths import load_tool_paths, TOOL_PATHS
    load_tool_paths()        # Called once at startup
    print(TOOL_PATHS.mafft)  # → "/usr/local/bin/mafft" or ""
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── YAML loading ───────────────────────────────────────────────────

_tool_paths_loaded = False
TOOL_PATHS: Dict[str, str] = {}


def _yaml_available() -> bool:
    try:
        import yaml
        return True
    except ImportError:
        return False


def _find_tool_paths_yaml() -> Optional[Path]:
    """Locate config/tool_paths.yaml or the example fallback."""
    # 1. Env override
    env = os.getenv("MINIPROT_TOOL_PATHS", "").strip()
    if env and os.path.isfile(env):
        return Path(env)

    # 2. CWD-relative
    cwd = Path.cwd() / "config" / "tool_paths.yaml"
    if cwd.is_file():
        return cwd

    # 3. Source-relative
    src = Path(__file__).resolve().parents[2] / "config" / "tool_paths.yaml"
    if src.is_file():
        return src

    # 4. Example fallback (no user config)
    for base in [Path.cwd(), Path(__file__).resolve().parents[2]]:
        ex = base / "config" / "tool_paths.example.yaml"
        if ex.is_file():
            return None  # No user config, silently OK

    return None


def load_tool_paths(reload: bool = False) -> Dict[str, str]:
    """Load tool paths from config/tool_paths.yaml.

    Called once at startup by ToolBridge. Sets environment variables
    for discovered paths so enzyme_update's subprocess calls find them.

    Returns:
        Dict mapping tool keys to their resolved paths.
    """
    global _tool_paths_loaded, TOOL_PATHS

    if _tool_paths_loaded and not reload:
        return TOOL_PATHS

    if not _yaml_available():
        _tool_paths_loaded = True
        return TOOL_PATHS

    import yaml

    path = _find_tool_paths_yaml()
    if path is None or not path.is_file():
        _tool_paths_loaded = True
        return TOOL_PATHS

    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load tool_paths.yaml: %s", e)
        _tool_paths_loaded = True
        return TOOL_PATHS

    # Flatten nested YAML into TOOL_PATHS dict
    flat: Dict[str, str] = {}
    for section_key, section in yaml_cfg.items():
        if isinstance(section, dict):
            for tool_key, tool_path in section.items():
                if isinstance(tool_path, str) and tool_path.strip():
                    flat[tool_key] = tool_path.strip()

    TOOL_PATHS = flat

    # Apply to environment so enzyme_update subprocesses find the tools
    _apply_to_env(flat)

    _tool_paths_loaded = True
    count = len(flat)
    if count > 0:
        logger.info("Loaded %d local tool paths from %s", count, path)
    return TOOL_PATHS


def _apply_to_env(paths: Dict[str, str]) -> None:
    """Set environment variables from tool paths.

    Maps YAML keys to the env vars that enzyme_update tools look for.
    """
    env_map = {
        # Sequence
        "mafft": "MAFFT_BIN",
        "mmseqs": "MMSEQS_BIN",
        "cdhit": "CDHIT_BIN",
        "hmmer_bin_dir": "HMMER_BIN_DIR",
        # Structure
        "foldseek": "FOLDSEEK_BIN",
        "tmalign": "TMALIGN_BIN",
        "omega_fold_python": "OMEGAFOLD_PYTHON",
        "esmfold_device": "ESMFOLD_DEVICE",
        # Docking
        "vina": "VINA_BIN",
        "obabel": "OBABEL_BIN",
        "meeko_python": "MEEKO_PYTHON",
        "pdbfixer_python": "PDBFIXER_PYTHON",
        "p2rank_home": "P2RANK_HOME",
        "java_home": "JAVA_HOME",
        # Visualization
        "pymol": "PYMOL_BIN",
        "fasttree": "FASTTREE_BIN",
        # Specialized
        "enzymecage_python": "ENZYMECAGE_PYTHON",
        "enzyme_checkpoint": "ENZYME_SPECIFICITY_CHECKPOINT",
        "scwrl4": "SCWRL4_BIN",
    }

    for tool_key, tool_path in paths.items():
        if not tool_path:
            continue
        env_var = env_map.get(tool_key)
        if env_var:
            if env_var not in os.environ:  # Don't override user's manual env setting
                os.environ[env_var] = tool_path
                logger.debug("ENV %s = %s", env_var, tool_path)

        # Add tool directory to PATH so subprocesses find the binary
        # For *_home keys (java_home, p2rank_home, hmmer_bin_dir), add bin/ subdir
        tool_dir = str(Path(tool_path))
        if tool_key.endswith("_home"):
            tool_dir = str(Path(tool_path) / "bin")
        elif os.path.isfile(tool_path):
            tool_dir = str(Path(tool_path).parent)

        if tool_dir and tool_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = tool_dir + os.pathsep + os.environ.get("PATH", "")


def get_tool_path(tool_key: str) -> str:
    """Get the resolved path for a tool.

    Returns empty string if not configured (use system PATH).
    """
    if not _tool_paths_loaded:
        load_tool_paths()
    return TOOL_PATHS.get(tool_key, "")


def print_tool_paths() -> None:
    """Print a summary of configured local tool paths."""
    if not _tool_paths_loaded:
        load_tool_paths()

    if not TOOL_PATHS:
        print("  No local tool paths configured. Using Docker/system PATH.")
        print("  To use local tools: cp config/tool_paths.example.yaml config/tool_paths.yaml")
        print("  Then edit tool_paths.yaml with paths to your installations.")
        return

    print(f"\n  Local tool paths ({len(TOOL_PATHS)} configured):")
    for k, v in sorted(TOOL_PATHS.items()):
        exists = "OK" if Path(v).exists() else "MISSING"
        print(f"    {k:<25} = {v:<50} [{exists}]")
