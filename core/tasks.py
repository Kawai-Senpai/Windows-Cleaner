# core/tasks.py
# Every cleanup category is a CleanTask. The GUI lists them, scans them for
# reclaimable size, and runs only the ones the user opts into.
#
# Two kinds of work:
#   - path-based tasks (clear children / remove dirs / glob / regex) -> use engine
#   - command tasks (DISM, hibernation, shadow storage) -> 'requires_admin' + custom run
#   - docker -> delegated to core.docker

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import engine
from . import docker

Logger = Callable[[str], None]


@dataclass
class CleanTask:
    key: str
    label: str
    description: str
    group: str = "Other"                # GUI collapsible group
    requires_admin: bool = False
    risky: bool = False                 # show a warning emphasis in GUI
    default_on: bool = True
    action: str = "prune"               # "prune" = delete, "archive" = move to archive_root then remove
    archive_root: str = "D:\\_CleanerArchive"

    # path-based config
    clear_children: List[str] = field(default_factory=list)   # delete contents, keep folder
    remove_dirs: List[str] = field(default_factory=list)      # delete whole folder
    remove_files: List[str] = field(default_factory=list)     # delete specific files
    glob_in_dir: List[tuple] = field(default_factory=list)    # (dir, glob) pairs
    regex_under: List[tuple] = field(default_factory=list)    # (root, [regex]) pairs
    # (root, [name_globs], [exclude_dir_names]) - recursive file match honouring task.action
    walk_match: List[tuple] = field(default_factory=list)

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
        archiving = self.action == "archive"
        for pat in self.clear_children:
            for p in engine.iter_paths(pat):
                if archiving:
                    # archive each child, keeping the parent folder
                    if p.exists() and p.is_dir():
                        for child in list(p.iterdir()):
                            freed += engine.archive_path(child, self.archive_root, dry_run, log)
                else:
                    freed += engine.delete_children(p, dry_run, log)
        for pat in self.remove_dirs:
            for p in engine.iter_paths(pat):
                if archiving:
                    freed += engine.archive_path(p, self.archive_root, dry_run, log)
                else:
                    freed += engine.remove_path(p, dry_run, log)
        for f in self.remove_files:
            for p in engine.iter_paths(f):
                if archiving:
                    freed += engine.archive_path(p, self.archive_root, dry_run, log)
                else:
                    freed += engine.remove_path(p, dry_run, log)
        for d, pattern in self.glob_in_dir:
            for p in engine.iter_paths(d):
                freed += engine.delete_glob_in_dir(p, pattern, dry_run, log)
        for root, regexes in self.regex_under:
            for p in engine.iter_paths(root):
                freed += engine.delete_files_by_pattern(p, regexes, dry_run, log)
        for root, globs, exclude in self.walk_match:
            for p in engine.iter_paths(root):
                freed += engine.walk_files_action(
                    p, globs, self.action, self.archive_root, dry_run,
                    exclude_dir_names=exclude, log=log,
                )
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


def _python_install_roots():
    """Yield Python install roots to scan for site-packages."""
    import os as _os
    candidates = [
        _os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python"),
        _os.path.expandvars(r"%APPDATA%\Python"),
        r"C:\Python",
    ]
    seen = set()
    for c in candidates:
        if _os.path.isdir(c):
            for entry in _os.scandir(c):
                if entry.is_dir() and entry.path not in seen:
                    seen.add(entry.path)
                    yield entry.path
            # also the dir itself (in case site-packages is directly under)
            yield c


def _iter_tilde_dirs():
    """Yield Path objects for every '~*' folder inside any site-packages."""
    import os as _os
    from pathlib import Path as _Path
    for root in _python_install_roots():
        for base, dirs, _files in _os.walk(root):
            if base.lower().endswith("site-packages"):
                for d in list(dirs):
                    if d.startswith("~"):
                        yield _Path(base) / d
                # don't descend deeper than site-packages children for ~ dirs
        # walk already recurses; fine for our sizes


def _scan_python_tilde_junk() -> int:
    total = 0
    for d in _iter_tilde_dirs():
        total += engine.dir_size(d)
    return total


def _run_python_tilde_junk(log: Logger, dry_run: bool) -> int:
    freed = 0
    for d in _iter_tilde_dirs():
        freed += engine.remove_path(d, dry_run, log)
    return freed


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

    # ----- Python ~* corrupted pip leftovers -----
    # When pip is interrupted mid-uninstall it leaves '~xxx' folders that Python
    # cannot import. Pure junk, always safe. We hunt them under every Python install.
    tasks.append(CleanTask(
        key="python_tilde_junk", label="Python corrupted pip leftovers (~*)",
        description="Finds and removes '~'-prefixed folders in site-packages (failed pip uninstalls). Always safe; they cannot be imported.",
        default_on=True,
        custom_run=_run_python_tilde_junk,
        custom_scan=_scan_python_tilde_junk,
    ))

    # ----- VS Code (caches) -----
    tasks.append(CleanTask(
        key="vscode", label="VS Code caches (~4.5GB)",
        description="Clears WebStorage, CachedExtensionVSIXs, Crashpad, Partitions, Cache/CachedData/GPUCache/logs. Settings, keybindings, snippets, History are kept.",
        default_on=True,
        clear_children=[
            r"%APPDATA%\Code\Cache",
            r"%APPDATA%\Code\CachedData",
            r"%APPDATA%\Code\Code Cache",
            r"%APPDATA%\Code\GPUCache",
            r"%APPDATA%\Code\logs",
            r"%APPDATA%\Code\WebStorage",
            r"%APPDATA%\Code\Crashpad",
            r"%APPDATA%\Code\Partitions",
            r"%APPDATA%\Code\Service Worker\CacheStorage",
            r"%APPDATA%\Code\CachedExtensionVSIXs",
        ],
    ))

    # ----- VS Code workspaceStorage (the big 13.8GB; per-workspace state) -----
    tasks.append(CleanTask(
        key="vscode_workspacestorage", label="VS Code workspaceStorage (~13.8GB)",
        description="Clears per-workspace state (layout, undo history, per-folder extension data). Settings/extensions stay. You lose remembered per-folder undo.",
        default_on=True,
        clear_children=[r"%APPDATA%\Code\User\workspaceStorage"],
    ))

    # ----- vscode-cpptools IntelliSense DB (7.2GB) -----
    tasks.append(CleanTask(
        key="vscode_cpptools", label="VS Code C++ IntelliSense cache (~7GB)",
        description="Clears the C/C++ extension's IntelliSense database. Rebuilds automatically when you open C++ projects.",
        default_on=True,
        clear_children=[r"%LOCALAPPDATA%\Microsoft\vscode-cpptools"],
    ))

    # ----- Other VS Code-based editors (Kiro, Cursor, Windsurf, VSCodium, Insiders) -----
    # Same Electron cache layout; clears caches + workspaceStorage, keeps settings.
    _editor_roots = [
        r"%APPDATA%\Kiro", r"%APPDATA%\Cursor", r"%APPDATA%\Windsurf",
        r"%APPDATA%\VSCodium", r"%APPDATA%\Code - Insiders",
    ]
    _editor_children = []
    for _r in _editor_roots:
        for _sub in ("Cache", "CachedData", "Code Cache", "GPUCache", "logs",
                     "WebStorage", "Crashpad", "CachedExtensionVSIXs",
                     r"User\workspaceStorage"):
            _editor_children.append(_r + "\\" + _sub)
    tasks.append(CleanTask(
        key="other_editors", label="Other code editors (Kiro/Cursor/Windsurf/VSCodium)",
        description="Clears caches & workspaceStorage for other VS Code-based editors if installed. Settings kept.",
        default_on=True,
        clear_children=_editor_children,
    ))

    # ----- Game/engine ASSETS, plugins, downloaded content (OPT-IN, off by default) -----
    # Removing these means re-downloading large assets. NEVER on by default; archive.
    tasks.append(CleanTask(
        key="game_assets", label="Game/engine downloaded assets & plugins (opt-in)",
        description="Archives Epic VaultCache, Quixel/Megascans/Fab libraries, and UE DerivedDataCache. OFF by default - removing means re-downloading large assets. Excludes games you play.",
        default_on=False, risky=True, action="archive",
        remove_dirs=[
            r"C:\Program Files\Epic Games\Launcher\VaultCache",
            r"%USERPROFILE%\Documents\Megascans Library\Downloaded",
            r"%LOCALAPPDATA%\Quixel\Cache",
            r"%LOCALAPPDATA%\UnrealEngine\Common\DerivedDataCache",
        ],
    ))

    # ----- AI model caches (HuggingFace etc.) -----
    tasks.append(CleanTask(
        key="ai_models", label="AI model caches (HuggingFace ~12GB)",
        description="Clears downloaded HuggingFace models/datasets cache. Re-downloads on demand. You asked: no models on C:.",
        default_on=True,
        clear_children=[
            r"%USERPROFILE%\.cache\huggingface",
            r"%USERPROFILE%\.cache\torch",
            r"%LOCALAPPDATA%\huggingface",
        ],
    ))

    # ----- Browser automation caches -----
    tasks.append(CleanTask(
        key="browser_automation", label="Playwright / Puppeteer caches",
        description="Clears ms-playwright browser binaries and puppeteer cache. Re-installed when needed.",
        default_on=True,
        clear_children=[
            r"%LOCALAPPDATA%\ms-playwright",
            r"%USERPROFILE%\.cache\puppeteer",
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

    # ----- AI assistant chat / session history (archive, not delete) -----
    # Preserves skills, config, AND every 'memory' subfolder. Only past chats/sessions/logs.
    tasks.append(CleanTask(
        key="ai_history", label="AI chat & session history (Claude/Codex/Copilot)",
        description="Archives then removes Codex sessions, Claude session .jsonl transcripts, file-history, and Copilot chat history. Keeps skills, config, and ALL memories.",
        default_on=True,
        action="archive",
        # whole-folder history (safe to move entirely)
        remove_dirs=[
            r"%USERPROFILE%\.codex\sessions",
            r"%USERPROFILE%\.codex\archived_sessions",
            r"%USERPROFILE%\.codex\.tmp",
            r"%USERPROFILE%\.claude\file-history",
            r"%USERPROFILE%\.claude\shell-snapshots",
            r"%APPDATA%\Code\User\globalStorage\github.copilot-chat",
            r"%APPDATA%\Code\User\globalStorage\emptyWindowChatSessions",
        ],
        # single files
        remove_files=[
            r"%USERPROFILE%\.codex\logs_2.sqlite",
            r"%USERPROFILE%\.codex\logs_2.sqlite-wal",
            r"%USERPROFILE%\.codex\logs_2.sqlite-shm",
        ],
        # Claude transcripts: archive every *.jsonl under projects\ BUT never descend
        # into 'memory' or 'skills' subfolders.
        walk_match=[
            (r"%USERPROFILE%\.claude\projects", ["*.jsonl"], ["memory", "skills"]),
        ],
    ))

    # ----- App leftovers & extra caches (green-lit 2026-06-28) -----
    tasks.append(CleanTask(
        key="app_leftovers", label="App leftovers & extra caches",
        description="Steam html cache, pyppeteer, TabNine, Chrome/Zoom caches, and updater leftovers (obsidian, dbd, Package Cache, Revo logs). All safe / regenerate.",
        default_on=True,
        clear_children=[
            r"%LOCALAPPDATA%\Steam\htmlcache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Code Cache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\GPUCache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Service Worker\CacheStorage",
            r"%APPDATA%\Zoom\data\Structured",
            r"%APPDATA%\Zoom\logs",
            # nested caches inside kept apps (regenerate)
            r"%APPDATA%\discord\Cache",
            r"%APPDATA%\discord\Code Cache",
            r"%APPDATA%\discord\GPUCache",
            r"%PROGRAMDATA%\Adobe\ARM",
        ],
        remove_dirs=[
            r"%LOCALAPPDATA%\pyppeteer",
            r"%APPDATA%\TabNine",
            r"%LOCALAPPDATA%\obsidian-updater",
            r"%LOCALAPPDATA%\dbdicontoolbox-updater",
            r"%LOCALAPPDATA%\Package Cache",
            r"%PROGRAMDATA%\Package Cache",
            r"%LOCALAPPDATA%\VS Revo Group",
        ],
    ))

    # ----- Game / engine leftover data (archive; removed apps leave config/cache) -----
    tasks.append(CleanTask(
        key="game_engine_leftovers", label="Game/engine leftover data",
        description="Archives leftover UE config/crash data and launcher webcaches from removed games/engines (UnrealEngine, Epic webcache, packaged-game Saved configs).",
        default_on=False, action="archive",
        remove_dirs=[
            r"%LOCALAPPDATA%\UnrealEngine",
            r"%LOCALAPPDATA%\UnrealEngineLauncher",
        ],
        clear_children=[
            r"%LOCALAPPDATA%\EpicGamesLauncher\Saved\webcache",
            r"%LOCALAPPDATA%\EpicGamesLauncher\Saved\webcache_*",
        ],
    ))

    # ----- Build-tool caches (Gradle JDKs/daemon, etc.) -----
    tasks.append(CleanTask(
        key="build_caches", label="Build-tool caches (Gradle JDKs/daemon)",
        description="Clears Gradle-downloaded JDK toolchains and daemon/native caches. Re-downloaded on next build. Big if you've done Android/UE builds.",
        default_on=True,
        remove_dirs=[
            r"%USERPROFILE%\.gradle\jdks",
            r"%USERPROFILE%\.gradle\daemon",
            r"%USERPROFILE%\.gradle\native",
        ],
    ))

    # ----- Temp -----
    tasks.append(CleanTask(
        key="temp", label="User & Windows temp folders",
        description="Clears %TEMP% and C:\\Windows\\Temp. Some in-use files may be skipped.",
        default_on=True,
        clear_children=[r"%TEMP%", r"%WINDIR%\Temp"],
    ))

    # ----- Adobe (broader: all app caches, not just media) -----
    tasks.append(CleanTask(
        key="adobe_full", label="Adobe app caches & logs (broad)",
        description="Clears Creative Cloud caches, CameraRaw cache, peer-app caches and Adobe logs across LocalAppData/Roaming/ProgramData.",
        default_on=True,
        clear_children=[
            r"%LOCALAPPDATA%\Adobe\CameraRaw\Cache",
            r"%LOCALAPPDATA%\Adobe\TypeSupport",
            r"%LOCALAPPDATA%\Adobe\CoreSync\plugins\livetype\.cache",
            r"%LOCALAPPDATA%\Adobe\OOBE",
            r"%APPDATA%\Adobe\Common\Media Cache",
            r"%APPDATA%\Adobe\Common\Media Cache Files",
            r"%APPDATA%\Adobe\Lightroom\Caches",
            r"%PROGRAMDATA%\Adobe\ARMDC\Logs",
            r"%PROGRAMDATA%\Adobe\Setup",
        ],
    ))

    # ----- Browser data (full cache across browsers) -----
    tasks.append(CleanTask(
        key="browsers", label="Browser caches (Chrome, Edge, Brave, Firefox)",
        description="Clears Cache/Code Cache/GPUCache for Chrome, Edge, Brave and Firefox cache2. History/passwords are NOT touched.",
        default_on=True,
        clear_children=[
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Code Cache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\GPUCache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Service Worker\CacheStorage",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Default\Cache",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Default\Code Cache",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Default\GPUCache",
            r"%APPDATA%\Mozilla\Firefox\Profiles\*\cache2",
            r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*\cache2",
        ],
    ))

    # ----- Windows extras (admin): logs, prefetch, font cache, error reports -----
    tasks.append(CleanTask(
        key="windows_extras", label="Windows logs, prefetch & error reports",
        description="Clears CBS/DISM logs, Prefetch, WER archives, and old setup logs. Admin recommended.",
        requires_admin=True, default_on=True,
        clear_children=[
            r"%WINDIR%\Prefetch",
            r"%WINDIR%\Logs\CBS",
            r"%WINDIR%\Logs\DISM",
            r"%PROGRAMDATA%\Microsoft\Windows\WER\ReportArchive",
            r"%PROGRAMDATA%\Microsoft\Windows\WER\Temp",
            r"%WINDIR%\Panther",
        ],
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

    # Assign GUI groups by key (keeps task definitions above uncluttered).
    GROUPS = {
        "docker": "Docker",
        "hibernation": "System (admin)", "dism": "System (admin)",
        "dism_resetbase": "System (admin)", "update_downloads": "System (admin)",
        "delivery_optimization": "System (admin)", "shadow_resize": "System (admin)",
        "delete_shadows": "System (admin)", "crash_dumps": "System (admin)",
        "dev": "Dev & Python", "python_tilde_junk": "Dev & Python",
        "vscode": "Dev & Python", "vscode_workspacestorage": "Dev & Python",
        "vscode_cpptools": "Dev & Python", "browser_automation": "Dev & Python",
        "other_editors": "Dev & Python", "game_assets": "Media",
        "ai_models": "Caches", "nvidia": "Caches", "media": "Caches",
        "ms_caches": "Caches", "adobe_dunamis": "Caches", "games": "Caches",
        "app_leftovers": "Caches", "temp": "Caches", "adobe_full": "Caches",
        "browsers": "Caches", "game_engine_leftovers": "Caches",
        "build_caches": "Dev & Python",
        "windows_extras": "System (admin)",
        "ai_history": "AI history",
        "nle_previews": "Media",
    }
    for t in tasks:
        t.group = GROUPS.get(t.key, "Other")

    return tasks
