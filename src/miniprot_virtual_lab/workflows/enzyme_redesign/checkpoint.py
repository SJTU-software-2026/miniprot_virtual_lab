import json
from pathlib import Path
from dataclasses import asdict

from .state import WorkflowState


class CheckpointManager:
    """
    管理 workflow checkpoint
    """

    def __init__(self, checkpoint_file: Path):
        self.checkpoint_file = checkpoint_file

    def save(self, state: WorkflowState):
        """
        保存 workflow state
        """
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        state_dict = asdict(state)
        for key, value in state_dict.items():
            if isinstance(value, Path):
                state_dict[key] = str(value)

        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=4)

    def load(self) -> WorkflowState:
        """
        从 checkpoint 恢复 workflow state
        """

        with open(self.checkpoint_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        data["enzyme_pdb"] = Path(data["enzyme_pdb"])
        data["ligand_sdf"] = Path(data["ligand_sdf"])
        data["work_dir"] = Path(data["work_dir"])

        if data.get("prepared_pdb") is not None:
            data["prepared_pdb"] = Path(data["prepared_pdb"])

        return WorkflowState(**data)

    def exists(self) -> bool:
        """
        checkpoint是否存在
        """

        return self.checkpoint_file.exists()
