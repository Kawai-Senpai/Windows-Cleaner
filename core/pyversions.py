# core/pyversions.py
# Discover installed Python versions, their sizes, and protection flags
# (running / on-PATH / py-launcher default) so the GUI can offer safe removal.

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

from . import engine

PY_ROOT = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class PyVersion:
    name: str
    path: str
    size: int
    running: bool
    on_path: bool
    is_default: bool

    @property
    def protected(self) -> bool:
        return self.running or self.on_path or self.is_default

    @property
    def flags(self) -> str:
        f = []
        if self.running:
            f.append("RUNNING")
        if self.on_path:
            f.append("ON-PATH")
        if self.is_default:
            f.append("DEFAULT")
        return ", ".join(f)


def _launcher_default() -> str:
    try:
        res = subprocess.run(["py", "--list"], capture_output=True, text=True,
                             creationflags=_NO_WINDOW)
        for line in res.stdout.splitlines():
            if "*" in line:
                return line.strip()
    except Exception:
        pass
    return ""


def _on_path(exe_dir: str) -> bool:
    parts = [p.strip().lower() for p in os.environ.get("PATH", "").split(os.pathsep)]
    return exe_dir.lower() in parts


def list_versions() -> List[PyVersion]:
    out: List[PyVersion] = []
    if not os.path.isdir(PY_ROOT):
        return out
    default = _launcher_default()
    running_dir = os.path.dirname(os.path.abspath(sys.executable)).lower()
    for entry in os.scandir(PY_ROOT):
        if entry.is_dir() and entry.name.lower().startswith("python"):
            exe_dir = entry.path
            ver_tag = entry.name[-2:]  # e.g. "11", "12"
            out.append(PyVersion(
                name=entry.name,
                path=entry.path,
                size=engine.dir_size(Path(entry.path)),
                running=(entry.path.lower() == running_dir),
                on_path=_on_path(exe_dir),
                is_default=(bool(default) and ver_tag in default),
            ))
    out.sort(key=lambda v: v.size, reverse=True)
    return out


def remove_version(v: PyVersion, archive_root: str, archive: bool, dry_run: bool, log) -> int:
    p = Path(v.path)
    if archive:
        return engine.archive_path(p, archive_root, dry_run, log)
    return engine.remove_path(p, dry_run, log)
