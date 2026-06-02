"""
Repair PDB structures: missing residues, missing atoms, chain breaks, non-standard residues.

When missing residues or chain breaks are detected, PDBFixer (and optionally Modeller) are used
to repair the structure so it can be used for docking or other downstream steps.
"""
try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

import logging
import os
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/pdb_repair"


try:
    from utils.path_utils import safe_dir, resolve_output_dir, ensure_file_permissions
except ImportError:
    from ..utils.path_utils import safe_dir, resolve_output_dir, ensure_file_permissions


def _check_issues(fixer) -> Dict[str, Any]:
    """Detect missing residues, missing atoms, non-standard residues. Return a summary dict."""
    issues: Dict[str, Any] = {
        "missing_residues": False,
        "missing_atoms": False,
        "nonstandard_residues": False,
        "missing_residues_count": 0,
        "missing_atoms_count": 0,
        "nonstandard_count": 0,
    }
    try:
        fixer.findMissingResidues()
        mr = getattr(fixer, "missingResidues", None) or {}
        if mr:
            issues["missing_residues"] = True
            issues["missing_residues_count"] = sum(len(v) for v in mr.values())
    except Exception as e:
        logger.debug("findMissingResidues: %s", e)
    try:
        fixer.findMissingAtoms()
        ma = getattr(fixer, "missingAtoms", None) or {}
        if ma:
            issues["missing_atoms"] = True
            issues["missing_atoms_count"] = sum(len(s) for s in ma.values())
    except Exception as e:
        logger.debug("findMissingAtoms: %s", e)
    try:
        fixer.findNonstandardResidues()
        ns = getattr(fixer, "nonstandardResidues", None) or []
        if ns:
            issues["nonstandard_residues"] = True
            issues["nonstandard_count"] = len(ns)
    except Exception as e:
        logger.debug("findNonstandardResidues: %s", e)
    return issues


def _repair_with_pdbfixer(pdb_path: str, out_path: str, add_hydrogens: bool = False) -> Dict[str, Any]:
    """
    Run PDBFixer: add missing residues, missing atoms, replace non-standard residues; write PDB.
    Returns dict with success, message, and any exception.
    """
    try:
        from pdbfixer import PDBFixer  # type: ignore[import-untyped]
        import openmm.app as app  # type: ignore[import-untyped]
    except ImportError as e:
        return {"success": False, "message": "PDBFixer/OpenMM not installed.", "error": str(e)}

    try:
        fixer = PDBFixer(filename=pdb_path)
    except Exception as e:
        return {"success": False, "message": f"Failed to load PDB: {e}", "error": str(e)}

    issues = _check_issues(fixer)
    has_issues = (
        issues["missing_residues"]
        or issues["missing_atoms"]
        or issues["nonstandard_residues"]
    )

    try:
        if issues["missing_residues"]:
            fixer.findMissingResidues()
            fixer.addMissingResidues()
        fixer.findMissingAtoms()
        if issues["missing_atoms"] or issues["missing_residues"]:
            fixer.addMissingAtoms()
        if issues["nonstandard_residues"]:
            fixer.findNonstandardResidues()
            fixer.replaceNonstandardResidues()
        if add_hydrogens:
            fixer.addMissingHydrogens()
    except Exception as e:
        logger.warning("PDBFixer repair step failed: %s", e)
        return {
            "success": False,
            "message": f"Repair failed: {e}",
            "error": str(e),
            "issues_found": issues,
        }

    try:
        with open(out_path, "w") as f:
            app.PDBFile.writeFile(fixer.topology, fixer.positions, f)
        ensure_file_permissions(out_path)
    except Exception as e:
        return {"success": False, "message": f"Failed to write PDB: {e}", "error": str(e)}

    return {
        "success": True,
        "message": "Repaired with PDBFixer (missing residues/atoms/nonstandard replaced).",
        "issues_found": issues,
        "had_issues": has_issues,
    }


def _try_modeller_refine(in_path: str, out_path: str) -> Dict[str, Any]:
    """
    Optionally refine added loops/regions with Modeller. Returns success and message.
    Modeller requires a license key from Salilab; if not available, we skip and return success=False for this step.
    """
    try:
        import modeller  # type: ignore[import-untyped]
    except ImportError:
        return {"success": False, "message": "Modeller not installed or license not set."}

    try:
        env = modeller.Environ()
        env.io.atom_files_directory = [os.path.dirname(os.path.abspath(in_path))]
        mdl = modeller.Model(env, file=os.path.basename(in_path))
        # Simple refinement: only if we have a valid Modeller env (e.g. key set)
        mdl.write(file=out_path)
        return {"success": True, "message": "Modeller wrote structure (no loop refinement run)."}
    except Exception as e:
        logger.debug("Modeller refine: %s", e)
        return {"success": False, "message": str(e)}


class PDBRepairTool(BaseTool):
    """
    Repair PDB structures when missing residues, chain breaks, or non-standard residues are found.
    Uses PDBFixer to add missing residues/atoms and replace non-standard residues; optionally Modeller.
    Call this before docking when structures fail due to missing residues or chain breaks.
    """

    def __init__(self):
        self._name = "pdb_repair"
        self._description = (
            "Repair PDB structures: when missing residues, chain breaks, or non-standard residues are found, "
            "use PDBFixer (and optionally Modeller) to fix the structure. Use before docking if preparation "
            "fails with missing residues or chain breaks. Input: pdb_path or pdb_paths (list). Output: repaired PDB path(s)."
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
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
                        "pdb_path": {"type": "string", "description": "Path to a single PDB file to repair."},
                        "pdb_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Paths to multiple PDB files to repair (optional; use instead of pdb_path).",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": "Output directory for repaired PDB files (default: data/outputs/pdb_repair).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "force_repair": {
                            "type": "boolean",
                            "description": "If true, run repair even when no issues are detected (e.g. add hydrogens).",
                            "default": False,
                        },
                        "use_modeller": {
                            "type": "boolean",
                            "description": "If true and Modeller is installed, use it for refinement (optional; often requires license).",
                            "default": False,
                        },
                        "add_hydrogens": {
                            "type": "boolean",
                            "description": "Add missing hydrogens (default false; set true for downstream MD).",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        pdb_path = (kwargs.get("pdb_path") or "").strip()
        pdb_paths = kwargs.get("pdb_paths") or []
        if isinstance(pdb_paths, str):
            pdb_paths = [pdb_paths]
        pdb_paths = [p.strip() for p in pdb_paths if p and isinstance(p, str)]
        if pdb_path and os.path.isfile(pdb_path):
            paths_to_repair = [pdb_path]
        elif pdb_paths:
            paths_to_repair = [p for p in pdb_paths if os.path.isfile(p)]
        else:
            return {"success": False, "error": "Provide pdb_path or pdb_paths (at least one existing file).", "data": {}}

        if not paths_to_repair:
            return {"success": False, "error": "No existing PDB files found.", "data": {}}

        output_dir = safe_dir(resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip()))
        force_repair = bool(kwargs.get("force_repair", False))
        use_modeller = bool(kwargs.get("use_modeller", False))
        add_hydrogens = bool(kwargs.get("add_hydrogens", False))

        try:
            from pdbfixer import PDBFixer  # type: ignore[import-untyped]
        except ImportError:
            return {
                "success": False,
                "error": "PDBFixer is required for pdb_repair. Install: pip install pdbfixer openmm (or conda install -c conda-forge pdbfixer openmm).",
                "data": {},
            }

        repaired: List[str] = []
        details: List[Dict[str, Any]] = []
        for in_path in paths_to_repair:
            base = os.path.splitext(os.path.basename(in_path))[0]
            out_path = os.path.join(output_dir, f"{base}_repaired.pdb")

            fixer = None
            try:
                fixer = PDBFixer(filename=in_path)
            except Exception as e:
                details.append({"path": in_path, "success": False, "error": str(e)})
                continue

            issues = _check_issues(fixer)
            has_issues = (
                issues["missing_residues"]
                or issues["missing_atoms"]
                or issues["nonstandard_residues"]
            )

            if not has_issues and not force_repair:
                # No issues: copy or write as-is so user still gets an output path
                import shutil
                shutil.copy2(in_path, out_path)
                repaired.append(out_path)
                details.append({"path": in_path, "repaired_path": out_path, "issues": issues, "repaired": False, "message": "No issues found; file copied."})
                continue

            result = _repair_with_pdbfixer(in_path, out_path, add_hydrogens=add_hydrogens)
            if not result["success"]:
                details.append({"path": in_path, "success": False, "error": result.get("error") or result.get("message")})
                continue

            repaired.append(out_path)
            details.append({
                "path": in_path,
                "repaired_path": out_path,
                "issues": result.get("issues_found", issues),
                "repaired": result.get("had_issues", True),
                "message": result.get("message", ""),
            })

            if use_modeller and result["success"]:
                mod_out = os.path.join(output_dir, f"{base}_repaired_modeller.pdb")
                mod_result = _try_modeller_refine(out_path, mod_out)
                if mod_result["success"] and os.path.isfile(mod_out):
                    ensure_file_permissions(mod_out)
                    repaired.append(mod_out)
                    details[-1]["modeller_path"] = mod_out

        return {
            "success": len(repaired) > 0,
            "data": {
                "repaired_paths": repaired,
                "details": details,
                "output_dir": output_dir,
                "message": f"Repaired {len(repaired)} file(s). Use repaired_paths for docking or downstream steps." if repaired else "No files could be repaired.",
            },
            "error": None if repaired else "Repair failed for all files; see data.details.",
        }
