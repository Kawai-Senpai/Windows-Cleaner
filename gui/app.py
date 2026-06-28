# gui/app.py
# ttkbootstrap dark-themed GUI for the Windows cleaner.
# Flow: pick categories -> Scan (shows reclaimable size per task) -> Clean (confirm) .

import threading
import queue
from typing import Dict, List

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledFrame
from tkinter import messagebox

from core import engine, docker, scanner
from core.config import load_config, save_config
from core.tasks import build_tasks


class CleanerApp(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly", title="Windows Cleaner", size=(940, 760))
        self.minsize(820, 600)

        self.cfg = load_config()
        self.is_admin = engine.is_admin()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.busy = False

        # task key -> {var, size_label, task}
        self.task_widgets: Dict[str, dict] = {}
        self.docker_vars: Dict[str, tb.BooleanVar] = {}

        self.tasks = build_tasks()
        self._build_ui()
        self._poll_log()

    # ---------- UI construction ----------

    def _build_ui(self):
        header = tb.Frame(self, padding=(16, 12))
        header.pack(fill=X)

        tb.Label(header, text="🧹  Windows Cleaner",
                 font=("Segoe UI Semibold", 20)).pack(side=LEFT)

        self.free_label = tb.Label(header, text="", font=("Segoe UI", 10), bootstyle=SECONDARY)
        self.free_label.pack(side=RIGHT)
        self._update_free_label()

        if not self.is_admin:
            warn = tb.Frame(self, padding=(16, 0))
            warn.pack(fill=X)
            tb.Label(
                warn,
                text="⚠  Not running as administrator — system tasks (DISM, Windows Update, restore points) will fail. Use 'Restart as Admin'.",
                bootstyle=WARNING, wraplength=880, justify=LEFT,
            ).pack(fill=X, pady=(0, 6))

        # main split: left = task list, right = log
        body = tb.Frame(self, padding=(16, 8))
        body.pack(fill=BOTH, expand=YES)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_task_panel(body)
        self._build_log_panel(body)
        self._build_footer()

    def _build_task_panel(self, parent):
        left = tb.Labelframe(parent, text="Cleanup categories", padding=8)
        left.grid(row=0, column=0, sticky=NSEW, padx=(0, 8))

        sf = ScrolledFrame(left, autohide=True)
        sf.pack(fill=BOTH, expand=YES)

        dry = self.cfg.get("dry_run", False)
        for task in self.tasks:
            row = tb.Frame(sf, padding=(4, 6))
            row.pack(fill=X)

            enabled = self.cfg.get("enabled_tasks", {}).get(task.key, task.default_on)
            var = tb.BooleanVar(value=enabled)

            style = DANGER if task.risky else (WARNING if task.requires_admin else PRIMARY)
            cb = tb.Checkbutton(row, text=task.label, variable=var, bootstyle=f"{style}-round-toggle")
            cb.pack(side=LEFT, anchor=W)

            size_lbl = tb.Label(row, text="—", width=10, anchor=E, bootstyle=SECONDARY)
            size_lbl.pack(side=RIGHT)

            desc = tb.Label(sf, text="    " + task.description, font=("Segoe UI", 8),
                            bootstyle=SECONDARY, wraplength=470, justify=LEFT)
            desc.pack(fill=X, padx=(28, 4), pady=(0, 2))

            self.task_widgets[task.key] = {"var": var, "size": size_lbl, "task": task}

            if task.key == "docker":
                self._build_docker_suboptions(sf)

    def _build_docker_suboptions(self, parent):
        box = tb.Frame(parent, padding=(40, 0, 4, 6))
        box.pack(fill=X)
        for k, op in docker.PRUNE_OPS.items():
            on = self.cfg.get("docker_ops", {}).get(k, op["default"])
            var = tb.BooleanVar(value=on)
            self.docker_vars[k] = var
            style = DANGER if k == "volumes" else INFO
            tb.Checkbutton(box, text="• " + op["label"], variable=var,
                           bootstyle=f"{style}-round-toggle").pack(anchor=W)

    def _build_log_panel(self, parent):
        right = tb.Labelframe(parent, text="Activity log", padding=8)
        right.grid(row=0, column=1, sticky=NSEW)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.log_text = tb.ScrolledText(right, height=10, autohide=True, wrap=WORD,
                                        font=("Cascadia Mono", 9))
        self.log_text.grid(row=0, column=0, sticky=NSEW)
        self.log_text.text.configure(state=DISABLED)

    def _build_footer(self):
        footer = tb.Frame(self, padding=(16, 10))
        footer.pack(fill=X)

        self.dry_var = tb.BooleanVar(value=self.cfg.get("dry_run", False))
        tb.Checkbutton(footer, text="Preview only (dry-run, deletes nothing)",
                       variable=self.dry_var, bootstyle="info-round-toggle").pack(side=LEFT)

        self.total_label = tb.Label(footer, text="", font=("Segoe UI Semibold", 11))
        self.total_label.pack(side=LEFT, padx=20)

        self.clean_btn = tb.Button(footer, text="Clean selected", bootstyle=SUCCESS,
                                   command=self._on_clean, width=16)
        self.clean_btn.pack(side=RIGHT, padx=(8, 0))

        self.scan_btn = tb.Button(footer, text="Scan", bootstyle=(INFO, OUTLINE),
                                  command=self._on_scan, width=12)
        self.scan_btn.pack(side=RIGHT, padx=(8, 0))

        tb.Button(footer, text="Big folders…", bootstyle=(SECONDARY, OUTLINE),
                  command=self._on_big_folders, width=14).pack(side=RIGHT, padx=(8, 0))

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
                text=f"C:  {engine.human_size(free)} free of {engine.human_size(total)}"
            )
        except Exception:
            pass

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = DISABLED if busy else NORMAL
        self.scan_btn.configure(state=state)
        self.clean_btn.configure(state=state)

    def _selected_docker_keys(self) -> List[str]:
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
        grand_total = 0
        for key, w in self.task_widgets.items():
            task = w["task"]
            try:
                size = task.scan()
            except Exception as e:
                self.log(f"  ! scan failed for {task.label}: {e}")
                size = 0
            grand_total += size
            text = engine.human_size(size) if size else ("?" if task.regex_under else "—")
            self.after(0, lambda lbl=w["size"], t=text: lbl.configure(text=t))
        self.after(0, lambda: self.total_label.configure(
            text=f"≈ {engine.human_size(grand_total)} reclaimable"))
        self.log(f"── Scan done: ≈ {engine.human_size(grand_total)} across all categories ──")
        self.after(0, lambda: self._set_busy(False))

    # ---------- clean ----------

    def _on_clean(self):
        if self.busy:
            return
        selected = [w["task"] for w in self.task_widgets.values() if w["var"].get()]
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick at least one category to clean.")
            return

        dry = self.dry_var.get()
        risky = [t.label for t in selected if t.risky]
        vol = "volumes" in self._selected_docker_keys() and any(t.key == "docker" for t in selected)

        msg = "Preview only — nothing will be deleted.\n\n" if dry else ""
        msg += f"About to process {len(selected)} categories.\n"
        if risky:
            msg += "\n⚠ Aggressive/irreversible:\n  - " + "\n  - ".join(risky) + "\n"
        if vol:
            msg += "\n⚠ Docker volumes will be DELETED (data loss unless bind-mounted).\n"
        msg += "\nContinue?"

        if not messagebox.askyesno("Confirm cleanup", msg):
            return

        self._persist()
        # rebuild docker task's run with current selection
        for t in selected:
            if t.key == "docker":
                keys = self._selected_docker_keys()
                t.custom_run = (lambda kk: (lambda log, d: docker.run_prune(kk, log, d)))(keys)

        self._set_busy(True)
        threading.Thread(target=self._clean_worker, args=(selected, dry), daemon=True).start()

    def _clean_worker(self, selected, dry):
        mode = "[PREVIEW] " if dry else ""
        self.log(f"══ {mode}Cleaning {len(selected)} categories ══")
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
        verb = "would free" if dry else "freed"
        self.log(f"══ Done — {verb} ≈ {engine.human_size(grand)} ══")
        self.after(0, self._update_free_label)
        self.after(0, lambda: self._set_busy(False))

    # ---------- big folders ----------

    def _on_big_folders(self):
        if self.busy:
            return
        self._set_busy(True)
        self.log("── Scanning C:\\ for big folders (this can take a while) ──")
        threading.Thread(target=self._big_folders_worker, daemon=True).start()

    def _big_folders_worker(self):
        sc = self.cfg.get("scanner", {})
        try:
            results = scanner.scan_big_folders(
                root=sc.get("root", "C:\\"),
                min_size_gb=sc.get("min_size_gb", 1.0),
                top=sc.get("top", 25),
                progress=lambda p: None,
            )
            self.log(f"Top {len(results)} folders ≥ {sc.get('min_size_gb',1)}GB:")
            for r in results:
                self.log(f"  {engine.human_size(r.size_bytes):>10}  {r.path}")
        except Exception as e:
            self.log(f"  ! scan failed: {e}")
        self.log("── Big-folder scan done ──")
        self.after(0, lambda: self._set_busy(False))

    # ---------- admin ----------

    def _restart_admin(self):
        if engine.relaunch_as_admin():
            self.destroy()


def run():
    app = CleanerApp()
    app.place_window_center()
    app.mainloop()
