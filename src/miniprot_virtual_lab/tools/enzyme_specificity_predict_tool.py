"""
Enzyme–substrate specificity inference using the structure_sequence (SS) model
from the sibling `enzyme_prediction` project (PyTorch Lightning checkpoint).
"""
from __future__ import annotations

import glob
import logging
import os
import sys
from typing import Any, Dict, List

try:
    from .base_tools import BaseTool
except ImportError:
    from base_tools import BaseTool

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "data/outputs/enzyme_specificity_predict"

# Default checkpoint committed under enzyme_update/models/enzyme_specificity/
_BUNDLED_CKPT_NAME = "ss_lightning.ckpt"


def _bundled_checkpoint_path() -> str:
    try:
        from utils.path_utils import workspace_root
    except ImportError:
        from ..utils.path_utils import workspace_root

    return os.path.abspath(
        os.path.join(workspace_root(), "models", "enzyme_specificity", _BUNDLED_CKPT_NAME)
    )


def _enzyme_prediction_root() -> str:
    env = (os.environ.get("ENZYME_PREDICTION_ROOT") or "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    try:
        from utils.path_utils import workspace_root
    except ImportError:
        from ..utils.path_utils import workspace_root

    wr = workspace_root()
    for rel in ("../enzyme_prediction", "../../enzyme_mining/enzyme_prediction"):
        c = os.path.abspath(os.path.join(wr, rel))
        if os.path.isdir(os.path.join(c, "src", "src", "Datasets")):
            return c
    return os.path.abspath(os.path.join(os.path.dirname(wr), "enzyme_prediction"))


def _prediction_src() -> str:
    return os.path.join(_enzyme_prediction_root(), "src", "src")


def _default_config_path() -> str:
    return os.path.join(
        _enzyme_prediction_root(),
        "saved_model",
        "saved_model",
        "model",
        "run_0",
        "complete-full-random-all-0-complex.yml",
    )


def _discover_checkpoints() -> List[str]:
    """Prefer repo-bundled weights, then checkpoints under ENZYME_PREDICTION_ROOT."""
    found_ordered: List[str] = []
    seen: set = set()

    def _add(path: str) -> None:
        ab = os.path.abspath(path)
        if not os.path.isfile(ab) or ab in seen:
            return
        base = os.path.basename(ab)
        if "-v" in base and base.lower().endswith(".ckpt"):
            return
        seen.add(ab)
        found_ordered.append(ab)

    bundled = _bundled_checkpoint_path()
    if os.path.isfile(bundled):
        _add(bundled)

    root = _enzyme_prediction_root()
    patterns = [
        os.path.join(root, "saved_model", "**", "*.ckpt"),
        os.path.join(root, "**", "models", "*.ckpt"),
    ]
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            if os.path.isfile(p):
                _add(p)
    return found_ordered


class EnzymeSpecificityPredictTool(BaseTool):
    """Run SS (structure_sequence) test inference; writes a CSV with logits/probabilities."""

    def __init__(self) -> None:
        self._name = "enzyme_specificity_predict"
        self._description = (
            "Predict enzyme–substrate specificity scores using the trained structure_sequence (SS) "
            "Lightning model. This repo ships a default checkpoint at models/enzyme_specificity/"
            f"{_BUNDLED_CKPT_NAME} (copied from enzyme_prediction). Inference still needs a YAML whose "
            "train/val/test CSV and LMDB feature paths exist on your machine. "
            "Override checkpoint with checkpoint_path or ENZYME_SPECIFICITY_CHECKPOINT; config with "
            "config_path or ENZYME_PREDICTION_ROOT for the enzyme_prediction tree."
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
                        "checkpoint_path": {
                            "type": "string",
                            "description": (
                                "Path to a Lightning .ckpt for Models.ss.SS. "
                                "If omitted: env ENZYME_SPECIFICITY_CHECKPOINT, else models/enzyme_specificity/"
                                f"{_BUNDLED_CKPT_NAME} in the workspace, else first *.ckpt under "
                                "ENZYME_PREDICTION_ROOT/saved_model."
                            ),
                        },
                        "config_path": {
                            "type": "string",
                            "description": (
                                "YAML config compatible with enzyme_prediction (same keys as training). "
                                "Default: saved_model/saved_model/model/run_0/complete-full-random-all-0-complex.yml "
                                "under ENZYME_PREDICTION_ROOT."
                            ),
                        },
                        "train_data_path": {
                            "type": "string",
                            "description": "Optional: override config.data.train_data_path with this single CSV.",
                        },
                        "val_data_path": {
                            "type": "string",
                            "description": "Optional: override config.data.val_data_path with this single CSV.",
                        },
                        "test_data_path": {
                            "type": "string",
                            "description": "Optional: override config.data.test_data_path with this single CSV.",
                        },
                        "output_dir": {
                            "type": "string",
                            "description": f"Directory for predictions CSV (default: {DEFAULT_OUTPUT_DIR}).",
                            "default": DEFAULT_OUTPUT_DIR,
                        },
                        "accelerator": {
                            "type": "string",
                            "description": "Lightning Trainer accelerator: auto, cpu, gpu, cuda (default: auto).",
                            "default": "auto",
                        },
                        "devices": {
                            "type": "integer",
                            "description": "Number of devices for Trainer (default: 1).",
                            "default": 1,
                        },
                    },
                    "required": [],
                },
            },
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            from utils.path_utils import (
                ensure_file_permissions,
                resolve_output_dir,
                safe_dir,
                safe_run_id,
            )
        except ImportError:
            from ..utils.path_utils import (
                ensure_file_permissions,
                resolve_output_dir,
                safe_dir,
                safe_run_id,
            )

        checkpoint_path = (kwargs.get("checkpoint_path") or "").strip()
        config_path = (kwargs.get("config_path") or "").strip()
        train_override = (kwargs.get("train_data_path") or "").strip()
        val_override = (kwargs.get("val_data_path") or "").strip()
        test_override = (kwargs.get("test_data_path") or "").strip()
        output_dir = resolve_output_dir((kwargs.get("output_dir") or DEFAULT_OUTPUT_DIR).strip())
        accelerator = (kwargs.get("accelerator") or os.environ.get("ENZYME_PREDICTION_ACCELERATOR") or "auto").strip()
        try:
            devices = int(kwargs.get("devices") if kwargs.get("devices") is not None else os.environ.get("ENZYME_PREDICTION_DEVICES", "1"))
        except (TypeError, ValueError):
            devices = 1

        if not config_path or not os.path.isfile(config_path):
            default_cfg = _default_config_path()
            if os.path.isfile(default_cfg):
                config_path = default_cfg
            else:
                return {
                    "success": False,
                    "error": (
                        "config_path must point to an existing YAML. "
                        f"Default not found at {default_cfg}. Set config_path or ENZYME_PREDICTION_ROOT."
                    ),
                    "data": {"enzyme_prediction_root": _enzyme_prediction_root()},
                }

        if not checkpoint_path or not os.path.isfile(checkpoint_path):
            env_ckpt = (os.environ.get("ENZYME_SPECIFICITY_CHECKPOINT") or "").strip()
            if env_ckpt and os.path.isfile(env_ckpt):
                checkpoint_path = env_ckpt
            else:
                cands = _discover_checkpoints()
                if cands:
                    checkpoint_path = cands[0]
                    logger.info("Using discovered checkpoint: %s", checkpoint_path)

        if not checkpoint_path or not os.path.isfile(checkpoint_path):
            return {
                "success": False,
                "error": (
                    "checkpoint_path is required (Lightning .ckpt). "
                    "Set checkpoint_path, or ENZYME_SPECIFICITY_CHECKPOINT, or place a .ckpt under "
                    f"{_enzyme_prediction_root()}/saved_model. Discovered: {_discover_checkpoints()[:5]}"
                ),
                "data": {"enzyme_prediction_root": _enzyme_prediction_root()},
            }

        pred_src = _prediction_src()
        if not os.path.isdir(os.path.join(pred_src, "Datasets")):
            return {
                "success": False,
                "error": f"enzyme_prediction source tree not found at {pred_src}. Set ENZYME_PREDICTION_ROOT.",
                "data": {},
            }

        if pred_src not in sys.path:
            sys.path.insert(0, pred_src)

        try:
            import numpy as np
            import pandas as pd
            import pytorch_lightning as pl
            import torch
            from utils import load_config
            from Datasets.brenda import Singledataset
            from Models.ss import SS
        except ImportError as exc:
            return {
                "success": False,
                "error": (
                    f"Import failed ({exc}). Install inference deps in this environment: "
                    "pytorch_lightning, torch, torch_geometric, torch_scatter, easydict, PyYAML, "
                    "pandas, numpy, scikit-learn, rdkit, tqdm, warmup-scheduler (imported by SS), etc."
                ),
                "data": {"prediction_src": pred_src},
            }

        try:
            config = load_config(config_path)
            if train_override:
                config.data.train_data_path = [train_override]
            if val_override:
                config.data.val_data_path = [val_override]
            if test_override:
                config.data.test_data_path = [test_override]

            try:
                ncpu = min(
                    int(os.environ.get("ENZYME_PREDICTION_NUM_CPUS", "4")),
                    os.cpu_count() or 4,
                )
            except ValueError:
                ncpu = 4
            config.num_cpus = max(1, ncpu)

            dm = Singledataset(config)
            map_location = None
            if accelerator == "cpu" or not torch.cuda.is_available():
                map_location = torch.device("cpu")
            model = SS.load_from_checkpoint(
                checkpoint_path,
                config=config,
                map_location=map_location,
            )

            trainer = pl.Trainer(
                accelerator=accelerator,
                devices=devices,
                logger=False,
                enable_checkpointing=False,
                enable_progress_bar=False,
            )
            trainer.test(model, datamodule=dm)

            logits = getattr(model, "logits", None)
            df = getattr(dm, "test_prediction_df", None)
            rows = int(len(df)) if df is not None else 0
            if logits is not None and df is not None and rows == int(np.asarray(logits).reshape(-1).shape[0]):
                flat = np.asarray(logits).reshape(-1)
                df = df.copy()
                df["logit"] = flat
                df["probability"] = 1.0 / (1.0 + np.exp(-flat))
            elif logits is not None:
                logger.warning("Could not align logits length with test_prediction_df; CSV will omit per-row scores.")

            run_id = safe_run_id()
            safe_dir(output_dir)
            out_name = f"specificity_predictions_{run_id}.csv"
            out_path = os.path.join(output_dir, out_name)
            if df is not None:
                df.to_csv(out_path, index=False)
                ensure_file_permissions(out_path)
            else:
                out_path = os.path.join(output_dir, f"logits_only_{run_id}.npy")
                if logits is not None:
                    np.save(out_path, np.asarray(logits))
                    ensure_file_permissions(out_path)

            return {
                "success": True,
                "data": {
                    "output_dir": output_dir,
                    "predictions_csv": out_path if df is not None else None,
                    "artifact_path": out_path,
                    "checkpoint_path": os.path.abspath(checkpoint_path),
                    "config_path": os.path.abspath(config_path),
                    "enzyme_prediction_root": _enzyme_prediction_root(),
                    "rows": rows,
                    "generated_files": [os.path.abspath(out_path)],
                    "artifact_refs": [os.path.abspath(out_path)],
                },
            }
        except Exception as exc:
            logger.exception("enzyme_specificity_predict failed")
            return {
                "success": False,
                "error": str(exc),
                "data": {
                    "enzyme_prediction_root": _enzyme_prediction_root(),
                    "checkpoint_path": checkpoint_path,
                    "config_path": config_path,
                },
            }
