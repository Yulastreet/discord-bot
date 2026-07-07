"""Tracker pour jobs d'import cards en background.

Stocke etat des jobs in-memory pour permettre polling progression.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable


_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def create_job(label: str = "") -> str:
    """Cree un nouveau job, retourne job_id."""
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id":          job_id,
            "label":       label,
            "status":      "running",  # running | done | error
            "progress":    0,           # 0-100
            "current":     0,
            "total":       0,
            "stats":       {},
            "error":       None,
            "started_at":  time.time(),
            "finished_at": None,
        }
    return job_id


def update_job(job_id: str, **kwargs) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j: return
        j.update(kwargs)
        if "current" in kwargs and j.get("total"):
            j["progress"] = min(100, int(j["current"] * 100 / j["total"]))


def finish_job(job_id: str, stats: dict | None = None, error: str | None = None) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j: return
        j["status"] = "error" if error else "done"
        j["progress"] = 100 if not error else j.get("progress", 0)
        j["error"] = error
        if stats is not None:
            j["stats"] = stats
        j["finished_at"] = time.time()


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


def cleanup_old_jobs(max_age_sec: int = 3600) -> None:
    """Supprime jobs termines depuis plus de max_age_sec."""
    now = time.time()
    with _LOCK:
        to_del = [jid for jid, j in _JOBS.items()
                    if j.get("finished_at") and (now - j["finished_at"]) > max_age_sec]
        for jid in to_del:
            _JOBS.pop(jid, None)


def run_async(job_label: str, fn: Callable, *args, **kwargs) -> str:
    """Lance fn en thread separe avec job tracking.
    fn doit accepter kwarg 'progress_cb=callable(current, total)' (optionnel).
    Retourne job_id."""
    job_id = create_job(label=job_label)

    def _progress_cb(current: int, total: int):
        update_job(job_id, current=current, total=total)

    def _wrapper():
        try:
            kwargs["progress_cb"] = _progress_cb
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and "error" in result:
                finish_job(job_id, error=str(result["error"]))
            else:
                finish_job(job_id, stats=result if isinstance(result, dict) else {})
        except Exception as e:
            import traceback
            traceback.print_exc()
            finish_job(job_id, error=f"{type(e).__name__}: {e}")

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    return job_id
