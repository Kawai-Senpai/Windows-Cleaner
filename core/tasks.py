# core/tasks.py
# Every cleanup category is a CleanTask. The GUI lists them, scans them for
# reclaimable size, and runs only the ones the user opts into.
#
# Two kinds of work:
#   - path-based tasks (clear children / remove dirs / glob / regex) -> use engine
#   - command tasks (DISM, hibernation, shadow storage) -> 'requires_admin' + custom run
#   - docker -> delegated to core.docker

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import engine
from . import docker

Logger = Callable[[str], None]


@dataclass
class CleanTask:
    key: str
    label: str
    description: str
    requires_admin: bool = False
    risky: bool = False                 # show a warning emphasis in GUI
    default_on: bool = True

    # path-based config
    clear_children: List[str] = field(default_factory=list)   # delete contents, keep folder
    remove_dirs: List[str] = field(default_factory=list)      # delete whole folder
    remove_files: List[str] = field(default_factory=list)     # delete specific files
    glob_in_dir: List[tuple] = field(default_factory=list)    # (dir, glob) pairs
    regex_under: List[tuple] = field(default_factory=list)    # (root, [regex]) pairs

    # for command-style tasks
    custom_scan: Optional[Callable[[], int]] = None
    custom_run: Optional[Callable[[Logger, bool], int]] = None

    def scan(self) -> int:
        """Estimate reclaimable bytes without deleting anything."""
        if self.custom_scan is not None:
            try:
                return self.custom_scan()
            except Exception:
                return 0
        total = 0
        total += engine.paths_total_size(self.clear_children, children_only=True)
        total += engine.paths_total_size(self.remove_dirs, children_only=False)
        for f in self.remove_files:
            for p in engine.iter_paths(f):
                if p.exists() and p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        for d, pattern in self.glob_in_dir:
            for p in engine.iter_paths(d):
                for hit in p.glob(pattern) if p.exists() else []:
                    try:
                        total += hit.stat().st_size
                    except OSError:
                        pass
        # regex tasks are expensive to pre-scan; skip estimate (return 0 for those)
        return total

    def run(self, log: Logger, dry_run: bool) -> int:
        """Execute the cleanup. Returns bytes freed."""
        if self.custom_run is not None:
            return self.custom_run(log, dry_run)
        freed = 0
        for pat in self.clear_children:
            for p in engine.iter_paths(pat):
                freed += engine.delete_children(p, dry_run, log)
        for pat in self.remove_dirs:
            for p in engine.iter_paths(pat):
                freed += engine.remove_path(p, dry_run, log)
        for f in self.remove_files:
            for p in engine.iter_paths(f):
                freed += engine.remove_path(p, dry_run, log)
        for d, pattern in self.glob_in_dir:
            for p in engine.iter_paths(d):
                freed += engine.delete_glob_in_dir(p, pattern, dry_run, log)
        for root, regexes in self.regex_under:
            for p in engine.iter_paths(root):
                freed += engine.delete_files_by_pattern(p, regexes, dry_run, log)
        return freed


# ---------- command-style task implementations ----------

def _run_hibernation(log: Logger, dry_run: bool) -> int:
    if dry_run:
        log("  would run: powercfg -h off")
        return 0
    engine.run_cmd("powercfg -h off", log)
    return 0  # frees hiberfil.sys but size is hard to attribute precisely


def _run_dism(reset_base: bool):
    def inner(log: Logger, dry_run: bool) -> int:
        if dry_run:
            log("  would run: DISM /online /cleanup-image /startcomponentcleanup"
                + (" /resetbase" if reset_base else ""))
            return 0
        engine.run_cmd("dism /online /cleanup-image /startcomponentcleanup", log)
        if reset_base:
            engine.run_cmd("dism /online /cleanup-image /startcomponentcleanup /resetbase", log)
        return 0
    return inner


def _run_update_downloads(log: Logger, dry_run: bool) -> int:
    paths = [r"%WINDIR%\SoftwareDistribution\Download"]
    if dry_run:
        for pat in paths:
            for p in engine.iter_paths(pat):
                log(f"  would clear: {p}")
        return engine.paths_total_size(paths, True)
    engine.run_cmd("net stop wuauserv", log)
    engine.run_cmd("net stop bits", log)
    freed = 0
    for pat in paths:
        for p in engine.iter_paths(pat):
            freed += engine.delete_children(p, dry_run, log)
    engine.run_cmd("net start bits", log)
    engine.run_cmd("net start wuauserv", log)
    return freed


def _run_shadow_resize(max_gb: int):
    def inner(log: Logger, dry_run: bool) -> int:
        if dry_run:
            log(f"  would set shadow storage max to {max_gb}GB on C:")
            return 0
        engine.run_cmd(
            f'vssadmin resize shadowstorage /for=C: /on=C: /maxsize={max_gb}GB', log
        )
        return 0
    return inner


def _run_delete_shadows(log: Logger, dry_run: bool) -> int:
    if dry_run:
        log("  would run: vssadmin delete shadows /all /quiet")
        return 0
    engine.run_cmd("vssadmin delete shadows /all /quiet", log)
    return 0


# ---------- task registry ----------

def build_tasks(docker_keys: Optional[List[str]] = None) -> List[CleanTask]:
    """Return the full ordered list of cleanup tasks."""
    tasks: List[CleanTask] = []

    # ----- Docker (first, as requested) -----
    def docker_scan() -> int:
        u = docker.get_usage()
        return u.total_reclaimable if u else 0

    def docker_run_factory(keys: List[str]):
        def inner(log: Logger, dry_run: bool) -> int:
            return docker.run_prune(keys, log, dry_run)
        return inner

    tasks.append(CleanTask(
        key="docker",
        label="Docker (images, cache, containers)",
        description="Prune Docker build cache, unused images, stopped containers, networks (and optionally volumes).",
        requires_admin=False,
        default_on=True,
        custom_scan=docker_scan,
        custom_run=docker_run_factory(docker_keys or [
            k for k, v in docker.PRUNE_OPS.items() if v["default"]
        ]),
    ))

    # ----- Core system (admin) -----
    tasks.append(CleanTask(
        key="hibernation", label="Disable hibernation (removes hiberfil.sys)",
        description="Turns off hibernation and deletes hiberfil.sys (can be several GB).",
        requires_admin=True, default_on=False,
        custom_run=_run_hibernation,
    ))
    tasks.append(CleanTask(
        key="dism", label="Windows component cleanup (DISM)",
        description="Removes superseded Windows update components from WinSxS.",
        requires_admin=True, default_on=True,
        custom_run=_run_dism(reset_base=False),
    ))
    tasks.append(CleanTask(
        key="dism_resetbase", label="DISM /resetbase (aggressive)",
        description="Makes current components the new base. You cannot uninstall past updates after this.",
        requires_admin=True, risky=True, default_on=False,
        custom_run=_run_dism(reset_base=True),
    ))
    tasks.append(CleanTask(
        key="update_downloads", label="Windows Update download cache",
        description="Stops update services, clears SoftwareDistribution\\Download, restarts them.",
        requires_admin=True, default_on=True,
        custom_run=_run_update_downloads,
    ))
    tasks.append(CleanTask(
        key="delivery_optimization", label="Delivery Optimization cache",
        description="Clears the peer-to-peer Windows update delivery cache.",
        requires_admin=True, default_on=True,
        clear_children=[
            r"%WINDIR%\SoftwareDistribution\DeliveryOptimization\Download",
            r"%WINDIR%\SoftwareDistribution\DeliveryOptimization\Cache",
        ],
    ))
    tasks.append(CleanTask(
        key="shadow_resize", label="Cap restore-point storage at 3GB",
        description="Limits shadow copy storage on C: to 3GB (frees excess restore points).",
        requires_admin=True, default_on=False,
        custom_run=_run_shadow_resize(3),
    ))
    tasks.append(CleanTask(
        key="delete_shadows", label="Delete ALL restore points (aggressive)",
        description="Removes every shadow copy / system restore point on C:.",
        requires_admin=True, risky=True, default_on=False,
        custom_run=_run_delete_shadows,
    ))
    tasks.append(CleanTask(
        key="crash_dumps", label="Crash dumps & error reports",
        description="Deletes MEMORY.DMP, minidumps, and the WER report queue.",
        requires_admin=True, default_on=True,
        remove_files=[r"C:\Windows\MEMORY.DMP"],
        clear_children=[
            r"C:\Windows\Minidump",
            r"C:\ProgramData\Microsoft\Windows\WER\ReportQueue",
        ],
    ))

    # ----- Media -----
    tasks.append(CleanTask(
        key="media", label="Adobe & DaVinci media caches",
        description="Clears Adobe Media Cache, After Effects disk cache, DaVinci proxy/cache.",
        default_on=True,
        clear_children=[
            r"%APPDATA%\Adobe\Common\Media Cache",
            r"%APPDATA%\Adobe\Common\Media Cache Files",
            r"%LOCALAPPDATA%\Adobe\After Effects\*\Disk Cache",
            r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\CacheClip",
            r"%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Proxy",
        ],
    ))

    # ----- NVIDIA -----
    tasks.append(CleanTask(
        key="nvidia", label="NVIDIA shader & NGX caches",
        description="Clears NV_Cache, DXCache, GLCache and removes NGX model cache.",
        default_on=True,
        remove_dirs=[r"C:\ProgramData\NVIDIA\NGX\models"],
        clear_children=[
            r"C:\ProgramData\NVIDIA Corporation\NV_Cache",
            r"%LOCALAPPDATA%\NVIDIA\DXCache",
            r"%LOCALAPPDATA%\NVIDIA\GLCache",
        ],
    ))

    # ----- Dev caches -----
    tasks.append(CleanTask(
        key="dev", label="Dev package caches (npm, pip, gradle, maven...)",
        description="Clears npm/yarn/pnpm/pip/Poetry/NuGet/Gradle/Maven caches. They re-download on demand.",
        default_on=True,
        clear_children=[
            r"%APPDATA%\npm-cache",
            r"%LOCALAPPDATA%\npm-cache",
            r"%LOCALAPPDATA%\Yarn\Cache",
            r"%LOCALAPPDATA%\pnpm\store\v3",
            r"%LOCALAPPDATA%\pip\Cache",
            r"%APPDATA%\pypoetry\Cache",
            r"%USERPROFILE%\.cache\pypoetry",
            r"%USERPROFILE%\.nuget\packages",
            r"%LOCALAPPDATA%\NuGet\v3-cache",
            r"%USERPROFILE%\.gradle\caches",
            r"%USERPROFILE%\.gradle\wrapper\dists",
            r"%USERPROFILE%\.m2\repository",
        ],
    ))

    # ----- VS Code -----
    tasks.append(CleanTask(
        key="vscode", label="VS Code caches",
        description="Clears VS Code Cache/CachedData/GPUCache/logs/workspaceStorage.",
        default_on=True,
        clear_children=[
            r"%APPDATA%\Code\Cache",
            r"%APPDATA%\Code\CachedData",
            r"%APPDATA%\Code\Code Cache",
            r"%APPDATA%\Code\GPUCache",
            r"%APPDATA%\Code\logs",
            r"%APPDATA%\Code\Service Worker\CacheStorage",
            r"%APPDATA%\Code\CachedExtensionVSIXs",
        ],
    ))

    # ----- Games -----
    tasks.append(CleanTask(
        key="games", label="Steam downloading & shader cache",
        description="Clears Steam's interrupted downloads and shader cache.",
        default_on=True,
        clear_children=[
            r"C:\Program Files (x86)\Steam\steamapps\downloading",
            r"C:\Program Files (x86)\Steam\steamapps\shadercache",
            r"C:\Program Files\Steam\steamapps\downloading",
            r"C:\Program Files\Steam\steamapps\shadercache",
        ],
    ))

    # ----- Microsoft caches -----
    tasks.append(CleanTask(
        key="ms_caches", label="Edge / Teams / OneDrive / Office caches",
        description="Clears browser & app caches and Office file cache. Thumbnails handled separately.",
        default_on=True,
        clear_children=[
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache",
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Code Cache",
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\GPUCache",
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Service Worker\CacheStorage",
            r"%LOCALAPPDATA%\Microsoft\Teams\Cache",
            r"%LOCALAPPDATA%\Microsoft\Teams\Code Cache",
            r"%LOCALAPPDATA%\Microsoft\Teams\GPUCache",
            r"%LOCALAPPDATA%\Microsoft\Teams\tmp",
            r"%LOCALAPPDATA%\Microsoft\OneDrive\logs",
            r"%LOCALAPPDATA%\Microsoft\Office\16.0\OfficeFileCache",
        ],
        glob_in_dir=[(r"%LOCALAPPDATA%\Microsoft\Windows\Explorer", "thumbcache_*.db")],
    ))

    # ----- Adobe dunamis -----
    tasks.append(CleanTask(
        key="adobe_dunamis", label="Adobe Dunamis logs",
        description="Removes Adobe's com.adobe.dunamis log folders.",
        default_on=True,
        remove_dirs=[
            r"%APPDATA%\com.adobe.dunamis",
            r"%LOCALAPPDATA%\Temp\Adobe\com.adobe.dunamis",
        ],
    ))

    # ----- Temp -----
    tasks.append(CleanTask(
        key="temp", label="User & Windows temp folders",
        description="Clears %TEMP% and C:\\Windows\\Temp. Some in-use files may be skipped.",
        default_on=True,
        clear_children=[r"%TEMP%", r"%WINDIR%\Temp"],
    ))

    # ----- NLE previews (off by default, regex) -----
    tasks.append(CleanTask(
        key="nle_previews", label="NLE preview files (.cfa .pek ...)",
        description="Scans your user profile for video editor preview/index files. Slow scan; off by default.",
        risky=True, default_on=False,
        regex_under=[(r"%USERPROFILE%", [
            r".*\.cfa", r".*\.pek", r".*\.ims", r".*\.mcdb", r".*\.idlk", r".*\.prv",
            r".*Preview.*\.mpeg", r".*\.mpgindex", r".*\.prmdc", r".*\.wavpk",
        ])],
    ))

    return tasks
