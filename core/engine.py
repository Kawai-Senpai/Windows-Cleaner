# core/engine.py
# Low-level filesystem helpers: expand paths, measure sizes, delete safely.
# All deletion goes through here so dry-run and logging are centralized.

import os
import re
import sys
import stat
import glob
import shutil
import ctypes
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List

# A logger is a function that takes a single string line.
Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ---------- platform / admin ----------

def is_windows() -> bool:
    return os.name == "nt"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Relaunch the current process elevated. Returns True if a relaunch was triggered."""
    try:
        params = " ".join(f'"{a}"' for a in sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return True
    except Exception:
        return False


# ---------- path expansion ----------

def expand(path_str: str) -> str:
    return os.path.expandvars(path_str)


def iter_paths(pattern: str) -> Iterable[Path]:
    """Expand env vars + globs, yielding Path objects (existing or not)."""
    expanded = expand(pattern)
    if any(ch in expanded for ch in "*?[]"):
        for hit in glob.glob(expanded):
            yield Path(hit)
    else:
        yield Path(expanded)


# ---------- sizing ----------

def dir_size(path: Path) -> int:
    """Total bytes under a directory, skipping reparse points (junctions/symlinks)."""
    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    for base, dirs, files in os.walk(path, topdown=True):
        # prune reparse points to avoid double counting / loops
        pruned = []
        for d in dirs:
            full = os.path.join(base, d)
            try:
                if os.path.isdir(full) and not _is_reparse(full):
                    pruned.append(d)
            except OSError:
                pass
        dirs[:] = pruned
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def _is_reparse(path: str) -> bool:
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return False
        return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except Exception:
        return False


def children_size(path: Path) -> int:
    """Size of the contents of a directory (used by 'clear children' tasks)."""
    if not path.exists() or not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.iterdir():
            if item.is_dir():
                total += dir_size(item)
            else:
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def paths_total_size(patterns: List[str], children_only: bool = True) -> int:
    """Sum reclaimable bytes across a list of glob/env patterns."""
    total = 0
    for pat in patterns:
        for p in iter_paths(pat):
            total += children_size(p) if children_only else dir_size(p)
    return total


# ---------- deletion ----------

def _make_writable(p: Path) -> None:
    try:
        if p.exists():
            os.chmod(str(p), stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except Exception:
        pass


def _on_rm_error(func, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def delete_children(dir_path: Path, dry_run: bool, log: Logger = _noop) -> int:
    """Delete the contents of a directory but keep the directory itself.
    Returns bytes freed (estimated as size before deletion of removed items)."""
    if not dir_path.exists():
        log(f"  skip (not found): {dir_path}")
        return 0
    if not dir_path.is_dir():
        log(f"  skip (not a dir): {dir_path}")
        return 0

    freed = 0
    for item in list(dir_path.iterdir()):
        size = dir_size(item) if item.is_dir() else _safe_file_size(item)
        if dry_run:
            log(f"  would remove: {item}")
            freed += size
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, onerror=_on_rm_error)
            else:
                _make_writable(item)
                item.unlink(missing_ok=True)
            freed += size
        except Exception as e:
            log(f"  ! could not remove {item}: {e}")
    return freed


def remove_path(target: Path, dry_run: bool, log: Logger = _noop) -> int:
    """Delete a file or an entire directory. Returns bytes freed."""
    if not target.exists():
        log(f"  skip (not found): {target}")
        return 0
    size = dir_size(target) if target.is_dir() else _safe_file_size(target)
    if dry_run:
        log(f"  would remove: {target}")
        return size
    try:
        if target.is_dir():
            shutil.rmtree(target, onerror=_on_rm_error)
        else:
            _make_writable(target)
            target.unlink(missing_ok=True)
        return size
    except Exception as e:
        log(f"  ! could not remove {target}: {e}")
        return 0


def archive_path(target: Path, archive_root: str, dry_run: bool, log: Logger = _noop) -> int:
    r"""Move a file/folder into a dated archive folder instead of deleting it.

    Layout: <archive_root>\<YYYY-MM-DD>\<drive-stripped relative path>
    Returns bytes moved (so the GUI can report space freed from the source drive).
    """
    if not target.exists():
        log(f"  skip (not found): {target}")
        return 0
    size = dir_size(target) if target.is_dir() else _safe_file_size(target)

    date_dir = datetime.now().strftime("%Y-%m-%d")
    # strip drive colon so "C:\Users\x" -> "C\Users\x" under the archive
    rel = str(target)
    if len(rel) >= 2 and rel[1] == ":":
        rel = rel[0] + rel[2:]
    dest = Path(archive_root) / date_dir / rel
    if dry_run:
        log(f"  would archive: {target}  ->  {dest}")
        return size
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # if dest already exists (re-run), suffix with a counter
        final = dest
        n = 1
        while final.exists():
            final = dest.with_name(dest.name + f"_{n}")
            n += 1
        shutil.move(str(target), str(final))
        log(f"  archived: {target}  ->  {final}")
        return size
    except Exception as e:
        log(f"  ! could not archive {target}: {e}")
        return 0


def walk_files_action(
    root: Path,
    name_globs: List[str],
    action: str,
    archive_root: str,
    dry_run: bool,
    exclude_dir_names: List[str] = None,
    log: Logger = _noop,
) -> int:
    """Walk a tree, match files by fnmatch glob(s), and prune or archive each.

    exclude_dir_names: directory names to skip entirely (e.g. ['memory', 'skills'])
    so precious folders are never descended into. action is 'prune' or 'archive'.
    """
    import fnmatch
    if not root.exists():
        log(f"  skip (not found): {root}")
        return 0
    exclude = {e.lower() for e in (exclude_dir_names or [])}
    freed = 0
    for base, dirs, files in os.walk(root):
        # prune excluded dirs in-place so os.walk does not descend into them
        dirs[:] = [d for d in dirs if d.lower() not in exclude]
        for f in files:
            if any(fnmatch.fnmatch(f, g) for g in name_globs):
                p = Path(base) / f
                if action == "archive":
                    freed += archive_path(p, archive_root, dry_run, log)
                else:
                    size = _safe_file_size(p)
                    if dry_run:
                        log(f"  would delete: {p}")
                        freed += size
                    else:
                        try:
                            _make_writable(p)
                            p.unlink(missing_ok=True)
                            freed += size
                        except Exception as e:
                            log(f"  ! could not delete {p}: {e}")
    return freed


def delete_files_by_pattern(root: Path, regex_list: List[str], dry_run: bool, log: Logger = _noop) -> int:
    """Walk a tree and delete files whose name fully matches any regex."""
    if not root.exists():
        log(f"  skip (not found): {root}")
        return 0
    compiled = [re.compile(p, re.IGNORECASE) for p in regex_list]
    freed = 0
    for base, _dirs, files in os.walk(root):
        for f in files:
            if any(rx.fullmatch(f) for rx in compiled):
                p = Path(base) / f
                size = _safe_file_size(p)
                if dry_run:
                    log(f"  would delete: {p}")
                    freed += size
                else:
                    try:
                        _make_writable(p)
                        p.unlink(missing_ok=True)
                        freed += size
                    except Exception as e:
                        log(f"  ! could not delete {p}: {e}")
    return freed


def delete_glob_in_dir(dir_path: Path, pattern: str, dry_run: bool, log: Logger = _noop) -> int:
    """Delete only files matching a glob inside a single directory (e.g. thumbcache_*.db)."""
    if not dir_path.exists():
        return 0
    freed = 0
    for f in dir_path.glob(pattern):
        size = _safe_file_size(f)
        if dry_run:
            log(f"  would delete: {f}")
            freed += size
        else:
            try:
                _make_writable(f)
                f.unlink(missing_ok=True)
                freed += size
            except Exception as e:
                log(f"  ! could not delete {f}: {e}")
    return freed


def _safe_file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


# ---------- shell commands ----------

def run_cmd(cmd: str, log: Logger = _noop) -> bool:
    """Run a shell command, streaming output to the logger. Returns True on success."""
    log(f"  $ {cmd}")
    try:
        res = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if out:
            for line in out.splitlines():
                log(f"    {line}")
        if err:
            for line in err.splitlines():
                log(f"    {line}")
        return res.returncode == 0
    except Exception as e:
        log(f"  ! command failed: {e}")
        return False


# ---------- formatting ----------

def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} PB"
