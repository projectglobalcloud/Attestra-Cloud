"""
Continuous verification scheduler.

One daemon thread wakes every 30 seconds and runs an audit against every
active dataset whose owners enabled a verification schedule. Results land in
the same AuditRow/EventRow history as manual verifications, so the Monitor
charts and the Workbench feed pick them up with no extra plumbing.

Last-run times are kept in memory only: after a restart the protocol state is
gone anyway (datasets show as stale) until they are onboarded again.
"""

import threading
import time

_started = False
_lock = threading.Lock()
_last_run: dict[int, float] = {}


def start(app) -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(app,), daemon=True,
                     name="attestra-continuous-verification").start()


def _loop(app) -> None:
    while True:
        time.sleep(30)
        try:
            _tick(app)
        except Exception:
            # A failed cycle must never kill the scheduler thread.
            pass


def _tick(app) -> None:
    from extensions import db
    from models import AuditRow, Dataset, record_event
    from services.protocol import ProtocolHub

    hub = ProtocolHub.instance()
    now = time.time()
    with app.app_context():
        rows = Dataset.query.filter(Dataset.audit_every_min.isnot(None),
                                    Dataset.status == "active").all()
        for d in rows:
            if d.id not in hub.spaces:
                continue
            due = _last_run.get(d.id, 0) + d.audit_every_min * 60
            if now < due:
                continue
            _last_run[d.id] = now
            try:
                result = hub.audit(d.id, c=min(10, d.n_blocks or 10))
            except Exception:
                continue
            db.session.add(AuditRow(
                dataset_id=d.id,
                passed=result["passed"],
                challenged=result["challenged"],
                total_blocks=result["total_blocks"],
                detection_pct=result["detection_pct"],
                duration_ms=result["duration_ms"],
                note="scheduled",
            ))
            db.session.commit()
            record_event(
                "audit",
                f"Scheduled verification {'passed' if result['passed'] else 'FAILED'}: "
                f"{result['challenged']}/{result['total_blocks']} blocks in "
                f"{result['duration_ms']:.0f} ms",
                d.id,
            )


def next_run_s(dataset_id: int, every_min: int | None) -> int | None:
    """Seconds until the next scheduled verification, for display."""
    if not every_min:
        return None
    last = _last_run.get(dataset_id)
    if last is None:
        return 0
    return max(0, int(last + every_min * 60 - time.time()))
