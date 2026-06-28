# gui/app.py
# ttkbootstrap dark-themed GUI with collapsible category groups, a Docker
# sub-panel, and a Python-versions panel. Flow: Scan -> tick -> Clean (confirm).

import threading
import queue
from collections import OrderedDict
from typing import Dict, List

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledFrame, ScrolledText
from tkinter import messagebox

from core import engine, docker, scanner, pyversions
from core.config import load_config, save_config
from core.tasks import build_tasks


class Collapsible(tb.Frame):
    """A collapsible section with a clickable header showing a total."""

    def __init__(self, parent, title, expanded=True):
        super().__init__(parent)
        self._open = expanded
        self.header = tb.Frame(self, padding=(6, 6))
        self.header.pack(fill=X)
        self.arrow = tb.Label(self.header, text="▾" if expanded else "▸",
                              font=("Segoe UI", 11), width=2)
        self.arrow.pack(side=LEFT)
        self.title_lbl = tb.Label(self.header, text=title, font=("Segoe UI Semibold", 11))
        self.title_lbl.pack(side=LEFT)
        self.total_lbl = tb.Label(self.header, text="", bootstyle=SECONDARY)
        self.total_lbl.pack(side=RIGHT)
        self.body = tb.Frame(self, padding=(22, 0, 4, 6))
        if expanded:
            self.body.pack(fill=X)
        for w in (self.header, self.arrow, self.title_lbl, self.total_lbl):
            w.bind("<Button-1>", lambda e: self.toggle())

    def toggle(self):
        self._open = not self._open
        self.arrow.configure(text="▾" if self._open else "▸")
        if self._open:
            self.body.pack(fill=X)
        else:
            self.body.forget()

    def set_total(self, text):
        self.total_lbl.configure(text=text)


class CleanerApp(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly", title="Windows Cleaner", size=(1000, 800))
        self.minsize(880, 620)

        self.cfg = load_config()
        self.is_admin = engine.is_admin()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.busy = False

        self.task_widgets: Dict[str, dict] = {}
        self.docker_vars: Dict[str, tb.BooleanVar] = {}
        self.pyver_widgets: Dict[str, dict] = {}
        self.group_sections: Dict[str, Collapsible] = {}

        self.tasks = build_tasks()
        self._build_ui()
        self._poll_log()

    # ---------- UI ----------

    def _build_ui(self):
        header = tb.Frame(self, padding=(16, 12))
        header.pack(fill=X)
        tb.Label(header, text="🧹  Windows Cleaner",
                 font=("Segoe UI Semibold", 20)).pack(side=LEFT)
        self.free_label = tb.Label(header, text="", bootstyle=SECONDARY)
        self.free_label.pack(side=RIGHT)
        self._update_free_label()

        if not self.is_admin:
            warn = tb.Frame(self, padding=(16, 0))
            warn.pack(fill=X)
            tb.Label(warn, text="⚠  Not admin — System (admin) tasks will be skipped. Use 'Restart as Admin'.",
                     bootstyle=WARNING, wraplength=940, justify=LEFT).pack(fill=X, pady=(0, 6))

        body = tb.Frame(self, padding=(16, 8))
        body.pack(fill=BOTH, expand=YES)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        # left side: a notebook with Clean / Programs / Saved games tabs
        nb = tb.Notebook(body)
        nb.grid(row=0, column=0, sticky=NSEW, padx=(0, 8))
        clean_tab = tb.Frame(nb, padding=4)
        prog_tab = tb.Frame(nb, padding=4)
        saves_tab = tb.Frame(nb, padding=4)
        nb.add(clean_tab, text="Clean")
        nb.add(prog_tab, text="Programs / leftovers")
        nb.add(saves_tab, text="Saved games")

        self._build_groups(clean_tab)
        self._build_programs_tab(prog_tab)
        self._build_saves_tab(saves_tab)
        self._build_log_panel(body)
        self._build_footer()

    def _build_groups(self, parent):
        self.sf = ScrolledFrame(parent, autohide=True)
        self.sf.pack(fill=BOTH, expand=YES)

        # group tasks in a stable order
        order = ["Caches", "Dev & Python", "AI history", "Docker",
                 "System (admin)", "Media", "Other"]
        grouped: "OrderedDict[str, list]" = OrderedDict((g, []) for g in order)
        for t in self.tasks:
            grouped.setdefault(t.group, []).append(t)

        enabled_cfg = self.cfg.get("enabled_tasks", {})
        for group, tlist in grouped.items():
            if not tlist:
                continue
            sect = Collapsible(self.sf, group, expanded=(group in ("Caches", "Dev & Python", "AI history")))
            sect.pack(fill=X, pady=2)
            self.group_sections[group] = sect

            for task in tlist:
                self._add_task_row(sect.body, task, enabled_cfg)
                if task.key == "docker":
                    self._build_docker_sub(sect.body)

            if group == "Dev & Python":
                self._build_pyversions_panel(sect.body)

    def _add_task_row(self, parent, task, enabled_cfg):
        row = tb.Frame(parent, padding=(2, 3))
        row.pack(fill=X)
        var = tb.BooleanVar(value=enabled_cfg.get(task.key, task.default_on))
        style = DANGER if task.risky else (WARNING if task.requires_admin else PRIMARY)
        cb = tb.Checkbutton(row, text=task.label, variable=var,
                            bootstyle=f"{style}-round-toggle")
        cb.pack(side=LEFT, anchor=W)
        size_lbl = tb.Label(row, text="—", width=10, anchor=E, bootstyle=SECONDARY)
        size_lbl.pack(side=RIGHT)
        tb.Label(parent, text=task.description, font=("Segoe UI", 8),
                 bootstyle=SECONDARY, wraplength=480, justify=LEFT).pack(
            fill=X, padx=(24, 4), pady=(0, 2))
        self.task_widgets[task.key] = {"var": var, "size": size_lbl, "task": task}

    def _build_docker_sub(self, parent):
        box = tb.Frame(parent, padding=(24, 0, 4, 4))
        box.pack(fill=X)
        for k, op in docker.PRUNE_OPS.items():
            v = tb.BooleanVar(value=self.cfg.get("docker_ops", {}).get(k, op["default"]))
            self.docker_vars[k] = v
            style = DANGER if k == "volumes" else INFO
            tb.Checkbutton(box, text="• " + op["label"], variable=v,
                           bootstyle=f"{style}-round-toggle").pack(anchor=W)

    def _build_pyversions_panel(self, parent):
        tb.Label(parent, text="Installed Python versions", font=("Segoe UI Semibold", 9),
                 bootstyle=INFO).pack(anchor=W, pady=(6, 0))
        box = tb.Frame(parent, padding=(24, 0, 4, 4))
        box.pack(fill=X)
        try:
            versions = pyversions.list_versions()
        except Exception:
            versions = []
        if not versions:
            tb.Label(box, text="  (none found)", bootstyle=SECONDARY).pack(anchor=W)
            return
        for v in versions:
            row = tb.Frame(box)
            row.pack(fill=X)
            var = tb.BooleanVar(value=False)
            badge = f"  [{v.flags}]" if v.flags else ""
            style = WARNING if v.protected else SECONDARY
            tb.Checkbutton(row, text=f"• {v.name}{badge}", variable=var,
                           bootstyle=f"{style}-round-toggle").pack(side=LEFT, anchor=W)
            tb.Label(row, text=engine.human_size(v.size), width=10, anchor=E,
                     bootstyle=SECONDARY).pack(side=RIGHT)
            self.pyver_widgets[v.name] = {"var": var, "ver": v}

    # ---------- Programs / leftovers tab ----------

    def _build_programs_tab(self, parent):
        self.program_widgets: Dict[str, dict] = {}
        top = tb.Frame(parent, padding=(2, 4))
        top.pack(fill=X)
        tb.Label(top, text="App data folders. 'leftover' = no matching installed app.",
                 font=("Segoe UI", 8), bootstyle=SECONDARY, wraplength=460,
                 justify=LEFT).pack(side=LEFT)
        self.prog_scan_btn = tb.Button(top, text="Scan apps", bootstyle=(INFO, OUTLINE),
                                       command=self._on_scan_programs, width=11)
        self.prog_scan_btn.pack(side=RIGHT)
        self.only_leftovers = tb.BooleanVar(value=True)
        tb.Checkbutton(parent, text="Show only likely leftovers (uninstalled apps)",
                       variable=self.only_leftovers, bootstyle="info-round-toggle",
                       command=self._render_programs).pack(anchor=W, pady=(2, 4))
        self.prog_sf = ScrolledFrame(parent, autohide=True)
        self.prog_sf.pack(fill=BOTH, expand=YES)
        self._program_data = []  # cached scan results
        tb.Label(self.prog_sf, text="Click 'Scan apps' to list app data folders.",
                 bootstyle=SECONDARY).pack(anchor=W, pady=8)

        rm = tb.Frame(parent, padding=(2, 6))
        rm.pack(fill=X)
        tb.Label(rm, text="Selected are archived to D: before removal.",
                 font=("Segoe UI", 8), bootstyle=SECONDARY).pack(side=LEFT)
        tb.Button(rm, text="Archive & remove selected", bootstyle=(WARNING, OUTLINE),
                  command=self._on_remove_programs).pack(side=RIGHT)

    def _on_scan_programs(self):
        if self.busy:
            return
        self._set_busy(True)
        self.prog_scan_btn.configure(state=DISABLED)
        self.log("── Scanning installed apps & data folders ──")
        threading.Thread(target=self._scan_programs_worker, daemon=True).start()

    def _scan_programs_worker(self):
        from core import programs
        try:
            data = programs.scan_data_folders(min_mb=20)
        except Exception as e:
            self.log(f"  ! program scan failed: {e}")
            data = []
        self._program_data = data
        n_left = sum(1 for d in data if d.matched_app == "")
        self.log(f"  {len(data)} data folders, {n_left} look like leftovers.")
        self.after(0, self._render_programs)
        self.after(0, lambda: self.prog_scan_btn.configure(state=NORMAL))
        self.after(0, lambda: self._set_busy(False))

    def _render_programs(self):
        for child in self.prog_sf.winfo_children():
            child.destroy()
        self.program_widgets.clear()
        only = self.only_leftovers.get()
        rows = [d for d in self._program_data if (d.matched_app == "" or not only)]
        if not rows:
            tb.Label(self.prog_sf, text="(nothing to show — try Scan apps)",
                     bootstyle=SECONDARY).pack(anchor=W, pady=8)
            return
        for d in rows:
            row = tb.Frame(self.prog_sf, padding=(2, 2))
            row.pack(fill=X)
            var = tb.BooleanVar(value=False)
            leftover = d.matched_app == ""
            tag = "  [leftover]" if leftover else f"  ~{d.matched_app}"
            style = DANGER if leftover else SECONDARY
            tb.Checkbutton(row, text=d.name + tag, variable=var,
                           bootstyle=f"{style}-round-toggle").pack(side=LEFT, anchor=W)
            tb.Label(row, text=engine.human_size(d.size), width=10, anchor=E,
                     bootstyle=SECONDARY).pack(side=RIGHT)
            self.program_widgets[d.path] = {"var": var, "data": d}

    def _on_remove_programs(self):
        if self.busy:
            return
        picked = [w["data"] for w in self.program_widgets.values() if w["var"].get()]
        if not picked:
            messagebox.showinfo("Nothing selected", "Tick folders to archive & remove.")
            return
        total = sum(d.size for d in picked)
        msg = (f"Archive {len(picked)} folder(s) (~{engine.human_size(total)}) to "
               f"{self.cfg.get('archive_root', 'D:\\_CleanerArchive')} then remove from C:?\n\n"
               + "\n".join(f"  • {d.name}" for d in picked[:15]))
        if len(picked) > 15:
            msg += f"\n  …+{len(picked)-15} more"
        if not messagebox.askyesno("Confirm archive & remove", msg):
            return
        self._set_busy(True)
        threading.Thread(target=self._remove_programs_worker, args=(picked,), daemon=True).start()

    def _remove_programs_worker(self, picked):
        from core import programs
        root = self.cfg.get("archive_root", r"D:\_CleanerArchive")
        self.log(f"══ Archiving {len(picked)} app folders to {root} ══")
        grand = 0
        for d in picked:
            self.log(f"▶ {d.name}")
            try:
                grand += programs.remove_data_folder(d, root, dry_run=False, log=self.log)
            except Exception as e:
                self.log(f"   ! {e}")
        self.log(f"══ Done — moved ≈ {engine.human_size(grand)} ══")
        self.after(0, self._update_free_label)
        self.after(0, self._on_scan_programs)
        self.after(0, lambda: self._set_busy(False))

    # ---------- Saved games tab ----------

    def _build_saves_tab(self, parent):
        self.save_widgets: Dict[str, dict] = {}
        top = tb.Frame(parent, padding=(2, 4))
        top.pack(fill=X)
        tb.Label(top, text="Game saves & config. Review carefully — these are your saves.",
                 font=("Segoe UI", 8), bootstyle=WARNING, wraplength=440,
                 justify=LEFT).pack(side=LEFT)
        self.saves_scan_btn = tb.Button(top, text="Find saves", bootstyle=(INFO, OUTLINE),
                                        command=self._on_scan_saves, width=11)
        self.saves_scan_btn.pack(side=RIGHT)
        self.saves_sf = ScrolledFrame(parent, autohide=True)
        self.saves_sf.pack(fill=BOTH, expand=YES)
        tb.Label(self.saves_sf, text="Click 'Find saves' to list game save locations.",
                 bootstyle=SECONDARY).pack(anchor=W, pady=8)
        rm = tb.Frame(parent, padding=(2, 6))
        rm.pack(fill=X)
        tb.Label(rm, text="Always archived to D: before removal (recoverable).",
                 font=("Segoe UI", 8), bootstyle=SECONDARY).pack(side=LEFT)
        tb.Button(rm, text="Archive & remove selected", bootstyle=(DANGER, OUTLINE),
                  command=self._on_remove_saves).pack(side=RIGHT)

    def _on_scan_saves(self):
        if self.busy:
            return
        self._set_busy(True)
        self.saves_scan_btn.configure(state=DISABLED)
        self.log("── Searching for game saves ──")
        threading.Thread(target=self._scan_saves_worker, daemon=True).start()

    def _scan_saves_worker(self):
        from core import savedgames
        try:
            data = savedgames.discover(min_mb=5)
        except Exception as e:
            self.log(f"  ! save scan failed: {e}")
            data = []
        self.after(0, lambda: self._render_saves(data))
        self.after(0, lambda: self.saves_scan_btn.configure(state=NORMAL))
        self.after(0, lambda: self._set_busy(False))

    def _render_saves(self, data):
        for child in self.saves_sf.winfo_children():
            child.destroy()
        self.save_widgets.clear()
        if not data:
            tb.Label(self.saves_sf, text="(no save folders found)",
                     bootstyle=SECONDARY).pack(anchor=W, pady=8)
            return
        for s in data:
            row = tb.Frame(self.saves_sf, padding=(2, 2))
            row.pack(fill=X)
            var = tb.BooleanVar(value=False)
            tb.Checkbutton(row, text=s.name, variable=var,
                           bootstyle="warning-round-toggle").pack(side=LEFT, anchor=W)
            tb.Label(row, text=engine.human_size(s.size), width=10, anchor=E,
                     bootstyle=SECONDARY).pack(side=RIGHT)
            tb.Label(self.saves_sf, text="    " + s.path, font=("Segoe UI", 8),
                     bootstyle=SECONDARY, wraplength=470, justify=LEFT).pack(
                fill=X, padx=(24, 4))
            self.save_widgets[s.path] = {"var": var, "data": s}

    def _on_remove_saves(self):
        if self.busy:
            return
        picked = [w["data"] for w in self.save_widgets.values() if w["var"].get()]
        if not picked:
            messagebox.showinfo("Nothing selected", "Tick saves to archive & remove.")
            return
        total = sum(s.size for s in picked)
        root = self.cfg.get("archive_root", r"D:\_CleanerArchive")
        msg = (f"Archive {len(picked)} save folder(s) (~{engine.human_size(total)}) to {root} "
               f"then remove from C:?\n\n" + "\n".join(f"  • {s.name}" for s in picked))
        if not messagebox.askyesno("Confirm — your game saves", msg):
            return
        self._set_busy(True)
        threading.Thread(target=self._remove_saves_worker, args=(picked, root), daemon=True).start()

    def _remove_saves_worker(self, picked, root):
        from core import savedgames
        self.log(f"══ Archiving {len(picked)} save folders ══")
        grand = 0
        for s in picked:
            self.log(f"▶ {s.name}")
            try:
                grand += savedgames.archive_save(s, root, dry_run=False, log=self.log)
            except Exception as e:
                self.log(f"   ! {e}")
        self.log(f"══ Done — archived ≈ {engine.human_size(grand)} ══")
        self.after(0, self._update_free_label)
        self.after(0, self._on_scan_saves)
        self.after(0, lambda: self._set_busy(False))

    def _build_log_panel(self, parent):
        right = tb.Labelframe(parent, text="Activity log", padding=8)
        right.grid(row=0, column=1, sticky=NSEW)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(right, autohide=True, wrap=WORD,
                                     font=("Cascadia Mono", 9))
        self.log_text.grid(row=0, column=0, sticky=NSEW)
        self.log_text.text.configure(state=DISABLED)

    def _build_footer(self):
        footer = tb.Frame(self, padding=(16, 10))
        footer.pack(fill=X)
        self.dry_var = tb.BooleanVar(value=self.cfg.get("dry_run", False))
        tb.Checkbutton(footer, text="Preview only (dry-run)", variable=self.dry_var,
                       bootstyle="info-round-toggle").pack(side=LEFT)
        self.total_label = tb.Label(footer, text="", font=("Segoe UI Semibold", 11))
        self.total_label.pack(side=LEFT, padx=20)
        self.clean_btn = tb.Button(footer, text="Clean selected", bootstyle=SUCCESS,
                                   command=self._on_clean, width=16)
        self.clean_btn.pack(side=RIGHT, padx=(8, 0))
        self.scan_btn = tb.Button(footer, text="Scan", bootstyle=(INFO, OUTLINE),
                                  command=self._on_scan, width=10)
        self.scan_btn.pack(side=RIGHT, padx=(8, 0))
        tb.Button(footer, text="Big folders…", bootstyle=(SECONDARY, OUTLINE),
                  command=self._on_big_folders, width=13).pack(side=RIGHT, padx=(8, 0))
        if not self.is_admin:
            tb.Button(footer, text="Restart as Admin", bootstyle=(WARNING, OUTLINE),
                      command=self._restart_admin).pack(side=RIGHT, padx=(8, 0))

    # ---------- logging ----------

    def log(self, line: str):
        self.log_queue.put(line)

    def _poll_log(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.text.configure(state=NORMAL)
                self.log_text.text.insert(END, line + "\n")
                self.log_text.text.see(END)
                self.log_text.text.configure(state=DISABLED)
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    # ---------- helpers ----------

    def _update_free_label(self):
        try:
            free, total = scanner.drive_free_space("C:\\")
            self.free_label.configure(
                text=f"C:  {engine.human_size(free)} free of {engine.human_size(total)}")
        except Exception:
            pass

    def _set_busy(self, busy):
        self.busy = busy
        st = DISABLED if busy else NORMAL
        self.scan_btn.configure(state=st)
        self.clean_btn.configure(state=st)

    def _selected_docker_keys(self):
        return [k for k, v in self.docker_vars.items() if v.get()]

    def _persist(self):
        self.cfg["dry_run"] = self.dry_var.get()
        self.cfg["enabled_tasks"] = {k: w["var"].get() for k, w in self.task_widgets.items()}
        self.cfg["docker_ops"] = {k: v.get() for k, v in self.docker_vars.items()}
        save_config(self.cfg)

    # ---------- scan ----------

    def _on_scan(self):
        if self.busy:
            return
        self._set_busy(True)
        self.log("── Scanning reclaimable space ──")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        group_totals: Dict[str, int] = {}
        grand = 0
        for key, w in self.task_widgets.items():
            task = w["task"]
            try:
                size = task.scan()
            except Exception:
                size = 0
            grand += size
            group_totals[task.group] = group_totals.get(task.group, 0) + size
            text = engine.human_size(size) if size else ("?" if task.regex_under else "—")
            self.after(0, lambda lbl=w["size"], t=text: lbl.configure(text=t))
        for g, sect in self.group_sections.items():
            tot = group_totals.get(g, 0)
            self.after(0, lambda s=sect, t=tot: s.set_total(engine.human_size(t) if t else ""))
        self.after(0, lambda: self.total_label.configure(
            text=f"≈ {engine.human_size(grand)} reclaimable"))
        self.log(f"── Scan done: ≈ {engine.human_size(grand)} ──")
        self.after(0, lambda: self._set_busy(False))

    # ---------- clean ----------

    def _on_clean(self):
        if self.busy:
            return
        selected = [w["task"] for w in self.task_widgets.values() if w["var"].get()]
        pyvers = [d["ver"] for d in self.pyver_widgets.values() if d["var"].get()]
        if not selected and not pyvers:
            messagebox.showinfo("Nothing selected", "Tick at least one item.")
            return

        dry = self.dry_var.get()
        risky = [t.label for t in selected if t.risky]
        protected_py = [v.name for v in pyvers if v.protected]
        vol = "volumes" in self._selected_docker_keys() and any(t.key == "docker" for t in selected)

        msg = "Preview only — nothing will be deleted.\n\n" if dry else ""
        msg += f"Process {len(selected)} categories"
        if pyvers:
            msg += f" + remove {len(pyvers)} Python version(s)"
        msg += ".\n"
        if risky:
            msg += "\n⚠ Aggressive:\n  - " + "\n  - ".join(risky) + "\n"
        if protected_py:
            msg += "\n⚠ PROTECTED Python (on-PATH/default/running): " + ", ".join(protected_py) + "\n  Removing these may break `python` commands.\n"
        if vol:
            msg += "\n⚠ Docker volumes will be DELETED.\n"
        msg += "\nContinue?"
        if not messagebox.askyesno("Confirm", msg):
            return

        self._persist()
        for t in selected:
            if t.key == "docker":
                keys = self._selected_docker_keys()
                t.custom_run = (lambda kk: (lambda log, d: docker.run_prune(kk, log, d)))(keys)

        self._set_busy(True)
        threading.Thread(target=self._clean_worker, args=(selected, pyvers, dry), daemon=True).start()

    def _clean_worker(self, selected, pyvers, dry):
        mode = "[PREVIEW] " if dry else ""
        self.log(f"══ {mode}Cleaning ══")
        archive_root = self.cfg.get("archive_root", r"D:\_CleanerArchive")
        grand = 0
        for task in selected:
            if task.requires_admin and not self.is_admin:
                self.log(f"⊘ {task.label}: needs admin — skipped")
                continue
            self.log(f"▶ {task.label}")
            try:
                freed = task.run(self.log, dry)
                grand += freed
                self.log(f"   freed ≈ {engine.human_size(freed)}")
            except Exception as e:
                self.log(f"   ! error: {e}")
        for v in pyvers:
            self.log(f"▶ Python {v.name}" + (f"  [{v.flags}]" if v.flags else ""))
            try:
                freed = pyversions.remove_version(v, archive_root, archive=False, dry_run=dry, log=self.log)
                grand += freed
                self.log(f"   freed ≈ {engine.human_size(freed)}")
            except Exception as e:
                self.log(f"   ! error: {e}")
        verb = "would free" if dry else "freed"
        self.log(f"══ Done — {verb} ≈ {engine.human_size(grand)} ══")
        self.after(0, self._update_free_label)
        self.after(0, lambda: self._set_busy(False))

    # ---------- big folders ----------

    def _on_big_folders(self):
        if self.busy:
            return
        self._set_busy(True)
        self.log("── Scanning C:\\ for big folders ──")
        threading.Thread(target=self._big_folders_worker, daemon=True).start()

    def _big_folders_worker(self):
        sc = self.cfg.get("scanner", {})
        try:
            results = scanner.scan_big_folders(
                root=sc.get("root", "C:\\"), min_size_gb=sc.get("min_size_gb", 1.0),
                top=sc.get("top", 25))
            for r in results:
                self.log(f"  {engine.human_size(r.size_bytes):>10}  {r.path}")
        except Exception as e:
            self.log(f"  ! {e}")
        self.log("── done ──")
        self.after(0, lambda: self._set_busy(False))

    def _restart_admin(self):
        if engine.relaunch_as_admin():
            self.destroy()


def run():
    app = CleanerApp()
    app.place_window_center()
    app.mainloop()
