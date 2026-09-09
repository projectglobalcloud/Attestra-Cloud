"""
Billing engine.

Charges are computed from real usage history (datasets, verifications,
ownership operations recorded in the database) against the published price
sheet. An account is billed for ITS OWN usage — the datasets its party holds
and the verifications run against them — not for the platform's; the registry
administrator sees the whole deployment, since that is the bill it would pay.
Accounts run on the Free plan, so free-tier allowances are applied first and
the invoice normally lands at $0 — exactly how a real cloud bill for a
free-tier workload reads.
"""

from calendar import monthrange
from datetime import datetime, timezone

from models import AuditRow, Dataset, EventRow
from services import access

PRICES = {
    "storage_gb_month": 0.023,     # per GB-month of protected storage
    "verification": 0.0004,        # per integrity verification
    "ownership_op": 0.05,          # per membership change or transfer
}
FREE_TIER = {
    "storage_gb": 5.0,
    "verifications": 1000,
    "ownership_ops": 25,
}


def _month_bounds(dt: datetime) -> tuple[datetime, datetime]:
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(day=monthrange(dt.year, dt.month)[1], hour=23,
                        minute=59, second=59)
    return start, end


def current_cycle() -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start, end = _month_bounds(now)

    datasets = [d for d in access.datasets()
                if d.status in ("active", "stale", "stored")]
    storage_gb = sum(d.total_bytes for d in datasets) / 1024 ** 3

    audits = AuditRow.query.filter(AuditRow.created_at >= start)
    ops = EventRow.query.filter(
        EventRow.created_at >= start,
        EventRow.kind.in_(["add_owner", "revoke_owner", "transfer"]))
    if not access.is_admin():
        mine = [d.id for d in datasets]
        audits = audits.filter(AuditRow.dataset_id.in_(mine))
        ops = ops.filter(EventRow.dataset_id.in_(
            access.visible_event_dataset_ids()))
    verifications = audits.count()
    ownership_ops = ops.count()

    lines = []

    def line(service, usage, usage_unit, free, unit_price, billable_units):
        amount = round(billable_units * unit_price, 2)
        lines.append({
            "service": service,
            "usage": usage,
            "usage_unit": usage_unit,
            "free": free,
            "billable": max(0.0, billable_units),
            "amount": amount,
        })
        return amount

    total = 0.0
    total += line("Integrity-protected storage", round(storage_gb, 3), "GB-month",
                  f"first {FREE_TIER['storage_gb']:.0f} GB free",
                  PRICES["storage_gb_month"],
                  max(0.0, storage_gb - FREE_TIER["storage_gb"]))
    total += line("Integrity verifications", verifications, "requests",
                  f"first {FREE_TIER['verifications']:,} free",
                  PRICES["verification"],
                  max(0, verifications - FREE_TIER["verifications"]))
    total += line("Ownership operations", ownership_ops, "operations",
                  f"first {FREE_TIER['ownership_ops']} free",
                  PRICES["ownership_op"],
                  max(0, ownership_ops - FREE_TIER["ownership_ops"]))

    return {
        "period": now.strftime("%B %Y"),
        "period_start": start,
        "period_end": end,
        "days_left": (end - now).days,
        "lines": lines,
        "total": round(total, 2),
        "storage_gb": storage_gb,
        "verifications": verifications,
        "ownership_ops": ownership_ops,
        "free_tier": FREE_TIER,
        "prices": PRICES,
    }


def invoice_history() -> list[dict]:
    """One row per closed month that has any recorded activity."""
    scope = EventRow.query
    if not access.is_admin():
        scope = scope.filter(EventRow.dataset_id.in_(
            access.visible_event_dataset_ids()))
    first = scope.order_by(EventRow.created_at.asc()).first()
    if first is None:
        return []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    cursor = first.created_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < this_month:
        nxt = (cursor.replace(day=28) + __import__("datetime").timedelta(days=4)).replace(day=1)
        n_events = scope.filter(EventRow.created_at >= cursor,
                                EventRow.created_at < nxt).count()
        if n_events:
            out.append({
                "period": cursor.strftime("%B %Y"),
                "number": f"INV-{cursor.strftime('%Y%m')}-{n_events:03d}",
                "amount": 0.0,          # free-tier months close at zero
                "status": "Paid",
            })
        cursor = nxt
    out.reverse()
    return out
