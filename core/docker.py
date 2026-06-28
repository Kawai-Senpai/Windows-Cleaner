# core/docker.py
# Docker cleanup. Each prune is exposed separately so the GUI can opt in/out.
# Docker only allows ONE prune at a time, so run() executes them sequentially.

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

Logger = Callable[[str], None]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _run(args: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def daemon_running() -> bool:
    if not docker_available():
        return False
    try:
        res = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
        return res.returncode == 0
    except Exception:
        return False


@dataclass
class DockerUsage:
    images_size: int = 0
    images_reclaimable: int = 0
    containers_size: int = 0
    containers_reclaimable: int = 0
    volumes_size: int = 0
    volumes_reclaimable: int = 0
    build_cache_size: int = 0
    build_cache_reclaimable: int = 0
    raw: str = ""

    @property
    def total_reclaimable(self) -> int:
        return (
            self.images_reclaimable
            + self.containers_reclaimable
            + self.volumes_reclaimable
            + self.build_cache_reclaimable
        )


def get_usage() -> Optional[DockerUsage]:
    """Parse `docker system df` JSON into a DockerUsage. None if unavailable."""
    if not daemon_running():
        return None
    try:
        res = _run(["docker", "system", "df", "--format", "{{json .}}"], timeout=30)
        if res.returncode != 0:
            return None
        usage = DockerUsage(raw=res.stdout)
        for line in res.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = row.get("Type", "")
            size = _parse_size(row.get("Size", "0B"))
            recl = _parse_reclaimable(row.get("Reclaimable", "0B"))
            if typ == "Images":
                usage.images_size, usage.images_reclaimable = size, recl
            elif typ == "Containers":
                usage.containers_size, usage.containers_reclaimable = size, recl
            elif typ == "Local Volumes":
                usage.volumes_size, usage.volumes_reclaimable = size, recl
            elif typ == "Build Cache":
                usage.build_cache_size, usage.build_cache_reclaimable = size, recl
        return usage
    except Exception:
        return None


def _parse_size(s: str) -> int:
    s = (s or "0B").strip()
    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
             "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}
    num = ""
    unit = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        elif not ch.isspace():
            unit += ch
    try:
        val = float(num) if num else 0.0
    except ValueError:
        return 0
    return int(val * units.get(unit.upper(), 1))


def _parse_reclaimable(s: str) -> int:
    # Reclaimable looks like "242GB (97%)" - strip the percentage.
    s = (s or "0B").split("(")[0].strip()
    return _parse_size(s)


# ---------- prune operations ----------
# Each entry: key -> (label, [command args], default_on)
PRUNE_OPS: Dict[str, dict] = {
    "build_cache": {
        "label": "Build cache (all)",
        "args": ["docker", "builder", "prune", "-a", "-f"],
        "default": True,
        "desc": "Removes all cached build layers. Safe; only slows the next build.",
    },
    "stopped_containers": {
        "label": "Stopped containers",
        "args": ["docker", "container", "prune", "-f"],
        "default": True,
        "desc": "Removes containers that are not running. Running ones are untouched.",
    },
    "dangling_images": {
        "label": "Dangling images only",
        "args": ["docker", "image", "prune", "-f"],
        "default": False,
        "desc": "Removes only untagged <none> images. Conservative.",
    },
    "all_unused_images": {
        "label": "ALL unused images",
        "args": ["docker", "image", "prune", "-a", "-f"],
        "default": True,
        "desc": "Removes every image not used by a running container. Big space win; re-pull needed.",
    },
    "networks": {
        "label": "Unused networks",
        "args": ["docker", "network", "prune", "-f"],
        "default": True,
        "desc": "Removes custom networks not used by any container.",
    },
    "volumes": {
        "label": "Unused volumes",
        "args": ["docker", "volume", "prune", "-a", "-f"],
        "default": False,
        "desc": "DELETES volume data not attached to a container. Irreversible unless data is bind-mounted.",
    },
}


def run_prune(selected_keys: List[str], log: Logger, dry_run: bool = False) -> int:
    """Run the selected prune operations sequentially. Returns approx bytes reclaimed."""
    if not daemon_running():
        log("  ! Docker daemon is not running - skipping Docker cleanup.")
        return 0

    before = get_usage()
    before_recl = before.total_reclaimable if before else 0

    # Order matters: containers -> images -> cache -> networks -> volumes.
    order = ["stopped_containers", "dangling_images", "all_unused_images",
             "build_cache", "networks", "volumes"]
    for key in order:
        if key not in selected_keys:
            continue
        op = PRUNE_OPS[key]
        log(f"  -> {op['label']}")
        if dry_run:
            log(f"     would run: {' '.join(op['args'])}")
            continue
        try:
            res = _run(op["args"], timeout=900)
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            for line in (out + ("\n" + err if err else "")).splitlines():
                if line.strip():
                    log(f"     {line.strip()}")
        except subprocess.TimeoutExpired:
            log(f"     ! timed out (still running in background possibly)")
        except Exception as e:
            log(f"     ! failed: {e}")

    if dry_run:
        return before_recl

    after = get_usage()
    after_recl = after.total_reclaimable if after else 0
    freed = max(0, before_recl - after_recl)
    return freed
