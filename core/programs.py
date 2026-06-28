# core/programs.py
# Enumerate installed programs (registry) and the per-app data folders under
# AppData / ProgramData. Match them so the GUI can show:
#   - apps you have installed (their data folders)
#   - data folders whose app is NOT installed (likely leftovers to clear)
#
# Removal always goes through engine.archive_path (backup-then-delete) because
# app data may be wanted back.

import os
import re
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set

from . import engine

UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

DATA_ROOTS = [
    os.path.expandvars(r"%LOCALAPPDATA%"),
    os.path.expandvars(r"%APPDATA%"),
    r"C:\ProgramData",
]

# Folders that are NOT per-app data (skip from the leftovers view).
SKIP_NAMES = {
    "microsoft", "packages", "temp", "tmp", "programs", "google", "nvidia",
    "nvidia corporation", "package cache", "connecteddevicesplatform",
    "comms", "d3dscache", "publishers", "powershell", "windows", "ms-playwright",
}


@dataclass
class InstalledApp:
    name: str
    publisher: str
    location: str


@dataclass
class DataFolder:
    name: str
    path: str
    size: int
    matched_app: str  # "" if no installed app matched -> candidate leftover


def list_installed() -> List[InstalledApp]:
    apps: Dict[str, InstalledApp] = {}
    for root, subpath in UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(root, subpath) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub) as app:
                            name = _val(app, "DisplayName")
                            if not name:
                                continue
                            if _val(app, "SystemComponent") == 1:
                                continue
                            apps[name.lower()] = InstalledApp(
                                name=name,
                                publisher=_val(app, "Publisher") or "",
                                location=_val(app, "InstallLocation") or "",
                            )
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(apps.values(), key=lambda a: a.name.lower())


def _val(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _installed_tokens(apps: List[InstalledApp]) -> Set[str]:
    """Normalized name + publisher tokens to match folders against."""
    tokens: Set[str] = set()
    for a in apps:
        n = _norm(a.name)
        if n:
            tokens.add(n)
            tokens.add(n[:6])  # prefix match
        p = _norm(a.publisher)
        if p:
            tokens.add(p)
    return tokens


def _folder_matches(folder_name: str, tokens: Set[str]) -> str:
    fn = _norm(folder_name)
    if len(fn) < 3:
        return "?"  # too short to judge -> treat as ambiguous, keep
    if fn in tokens:
        return folder_name
    # prefix / substring either direction
    for t in tokens:
        if len(t) >= 4 and (t in fn or fn[:6] in t):
            return t
    return ""


def scan_data_folders(min_mb: int = 20) -> List[DataFolder]:
    """List per-app data folders with sizes and whether the app is installed."""
    apps = list_installed()
    tokens = _installed_tokens(apps)
    out: List[DataFolder] = []
    seen: Set[str] = set()
    for root in DATA_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.scandir(root):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name.lower() in SKIP_NAMES:
                    continue
                key = entry.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                size = engine.dir_size(Path(entry.path))
                if size < min_mb * 1024 * 1024:
                    continue
                out.append(DataFolder(
                    name=entry.name,
                    path=entry.path,
                    size=size,
                    matched_app=_folder_matches(entry.name, tokens),
                ))
        except OSError:
            continue
    out.sort(key=lambda d: d.size, reverse=True)
    return out


def leftover_folders(min_mb: int = 20) -> List[DataFolder]:
    """Only the data folders whose app is NOT installed (matched_app == '')."""
    return [d for d in scan_data_folders(min_mb) if d.matched_app == ""]


def remove_data_folder(folder: DataFolder, archive_root: str, dry_run: bool, log) -> int:
    """Archive (backup) then remove an app data folder."""
    return engine.archive_path(Path(folder.path), archive_root, dry_run, log)
