import json
import os
import shutil
import sqlite3
import time
import getpass
import urllib.error
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
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


def _rel_or_abs(path):
    try:
        return os.path.relpath(path, ROOT_DIR)
    except ValueError:
        return str(path)


def _firefox_roots(home_path):
    home = Path(home_path)
    return [
        ("classic", home / ".mozilla" / "firefox"),
        ("snap", home / "snap" / "firefox" / "common" / ".mozilla" / "firefox"),
        ("flatpak", home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox"),
    ]


def _firefox_profiles(home_path):
    profiles = []
    for source, root in _firefox_roots(home_path):
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            cookies = child / "cookies.sqlite"
            prefs = child / "prefs.js"
            if not cookies.exists() and not prefs.exists():
                continue
            profiles.append({
                "source": source,
                "path": str(child),
                "cookies_exists": cookies.exists(),
                "cookies_modified_at": cookies.stat().st_mtime if cookies.exists() else None,
            })
    return profiles


def best_firefox_cookie_profile(home_path=None):
    home = Path(home_path) if home_path is not None else Path.home()
    profiles = [p for p in _firefox_profiles(home) if p["cookies_exists"]]
    if not profiles:
        return None
    profiles.sort(key=lambda p: p.get("cookies_modified_at") or 0, reverse=True)
    return profiles[0]["path"]


def _process_user(pid):
    if not pid:
        return None
    try:
        import psutil
        return psutil.Process(int(pid)).username()
    except Exception:
        return None


def _bgutil_status(url, timeout):
    if not url:
        return {"configured": False, "reachable": False, "url": None}
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {
                "configured": True,
                "reachable": True,
                "url": url,
                "status": getattr(resp, "status", None),
            }
    except urllib.error.HTTPError as e:
        return {
            "configured": True,
            "reachable": True,
            "url": url,
            "status": e.code,
        }
    except Exception as e:
        return {
            "configured": True,
            "reachable": False,
            "url": url,
            "error": type(e).__name__,
        }


def youtube_diagnostics(env=None, home_path=None, bot_state=None, check_bgutil=True, bgutil_timeout=0.5):
    env = env or os.environ
    home = Path(home_path) if home_path is not None else Path.home()
    use_firefox = str(env.get("YT_USE_FIREFOX_COOKIES", "1")) == "1"
    bgutil_url = env.get("BGUTIL_POT_URL", "http://127.0.0.1:4416")
    cookies_path = ROOT_DIR / "cookies.txt"
    cookies = file_info(cookies_path)
    cookies["path"] = _rel_or_abs(cookies["path"])
    profiles = _firefox_profiles(home)
    firefox_cookies_accessible = any(p["cookies_exists"] for p in profiles)

    firefox_bins = {
        "firefox": shutil.which("firefox"),
        "firefox-esr": shutil.which("firefox-esr"),
        "flatpak": shutil.which("flatpak"),
        "snap": shutil.which("snap"),
    }

    warnings = []
    if use_firefox and not firefox_cookies_accessible:
        warnings.append("firefox_cookies_missing")
    if not use_firefox and not cookies["exists"]:
        warnings.append("cookies_txt_missing")

    bot_user = _process_user((bot_state or {}).get("pid")) if bot_state else None
    web_user = getpass.getuser()
    if bot_user and web_user and bot_user != web_user:
        warnings.append("bot_web_user_mismatch")

    if use_firefox:
        effective_mode = "firefox"
    elif cookies["exists"]:
        effective_mode = "cookies.txt"
    else:
        effective_mode = "bgutil_only"

    return {
        "effective_mode": effective_mode,
        "yt_use_firefox_cookies": use_firefox,
        "web_user": web_user,
        "bot_user": bot_user,
        "home": str(home),
        "firefox_bins": firefox_bins,
        "firefox_roots": [
            {"source": source, "path": str(root), "exists": root.exists()}
            for source, root in _firefox_roots(home)
        ],
        "firefox_profiles": profiles,
        "firefox_cookies_accessible": firefox_cookies_accessible,
        "cookies_txt": cookies,
        "bgutil": _bgutil_status(bgutil_url, bgutil_timeout) if check_bgutil else {
            "configured": bool(bgutil_url),
            "reachable": None,
            "url": bgutil_url,
            "skipped": True,
        },
        "warnings": warnings,
    }
