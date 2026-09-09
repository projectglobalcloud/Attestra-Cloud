"""
Minimal background-job registry.

Protocol operations on pure-Python pairings take seconds to minutes, far too
long for a request/response cycle. Each long operation runs in a daemon thread;
the browser polls /api/jobs/<id> and renders progress + a live log.

A job also writes itself to the database — once when it starts and once when it
ends — so the record of what the platform did outlives the process that did it.
The in-memory registry stays authoritative while a job is running; the table is
what remains after a restart or a redeploy.
"""

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"          # running | done | error
    progress: float = 0.0            # 0..1, estimated for monolithic ops
    message: str = ""
    log: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # Who this operation belongs to and what it is about, so the console can
    # show each account the work being done on its behalf — and only that.
    title: str = ""
    subject: str = ""
    href: str = ""
    party_ids: tuple = ()
    user_id: int | None = None

    def emit(self, line: str) -> None:
        self.log.append(line)

    @property
    def headline(self) -> str:
        return self.title or self.kind.replace(":", " ").title()

    def to_dict(self, with_log: bool = True) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "log": self.log if with_log else [],
            "result": self.result,
            "error": self.error,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1),
            "title": self.headline,
            "subject": self.subject,
            "href": self.href,
            "started_at": self.started_at,
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def recent(limit: int = 12) -> list[Job]:
    """Every operation this process has run, newest first."""
    with _lock:
        rows = list(_jobs.values())
    rows.sort(key=lambda j: j.started_at, reverse=True)
    return rows[:limit]


# Set by the app factory: jobs run in worker threads with no application
# context of their own, so they need the app to open one when they persist.
_app = None


def bind_app(app) -> None:
    global _app
    _app = app


# The protection job reports itself through its log rather than a stage list,
# so the same five steps are derived here — once, in one place, for both the
# live feed and the stored history.
PROTECTION_STEPS = [
    ("Chunk the file into blocks", "Loading"),
    ("Issue a signing key to every owner", "KeyGen"),
    ("Seal every block with a BLS12-381 tag", "TagGen: computing"),
    ("Hand blocks and tags to the storage provider", "Stored"),
    ("Publish the verification parameters", "Published"),
]


def stages_for(job: "Job") -> list:
    """The step list for this job, however it happens to report itself."""
    stages = job.result.get("stages")
    if stages:
        return stages
    if job.kind not in ("onboard", "protect"):
        return []
    text = " ".join(job.log)
    reached = -1
    for i, (_, needle) in enumerate(PROTECTION_STEPS):
        if needle in text:
            reached = i
    finished = job.status == "done"
    out = []
    for i, (label, _) in enumerate(PROTECTION_STEPS):
        state = ("done" if (finished or i < reached)
                 else ("failed" if job.status == "error" and i == reached
                       else ("running" if i == reached else "pending")))
        out.append({"key": f"protect-{i}", "label": label, "state": state,
                    "detail": ""})
    return out


def _persist(job: "Job") -> None:
    """Write this job's current state to the process history.

    Failing to record a process must never fail the process, so every error
    here is swallowed after being noted on the job itself.
    """
    if _app is None:
        return
    try:
        from extensions import db
        from models import ProcessRow, utcnow

        with _app.app_context():
            row = ProcessRow.query.filter_by(job_id=job.id).first()
            if row is None:
                row = ProcessRow(job_id=job.id)
                db.session.add(row)
            row.kind = job.kind
            row.title = job.headline[:200]
            row.subject = (job.subject or "")[:400]
            row.href = (job.href or "")[:200]
            row.status = job.status
            row.message = (job.message or "")[:300]
            row.error = (job.error or "")[:400]
            row.stages_json = json.dumps(stages_for(job))
            row.outcome_json = json.dumps(
                {k: v for k, v in job.result.items()
                 if k != "stages" and isinstance(v, (str, int, float, bool))})
            row.party_ids_json = json.dumps([p for p in job.party_ids if p])
            row.user_id = job.user_id
            row.duration_ms = round(
                ((job.finished_at or time.time()) - job.started_at) * 1000, 1)
            if job.status != "running":
                row.finished_at = utcnow()
            db.session.commit()
    except Exception as exc:      # noqa: BLE001 — recording is best-effort
        job.emit(f"(process history not written: {type(exc).__name__})")


def start(kind: str, target, *, title: str = "", subject: str = "",
          href: str = "", party_ids: tuple = (), user_id: int | None = None) -> Job:
    """Run target(job) in a daemon thread; target sets job.result.

    The metadata is what lets the console narrate the operation to the account
    it belongs to: what is being done, to what, and on whose behalf.
    """
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, subject=subject,
              href=href, party_ids=tuple(p for p in party_ids if p),
              user_id=user_id)
    with _lock:
        _jobs[job.id] = job

    _persist(job)

    def _run():
        try:
            target(job)
            job.status = "done"
            job.progress = 1.0
        except Exception as exc:  # surfaced to the UI, not swallowed
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.emit(traceback.format_exc(limit=4))
        finally:
            job.finished_at = time.time()
            _persist(job)

    threading.Thread(target=_run, daemon=True).start()
    return job
