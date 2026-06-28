<div align="center">

# 🧹 Windows Cleaner

**A configurable Windows disk-cleanup app with a dark, modern GUI.**

Finds the real space hogs - Docker layers, NVIDIA caches, AI chat history,
stale AppData - and lets you reclaim them on your terms. Scan first, opt in per
category, then clean. Risky data is archived, never silently deleted.

<br>

![Platform](https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-ttkbootstrap-5A189A?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-22c55e?style=flat-square)

</div>

---

## ✨ Why this exists

Built-in Windows cleanup is scattered across a dozen dialogs and misses the real
space hogs: Docker layers, NVIDIA shader caches, AI chat history, NLE preview
files, dev package managers, and gigabytes of stale app data buried in `AppData`.

Windows Cleaner puts all of it in **one panel**, shows you what each category
will reclaim **before** touching anything, and archives risky data instead of
nuking it.

> [!IMPORTANT]
> Nothing is deleted until you tick a category and confirm. A dry-run toggle lets
> you preview every action with zero risk.

---

## 🚀 Quick start

```powershell
pip install ttkbootstrap
python main.py
```

| Action | What it does |
| :-- | :-- |
| **Scan** | Estimates reclaimable space per category. No deletion. |
| **Clean selected** | Cleans ticked categories after a confirmation dialog. |
| **Find saves** | Lists game-save & config folders so you can archive them. |
| **Big folders...** | Recursive size scan of `C:\` to find where space went. |
| **Restart as Admin** | Relaunches elevated so system tasks become available. |
| **Preview only** *(toggle)* | Dry-run. Logs what it would delete, deletes nothing. |

> [!NOTE]
> The app opens **without** admin. System tasks (DISM, Windows Update cache,
> restore points) need elevation - use **Restart as Admin**, or launch
> `python main.py --admin`.

---

## 🛡️ Archive vs delete

Categories with `action="archive"` (for example **AI chat & session history**)
**move** data into a dated archive folder instead of deleting it, so everything
stays recoverable.

```
D:\_CleanerArchive\<date>\...
```

Change the destination via `archive_root` in `config.json`.

> [!TIP]
> The AI-history task preserves your skills, config, and **all** `memory` folders.
> It only archives past chats, sessions, and logs (Claude `.jsonl`, Codex
> sessions, Copilot chat).

---

## 🔎 Find pileups quickly

A report-only scanner for the usual cache offenders:

```powershell
python scripts/scan_devils.py            # AppData / .cache folders >= 100 MB
python scripts/scan_devils.py --min 50   # lower the threshold
python scripts/scan_devils.py --full     # also recurse C:\ (slow)
```

`[CACHE]` and `[UPDATER]` tags flag items that are usually safe to remove.

---

## 🗂️ Categories

<table>
<tr>
<td valign="top" width="50%">

**🐳 Docker**
Build cache, stopped containers, dangling / unused images, networks, and
(off by default) volumes. Each is individually toggleable.

**⚙️ Core system**
Hibernation file, DISM component cleanup (+ resetbase), Windows Update cache,
Delivery Optimization, restore-point cap / delete, crash dumps.

</td>
<td valign="top" width="50%">

**💾 Caches**
Adobe & DaVinci media, NVIDIA shaders, dev package managers, VS Code, Steam,
Microsoft (Edge / Teams / OneDrive / Office), Adobe Dunamis, temp folders.

**🎬 NLE previews**
Editor preview files matched by regex (off by default).

</td>
</tr>
</table>

> [!WARNING]
> Items marked **risky** (DISM resetbase, delete all restore points, Docker
> volumes, NLE previews) are **irreversible**. Read the description before
> enabling them.

---

## 🧩 Configuration

Settings persist to `config.json` next to `main.py`. Edit it directly or let the
GUI write it for you. Delete the file to reset to defaults.

---

## 📁 Project structure

```
main.py          Entry point
config.json      Persisted user choices (auto-created)
core/
  engine.py      Path expansion, sizing, safe deletion, shell commands
  scanner.py     Recursive big-folder scan
  docker.py      docker df parsing + prune operations
  tasks.py       Every cleanup category as data (scan + run)
  config.py      Load / save config.json
  savedgames.py  Game-save discovery & archival
gui/
  app.py         ttkbootstrap GUI
scripts/
  scan_devils.py Report-only cache pileup scanner
```

---

## 🤖 Operating guide for AI agents

This section is a precise, self-contained spec so an autonomous agent can drive
the tool without guessing. The app is **GUI-first** (no headless clean command),
so an agent operates it by editing `config.json` and reasoning over the scanner
output, then either invoking the GUI for the human or asking the human to click
**Clean selected**.

### Mental model

1. **`config.json` is the source of truth.** It records which task keys are
   enabled, which Docker ops are enabled, the dry-run flag, scanner settings, and
   the archive destination. The GUI reads it on launch and writes it on change.
2. **Cleaning happens only in the GUI** (`gui/app.py`), triggered by **Clean
   selected**. There is no `python main.py --clean` flag - do not invent one.
3. **Scanning is safe and read-only.** `scripts/scan_devils.py` and the GUI
   **Scan** button never delete anything.
4. **Archive tasks move, delete tasks remove.** A task's `action` is either
   `"archive"` (moved to `archive_root\<date>\...`, recoverable) or a deletion.

### Entry points

| Command | Effect | Side effects |
| :-- | :-- | :-- |
| `python main.py` | Launch GUI, non-admin | None until user clicks |
| `python main.py --admin` | Relaunch elevated, then GUI | UAC prompt |
| `python scripts/scan_devils.py` | Report AppData/.cache folders >= 100 MB | **None** (read-only) |
| `python scripts/scan_devils.py --min 50` | Same, threshold 50 MB | None |
| `python scripts/scan_devils.py --full` | Also recurse `C:\` top-level (slow) | None |

> [!CAUTION]
> An agent must **never** delete files directly to "help." Route all removal
> through the app so archiving, admin gating, and confirmations apply. Editing
> `config.json` is the correct way to change *what* will be cleaned.

### Recommended agent workflow

```text
1. Run:  python scripts/scan_devils.py --min 100
2. Parse the report. [CACHE]/[UPDATER] tags = usually safe; others = verify.
3. Decide which task keys to enable (see table below).
4. Read config.json, set the chosen keys in "enabled_tasks" to true/false,
   set "dry_run": true for a first pass. Write config.json back (UTF-8, indent 2).
5. Tell the human to launch `python main.py`, click Scan, review, then
   Clean selected. (Risky tasks still pop a confirmation dialog.)
6. After verifying the dry-run log, set "dry_run": false and repeat for real.
```

### `config.json` schema

```jsonc
{
  "dry_run": false,                  // true = log only, delete nothing
  "enabled_tasks": { "<task_key>": bool, ... },
  "docker_ops": {
    "build_cache": true,
    "stopped_containers": true,
    "dangling_images": false,
    "all_unused_images": true,
    "networks": true,
    "volumes": false                 // destructive; off by default
  },
  "scanner": { "root": "C:\\", "min_size_gb": 1.0, "top": 50 },
  "archive_root": "D:\\_CleanerArchive",
  "custom_targets": [                 // user-triaged entries from deep scans
    { "path": "...", "action": "prune|archive|ignore", "note": "..." }
  ]
}
```

Unknown keys are preserved and merged shallowly on load, so adding
`custom_targets` entries is safe across updates.

### Task keys reference

Set these inside `enabled_tasks`. **Bold = risky / irreversible**, leave off
unless explicitly requested. Sizes are typical observed values, not guarantees.

| Key | What it cleans | Default |
| :-- | :-- | :-- |
| `docker` | Docker build cache, containers, images, networks (see `docker_ops`) | on |
| `hibernation` | Disable hibernation, remove `hiberfil.sys` (admin) | off |
| `dism` | Windows component cleanup via DISM (admin) | on |
| **`dism_resetbase`** | DISM `/resetbase` - blocks update rollback (admin) | off |
| `update_downloads` | Windows Update download cache (admin) | on |
| `delivery_optimization` | Delivery Optimization cache (admin) | on |
| `shadow_resize` | Cap restore-point storage at 3 GB (admin) | off |
| **`delete_shadows`** | Delete ALL restore points (admin) | off |
| `crash_dumps` | Crash dumps & error reports | on |
| `media` | Adobe & DaVinci media caches | on |
| `nvidia` | NVIDIA shader & NGX caches | on |
| `dev` | npm / pip / gradle / maven package caches | on |
| `python_tilde_junk` | Corrupted pip leftovers (`~*` dirs) | on |
| `vscode` | VS Code caches (~4.5 GB) | on |
| `vscode_workspacestorage` | VS Code workspaceStorage (~13.8 GB) | on |
| `vscode_cpptools` | VS Code C++ IntelliSense cache (~7 GB) | on |
| `other_editors` | Kiro / Cursor / Windsurf / VSCodium caches | on |
| `game_assets` | Epic/Quixel/Fab libraries, UE DDC (archive) | off |
| `ai_models` | HuggingFace model caches (~12 GB) | on |
| `browser_automation` | Playwright / Puppeteer caches | on |
| `games` | Steam downloading & shader cache | on |
| `ms_caches` | Edge / Teams / OneDrive / Office caches | on |
| `adobe_dunamis` | Adobe Dunamis logs | on |
| `ai_history` | AI chat/session history, Claude/Codex/Copilot (archive) | on |
| `app_leftovers` | App leftovers & extra caches | on |
| `game_engine_leftovers` | Game/engine leftover data | off |
| `build_caches` | Gradle JDKs/daemon build caches | on |
| `temp` | User & Windows temp folders | on |
| `adobe_full` | Adobe app caches & logs (broad) | on |
| `browsers` | Chrome / Edge / Brave / Firefox caches | on |
| `windows_extras` | Windows logs, prefetch, error reports | on |
| **`nle_previews`** | NLE preview files (`.cfa`, `.pek`, regex) | off |

> [!NOTE]
> `ai_history` **archives** rather than deletes, and preserves all `memory`
> folders, skills, and config - it only moves past chats/sessions/logs.

### Safety contract

- A **dry-run pass first** (`"dry_run": true`) is strongly recommended before any
  real clean. The log shows exactly what would be removed.
- Tasks needing elevation are **skipped silently** when not admin (logged as
  `needs admin - skipped`). Use `--admin` to enable them.
- Risky tasks always trigger a GUI confirmation dialog regardless of config.
- To fully reset, delete `config.json`; defaults are rebuilt from `tasks.py`.

---

<div align="center">

Made for reclaiming gigabytes without the anxiety.

</div>
