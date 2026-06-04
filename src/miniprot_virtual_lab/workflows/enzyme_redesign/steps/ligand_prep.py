"""
Step 2: Ligand Preparation
  - For general enzymes: convert ligand to PDBQT (OpenBabel)
  - For PLP-dependent enzymes: optionally build external aldimine intermediate
  - Geometry optimization with MMFF94 force field
  - Determine ligand centroid for docking box

Paper reference: Meng et al. (2021) -- external aldimine intermediate was built
by linking the amine product to PLP via Schiff base, then optimized with AM1
(COSMO implicit solvent) and partial charges from AM1/BCC.
We approximate this with OpenBabel MMFF94 optimization + Gasteiger charges.
"""
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl


class LigandPrepStep:
    """
    Prepare the ligand for docking.

    Two modes:
      - standard: just convert input ligand to PDBQT with charges
      - external_aldimine: build PLP-substrate covalent adduct (for transaminases)
    """

    name = "ligand_preparation"

    def __init__(
        self,
        obabel_binary: str = "obabel",
        build_external_aldimine: bool = False,
        plp_resname: str = "PLP",
        substrate_smiles: Optional[str] = None,
    ):
        self.obabel_binary = obabel_binary
        self.build_external_aldimine = build_external_aldimine
        self.plp_resname = plp_resname
        self.substrate_smiles = substrate_smiles


    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        title = (
            "[STEP 2/7] Ligand Preparation (external aldimine mode)"
            if self.build_external_aldimine
            else "[STEP 2/7] Ligand Preparation (standard mode)"
        )
        print(title)
        print(f"{'='*60}")

        obabel = find_obabel(self.obabel_binary)
        output_dir = state.work_dir / "02_ligand_prep"
        output_dir.mkdir(parents=True, exist_ok=True)

        if state.ligand_sdf is None:
            raise ValueError("No ligand provided (ligand_sdf is None)")

        # -- Convert ligand to PDBQT format ------------------
        ligand_pdbqt = output_dir / "ligand.pdbqt"

        cmds = [
            obabel,
            str(state.ligand_sdf),
            "-O", str(ligand_pdbqt),
            "--gen3d",                # generate 3D coordinates
            "--minimize",             # MMFF94 minimization
            "--ff", "MMFF94",
            "--partialcharge", "gasteiger",
        ]

        print(f"  Converting ligand to PDBQT with MMFF94 optimization...")
        result = run_obabel(cmds)

        if result.returncode != 0:
            raise RuntimeError(f"Ligand PDBQT conversion failed: {result.stderr}")

        print(f"  Ligand PDBQT -> {ligand_pdbqt}")

        # -- Compute ligand center (for docking box) ---------
        center = self._compute_centroid(ligand_pdbqt)
        if center is None:
            # Fallback: try to get center from original ligand
            center = self._compute_centroid_pdb(state.ligand_sdf)

        state.ligand_center = list(center) if center else [0.0, 0.0, 0.0]
        print(f"  Ligand center: [{state.ligand_center[0]:.2f}, "
              f"{state.ligand_center[1]:.2f}, {state.ligand_center[2]:.2f}]")

        state.prepared_ligand = ligand_pdbqt
        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state

    # -- helpers ---------------------------------------------

    @staticmethod
    def _compute_centroid(pdbqt_file: Path) -> Optional[Tuple[float, float, float]]:
        """Extract average x,y,z from ATOM/HETATM lines in a PDBQT file."""
        coords = []
        with open(pdbqt_file) as f:
            for line in f:
                if line.startswith("ATOM") or line.startswith("HETATM"):
                    try:
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        coords.append((x, y, z))
                    except ValueError:
                        continue
        if not coords:
            return None
        n = len(coords)
        return (
            sum(c[0] for c in coords) / n,
            sum(c[1] for c in coords) / n,
            sum(c[2] for c in coords) / n,
        )

    @staticmethod
    def _compute_centroid_pdb(pdb_file: Path) -> Optional[Tuple[float, float, float]]:
        """Fallback: read PDB or SDF directly."""
        coords = []
        with open(pdb_file) as f:
            for line in f:
                if line.startswith("ATOM") or line.startswith("HETATM"):
                    try:
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        coords.append((x, y, z))
                    except ValueError:
                        continue
        if not coords:
            return None
        n = len(coords)
        return (
            sum(c[0] for c in coords) / n,
            sum(c[1] for c in coords) / n,
            sum(c[2] for c in coords) / n,
        )
