import json
import os
import shutil
import sqlite3
import time
import getpass
import urllib.error
import urllib.request
import importlib.metadata
from pathlib import Path


# Module a ete deplace dans services/ : on remonte d'un cran pour pointer sur
# la racine du repo (bot_state.json, bot_database.db, backups/ y vivent).
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "bot_database.db"
BACKUP_DIR = ROOT_DIR / "backups"
BACKUP_DB_PATH = BACKUP_DIR / "bot_database_backup.db"
BACKUP_META_PATH = BACKUP_DIR / "bot_database_backup.json"


def file_info(path):
    p = Path(path)
    if not p.exists():
        return {
            "exists": False,
            "path": str(p),
            "size_bytes": None,
            "modified_at": None,
        }
    st = p.stat()
    return {
        "exists": True,
        "path": str(p),
        "size_bytes": st.st_size,
        "modified_at": st.st_mtime,
    }


def db_info(db_path=DB_PATH):
    info = file_info(db_path)
    info["path"] = os.path.relpath(info["path"], ROOT_DIR)
    return info


def create_db_backup(db_path=DB_PATH, backup_path=BACKUP_DB_PATH, meta_path=BACKUP_META_PATH):
    src = Path(db_path)
    dst = Path(backup_path)
    meta = Path(meta_path)
    if not src.exists():
        raise FileNotFoundError(str(src))

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")

    try:
        source = sqlite3.connect(str(src))
        target = sqlite3.connect(str(tmp))
        with target:
            source.backup(target)
        target.close()
        source.close()
    except sqlite3.DatabaseError:
        if "source" in locals():
            source.close()
        if "target" in locals():
            target.close()
        shutil.copy2(src, tmp)

    os.replace(tmp, dst)
    created_at = time.time()
    payload = {
        "ok": True,
        "file": os.path.relpath(dst, ROOT_DIR),
        "size_bytes": dst.stat().st_size,
        "created_at": created_at,
        "overwrites_previous": True,
    }
    meta.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def read_backup_meta(meta_path=BACKUP_META_PATH):
    meta = Path(meta_path)
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None


def system_metrics(root_path=ROOT_DIR):
    try:
        import psutil
    except ImportError:
        return {"available": False, "error": "psutil_not_installed"}

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(root_path))
    boot_time = getattr(psutil, "boot_time", lambda: None)()
    return {
        "available": True,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "ram": {
            "total": vm.total,
            "used": vm.used,
            "available": vm.available,
            "percent": vm.percent,
        },
        "disk": {
            "path": str(root_path),
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "boot_time": boot_time,
    }



# ===== Moteur musique (post-Lavalink, juin 2026) =====
# Stack actif : yt-dlp + ffmpeg + WARP (SOCKS5 :40000) + bgutil-pot HTTP (:4416)
# + Privoxy (HTTP :8118 bridge vers WARP). Toutes les pieces tournent en local sur le VPS.

def _http_check(url, timeout=1.0, proxy=None):
    """GET simple. Retourne dict {ok, status, error, ip(opt)}. Optionnellement via proxy HTTP."""
    try:
        if proxy:
            req = urllib.request.Request(url)
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(handler)
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(url, timeout=timeout)
        body = resp.read(256).decode("utf-8", errors="replace").strip()
        return {"ok": True, "status": resp.getcode(), "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": str(e)}
    except Exception as e:
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}"}


def _warp_status():
    """Lance `warp-cli status`. Renvoie {connected, status_text}."""
    warp = shutil.which("warp-cli")
    if not warp:
        return {"connected": False, "status_text": "warp-cli non installe", "installed": False}
    try:
        import subprocess
        r = subprocess.run([warp, "status"], capture_output=True, text=True, timeout=3)
        out = (r.stdout + r.stderr).strip()
        return {
            "connected": "Connected" in out,
            "status_text": out.splitlines()[0] if out else "",
            "installed": True,
        }
    except Exception as e:
        return {"connected": False, "status_text": f"{type(e).__name__}", "installed": True}


def _ytdlp_version():
    try:
        return importlib.metadata.version("yt-dlp")
    except Exception:
        return None


def music_engine_diagnostics(env=None, timeout=1.0):
    """Etat du stack musique : yt-dlp, ffmpeg, WARP, bgutil-pot HTTP, Privoxy."""
    env = env or os.environ
    bgutil_url = env.get("BGUTIL_POT_URL", "http://127.0.0.1:4416")
    privoxy_url = env.get("FFMPEG_HTTP_PROXY", "http://127.0.0.1:8118")
    yt_proxy = env.get("YT_PROXY", "socks5://127.0.0.1:40000")

    warp = _warp_status()
    bgutil = _http_check(bgutil_url.rstrip("/") + "/ping", timeout=timeout)
    # Privoxy : test via curl vers un endpoint qui revoit l'IP
    privoxy = _http_check("https://api.ipify.org", timeout=timeout, proxy=privoxy_url)

    warnings = []
    if not warp["connected"]:
        warnings.append("warp_disconnected")
    if not bgutil["ok"]:
        warnings.append("bgutil_pot_unreachable")
    if not privoxy["ok"]:
        warnings.append("privoxy_unreachable")

    return {
        "yt_dlp_version": _ytdlp_version(),
        "ffmpeg":         shutil.which("ffmpeg"),
        "warp":           warp,
        "bgutil_pot":     {
            "url":     bgutil_url,
            "ok":      bgutil["ok"],
            "status":  bgutil.get("status"),
            "version": bgutil.get("body"),  # /ping renvoie JSON avec version
            "error":   bgutil.get("error"),
        },
        "privoxy":        {
            "url":   privoxy_url,
            "ok":    privoxy["ok"],
            "exit_ip": privoxy.get("body") if privoxy["ok"] else None,
            "error": privoxy.get("error"),
        },
        "yt_proxy":       yt_proxy,
        "warnings":       warnings,
    }
