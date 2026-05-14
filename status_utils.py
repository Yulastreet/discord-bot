import json
import os
import shutil
import sqlite3
import time
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
