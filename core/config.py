# core/config.py
# Loads/saves user choices to config.json next to the app.
# Stores which task keys are enabled, which docker ops are enabled,
# dry-run preference, and scanner settings.

import json
from pathlib import Path
from typing import Dict, List

from . import docker
from .tasks import build_tasks

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def default_config() -> dict:
    tasks = build_tasks()
    return {
        "dry_run": False,
        "enabled_tasks": {t.key: t.default_on for t in tasks},
        "docker_ops": {k: v["default"] for k, v in docker.PRUNE_OPS.items()},
        "scanner": {
            "root": "C:\\",
            "min_size_gb": 1.0,
            "top": 50,
        },
        # Where 'backup then remove' moves delicate files. Configurable.
        "archive_root": "D:\\_CleanerArchive",
        # Custom user-triaged entries discovered by deep scans, e.g.:
        #   {"path": "...", "action": "prune|archive|ignore", "note": "..."}
        "custom_targets": [],
    }


def load_config() -> dict:
    cfg = default_config()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # merge shallowly so new keys from updates get defaults
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass
