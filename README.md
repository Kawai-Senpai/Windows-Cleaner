# Windows Cleaner

A configurable Windows disk-cleanup app with a dark Tkinter GUI. Scan first, opt
in per category, then clean. Docker cleanup is built in.

## Install

```powershell
pip install ttkbootstrap
```

## Run

```powershell
python main.py
```

- The app opens without admin. System tasks (DISM, Windows Update cache, restore
  points) need admin — use the **Restart as Admin** button, or launch
  `python main.py --admin`.
- Click **Scan** to estimate reclaimable space per category (no deletion).
- Tick categories, then **Clean selected**. A confirmation dialog lists anything
  risky before deleting.
- **Preview only** toggle = dry-run (logs what it would delete, deletes nothing).
- **Big folders…** runs a recursive size scan of `C:\` to find where space went.

## Categories

- **Docker** — build cache, stopped containers, dangling / all unused images,
  networks, and (off by default) volumes. Each is individually toggleable.
- Core system: hibernation, DISM component cleanup (+ resetbase), Windows Update
  cache, Delivery Optimization, restore-point cap / delete, crash dumps.
- Caches: Adobe & DaVinci media, NVIDIA shaders, dev package managers, VS Code,
  Steam, Microsoft (Edge/Teams/OneDrive/Office), Adobe Dunamis, temp folders.
- NLE preview files (regex, off by default).

## Config

Settings persist to `config.json` next to `main.py`. Edit it directly or let the
GUI write it. Delete it to reset to defaults.

## Structure

```
main.py          entry point
config.json      persisted user choices (auto-created)
core/
  engine.py      path expansion, sizing, safe deletion, shell commands
  scanner.py     recursive big-folder scan
  docker.py      docker df parsing + prune operations
  tasks.py       every cleanup category as data (scan + run)
  config.py      load/save config.json
gui/
  app.py         ttkbootstrap GUI
```

Items marked **risky** (DISM resetbase, delete all restore points, Docker
volumes, NLE previews) are irreversible — read the description before enabling.
