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
from tkinter import scrolledtext, ttk, font as tkfont


def _resolve_repo_dir() -> str:
    """Trouve le dossier du repo (contient bot.py).
    - en dev : dirname(__file__)
    - en compile : dirname(sys.executable) si contient bot.py, sinon
      remonte les parents jusqu'a en trouver un.
    """
    if getattr(sys, "frozen", False):
        start = os.path.dirname(os.path.abspath(sys.executable))
    else:
        start = os.path.dirname(os.path.abspath(__file__))
    # Walk up max 4 levels
    cur = start
    for _ in range(5):
        if os.path.exists(os.path.join(cur, "bot.py")) and \
           os.path.exists(os.path.join(cur, "web.py")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # Fallback : retourne start meme si bot.py absent (l'erreur sera explicite)
    return start


REPO_DIR = _resolve_repo_dir()


def _find_system_python() -> str:
    """En mode compile (PyInstaller .exe), sys.executable = launcher.exe.
    On veut le vrai python.exe pour spawn bot.py. Cherche dans PATH puis
    emplacements standards Windows."""
    import shutil
    # 1) PATH
    for name in ("python.exe", "python3.exe", "py.exe"):
        p = shutil.which(name)
        if p:
            return p
    # 2) Emplacements typiques Windows
    candidates = [
        r"C:\Program Files\Python311\python.exe",
        r"C:\Program Files\Python312\python.exe",
        r"C:\Program Files\Python310\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python312\python.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


# En mode compile (frozen=True), on cherche le vrai python systeme.
# Sinon on utilise l'interpreteur courant (mode python dev_launcher.py).
if getattr(sys, "frozen", False):
    PYTHON = _find_system_python() or "python"
else:
    PYTHON = sys.executable

DASHBOARD_PORT = os.getenv("WEB_PORT", "5001")
DASHBOARD_URL  = f"http://localhost:{DASHBOARD_PORT}"

# === Palette TookBot ===
BG          = "#0a0c10"   # fond noir/bleu profond
BG_PANEL    = "#11151b"   # cards
BG_PANEL_2  = "#1a1f26"   # input/log
BORDER      = "#2a313c"
TEXT        = "#e6edf3"
TEXT_MUTED  = "#8b96a3"
ACCENT      = "#b9ff24"   # lime TookBot
ACCENT_DK   = "#8fcc1c"
GREEN       = "#3fb950"
RED         = "#f85149"
ORANGE      = "#ffa726"

FONT_FAMILY  = "Segoe UI Variable"
FONT_FAMILY_FALLBACK = "Segoe UI"


class ProcessTracker:
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
            # CREATE_NO_WINDOW : pas de console child visible (stdout
            # transit toujours par PIPE pour notre log)
            creationflags = (subprocess.CREATE_NEW_PROCESS_GROUP
                             | 0x08000000)  # CREATE_NO_WINDOW
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"  # evite cp1252 sur Windows
        env["PYTHONUTF8"] = "1"
        self.proc = subprocess.Popen(
            self.cmd, cwd=REPO_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, creationflags=creationflags,
            env=env, encoding="utf-8", errors="replace",
        )
        self.log_queue.put(("info", f"[{self.name}] started (pid={self.proc.pid})"))
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        return True

    def stop(self, timeout: float = 5):
        if not self.is_running():
            return False
        try:
            if os.name == "nt":
                self.proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.log_queue.put(("warn", f"[{self.name}] timeout, kill force"))
                self.proc.kill()
                self.proc.wait(timeout=2)
        except Exception as e:
            self.log_queue.put(("err", f"[{self.name}] stop err: {e}"))
        self.log_queue.put(("info", f"[{self.name}] stopped"))
        return True

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _reader_loop(self):
        try:
            for line in self.proc.stdout:
                self.log_queue.put((self.name, line.rstrip()))
        except Exception:
            pass


class StyledButton(tk.Canvas):
    """Bouton custom dessine sur Canvas pour controle total du look."""
    def __init__(self, parent, text, command=None, *, accent=False, width=130, height=36):
        super().__init__(parent, width=width, height=height,
                          bg=BG_PANEL, highlightthickness=0, bd=0)
        self.command = command
        self.text = text
        self.accent = accent
        self.btn_width  = width
        self.btn_height = height
        self.is_hover = False
        self._draw()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>",    self._on_enter)
        self.bind("<Leave>",    self._on_leave)

    def _draw(self):
        self.delete("all")
        if self.accent:
            fill = ACCENT_DK if self.is_hover else ACCENT
            tcol = "#08110a"
        else:
            fill = "#252b35" if self.is_hover else BG_PANEL_2
            tcol = TEXT
        self.create_rectangle(0, 0, self.btn_width, self.btn_height,
                              fill=fill, outline=BORDER, width=1)
        self.create_text(self.btn_width // 2, self.btn_height // 2,
                         text=self.text, fill=tcol,
                         font=(FONT_FAMILY, 10, "bold"))

    def _on_click(self, _e):
        if self.command:
            self.command()
    def _on_enter(self, _e):
        self.is_hover = True; self._draw(); self.config(cursor="hand2")
    def _on_leave(self, _e):
        self.is_hover = False; self._draw(); self.config(cursor="")


class DevLauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TookBot Dev Launcher")
        self.root.geometry("980x680")
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # Use modern font if available
        global FONT_FAMILY
        avail_fonts = set(tkfont.families())
        if FONT_FAMILY not in avail_fonts:
            FONT_FAMILY = FONT_FAMILY_FALLBACK

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.bot = ProcessTracker("BOT", [PYTHON, "bot.py"], self.log_queue)
        self.web = ProcessTracker("WEB", [PYTHON, "web.py"], self.log_queue)

        self._build_ui()
        self._poll_logs()
        self._poll_status()

    def _build_ui(self):
        # === Header ===
        header = tk.Frame(self.root, bg=BG, pady=18, padx=24)
        header.pack(fill="x")
        tk.Label(header, text="TookBot Dev Launcher", bg=BG, fg=TEXT,
                 font=(FONT_FAMILY, 18, "bold")).pack(side="left")
        tk.Label(header, text="environnement DEV local • Hetzner non touche",
                 bg=BG, fg=TEXT_MUTED, font=(FONT_FAMILY, 9)).pack(side="left", padx=(12, 0), pady=(4, 0))

        # === Top : 2 cards ===
        top = tk.Frame(self.root, bg=BG, padx=24)
        top.pack(fill="x")

        self.bot_card, self.bot_status_dot, self.bot_status_lbl, self.bot_pid_lbl = \
            self._make_card(top, "🤖  Bot Discord", "BOT",
                            self.start_bot, self.stop_bot, self.restart_bot)
        self.bot_card.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.web_card, self.web_status_dot, self.web_status_lbl, self.web_pid_lbl = \
            self._make_card(top, "🌐  Dashboard Web", "WEB",
                            self.start_web, self.stop_web, self.restart_web)
        self.web_card.pack(side="left", fill="both", expand=True, padx=(8, 0))

        # === Toolbar utilitaires ===
        toolbar = tk.Frame(self.root, bg=BG, padx=24, pady=14)
        toolbar.pack(fill="x")

        StyledButton(toolbar, "🚀  Tout demarrer", command=self.start_all,
                      accent=True, width=160).pack(side="left", padx=(0, 6))
        StyledButton(toolbar, "⏹  Tout arreter", command=self.stop_all,
                      width=140).pack(side="left", padx=6)
        StyledButton(toolbar, "🌐  Ouvrir Dashboard",
                      command=lambda: webbrowser.open(DASHBOARD_URL),
                      width=170).pack(side="left", padx=6)
        StyledButton(toolbar, "⬇  Git Pull", command=self.git_pull,
                      width=110).pack(side="left", padx=6)
        StyledButton(toolbar, "🗑  Clear logs", command=self.clear_logs,
                      width=120).pack(side="left", padx=6)
        StyledButton(toolbar, "✕  Quitter", command=self.quit,
                      width=100).pack(side="right")

        # === Console PowerShell embed ===
        cons_frame = tk.Frame(self.root, bg=BG, padx=24)
        cons_frame.pack(fill="x")
        cons_lbl = tk.Frame(cons_frame, bg=BG)
        cons_lbl.pack(fill="x", pady=(0, 4))
        tk.Label(cons_lbl, text="Console", bg=BG, fg=TEXT_MUTED,
                  font=(FONT_FAMILY, 10, "bold")).pack(side="left")
        tk.Label(cons_lbl, text="• Ctrl+Enter = execute • Enter = nouvelle ligne • PowerShell",
                  bg=BG, fg=TEXT_MUTED, font=(FONT_FAMILY, 9)).pack(side="left", padx=(8, 0))

        cons_row = tk.Frame(cons_frame, bg=BORDER, bd=1)
        cons_row.pack(fill="x")
        cons_inner = tk.Frame(cons_row, bg=BG_PANEL_2)
        cons_inner.pack(fill="x")
        self.cons_input = tk.Text(cons_inner, height=4, bg=BG_PANEL_2, fg=TEXT,
                                    insertbackground=TEXT, relief="flat", bd=0,
                                    font=("Cascadia Mono", 10) if "Cascadia Mono" in tkfont.families() else ("Consolas", 10),
                                    wrap="word", padx=10, pady=8)
        self.cons_input.pack(side="left", fill="both", expand=True)
        # Enter = newline (defaut). Ctrl+Enter = execute.
        self.cons_input.bind("<Control-Return>", lambda e: (self._console_execute(), "break")[1])
        # Bouton execute
        btn_zone = tk.Frame(cons_inner, bg=BG_PANEL_2, padx=8, pady=8)
        btn_zone.pack(side="left", fill="y")
        StyledButton(btn_zone, "▶ Run", command=self._console_execute,
                      accent=True, width=80, height=36).pack()

        # === Logs ===
        log_frame = tk.Frame(self.root, bg=BG, padx=24)
        log_frame.pack(fill="both", expand=True, pady=(8, 24))

        log_header = tk.Frame(log_frame, bg=BG)
        log_header.pack(fill="x")
        tk.Label(log_header, text="Logs en direct", bg=BG, fg=TEXT_MUTED,
                  font=(FONT_FAMILY, 10, "bold")).pack(side="left")
        tk.Label(log_header, text="• prefixe = source (BOT/WEB/launcher)",
                  bg=BG, fg=TEXT_MUTED, font=(FONT_FAMILY, 9)).pack(side="left", padx=(8, 0))

        log_container = tk.Frame(log_frame, bg=BORDER, bd=1)
        log_container.pack(fill="both", expand=True, pady=(6, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_container, bg=BG_PANEL_2, fg=TEXT,
            font=("Cascadia Mono", 9) if "Cascadia Mono" in tkfont.families() else ("Consolas", 9),
            wrap="word", relief="flat", bd=0,
            insertbackground=TEXT, padx=10, pady=8,
            selectbackground=ACCENT_DK,
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        # Tags couleur logs
        self.log_text.tag_config("BOT",      foreground="#79c0ff")
        self.log_text.tag_config("WEB",      foreground="#a5d6ff")
        self.log_text.tag_config("info",     foreground=ACCENT)
        self.log_text.tag_config("warn",     foreground=ORANGE)
        self.log_text.tag_config("err",      foreground=RED)
        self.log_text.tag_config("launcher", foreground=TEXT_MUTED)

    def _make_card(self, parent, title, name, start_cmd, stop_cmd, restart_cmd):
        outer = tk.Frame(parent, bg=BORDER, bd=1)
        inner = tk.Frame(outer, bg=BG_PANEL, padx=18, pady=16)
        inner.pack(fill="both", expand=True)

        # Header row
        head = tk.Frame(inner, bg=BG_PANEL)
        head.pack(fill="x")
        tk.Label(head, text=title, bg=BG_PANEL, fg=TEXT,
                  font=(FONT_FAMILY, 13, "bold")).pack(side="left")

        # Status row
        status_row = tk.Frame(inner, bg=BG_PANEL)
        status_row.pack(fill="x", pady=(10, 0))
        dot = tk.Label(status_row, text="●", bg=BG_PANEL, fg=RED,
                        font=(FONT_FAMILY, 14))
        dot.pack(side="left")
        status_lbl = tk.Label(status_row, text="Arrete", bg=BG_PANEL, fg=TEXT,
                                font=(FONT_FAMILY, 11, "bold"))
        status_lbl.pack(side="left", padx=(6, 0))
        pid_lbl = tk.Label(status_row, text="", bg=BG_PANEL, fg=TEXT_MUTED,
                            font=(FONT_FAMILY, 9))
        pid_lbl.pack(side="left", padx=(10, 0))

        # Buttons row
        btn_row = tk.Frame(inner, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=(16, 0))
        StyledButton(btn_row, "▶  Demarrer", command=start_cmd, accent=True,
                      width=110, height=34).pack(side="left", padx=(0, 6))
        StyledButton(btn_row, "■  Arreter", command=stop_cmd,
                      width=100, height=34).pack(side="left", padx=6)
        StyledButton(btn_row, "↻  Redemarrer", command=restart_cmd,
                      width=120, height=34).pack(side="left", padx=6)

        return outer, dot, status_lbl, pid_lbl

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
        def _run():
            try:
                res = subprocess.run(
                    ["git", "pull"], cwd=REPO_DIR,
                    capture_output=True, text=True, timeout=30,
                )
                self.log_queue.put(("info", f"git pull rc={res.returncode}"))
                for line in (res.stdout + res.stderr).splitlines():
                    self.log_queue.put(("launcher", line))
            except Exception as e:
                self.log_queue.put(("err", f"git pull err: {e}"))
        threading.Thread(target=_run, daemon=True).start()

    def _console_execute(self):
        cmd = self.cons_input.get("1.0", "end").strip()
        if not cmd:
            return
        self.cons_input.delete("1.0", "end")
        self.log_queue.put(("info", f"$ {cmd}"))

        def _run():
            try:
                creationflags = 0
                if os.name == "nt":
                    creationflags = 0x08000000  # CREATE_NO_WINDOW
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                # PowerShell -Command pour supporter cd, env, multi-cmd
                proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-Command", cmd],
                    cwd=REPO_DIR,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", bufsize=1, text=True,
                    creationflags=creationflags, env=env,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.log_queue.put(("launcher", line))
                rc = proc.wait()
                tag = "info" if rc == 0 else "err"
                self.log_queue.put((tag, f"[exit code {rc}]"))
            except Exception as e:
                self.log_queue.put(("err", f"console err: {type(e).__name__}: {e}"))
        threading.Thread(target=_run, daemon=True).start()


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
        self.log_queue.put(("info", f"[launcher] {msg}"))

    def _poll_logs(self):
        try:
            while True:
                tag, line = self.log_queue.get_nowait()
                # Tag dispatch : valid tag names dans log_text
                if tag not in ("BOT", "WEB", "info", "warn", "err", "launcher"):
                    tag = "launcher"
                self.log_text.config(state="normal")
                self.log_text.insert("end", line + "\n", tag)
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_logs)

    def _poll_status(self):
        if self.bot.is_running():
            self.bot_status_dot.config(fg=GREEN)
            self.bot_status_lbl.config(text="En cours", fg=TEXT)
            self.bot_pid_lbl.config(text=f"pid {self.bot.proc.pid}")
        else:
            self.bot_status_dot.config(fg=RED)
            self.bot_status_lbl.config(text="Arrete", fg=TEXT_MUTED)
            self.bot_pid_lbl.config(text="")
        if self.web.is_running():
            self.web_status_dot.config(fg=GREEN)
            self.web_status_lbl.config(text="En cours", fg=TEXT)
            self.web_pid_lbl.config(text=f"pid {self.web.proc.pid}")
        else:
            self.web_status_dot.config(fg=RED)
            self.web_status_lbl.config(text="Arrete", fg=TEXT_MUTED)
            self.web_pid_lbl.config(text="")
        self.root.after(800, self._poll_status)


def main():
    if not (os.path.exists(".env.dev") or os.path.exists(".env")):
        print("ATTENTION : ni .env.dev ni .env trouve dans", REPO_DIR)
    root = tk.Tk()
    app = DevLauncherApp(root)
    root.protocol("WM_DELETE_WINDOW", app.quit)
    root.mainloop()


if __name__ == "__main__":
    main()
