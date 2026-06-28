# core/scanner.py
# Port of calcspace.ps1 - find the biggest folders on a drive so you know
# where space is going before deciding what to clean.

import os
import ctypes
from dataclasses import dataclass
from typing import Callable, List, Optional

from .engine import _is_reparse


@dataclass
class FolderResult:
    path: str
    size_bytes: int


def scan_big_folders(
    root: str = "C:\\",
    min_size_gb: float = 1.0,
    top: int = 50,
    progress: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[FolderResult]:
    """Recursively size every folder under root, return the largest ones.

    progress(path) is called occasionally so a GUI can show activity.
    should_stop() lets a GUI cancel a long scan.
    """
    results: List[FolderResult] = []
    min_bytes = int(min_size_gb * (1024 ** 3))
    counter = {"dirs": 0}

    def recurse(path: str) -> int:
        if should_stop and should_stop():
            return 0
        total = 0
        # files directly in this dir
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except (PermissionError, OSError):
            return 0

        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    try:
                        total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                elif entry.is_dir(follow_symlinks=False):
                    if _is_reparse(entry.path):
                        continue
                    total += recurse(entry.path)
            except OSError:
                continue

        counter["dirs"] += 1
        if progress and counter["dirs"] % 200 == 0:
            progress(path)
        if total >= min_bytes:
            results.append(FolderResult(path=path, size_bytes=total))
        return total

    recurse(os.path.abspath(root))
    results.sort(key=lambda r: r.size_bytes, reverse=True)
    return results[:top]


def drive_free_space(drive: str = "C:\\") -> tuple[int, int]:
    """Return (free_bytes, total_bytes) for a drive."""
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(drive), None, ctypes.byref(total), ctypes.byref(free)
    )
    return free.value, total.value
