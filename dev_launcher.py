"""TookBot Dev Launcher — GUI Tkinter pour controler le bot + dashboard local.

Boutons :
- Start/Stop/Restart Bot
- Start/Stop/Restart Dashboard
- Open Dashboard (browser)
- Git Pull (recup les dernieres modifs sans push)
- View Logs (tail rolling)
- Quit (clean kill processes)

Lancement :
    python dev_launcher.py
ou via build_exe.bat pour generer dev_launcher.exe (PyInstaller).
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import scrolledtext, ttk


REPO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable  # meme interpreter que celui qui lance le launcher

DASHBOARD_PORT = os.getenv("WEB_PORT", "5001")
DASHBOARD_URL  = f"http://localhost:{DASHBOARD_PORT}"


class ProcessTracker:
    """Wrap subprocess + thread qui drain stdout dans une queue."""

    def __init__(self, name: str, cmd: list[str], log_queue: "queue.Queue[str]"):
        self.name = name
        self.cmd = cmd
        self.log_queue = log_queue
        self.proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None

    def start(self):
        if self.is_running():
            return False
        creationflags = 0
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP : permet Ctrl+Break propre
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=REPO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            creationflags=creationflags,
        )
        self.log_queue.put(f"[{self.name}] started (pid={self.proc.pid})")
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True,
        )
        self._reader_thread.start()
        return True

    def stop(self, timeout: float = 5):
        if not self.is_running():
            return False
        try:
            if os.name == "nt":
                # Ctrl+Break pour stop gracieux
                self.proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.log_queue.put(f"[{self.name}] timeout, kill force")
                self.proc.kill()
                self.proc.wait(timeout=2)
        except Exception as e:
            self.log_queue.put(f"[{self.name}] stop err: {e}")
        self.log_queue.put(f"[{self.name}] stopped")
        return True

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _reader_loop(self):
        try:
            for line in self.proc.stdout:
                self.log_queue.put(f"[{self.name}] {line.rstrip()}")
        except Exception:
            pass


class DevLauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TookBot Dev Launcher")
        self.root.geometry("900x600")
        self.root.configure(bg="#0d1117")

        self.log_queue: queue.Queue[str] = queue.Queue()

        self.bot = ProcessTracker("BOT", [PYTHON, "bot.py"], self.log_queue)
        self.web = ProcessTracker("WEB", [PYTHON, "web.py"], self.log_queue)

        self._build_ui()
        self._poll_logs()
        self._poll_status()

    def _build_ui(self):
        # Style ttk
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame",   background="#0d1117")
        style.configure("TLabel",   background="#0d1117", foreground="#e6edf3", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("OK.TLabel",   foreground="#3fb950")
        style.configure("KO.TLabel",   foreground="#f85149")
        style.configure("Big.TButton", padding=(12, 8), font=("Segoe UI", 10, "bold"))

        top = ttk.Frame(self.root, padding=14)
        top.pack(fill="x")

        # === Bot block ===
        bot_frame = ttk.LabelFrame(top, text="Bot Discord (DEV)", padding=10)
        bot_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.bot_status_lbl = ttk.Label(bot_frame, text="● Arrete", style="KO.TLabel")
        self.bot_status_lbl.pack(anchor="w")

        btn_row1 = ttk.Frame(bot_frame)
        btn_row1.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row1, text="Demarrer", style="Big.TButton",
                    command=self.start_bot).pack(side="left", padx=2)
        ttk.Button(btn_row1, text="Arreter",  style="Big.TButton",
                    command=self.stop_bot).pack(side="left", padx=2)
        ttk.Button(btn_row1, text="Redemarrer", style="Big.TButton",
                    command=self.restart_bot).pack(side="left", padx=2)

        # === Web block ===
        web_frame = ttk.LabelFrame(top, text="Dashboard Web (DEV)", padding=10)
        web_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self.web_status_lbl = ttk.Label(web_frame, text="● Arrete", style="KO.TLabel")
        self.web_status_lbl.pack(anchor="w")

        btn_row2 = ttk.Frame(web_frame)
        btn_row2.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row2, text="Demarrer", style="Big.TButton",
                    command=self.start_web).pack(side="left", padx=2)
        ttk.Button(btn_row2, text="Arreter",  style="Big.TButton",
                    command=self.stop_web).pack(side="left", padx=2)
        ttk.Button(btn_row2, text="Redemarrer", style="Big.TButton",
                    command=self.restart_web).pack(side="left", padx=2)

        # === Utilitaires ===
        util_frame = ttk.Frame(self.root, padding=(14, 4))
        util_frame.pack(fill="x")

        ttk.Button(util_frame, text="Ouvrir Dashboard",
                    command=lambda: webbrowser.open(DASHBOARD_URL)).pack(side="left", padx=2)
        ttk.Button(util_frame, text="Git Pull",
                    command=self.git_pull).pack(side="left", padx=2)
        ttk.Button(util_frame, text="Clear logs",
                    command=self.clear_logs).pack(side="left", padx=2)
        ttk.Button(util_frame, text="Tout demarrer",
                    command=self.start_all).pack(side="left", padx=2)
        ttk.Button(util_frame, text="Tout arreter",
                    command=self.stop_all).pack(side="left", padx=2)
        ttk.Button(util_frame, text="Quitter",
                    command=self.quit).pack(side="right", padx=2)

        # === Logs ===
        log_frame = ttk.LabelFrame(self.root, text="Logs", padding=4)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(8, 14))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, bg="#010409", fg="#c9d1d9",
            font=("Consolas", 9), wrap="word",
            insertbackground="#c9d1d9",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

    # === Actions ===
    def start_bot(self):
        if self.bot.start():
            self._info("Bot demarre")
    def stop_bot(self):
        if self.bot.stop():
            self._info("Bot stoppe")
    def restart_bot(self):
        self.stop_bot(); time.sleep(0.5); self.start_bot()

    def start_web(self):
        if self.web.start():
            self._info("Dashboard demarre")
    def stop_web(self):
        if self.web.stop():
            self._info("Dashboard stoppe")
    def restart_web(self):
        self.stop_web(); time.sleep(0.5); self.start_web()

    def start_all(self):
        self.start_bot(); self.start_web()
    def stop_all(self):
        self.stop_bot(); self.stop_web()

    def git_pull(self):
        try:
            res = subprocess.run(
                ["git", "pull"], cwd=REPO_DIR,
                capture_output=True, text=True, timeout=30,
            )
            self._info(f"git pull rc={res.returncode}")
            for line in (res.stdout + res.stderr).splitlines():
                self._info(line)
        except Exception as e:
            self._info(f"git pull err: {e}")

    def clear_logs(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def quit(self):
        self.stop_all()
        time.sleep(0.3)
        self.root.destroy()

    # === Internals ===
    def _info(self, msg: str):
        self.log_queue.put(f"[launcher] {msg}")

    def _poll_logs(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_logs)

    def _poll_status(self):
        if self.bot.is_running():
            self.bot_status_lbl.config(text=f"● En cours (pid={self.bot.proc.pid})", style="OK.TLabel")
        else:
            self.bot_status_lbl.config(text="● Arrete", style="KO.TLabel")
        if self.web.is_running():
            self.web_status_lbl.config(text=f"● En cours (pid={self.web.proc.pid})", style="OK.TLabel")
        else:
            self.web_status_lbl.config(text="● Arrete", style="KO.TLabel")
        self.root.after(800, self._poll_status)


def main():
    # Verifie qu'on a .env.dev ou .env (sinon avertir)
    if not (os.path.exists(".env.dev") or os.path.exists(".env")):
        print("ATTENTION : ni .env.dev ni .env trouve dans", REPO_DIR)
    root = tk.Tk()
    app = DevLauncherApp(root)
    root.protocol("WM_DELETE_WINDOW", app.quit)
    root.mainloop()


if __name__ == "__main__":
    main()
