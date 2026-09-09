"""Authenticated console pages (server-rendered; actions go through /api)."""

from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required

from models import (AccessKey, AuditRow, Certificate, CustodyEvent, Dataset,
                    EventRow, Listing, LoginRow, MEMBERSHIP_TYPES, PARTY_TYPES,
                    Party, PartyMember, REGION_NAMES, REGIONS, DEFAULT_REGION,
                    SupportTicket, TransferAgreement)
from extensions import db
from settings import SHOW_ACCESS_KEYS, SHOW_BILLING
from services import access, billing, certificates as certsvc, scheduler
from services.protocol import SUGGESTED_BUYERS, ProtocolHub

bp = Blueprint("console", __name__, url_prefix="/console")


@bp.app_context_processor
def _console_globals():
    """Values every console template needs (top bar, drawers, region)."""
    if not current_user.is_authenticated:
        return {}
    region = session.get("region", DEFAULT_REGION)
    # The activity feed is per-account: one party's operations are not another
    # party's business, however small this deployment is.
    events_q = EventRow.query.order_by(EventRow.created_at.desc())
    if not current_user.is_admin:
        events_q = events_q.filter(
            EventRow.dataset_id.in_(access.visible_event_dataset_ids()))
    unread = events_q.limit(8).all()
    ds = [(d.id, d.name) for d in access.datasets()]
    inbox = access.inbox()
    return {
        "show_access_keys": SHOW_ACCESS_KEYS,
        "show_billing": SHOW_BILLING,
        "regions": REGIONS,
        "region_names": REGION_NAMES,
        "current_region": region,
        "current_region_name": REGION_NAMES.get(region, region),
        "topbar_events": unread,
        "acting_as": current_user.acting_as,
        "is_admin": current_user.is_admin,
        "my_party": current_user.party,
        "inbox_count": len(inbox["incoming"]) if not current_user.is_admin
                       else len(inbox["pending"]),
        "search_datasets": [
            {"label": name, "href": url_for("console.storage_detail", dataset_id=i),
             "kind": "Dataset"} for i, name in ds
        ],
    }


def _mark_stale(datasets: list[Dataset]) -> None:
    """Datasets whose in-memory crypto state died with a previous server run."""
    hub = ProtocolHub.instance()
    for d in datasets:
        if d.status == "active" and d.id not in hub.spaces:
            d.status = "stale"


def _all_datasets() -> list[Dataset]:
    """The datasets this account may see, with lost protection state marked."""
    datasets = access.datasets()
    _mark_stale(datasets)
    return datasets


def _greeting() -> str:
    h = datetime.now().hour
    if h < 12:
        return "Good morning"
    if h < 17:
        return "Good afternoon"
    return "Good evening"


def _security_score(datasets, n_failed_7d: int) -> tuple[int, list[str]]:
    """A plain-language security posture score for the Workbench."""
    score = 100
    notes = []
    stale = [d for d in datasets if d.status == "stale"]
    if stale:
        score -= min(20, 5 * len(stale))
        notes.append(f"{len(stale)} dataset(s) need re-protection after the last "
                     "service restart")
    unscheduled = [d for d in datasets
                   if d.status == "active" and not d.audit_every_min]
    if unscheduled:
        score -= min(15, 3 * len(unscheduled))
        notes.append(f"{len(unscheduled)} active dataset(s) have no verification "
                     "schedule")
    if n_failed_7d:
        score -= min(25, 8 * n_failed_7d)
        notes.append(f"{n_failed_7d} failed verification(s) in the last 7 days")
    never_audited = [d for d in datasets
                     if d.status == "active" and not d.audits.count()]
    if never_audited:
        score -= 10
        notes.append(f"{len(never_audited)} dataset(s) have never been verified")
    if not notes:
        notes.append("No open findings — keep verification schedules on")
    return max(35, score), notes


@bp.get("/")
@login_required
def dashboard():
    datasets = _all_datasets()
    box = access.inbox()
    my_agreements = access.agreements()
    # An account's workbench reports its own holdings; the registry's reports
    # the whole platform, which is the one place that view is legitimate.
    audits = AuditRow.query.order_by(AuditRow.created_at.desc())
    if not access.is_admin():
        audits = audits.filter(AuditRow.dataset_id.in_([d.id for d in datasets]))
    events_q = EventRow.query.order_by(EventRow.created_at.desc())
    if not access.is_admin():
        events_q = events_q.filter(
            EventRow.dataset_id.in_(access.visible_event_dataset_ids()))
    recent_events = events_q.limit(9).all()
    total = audits.count()
    passed = audits.filter(AuditRow.passed.is_(True)).count()
    transfers = sum(1 for a in my_agreements if a.status == "completed")
    total_blocks = sum(d.n_blocks for d in datasets if d.status == "active")
    total_bytes = sum(d.total_bytes for d in datasets if d.status == "active")
    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    n_failed_7d = audits.filter(AuditRow.passed.is_(False),
                                AuditRow.created_at >= week_ago).count()
    score, score_notes = _security_score(datasets, n_failed_7d)
    cycle = billing.current_cycle() if SHOW_BILLING else None
    open_tickets = SupportTicket.query.filter_by(status="open").count()
    return render_template(
        "console/overview.html",
        active_nav="overview",
        greeting=_greeting(),
        datasets=datasets,
        n_active=sum(1 for d in datasets if d.status == "active"),
        n_stale=sum(1 for d in datasets if d.status == "stale"),
        n_certificates=len(access.certificates()),
        n_open_agreements=len(box["pending"]),
        inbox=box["incoming"],
        outgoing=box["outgoing"],
        settled_value=sum(a.consideration for a in my_agreements
                          if a.status == "completed"),
        n_audits=total,
        n_passed=passed,
        n_failed=total - passed,
        pass_rate=(100.0 * passed / total) if total else None,
        n_transfers=transfers,
        total_blocks=total_blocks,
        total_bytes=total_bytes,
        recent_audits=audits.limit(6).all(),
        recent_events=recent_events,
        score=score,
        score_notes=score_notes,
        cycle=cycle,
        n_keys=(AccessKey.query.filter_by(user_id=current_user.id).count()
                if SHOW_ACCESS_KEYS else 0),
        n_parties=Party.query.count(),
        n_people=PartyMember.query.count(),
        open_tickets=open_tickets,
    )


def _party_options() -> list[dict]:
    """The register, in the shape the upload and ownership forms need."""
    out = []
    for p in Party.query.order_by(Party.display_name).all():
        out.append({
            "id": p.id,
            "name": p.display_name,
            "type": p.party_type,
            "type_label": p.type_label,
            "verified": bool(p.verified),
            "owner_ids": p.owner_ids,
            "members": len(p.members),
        })
    return out


@bp.get("/storage")
@login_required
def storage():
    hub = ProtocolHub.instance()
    datasets = _all_datasets()
    holders = {p.id: p for p in Party.query.all()}
    return render_template(
        "console/storage.html",
        active_nav="storage",
        onboarded=datasets,
        n_active=sum(1 for d in datasets if d.status == "active"),
        n_stored=sum(1 for d in datasets if d.status == "stored"),
        catalog=hub.catalog(),
        parties=_party_options(),
        holders=holders,
        # Everything whose keys are not in memory right now — one press seals
        # them all again rather than making somebody walk the list.
        n_unprotected=sum(1 for d in datasets
                          if d.id not in hub.spaces
                          and d.source in ("upload", "catalog")),
    )


@bp.get("/storage/<int:dataset_id>")
@login_required
def storage_detail(dataset_id: int):
    dataset = Dataset.query.get_or_404(dataset_id)
    _mark_stale([dataset])
    hub = ProtocolHub.instance()
    live = hub.space_info(dataset_id)
    audits = (AuditRow.query.filter_by(dataset_id=dataset_id)
              .order_by(AuditRow.created_at.desc()).limit(30).all())
    events = (EventRow.query.filter_by(dataset_id=dataset_id)
              .order_by(EventRow.created_at.desc()).limit(30).all())
    buyer_name, buyer_owners = SUGGESTED_BUYERS.get(
        dataset.name, ("acquiring-organization", ["acquiring-organization"]))
    n_audits = AuditRow.query.filter_by(dataset_id=dataset_id).count()
    n_pass = AuditRow.query.filter_by(dataset_id=dataset_id, passed=True).count()
    open_offers = (TransferAgreement.query
                   .filter(TransferAgreement.dataset_id == dataset_id,
                           TransferAgreement.status == "awaiting_seller")
                   .order_by(TransferAgreement.consideration.desc()).all())
    return render_template(
        "console/storage_detail.html",
        active_nav="storage",
        dataset=dataset,
        holder=(db.session.get(Party, dataset.owner_party_id)
                if dataset.owner_party_id else None),
        live=live,
        audits=audits,
        events=events,
        n_audits=n_audits,
        n_pass=n_pass,
        suggested_buyer=buyer_name,
        suggested_buyer_owners=", ".join(buyer_owners),
        open_offers=open_offers,
        next_run_s=scheduler.next_run_s(dataset_id, dataset.audit_every_min),
    )


@bp.get("/integrity")
@login_required
def integrity():
    datasets = _all_datasets()
    rows = AuditRow.query.order_by(AuditRow.created_at.desc()).limit(100).all()
    total = AuditRow.query.count()
    passed = AuditRow.query.filter_by(passed=True).count()
    scheduled = [d for d in datasets if d.status == "active" and d.audit_every_min]
    return render_template(
        "console/integrity.html",
        active_nav="integrity",
        audits=rows,
        n_total=total,
        n_passed=passed,
        n_failed=total - passed,
        datasets=[d for d in datasets if d.status == "active"],
        scheduled=scheduled,
        next_runs={d.id: scheduler.next_run_s(d.id, d.audit_every_min)
                   for d in scheduled},
        names={d.id: d for d in datasets},
    )


@bp.get("/transfers")
@login_required
def transfers():
    q = (EventRow.query
         .filter(EventRow.kind.in_(["add_owner", "revoke_owner", "transfer"]))
         .order_by(EventRow.created_at.desc()))
    if not access.is_admin():
        q = q.filter(EventRow.dataset_id.in_(access.visible_event_dataset_ids()))
    events = q.limit(100).all()
    datasets = _all_datasets()
    return render_template(
        "console/transfers.html",
        active_nav="transfers",
        events=events,
        datasets=[d for d in datasets if d.status == "active"],
        names={d.id: d for d in datasets},
        buyers=SUGGESTED_BUYERS,
    )


@bp.get("/security")
@login_required
def security():
    validations = (EventRow.query.filter_by(kind="attack")
                   .order_by(EventRow.created_at.desc()).limit(20).all())
    return render_template("console/security.html", active_nav="security",
                           validations=validations)


@bp.get("/monitor")
@login_required
def monitor():
    return render_template("console/monitor.html", active_nav="monitor")


@bp.get("/activity")
@login_required
def activity():
    kind = request.args.get("kind", "")
    q = EventRow.query.order_by(EventRow.created_at.desc())
    if not access.is_admin():
        q = q.filter(EventRow.dataset_id.in_(access.visible_event_dataset_ids()))
    if kind:
        q = q.filter_by(kind=kind)
    events = q.limit(300).all()
    datasets = access.datasets()
    kinds = [k[0] for k in
             EventRow.query.with_entities(EventRow.kind).distinct().all()]
    return render_template(
        "console/activity.html",
        active_nav="activity",
        events=events,
        names={d.id: d for d in datasets},
        kinds=sorted(kinds),
        kind=kind,
    )


@bp.get("/billing")
@login_required
def billing_page():
    # Hidden from the console for now; the billing engine itself is untouched.
    # Flip ATTESTRA_BILLING=1 to bring the section back.
    if not SHOW_BILLING:
        abort(404)
    return render_template(
        "console/billing.html",
        active_nav="billing",
        cycle=billing.current_cycle(),
        invoices=billing.invoice_history(),
    )


@bp.get("/keys")
@login_required
def access_keys():
    # Hidden from the console for now; the API and the keys themselves still
    # work. Flip ATTESTRA_ACCESS_KEYS=1 to bring the page back.
    if not SHOW_ACCESS_KEYS:
        abort(404)
    keys = (AccessKey.query.filter_by(user_id=current_user.id)
            .order_by(AccessKey.created_at.desc()).all())
    return render_template("console/access_keys.html", active_nav="keys",
                           keys=keys,
                           # The examples are copy-pasteable against whatever
                           # host this deployment is actually answering on.
                           api_base=request.host_url.rstrip("/"))


@bp.get("/support")
@login_required
def support():
    tickets = (SupportTicket.query.filter_by(user_id=current_user.id)
               .order_by(SupportTicket.created_at.desc()).all())
    return render_template("console/support.html", active_nav="support",
                           tickets=tickets)


@bp.get("/settings")
@login_required
def settings_page():
    logins = (LoginRow.query.filter_by(user_id=current_user.id)
              .order_by(LoginRow.created_at.desc()).limit(12).all())
    return render_template("console/settings.html", active_nav="settings",
                           logins=logins)


# Legacy paths from earlier iterations keep working.

# ----------------------------------------------------------------------
# Ownership registry: parties, agreements, certificates, listings
# ----------------------------------------------------------------------

@bp.get("/parties")
@login_required
def parties():
    from models import User

    rows = Party.query.order_by(Party.display_name).all()
    accounts = {u.party_id: u for u in User.query.filter(
        User.party_id.isnot(None)).all()}
    holdings: dict[int, list[Dataset]] = {}
    for d in Dataset.query.all():
        if d.owner_party_id:
            holdings.setdefault(d.owner_party_id, []).append(d)
    return render_template(
        "console/parties.html",
        active_nav="parties",
        parties=rows,
        party_types=PARTY_TYPES,
        editable={p.id for p in rows if access.can_act_for(p.id)},
        accounts=accounts,
        membership_types=sorted(MEMBERSHIP_TYPES),
        holdings=holdings,
        n_verified=sum(1 for p in rows if p.verified),
        n_people=sum(len(p.members) for p in rows),
    )


@bp.get("/parties/<int:party_id>")
@login_required
def party_detail(party_id: int):
    """One party in full: who is inside it, and what it holds.

    For a group this is the page that answers the question its owner actually
    has — not just "who is a member" but "whose keys are on which dataset",
    because in this scheme those are different facts and only the second one
    decides who can prove possession.
    """
    from models import User

    party = Party.query.get_or_404(party_id)
    hub = ProtocolHub.instance()
    holdings = Dataset.query.filter_by(owner_party_id=party.id).all()
    _mark_stale(holdings)

    # For each dataset the party holds, which members' keys are actually on it.
    matrix = []
    for d in holdings:
        live = hub.space_info(d.id)
        owners = set(live["owners"] if live else d.owners)
        matrix.append({
            "dataset": d,
            "live": live is not None,
            "owner_ids": sorted(owners),
            "members": [{"member": m, "holds_key": m.slug in owners}
                        for m in party.members],
            # An owner id on the dataset that no longer matches a member of the
            # group: worth showing, because it is a key nobody on the roll holds.
            "unmatched": sorted(owners - {m.slug for m in party.members}),
        })

    return render_template(
        "console/party_detail.html",
        active_nav="parties",
        p=party,
        account=User.query.filter_by(party_id=party.id).first(),
        holdings=holdings,
        matrix=matrix,
        agreements=[a for a in access.agreements() if a.involves(party.id)],
        can_manage=access.can_act_for(party.id),
        is_owner_account=access.is_party(party.id),
    )


@bp.get("/agreements")
@login_required
def agreements():
    rows = access.agreements()
    certs = {c.agreement_id: c for c in Certificate.query.all()}
    box = access.inbox()

    # Offers grouped by the asset they are for, so a holder compares them
    # against each other rather than one at a time down a list.
    groups: dict[int, dict] = {}
    for a in rows:
        if a.status != "awaiting_seller" or not a.is_bid:
            continue
        if not access.can_manage_dataset(a.dataset):
            continue
        g = groups.setdefault(a.dataset_id,
                              {"dataset": a.dataset, "offers": []})
        g["offers"].append(a)
    offer_groups = sorted(groups.values(), key=lambda g: g["dataset"].name)
    grouped_ids = set()
    for g in offer_groups:
        g["offers"].sort(key=lambda a: a.consideration, reverse=True)
        g["best"] = g["offers"][0] if g["offers"] else None
        grouped_ids.update(a.id for a in g["offers"])
    # Anything already shown against its asset above is not repeated in the
    # queue below; what remains is the work that has no competing offer.
    box["incoming"] = [a for a in box["incoming"] if a.id not in grouped_ids]
    return render_template(
        "console/agreements.html",
        active_nav="agreements",
        agreements=rows,
        certs=certs,
        incoming=box["incoming"],
        outgoing=box["outgoing"],
        pending=box["pending"],
        offer_groups=offer_groups,
        n_offers=sum(len(g["offers"]) for g in offer_groups),
        datasets=[{"id": d.id, "name": d.name,
                    "owner_party_id": d.owner_party_id}
                   for d in _all_datasets() if d.status == "active"],
        parties=[{"id": p.id, "name": p.display_name, "type": p.type_label,
                  "verified": bool(p.verified)}
                 for p in Party.query.order_by(Party.display_name).all()],
        my_party_id=access.party_id(),
        open_count=sum(1 for a in rows
                       if a.status in ("awaiting_seller", "awaiting_buyer")),
        completed_count=sum(1 for a in rows if a.status == "completed"),
        value_settled=sum(a.consideration for a in rows if a.status == "completed"),
    )


@bp.get("/agreements/<int:agreement_id>")
@login_required
def agreement_detail(agreement_id: int):
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    if not access.can_see_agreement(agreement):
        abort(404)
    cert = Certificate.query.filter_by(agreement_id=agreement.id).first()
    return render_template(
        "console/agreement_detail.html",
        active_nav="agreements",
        a=agreement,
        cert=cert,
        steps=_agreement_steps(agreement),
        # Signing is the party's own act; the registry's route is approval.
        can_sign_seller=(agreement.status == "awaiting_seller"
                         and access.is_party(agreement.seller_party_id)),
        can_sign_buyer=(agreement.status == "awaiting_buyer"
                        and access.is_party(agreement.buyer_party_id)),
        can_decline=(agreement.status in ("awaiting_seller", "awaiting_buyer")
                     and access.is_party(agreement.awaiting_party_id)),
        # The party that raised an instrument can revise or withdraw it while
        # the other side has not answered.
        can_revise=(agreement.status == "awaiting_seller" and agreement.is_bid
                    and access.is_party(agreement.buyer_party_id)),
        can_withdraw=(agreement.status in ("awaiting_seller", "awaiting_buyer")
                      and access.is_party(agreement.buyer_party_id if agreement.is_bid
                                          else agreement.seller_party_id)),
        can_approve=(access.is_admin()
                     and agreement.status in ("awaiting_seller", "awaiting_buyer")
                     and bool(agreement.seller_signed_at or agreement.buyer_signed_at)),
    )


def _agreement_steps(a: TransferAgreement) -> list[dict]:
    """The four states the instrument passes through, for the stepper."""
    dead = a.status in ("declined", "cancelled")

    def state(reached: bool, current: bool) -> str:
        if reached:
            return "done"
        if dead:
            return "failed"
        return "running" if current else ""

    return [
        {"title": "Terms raised",
         "note": f"Agreement {a.agreement_ref} created "
                 f"{a.created_at.strftime('%d %b %Y, %H:%M')}",
         "state": "done"},
        {"title": "Transferor signs",
         "note": (f"Signed {a.seller_signed_at:%d %b %Y, %H:%M}"
                  if a.seller_signed_at else "Awaiting the current holder"),
         "state": state(bool(a.seller_signed_at), a.status == "awaiting_seller")},
        {"title": "Transferee countersigns",
         "note": (f"Signed {a.buyer_signed_at:%d %b %Y, %H:%M}"
                  if a.buyer_signed_at else "Awaiting the acquiring party"),
         "state": state(bool(a.buyer_signed_at), a.status == "awaiting_buyer")},
        {"title": "Ownership moves, deed issued",
         "note": (f"Completed {a.completed_at:%d %b %Y, %H:%M}" if a.completed_at
                  else ("Declined by the transferee" if a.status == "declined"
                        else "Runs automatically once both parties have signed")),
         "state": state(a.status == "completed", a.status == "executing")},
    ]


@bp.get("/certificates")
@login_required
def certificates_page():
    rows = access.certificates()
    # A transfer that is running right now has no deed yet, but the parties
    # should see that one is being produced rather than nothing at all.
    in_flight = [a for a in access.agreements() if a.status == "executing"]
    return render_template(
        "console/certificates.html",
        active_nav="certificates",
        certs=rows,
        in_flight=in_flight,
        n_valid=sum(1 for c in rows if c.status == "valid"),
    )


@bp.get("/certificates/<certificate_id>")
@login_required
def certificate_detail(certificate_id: str):
    cert = Certificate.query.filter_by(certificate_id=certificate_id).first_or_404()
    if not (access.is_admin() or access.can_see_agreement(cert.agreement)):
        abort(404)
    return render_template(
        "console/certificate_detail.html",
        active_nav="certificates",
        cert=cert,
        report=certsvc.verify(cert),
    )


@bp.get("/listings")
@login_required
def listings():
    rows = access.listings()
    return render_template(
        "console/listings.html",
        active_nav="listings",
        listings=rows,
        datasets=[{"id": d.id, "name": d.name, "region": d.region,
                    "owner_party_id": d.owner_party_id}
                   for d in _all_datasets() if d.status == "active"],
        parties=[{"id": p.id, "name": p.display_name, "type": p.type_label}
                 for p in Party.query.order_by(Party.display_name).all()],
        live=sum(1 for r in rows if r.status == "active"),
        sold=sum(1 for r in rows if r.status == "sold"),
        gross=sum(r.price for r in rows if r.status == "sold"),
    )

@bp.get("/datasets")
@login_required
def datasets():
    return redirect(url_for("console.storage"))


@bp.get("/datasets/<int:dataset_id>")
@login_required
def dataset_detail(dataset_id: int):
    return redirect(url_for("console.storage_detail", dataset_id=dataset_id))


@bp.get("/audits")
@login_required
def audits():
    return redirect(url_for("console.integrity"))


@bp.get("/ownership")
@login_required
def ownership():
    return redirect(url_for("console.transfers"))


@bp.get("/threats")
@login_required
def threats():
    return redirect(url_for("console.security"))


@bp.get("/analytics")
@login_required
def analytics():
    return redirect(url_for("console.monitor"))


@bp.get("/benchmarks")
@login_required
def benchmarks():
    return redirect(url_for("console.monitor"))


@bp.get("/documentation")
@login_required
def documentation():
    return redirect(url_for("console.dashboard"))
