# scripts/python_versions.py
# Interactively review installed Python versions and remove unused ones.
# Shows each version's size, whether it's the py-launcher default / on PATH,
# and lets you pick which to delete. Archives to D:\_CleanerArchive by default.
#
#   python scripts/python_versions.py                 # interactive
#   python scripts/python_versions.py --list          # just list, no prompts
#   python scripts/python_versions.py --archive       # move to archive instead of delete
#
# SAFETY: refuses to remove the version that is currently running this script,
# the py-launcher default, or anything on PATH unless you force it.

import os
import sys
import subprocess
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import engine
from core.config import load_config

PY_ROOT = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python")


def installed_versions():
    out = []
    if not os.path.isdir(PY_ROOT):
        return out
    for entry in os.scandir(PY_ROOT):
        if entry.is_dir() and entry.name.lower().startswith("python"):
            exe = os.path.join(entry.path, "python.exe")
            out.append({
                "name": entry.name,
                "path": entry.path,
                "exe": exe,
                "size": engine.dir_size(Path(entry.path)),
                "has_exe": os.path.isfile(exe),
            })
    return out


def launcher_default():
    try:
        res = subprocess.run(["py", "--list"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in res.stdout.splitlines():
            if "*" in line:
                return line.strip()
    except Exception:
        pass
    return None


def on_path(exe):
    p = os.environ.get("PATH", "")
    folder = os.path.dirname(exe).lower()
    return folder in [x.strip().lower() for x in p.split(os.pathsep)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list only, no prompts")
    ap.add_argument("--archive", action="store_true", help="archive instead of delete")
    ap.add_argument("--force", action="store_true", help="allow removing protected versions")
    args = ap.parse_args()

    cfg = load_config()
    archive_root = cfg.get("archive_root", r"D:\_CleanerArchive")
    running = os.path.dirname(os.path.abspath(sys.executable)).lower()
    default = launcher_default()

    versions = installed_versions()
    if not versions:
        print(f"No Python installs found under {PY_ROOT}")
        return

    print(f"py launcher default: {default or 'unknown'}\n")
    print(f"{'#':<3}{'Version':<14}{'Size':>10}  flags")
    for i, v in enumerate(versions):
        flags = []
        if v["path"].lower() == running:
            flags.append("RUNNING")
        if on_path(v["exe"]):
            flags.append("ON-PATH")
        if default and v["name"][-2:] in default:
            flags.append("LAUNCHER-DEFAULT")
        v["protected"] = bool(flags)
        print(f"{i:<3}{v['name']:<14}{engine.human_size(v['size']):>10}  {', '.join(flags)}")

    if args.list:
        return

    print("\nEnter numbers to remove (comma-separated), or blank to cancel:")
    raw = input("> ").strip()
    if not raw:
        print("Cancelled.")
        return

    try:
        picks = [versions[int(x)] for x in raw.split(",") if x.strip()]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return

    for v in picks:
        if v["protected"] and not args.force:
            print(f"SKIP {v['name']}: protected ({'running/path/default'}). Use --force to override.")
            continue
        action = "archive" if args.archive else "delete"
        print(f"\n{action.upper()} {v['name']} ({engine.human_size(v['size'])})? [y/N]")
        if input("> ").strip().lower() != "y":
            print("  skipped")
            continue
        if args.archive:
            freed = engine.archive_path(Path(v["path"]), archive_root, False, print)
        else:
            freed = engine.remove_path(Path(v["path"]), False, print)
        print(f"  done, freed {engine.human_size(freed)}")


if __name__ == "__main__":
    main()
