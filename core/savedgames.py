# core/savedgames.py
# Discover common save-game / game-data locations and their sizes so the user
# can pick which to archive. ALWAYS archive (never hard-delete) saves.

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from . import engine

USER = os.path.expanduser("~")

# Known roots where games stash saves/config. Globs allowed.
SAVE_ROOTS = [
    r"%USERPROFILE%\Saved Games",
    r"%USERPROFILE%\Documents\My Games",
    r"%LOCALAPPDATA%\*\Saved",          # UE games (e.g. EldenRing, Nightreign)
    r"%APPDATA%\*\Saves",
    r"%LOCALAPPDATA%\Packages\*\SystemAppData\wgs",  # Xbox/MS Store saves
]


@dataclass
class SaveLocation:
    name: str
    path: str
    size: int


def discover(min_mb: int = 5) -> List[SaveLocation]:
    out: List[SaveLocation] = []
    seen = set()
    for pattern in SAVE_ROOTS:
        for p in engine.iter_paths(pattern):
            if not p.exists() or not p.is_dir():
                continue
            # for the broad parent roots, descend one level to list each game
            if p.name.lower() in ("saved games", "my games"):
                children = [c for c in p.iterdir() if c.is_dir()]
            else:
                children = [p]
            for c in children:
                key = str(c).lower()
                if key in seen:
                    continue
                seen.add(key)
                size = engine.dir_size(c)
                if size >= min_mb * 1024 * 1024:
                    # for ".../<Game>/Saved" use the game folder name as label
                    label = c.name
                    if label.lower() == "saved":
                        label = f"{c.parent.name} (Saved)"
                    out.append(SaveLocation(name=label, path=str(c), size=size))
    out.sort(key=lambda s: s.size, reverse=True)
    return out


def archive_save(loc: SaveLocation, archive_root: str, dry_run: bool, log) -> int:
    return engine.archive_path(Path(loc.path), archive_root, dry_run, log)
