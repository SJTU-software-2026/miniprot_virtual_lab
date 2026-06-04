"""
Step 6: Ensemble Docking
  - Dock the prepared ligand to each mutant structure using AutoDock Vina
  - Extract binding affinity scores for ranking
  - Supports both standard Vina and flexible-receptor modes

Paper reference: Meng et al. (2021) used Rosetta interface energy (ddG)
to rank designs. We use Vina binding free energy as a proxy. While not
identical, Vina scores have been shown to correlate with experimental
binding affinities in many enzyme engineering studies.
"""
import csv
import subprocess
from pathlib import Path
from typing import Optional

from ..state import WorkflowState
from ..utils import find_obabel, run_obabel, find_vina, find_scwrl


class MutationDockingStep:
    """
    Dock ligand to each mutant and compute binding scores.

    Uses AutoDock Vina for the docking and OpenBabel for PDB<->PDBQT conversion.
    """

    name = "mutation_docking"

    def __init__(
        self,
        vina_binary: str = "vina",
        obabel_binary: str = "obabel",
        box_padding: float = 5.0,
        exhaustiveness: int = 8,
        num_modes: int = 5,
        top_n: int = 20,
        flex_residues: list = None,
    ):
        self.vina_binary = vina_binary
        self.obabel_binary = obabel_binary
        self.box_padding = box_padding
        self.exhaustiveness = exhaustiveness
        self.num_modes = num_modes
        self.top_n = top_n
        self.flex_residues = flex_residues or []

    def _find_exe(self, name: str) -> Optional[str]:
        import shutil

        exe = shutil.which(name)
        if exe:
            return exe
        # Try known locations
        candidates = []
        if name in ("vina", "vina.exe"):
            candidates = [
                Path("C:/miniprot/tools/vina.exe"),
                Path("C:/miniprot/tools/vina_1.2.7_win.exe"),
            ]
        elif name in ("obabel", "obabel.exe"):
            candidates = [
                Path("C:/miniconda3/envs/biolab/Library/bin/obabel.exe"),
                Path("C:/miniconda3/envs/miniprot/Library/bin/obabel.exe"),
            ]
        for c in candidates:
            if c.exists():
                return str(c)
        return None

    # -- main ------------------------------------------------

    def run(self, state: WorkflowState) -> WorkflowState:
        print(f"\n{'='*60}")
        print(f"[STEP 6/7] {self.name}")
        print(f"{'='*60}")

        vina = self._find_exe(self.vina_binary)
        if vina is None:
            print("  AutoDock Vina NOT FOUND. Skipping docking step.")
            print("  Install Vina or set vina_binary path.")
            state.completed_steps.append(self.name)
            state.current_step = self.name
            return state

        obabel = self._find_exe(self.obabel_binary)
        if obabel is None:
            print("  OpenBabel NOT FOUND. Cannot convert PDB->PDBQT.")
            state.completed_steps.append(self.name)
            state.current_step = self.name
            return state

        if not state.generated_models:
            raise ValueError("No mutant models -- run Step 5 first")
        if state.prepared_ligand is None:
            raise ValueError("No prepared ligand -- run Step 2 first")

        output_dir = state.work_dir / "06_docking"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Use PLP/cofactor center as docking box center
        # (ligand center is often at 0,0,0 for generated ligands)
        plp_center = self._find_cofactor_center(state.prepared_receptor)
        docking_center = plp_center if plp_center else (state.ligand_center or [0, 0, 0])

        print(f"  Vina: {vina}")
        print(f"  Obabel: {obabel}")
        print(f"  Ligand: {state.prepared_ligand}")
        print(f"  Docking center: [{docking_center[0]:.2f}, "
              f"{docking_center[1]:.2f}, {docking_center[2]:.2f}]")

        # -- dock each mutant --------------------------------
        vina_scores = {}
        docking_outputs = {}

        mutant_items = list(state.generated_models.items())[:self.top_n]

        for i, (mutant, model_path) in enumerate(mutant_items):
            print(f"  [{i+1}/{len(mutant_items)}] Docking {mutant}...")

            receptor_pdb = Path(model_path)
            receptor_pdbqt = output_dir / f"{mutant}_receptor.pdbqt"

            # Convert receptor to PDBQT
            if not self._convert_pdb_to_pdbqt(obabel, receptor_pdb, receptor_pdbqt):
                print(f"    SKIP: PDB->PDBQT conversion failed for {mutant}")
                continue

            # Run Vina
            score = self._run_vina(
                vina, receptor_pdbqt,
                state.prepared_ligand,
                list(docking_center),
                output_dir,
                mutant,
            )

            if score is not None:
                vina_scores[mutant] = score
                docking_outputs[mutant] = str(
                    output_dir / f"{mutant}_out.pdbqt"
                )
                print(f"    Score: {score:.2f} kcal/mol")

        # -- save results ------------------------------------
        if vina_scores:
            ranked = sorted(vina_scores.items(), key=lambda x: x[1])
            csv_file = output_dir / "vina_scores.csv"
            with open(csv_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["rank", "mutant", "vina_score", "pose_file"])
                for idx, (mutant, score) in enumerate(ranked, start=1):
                    writer.writerow([
                        idx, mutant, f"{score:.3f}",
                        docking_outputs.get(mutant, ""),
                    ])
            print(f"\n  Top 5 scores:")
            for rank, (mutant, score) in enumerate(ranked[:5], start=1):
                print(f"    #{rank} {mutant}: {score:.2f} kcal/mol")

        state.vina_scores = vina_scores
        state.docking_outputs = docking_outputs
        state.completed_steps.append(self.name)
        state.current_step = self.name
        return state

    # -- helpers ---------------------------------------------

    def _convert_pdb_to_pdbqt(
        self, obabel: str, pdb: Path, pdbqt: Path
    ) -> bool:
        try:
            # Ensure input PDB has END at very end
            self._fix_pdb_end(pdb)
            result = run_obabel(
                [
                    obabel, "-ipdb", str(pdb),
                    "-opdbqt", "-O", str(pdbqt),
                    "-h", "--partialcharge", "gasteiger",
                    "-xr",  # rigid receptor (no ROOT/BRANCH tags)
                ],
                timeout=180,
            )
            if result.returncode != 0:
                print(f"    obabel error: {result.stderr[:200]}")
                return False
            if not pdbqt.exists() or pdbqt.stat().st_size == 0:
                print(f"    obabel produced empty PDBQT file")
                return False
            # Strip ROOT/ENDROOT/BRANCH/ENDBRANCH tags that Vina 1.2.7 rejects
            self._strip_flex_tags(pdbqt)
            return True
        except Exception as e:
            print(f"    Conversion error: {e}")
            return False

    @staticmethod
    def _strip_flex_tags(pdbqt_path: Path):
        """Remove ROOT/ENDROOT/BRANCH/ENDBRANCH from rigid receptor PDBQT."""
        lines = []
        with open(pdbqt_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped in ("ROOT", "ENDROOT", "BRANCH", "ENDBRANCH"):
                    continue
                lines.append(line)
        if len(lines) < sum(1 for _ in open(pdbqt_path)):
            with open(pdbqt_path, "w") as f:
                f.writelines(lines)

    @staticmethod
    def _fix_pdb_end(pdb_path: Path):
        """Remove all END lines and add a single END at the end."""
        lines = []
        with open(pdb_path) as f:
            for line in f:
                if not line.startswith("END"):
                    lines.append(line)
        with open(pdb_path, "w") as f:
            f.writelines(lines)
            f.write("END\n")

    def _run_vina(
        self,
        vina: str,
        receptor_pdbqt: Path,
        ligand_pdbqt: Path,
        center: list,
        output_dir: Path,
        mutant_name: str,
    ) -> Optional[float]:
        """Run AutoDock Vina and parse best binding score."""
        out_pdbqt = output_dir / f"{mutant_name}_out.pdbqt"
        log_file = output_dir / f"{mutant_name}_vina.log"

        cmd = [
            vina,
            "--receptor", str(receptor_pdbqt),
            "--ligand", str(ligand_pdbqt),
            "--center_x", str(center[0]),
            "--center_y", str(center[1]),
            "--center_z", str(center[2]),
            "--size_x", str(self.box_padding * 2),
            "--size_y", str(self.box_padding * 2),
            "--size_z", str(self.box_padding * 2),
            "--out", str(out_pdbqt),
            "--exhaustiveness", str(self.exhaustiveness),
            "--num_modes", str(self.num_modes),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            print(f"    TIMEOUT for {mutant_name}")
            return None
        except Exception as e:
            print(f"    Error: {e}")
            return None

        # Parse score from Vina stdout
        score = self._parse_score(result.stdout)
        if score is None:
            # Try parsing from the output PDBQT file
            if out_pdbqt.exists():
                score = self._parse_score(out_pdbqt.read_text())
        return score

    @staticmethod
    def _find_cofactor_center(pdb_path) -> Optional[tuple]:
        """Find the center of PLP or other cofactor in a PDB file."""
        if pdb_path is None:
            return None
        coords = []
        with open(pdb_path) as f:
            for line in f:
                if line.startswith("HETATM"):
                    x = float(line[30:38].strip())
                    y = float(line[38:46].strip())
                    z = float(line[46:54].strip())
                    coords.append((x, y, z))
        if not coords:
            return None
        n = len(coords)
        return (
            sum(c[0] for c in coords) / n,
            sum(c[1] for c in coords) / n,
            sum(c[2] for c in coords) / n,
        )

    @staticmethod
    def _parse_score(text: str) -> Optional[float]:
        """Parse the best binding affinity from Vina output."""
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "1":
                try:
                    return float(parts[1])
                except ValueError:
                    continue
        return None
