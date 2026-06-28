# scan_devils.py
# Reusable "where did my space go" scanner. Run anytime to surface the
# biggest folders and the sneaky cache/leftover pileups across AppData and C:.
#
#   python scripts/scan_devils.py             # default: profile AppData + top C: dirs
#   python scripts/scan_devils.py --min 50    # show anything >= 50 MB
#   python scripts/scan_devils.py --full      # also recurse C:\ top-level
#
# It only REPORTS. Nothing is deleted. Use the GUI (main.py) to clean.

import os
import sys
import argparse
from pathlib import Path

# the app (core/) lives one level up from this scripts/ folder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import engine, scanner

USER = os.path.expanduser("~")

# Folder NAMES that are almost always safe-to-clear caches/leftovers.
CACHE_HINTS = {
    "cache", "caches", "code cache", "gpucache", "shadercache", "grshadercache",
    "logs", "log", "tmp", "temp", "crashpad", "crashdumps", "webcache",
    "htmlcache", "service worker", "cacheddata", "cachedextensionvsixs",
    "blob_storage", "webstorage", "partitions",
}
UPDATER_HINTS = ("-updater", "update", "package cache")


def human(n):
    return engine.human_size(n)


def scan_dir_children(path, min_mb):
    """List immediate subdirectories of path with their sizes."""
    out = []
    if not os.path.isdir(path):
        return out
    try:
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                size = engine.dir_size(Path(entry.path))
                if size >= min_mb * 1024 * 1024:
                    out.append((size, entry.name, entry.path))
    except OSError:
        pass
    out.sort(reverse=True)
    return out


def classify(name):
    low = name.lower()
    if low in CACHE_HINTS:
        return "CACHE"
    if any(h in low for h in UPDATER_HINTS):
        return "UPDATER"
    return ""


def report(title, rows):
    if not rows:
        return
    print(f"\n=== {title} ===")
    for size, name, path in rows:
        tag = classify(name)
        tag = f"[{tag}]" if tag else ""
        print(f"  {human(size):>10}  {tag:9} {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=100, help="min size in MB to show")
    ap.add_argument("--full", action="store_true", help="also recurse C:\\ top-level")
    args = ap.parse_args()

    free, total = scanner.drive_free_space("C:\\")
    print(f"C:  {human(free)} free of {human(total)}")

    for branch in (r"AppData\Local", r"AppData\Roaming", r"AppData\LocalLow", ".cache"):
        report(branch, scan_dir_children(os.path.join(USER, branch), args.min))

    print("\nLegend: [CACHE]/[UPDATER] = usually safe to clear. Others: verify first.")
    print("Clean via:  python main.py   (GUI, scan-then-confirm)")

    if args.full:
        print("\n=== C:\\ big folders (recursive, slow) ===")
        for r in scanner.scan_big_folders("C:\\", min_size_gb=1.0, top=30):
            print(f"  {human(r.size_bytes):>10}  {r.path}")


if __name__ == "__main__":
    main()
