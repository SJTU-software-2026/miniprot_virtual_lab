"""
PyMOL tool: protein structure viewing and image rendering via the PyMOL API.

https://pymol.org/dokuwiki/?id=api
https://pymol.org/pymol-api-only.html

Actions:
- load: Load a structure (PDB/mmCIF) into PyMOL (validates file; no GUI).
- render_image: Load structure, apply display (cartoon, etc.), and save PNG or PDF.
  Uses cmd.load, cmd.show, cmd.zoom, cmd.png or cmd.ray+cmd.png.

API-only (headless) mode: runs a small script with pymol -cq so no display is required.
Install: conda install -c conda-forge pymol-open-source (or pymol).
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/pymol"

try:
    from utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions, safe_run_id
except ImportError:
    from ..utils.path_utils import safe_dir, workspace_root, resolve_output_dir, ensure_file_permissions, safe_run_id


def _find_pymol() -> Optional[str]:
    """Return path to pymol executable or None."""
    import shutil
    return shutil.which("pymol") or shutil.which("PyMOL")


def _run_pymol_script(script_content: str, timeout: int = 120) -> tuple[bool, str, str]:
    """Run a Python script with PyMOL in headless mode. Returns (success, stdout, stderr)."""
    pymol_exe = _find_pymol()
    if not pymol_exe:
        return False, "", "PyMOL not found. Install e.g. conda install -c conda-forge pymol-open-source"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script_content)
        script_path = f.name
    try:
        r = subprocess.run(
            [pymol_exe, "-cq", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(script_path) or ".",
        )
        stdout = (r.stdout or "").strip()
        stderr = (r.stderr or "").strip()
        if r.returncode != 0:
            return False, stdout, stderr or f"exit {r.returncode}"
        return True, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, "", "PyMOL script timed out"
    except Exception as e:
        return False, "", str(e)
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


class PyMOLTool(BaseTool):
    """PyMOL: load and visualize protein structures; save images (PNG/PDF) via the PyMOL API."""

    @property
    def name(self) -> str:
        return "pymol"

    @property
    def description(self) -> str:
        return (
            "PyMOL: protein structure viewing and rendering. Load PDB/mmCIF files and save images (PNG/PDF). "
            "Use action load to validate a structure, or render_image to load and save a figure (cartoon, etc.). "
            "Runs in API-only (headless) mode; no display required. "
            "Install: conda install -c conda-forge pymol-open-source."
        )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "pymol",
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: load (load structure only) or render_image (load and save PNG/PDF). Default render_image.",
                        "enum": ["load", "render_image"],
                        "default": "render_image",
                    },
                    "pdb_path": {
                        "type": "string",
                        "description": "Path to structure file (PDB or mmCIF). Required for load and render_image.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output image path for render_image (PNG or PDF). If omitted, saved to output_dir with a timestamped name.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for rendered image. Default: data/outputs/pymol.",
                        "default": DEFAULT_OUTPUT_DIR,
                    },
                    "image_format": {
                        "type": "string",
                        "description": "Image format for render_image: png or pdf. Default: png.",
                        "enum": ["png", "pdf"],
                        "default": "png",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Image width in pixels (png). Default: 800.",
                        "default": 800,
                    },
                    "height": {
                        "type": "integer",
                        "description": "Image height in pixels (png). Default: 600.",
                        "default": 600,
                    },
                    "dpi": {
                        "type": "integer",
                        "description": "Resolution (DPI) for PNG output. Default: 300.",
                        "default": 300,
                    },
                    "ray": {
                        "type": "boolean",
                        "description": "If true, use ray-tracing for higher quality image. Default: false.",
                        "default": False,
                    },
                    "show_style": {
                        "type": "string",
                        "description": "Display style: cartoon, sticks, lines, surface, etc. Default: cartoon.",
                        "default": "cartoon",
                    },
                    "docked_complex": {
                        "type": "boolean",
                        "description": "If true, render as docked complex: receptor (cartoon), ligand (sticks), interacting residues (sticks), and H-bonds/dashes. Use when user asks for 'docked complex' image. Default: false.",
                        "default": False,
                    },
                    "interaction_cutoff_a": {
                        "type": "number",
                        "description": "For docked_complex: distance cutoff (Å) to define interacting residues around the ligand. Default: 5.0.",
                        "default": 5.0,
                    },
                    "measure_contacts": {
                        "type": "boolean",
                        "description": "For docked_complex: measure close-contact distances (as dashed distance objects). Default: true.",
                        "default": True,
                    },
                    "label_ligand_and_cofactors": {
                        "type": "boolean",
                        "description": "For docked_complex: label ligand, metals, and common cofactors (publication-style). Default: true.",
                        "default": True,
                    },
                    "background": {
                        "type": "string",
                        "description": "Background color: white or black. Default: white for all figures.",
                        "enum": ["white", "black"],
                        "default": "white",
                    },
                },
                "required": ["action", "pdb_path"],
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = (kwargs.get("action") or "render_image").strip().lower() or "render_image"
        pdb_path = (kwargs.get("pdb_path") or "").strip()
        if not pdb_path:
            return {"success": False, "error": "pdb_path is required.", "data": {}}
        if not os.path.isfile(pdb_path):
            return {"success": False, "error": f"Structure file not found: {pdb_path}", "data": {}}
        pdb_path = os.path.abspath(pdb_path)

        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.normpath(os.path.join(workspace_root(), output_dir)))
        default_base = os.path.normpath(os.path.join(workspace_root(), DEFAULT_OUTPUT_DIR))
        if output_dir == default_base:
            output_dir = safe_dir(os.path.join(output_dir, safe_run_id()))
        else:
            safe_dir(output_dir)

        if action == "load":
            script = f'''
from pymol import cmd
cmd.load("{pdb_path.replace(chr(92), "/")}")
cmd.zoom()
print("Loaded:", "{os.path.basename(pdb_path)}")
'''
            ok, stdout, stderr = _run_pymol_script(script)
            if not ok:
                return {"success": False, "error": stderr or stdout or "PyMOL load failed", "data": {}}
            return {
                "success": True,
                "data": {
                    "message": f"Loaded structure: {os.path.basename(pdb_path)}",
                    "pdb_path": pdb_path,
                    "downloaded": {"pdb": [pdb_path]},
                },
            }

        if action == "render_image":
            output_path = (kwargs.get("output_path") or "").strip()
            image_format = (kwargs.get("image_format") or "png").strip().lower() or "png"
            if image_format not in ("png", "pdf"):
                image_format = "png"
            width = int(kwargs.get("width") or 800)
            height = int(kwargs.get("height") or 600)
            dpi = int(kwargs.get("dpi") or 300)
            ray = bool(kwargs.get("ray"))
            show_style = (kwargs.get("show_style") or "cartoon").strip() or "cartoon"
            docked_complex = bool(kwargs.get("docked_complex", False))
            interaction_cutoff_a = float(kwargs.get("interaction_cutoff_a") or 5.0)
            measure_contacts = bool(kwargs.get("measure_contacts", True))
            label_ligand_and_cofactors = bool(kwargs.get("label_ligand_and_cofactors", True))
            bg = (kwargs.get("background") or "white").strip().lower() or "white"
            if not output_path:
                run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = os.path.join(output_dir, f"pymol_{run_id}.{image_format}")
            elif not os.path.isabs(output_path):
                output_path = os.path.normpath(os.path.join(output_dir, output_path))
            out_dir = os.path.dirname(output_path)
            safe_dir(out_dir)
            output_path = os.path.abspath(output_path)

            pdb_esc = pdb_path.replace("\\", "/")
            out_esc = output_path.replace("\\", "/")
            if image_format == "pdf":
                save_cmd = f'cmd.pdf("{out_esc}")'
            else:
                if ray:
                    save_cmd = f'cmd.ray({width}, {height})\ncmd.png("{out_esc}", {width}, {height}, {dpi}, ray=1)'
                else:
                    save_cmd = f'cmd.png("{out_esc}", {width}, {height}, {dpi})'

            if docked_complex:
                # Docked complex: receptor (cartoon), ligand (sticks), interacting residues (sticks), H-bonds (dashes), white background
                script = f'''
from pymol import cmd
cmd.reinitialize()
cmd.load("{pdb_esc}")
cmd.bg_color("{bg}")
cmd.set("ray_opaque_background", 0)
cmd.set("antialias", 2)
cmd.set("two_sided_lighting", 1)
cmd.set("cartoon_fancy_helices", 1)
cmd.set("cartoon_smooth_loops", 1)
cmd.set("stick_radius", 0.18)
cmd.set("dash_gap", 0.35)
cmd.set("dash_length", 0.12)
cmd.set("dash_radius", 0.05)
cmd.set("dash_color", "black")
cmd.set("label_color", "black")
cmd.set("label_size", 18)
cmd.set("label_font_id", 7)  # Sans, generally publication-friendly

cmd.select("receptor", "polymer and protein")
cmd.select("ligand", "organic")
cmd.show("cartoon", "receptor")
cmd.color("blue", "receptor")
cmd.show("sticks", "ligand")
cmd.color("orange", "ligand")

# Interacting residues within cutoff (default 5Å)
cutoff = {interaction_cutoff_a}
cmd.select("contacts", f"byres (receptor within {{cutoff}} of ligand)")
cmd.show("sticks", "contacts")
cmd.color("red", "contacts")

# Hydrogen-bond-like polar contacts (heuristic; depends on prepared structures)
cmd.distance("hbonds", "contacts and (elem N or elem O or elem S)", "ligand and (elem N or elem O or elem S)", 3.4, mode=2)
cmd.show("dashes", "hbonds")

if {str(measure_contacts)}:
    # Close contact distances (more inclusive than hbonds); keep cutoff modest to avoid clutter
    cmd.distance("close_contacts", "contacts", "ligand", 3.0, mode=2)
    cmd.show("dashes", "close_contacts")

# Cofactors and metal ions (if present)
cmd.select("metals", "receptor and inorganic and not solvent")
cmd.select("cofactors", "receptor and (not polymer) and (not solvent) and (not organic) and (not inorganic)")
if cmd.count_atoms("metals") > 0:
    cmd.show("spheres", "metals")
    cmd.color("tv_green", "metals")
    cmd.set("sphere_scale", 0.35, "metals")
if cmd.count_atoms("cofactors") > 0:
    cmd.show("sticks", "cofactors")
    cmd.color("teal", "cofactors")

def _safe_label_one(sel: str, text: str) -> None:
    # Pick a single atom to label to avoid dozens of labels.
    if cmd.count_atoms(sel) <= 0:
        return
    cmd.label("first (" + sel + ")", "\\"" + text + "\\"")

if {str(label_ligand_and_cofactors)}:
    _safe_label_one("ligand", "Ligand")
    if cmd.count_atoms("metals") > 0:
        _safe_label_one("metals", "Metal")
    if cmd.count_atoms("cofactors") > 0:
        _safe_label_one("cofactors", "Cofactor")

cmd.deselect()
cmd.zoom("receptor or ligand or contacts", buffer=10)
{save_cmd}
print("Saved:", "{output_path}")
'''
            else:
                # Single-style display; always use white background
                script = f'''
from pymol import cmd
cmd.load("{pdb_esc}")
cmd.bg_color("{bg}")
cmd.show("{show_style}")
cmd.zoom()
{save_cmd}
print("Saved:", "{output_path}")
'''
            ok, stdout, stderr = _run_pymol_script(script, timeout=180)
            if not ok:
                return {"success": False, "error": stderr or stdout or "PyMOL render failed", "data": {}}
            if not os.path.isfile(output_path):
                return {"success": False, "error": "Image file was not written.", "data": {}}
            ensure_file_permissions(output_path)
            return {
                "success": True,
                "data": {
                    "message": f"Rendered image: {output_path}",
                    "output_path": output_path,
                    "downloaded": {"figure": [output_path]},
                },
            }

        return {"success": False, "error": f"Unknown action: {action}. Use load or render_image.", "data": {}}
