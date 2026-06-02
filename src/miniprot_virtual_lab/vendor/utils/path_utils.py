"""
Shared path helpers: safe_dir, workspace_root, safe_filename, safe_run_id.
Used by all tools to avoid duplication and keep path behavior consistent.
Outputs must live under a tool-named directory with safe-named subdirectories (e.g. data/outputs/<tool_name>/<run_id>).
"""
import os
import re
from datetime import datetime


def safe_dir(path: str) -> str:
    """Create directory if needed; return normalized path. Uses mode=0o755 to avoid permission issues."""
    path = os.path.normpath(path)
    os.makedirs(path, exist_ok=True, mode=0o755)
    return path


def ensure_file_permissions(path: str, mode: int = 0o644) -> None:
    """Set permissions on a file so it is readable (and writable by owner). Safe to call after writing output files."""
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.chmod(path, mode)
    except (OSError, PermissionError):
        pass


def workspace_root() -> str:
    """Project root for resolving relative paths. Env: WORKSPACE_ROOT or MINIPROT_WORKSPACE, else cwd."""
    root = os.environ.get("WORKSPACE_ROOT") or os.environ.get("MINIPROT_WORKSPACE") or os.getcwd()
    return os.path.normpath(root)


def resolve_output_dir(path: str) -> str:
    """
    Resolve an output directory so we never create system paths like /data.
    When path is absolute and starts with /data/, treat as workspace-relative
    (e.g. /data/outputs/docking -> workspace/data/outputs/docking).
    Returns absolute path under workspace when path looks like /data/...; otherwise unchanged.
    """
    path = (path or "").strip()
    if not path:
        return path
    norm = path.replace("\\", "/")
    if norm.startswith("/data/"):
        root = workspace_root()
        # /data/outputs/docking -> workspace/data/outputs/docking
        rest = norm[6:]  # strip "/data/"
        return os.path.normpath(os.path.join(root, "data", rest))
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(workspace_root(), path))


def safe_filename(name: str, max_len: int = 200) -> str:
    """Replace characters unsafe in filenames; truncate to max_len."""
    return re.sub(r'[<>:"/\\|?*]', "_", (name or "")[:max_len * 2])[:max_len]


def safe_run_id() -> str:
    """Return a safe, unique run identifier (YYYYMMDD_HHMMSS) for subdirectory naming."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")
