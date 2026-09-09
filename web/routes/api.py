"""JSON API for console actions and background-job polling."""

import hashlib
import re
import shutil
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from extensions import db
from models import (AccessKey, AuditRow, Certificate, CustodyEvent, Dataset,
                    EMAIL_REQUIRED_TYPES, EventRow, Listing, MEMBERSHIP_TYPES,
                    PARTY_TYPE_NAMES, Party, PartyMember, ProcessRow,
                    REGION_NAMES, DEFAULT_REGION, SupportTicket,
                    TransferAgreement, record_custody, record_event)
from services import (access, agreements as agreementsvc, jobs, preview,
                      registry)
from services.protocol import ProtocolHub
from settings import MAX_UPLOAD_BYTES, UPLOAD_DIR

BLOCK_SIZES = {65536, 262144, 1048576}
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,79}$")

bp = Blueprint("api", __name__, url_prefix="/api")


def _hub() -> ProtocolHub:
    return ProtocolHub.instance()


def _err(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


def _forbidden(what: str):
    """Refused because the account is not the party it would be acting as."""
    return _err(f"your account is not entitled to {what} — it acts for "
                f"{current_user.acting_as}", 403)


def _guard_dataset(dataset: Dataset, verb: str):
    if not access.can_manage_dataset(dataset):
        return _forbidden(f"{verb} {dataset.name}")
    return None


def _require_live(dataset: Dataset):
    """Make sure the dataset's protection state is in memory, rebuilding it.

    Keys live only in the running process, so a restart empties them while the
    row still says "active". Everything needed to seal the file again is on
    record, so rather than telling somebody their asset is unusable in the
    middle of a sale, it is restored here and the operation carries on. Only a
    dataset that genuinely cannot be rebuilt — its file gone, or far too large
    to re-seal inside a request — is refused.
    """
    hub = _hub()
    if dataset.id in hub.spaces:
        return None
    try:
        hub.ensure_live(dataset)
    except Exception as exc:
        if dataset.status == "active":
            dataset.status = "stale"
            db.session.commit()
        return _err(f"{dataset.name} is not currently under protection and could "
                    f"not be restored automatically: {exc}")
    if dataset.status != "active":
        dataset.status = "active"
        db.session.commit()
    record_event("onboard",
                 f"Protection state for {dataset.name} rebuilt automatically "
                 f"after a service restart", dataset.id)
    return None


@bp.get("/jobs/<job_id>")
@login_required
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return _err("no such job", 404)
    return jsonify({"ok": True, "job": job.to_dict()})


@bp.get("/processes")
@login_required
def processes():
    """Everything the platform is doing, or has just done, for this account.

    The console narrates its own work: a client watching this page sees the
    protection, the verification and the ownership transfer happen step by
    step — including the BLS12-381 signing each one performs — rather than a
    spinner followed by an announcement.
    """
    pid = access.party_id()
    out = []
    seen = set()
    for job in jobs.recent(14):
        if not access.is_admin():
            if pid not in job.party_ids and job.user_id != current_user.id:
                continue
        seen.add(job.id)
        row = job.to_dict(with_log=False)
        row["stages"] = [{"key": st.get("key", ""), "label": st.get("label", ""),
                          "state": st.get("state", "pending"),
                          "detail": st.get("detail", ""), "ms": st.get("ms"),
                          "bls": st.get("bls")} for st in jobs.stages_for(job)]
        row["result"] = {k: v for k, v in row["result"].items() if k != "stages"}
        out.append(row)

    # Anything this process did not run itself — an earlier deployment, or the
    # session before the last restart — comes from the history table, so the
    # record does not vanish when the server does.
    history = (ProcessRow.query.order_by(ProcessRow.created_at.desc())
               .limit(60).all())
    for row in history:
        if row.job_id in seen:
            continue
        if not access.is_admin():
            if pid not in row.party_ids and row.user_id != current_user.id:
                continue
        item = row.to_dict()
        if item["status"] == "running":
            # Its process is gone, so it will never finish; say so rather than
            # leaving a spinner turning for ever.
            item["status"] = "error"
            item["error"] = item["error"] or (
                "interrupted — the service restarted while this was running")
        out.append(item)
        seen.add(row.job_id)

    out.sort(key=lambda r: r.get("started_at", 0), reverse=True)
    out = out[:14]
    running = [j for j in out if j["status"] == "running"]
    return jsonify({"ok": True, "processes": out, "running": len(running)})


# ----------------------------------------------------------------------
# Datasets
# ----------------------------------------------------------------------

@bp.post("/datasets/onboard")
@login_required
def onboard():
    data = request.get_json(force=True)
    name = data.get("name", "")
    owners = [o.strip() for o in data.get("owners", []) if o.strip()]
    max_blocks = int(data.get("max_blocks", 24))
    block_size = int(data.get("block_size", 262144))

    if name not in {c["name"] for c in _hub().catalog()}:
        return _err(f"unknown dataset {name!r}")
    if not owners:
        return _err("at least one owner is required")
    if len(owners) != len(set(owners)):
        return _err("owner names must be unique")
    if not (4 <= max_blocks <= 200):
        return _err("blocks must be between 4 and 200 for an interactive demo")

    region = data.get("region") or session.get("region", DEFAULT_REGION)
    if region not in REGION_NAMES:
        region = DEFAULT_REGION

    dataset = Dataset(
        name=name,
        file_id="(provisioning)",
        group_name=f"consortium-{name}",
        n_blocks=0,
        block_size=block_size,
        status="provisioning",
        region=region,
        created_by=current_user.id,
    )
    dataset.owners = owners
    db.session.add(dataset)
    db.session.commit()
    dataset_id = dataset.id

    app = current_app._get_current_object()

    def run(job):
        result = _hub().onboard(job, dataset_id, name, owners, max_blocks, block_size)
        with app.app_context():
            row = db.session.get(Dataset, dataset_id)
            row.status = "active"
            row.file_id = _hub().spaces[dataset_id].file_id
            row.n_blocks = result["n_blocks"]
            row.total_bytes = result["total_bytes"]
            row.tag_ms = result["tag_ms"]
            db.session.commit()
            record_event(
                "onboard",
                f"{name} placed under protection: {result['n_blocks']} blocks sealed by "
                f"{len(owners)} organization(s) in {result['tag_ms']:,.0f} ms",
                dataset_id,
            )
            # Open the chain of custody. Everything that happens to this
            # dataset afterwards appends to it, so a deed can always show
            # where the asset came from and not just its latest sale.
            holder = registry.get_or_create_by_slug(owners[0])
            row.owner_party_id = holder.id
            db.session.commit()
            record_custody(
                dataset_id, "registered",
                f"Placed under protection in {row.region} by "
                + ", ".join(owners),
                to_party=holder.display_name,
            )
        job.result.update(result)

    job = jobs.start(
        "onboard", run,
        title=f"Protecting {name}",
        subject="Signing keys are issued to every owner and each block is sealed "
                "with a BLS12-381 tag",
        href=f"/console/storage/{dataset_id}",
        party_ids=(access.party_id(),), user_id=current_user.id)

    def fail_guard(job_ref=job):
        pass  # job errors are surfaced by polling; row stays 'provisioning'

    return jsonify({"ok": True, "job_id": job.id, "dataset_id": dataset_id})


# ----------------------------------------------------------------------
# Uploading a dataset of your own
# ----------------------------------------------------------------------
#
# The bundled catalog exists so an examiner can protect something in one
# click. Real use starts from a file the operator already has, so it is
# uploaded here, written to this machine under instance/uploads/<id>/ — beside
# the database, not to any third-party store — and then goes through exactly
# the same protection path as a catalog dataset.


def _protect_job(dataset: Dataset, owners: list[str], max_blocks: int,
                 block_size: int):
    """Run TagGen + upload for a stored dataset, in a background thread."""
    app = current_app._get_current_object()
    dataset_id = dataset.id
    path = Path(dataset.storage_path)
    file_id = f"upload/{dataset.dataset_ref}/{path.name}#{dataset_id}"
    group_name = dataset.group_name
    holder_id = dataset.owner_party_id
    region = dataset.region

    dataset.status = "provisioning"
    db.session.commit()

    def run(job):
        try:
            result = _hub().onboard_file(job, dataset_id, path, file_id, owners,
                                         max_blocks, block_size,
                                         group_name=group_name)
        except Exception:
            with app.app_context():
                row = db.session.get(Dataset, dataset_id)
                if row is not None:
                    row.status = "stored"      # the file is still held; only
                    db.session.commit()        # the protection attempt failed
            raise
        with app.app_context():
            row = db.session.get(Dataset, dataset_id)
            row.status = "active"
            row.file_id = file_id
            row.n_blocks = result["n_blocks"]
            row.block_size = block_size
            row.total_bytes = result["total_bytes"]
            row.tag_ms = result["tag_ms"]
            row.owners = owners
            holder = db.session.get(Party, holder_id) if holder_id else None
            if holder is None:
                holder = registry.get_or_create_by_slug(owners[0])
                row.owner_party_id = holder.id
            db.session.commit()
            record_event(
                "onboard",
                f"{row.name} placed under protection: {result['n_blocks']} blocks "
                f"sealed by {len(owners)} owner(s) in {result['tag_ms']:,.0f} ms",
                dataset_id)
            record_custody(
                dataset_id, "protected",
                f"Placed under protection in {region}: {result['n_blocks']} blocks "
                f"sealed by " + ", ".join(owners),
                to_party=holder.display_name)
        job.result.update(result)

    return jobs.start(
        "protect", run,
        title=f"Protecting {dataset.name}",
        subject="Chunking the file, issuing owner keys and sealing every block "
                "with a BLS12-381 tag",
        href=f"/console/storage/{dataset_id}",
        party_ids=(holder_id or access.party_id(),), user_id=current_user.id)


def _owner_ids_for(holder: Party | None, raw: str) -> list[str]:
    """Whose keys seal this dataset: the field as typed, or the holder itself.

    For a group of individuals the members ARE the co-owners, so an empty
    field resolves to the group's membership roll rather than to one identifier
    standing in for several people.
    """
    owners = [o.strip() for o in (raw or "").replace("\n", ",").split(",") if o.strip()]
    if owners:
        return owners
    return list(holder.owner_ids) if holder is not None else []


@bp.get("/datasets")
@login_required
def list_datasets():
    """The datasets this account holds — the entry point for a pipeline."""
    rows = access.datasets()
    hub = _hub()
    return jsonify({"ok": True, "acting_as": current_user.acting_as, "datasets": [
        {"id": d.id, "ref": d.dataset_ref, "name": d.name, "region": d.region,
         "status": ("active" if d.id in hub.spaces else
                    ("stored" if d.status == "stored" else "stale")),
         "blocks": d.n_blocks, "bytes": d.total_bytes,
         "monitoring_minutes": d.audit_every_min,
         "holder": (db.session.get(Party, d.owner_party_id).display_name
                    if d.owner_party_id else None),
         "key_holders": d.owners,
         "href": f"/console/storage/{d.id}"} for d in rows]})


@bp.post("/datasets/upload")
@login_required
def upload_dataset():
    """Accept a file from the operator's machine and register it as a dataset."""
    upload = request.files.get("file")
    if upload is None or not (upload.filename or "").strip():
        return _err("choose a file to upload")

    form = request.form
    original = Path(upload.filename).name
    name = (form.get("name") or "").strip() or Path(original).stem
    name = name[:120]
    if not name:
        return _err("give the dataset a name")

    ref = (form.get("custom_ref") or "").strip()
    if ref:
        if not REF_RE.match(ref):
            return _err("a dataset ID may use letters, digits, dot, dash and "
                        "underscore only")
        if Dataset.query.filter_by(custom_ref=ref).first():
            return _err(f"dataset ID {ref!r} is already in use")

    region = form.get("region") or session.get("region", DEFAULT_REGION)
    if region not in REGION_NAMES:
        region = DEFAULT_REGION

    holder = None
    if form.get("holder_party_id"):
        holder = db.session.get(Party, int(form["holder_party_id"]))
        if holder is None:
            return _err("unknown holding party")
    elif not access.is_admin():
        holder = access.party()      # a member's own entry, by default
    if holder is not None and not access.can_act_for(holder.id):
        return _forbidden(f"place a dataset under {holder.display_name}")

    owners = _owner_ids_for(holder, form.get("owners", ""))
    protect_now = form.get("protect_now") in ("1", "true", "on", "yes")
    if protect_now and not owners:
        return _err("name at least one owner, or choose a holding party, "
                    "before protecting this dataset")
    if len(owners) != len(set(owners)):
        return _err("owner identifiers must be unique")

    every = form.get("audit_every_min") or ""
    audit_every_min = None
    if every not in ("", "0", "off"):
        try:
            audit_every_min = int(every)
        except ValueError:
            return _err("the monitoring interval must be a whole number of minutes")
        if not (1 <= audit_every_min <= 1440):
            return _err("the monitoring interval must be between 1 minute and 24 hours")

    block_size = int(form.get("block_size") or 262144)
    if block_size not in BLOCK_SIZES:
        return _err("unsupported block size")
    max_blocks = int(form.get("max_blocks") or 24)
    if not (4 <= max_blocks <= 200):
        return _err("blocks to protect must be between 4 and 200")

    row = Dataset(
        name=name,
        file_id="(stored)",
        group_name=(form.get("group_name") or "").strip()[:160]
                   or f"consortium-{registry.slugify(name)}",
        n_blocks=0,
        block_size=block_size,
        status="stored",
        region=region,
        source="upload",
        original_filename=original[:255],
        custom_ref=ref,
        audit_every_min=audit_every_min,
        created_by=current_user.id,
        owner_party_id=holder.id if holder else None,
    )
    row.owners = owners
    db.session.add(row)
    db.session.commit()

    # Write the bytes only once the row exists, so the file always lands in a
    # directory named after a dataset that is actually on the register.
    folder = UPLOAD_DIR / str(row.id)
    folder.mkdir(parents=True, exist_ok=True)
    safe = secure_filename(original) or "dataset.bin"
    target = folder / safe
    digest = hashlib.sha256()
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ValueError("file is larger than the upload limit")
                digest.update(chunk)
                out.write(chunk)
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        db.session.delete(row)
        db.session.commit()
        return _err(f"the upload could not be stored: {exc}")

    if written == 0:
        shutil.rmtree(folder, ignore_errors=True)
        db.session.delete(row)
        db.session.commit()
        return _err("that file is empty — there is nothing to protect")

    row.storage_path = str(target)
    row.total_bytes = written
    row.content_sha256 = digest.hexdigest()
    db.session.commit()

    record_event("upload",
                 f"{row.name} uploaded ({written:,} bytes, {safe}) and held in "
                 f"{region} as {row.dataset_ref}", row.id)
    if holder is not None:
        record_custody(row.id, "registered",
                       f"Uploaded to {region} and entered on the register as "
                       f"{row.dataset_ref}",
                       to_party=holder.display_name)

    payload = {"ok": True, "dataset_id": row.id, "ref": row.dataset_ref,
               "bytes": written, "sha256": row.content_sha256,
               "status": row.status}
    if protect_now:
        job = _protect_job(row, owners, max_blocks, block_size)
        payload["job_id"] = job.id
        payload["status"] = "provisioning"
    return jsonify(payload)


def _dataset_file(dataset: Dataset) -> Path | None:
    """Where this dataset's bytes actually are on this machine."""
    if dataset.source == "upload":
        return Path(dataset.storage_path) if dataset.storage_path else None
    try:
        return _hub().shard_path(dataset.name)
    except Exception:
        return None


@bp.get("/datasets/<int:dataset_id>/preview")
@login_required
def dataset_preview(dataset_id: int):
    """What is inside this dataset — for the party that holds it.

    The protocol proves possession without anybody reading the data; that is a
    property of the auditing, not a reason to hide a holder's own file from it.
    So this is scoped exactly like every other custody operation: the holder,
    and the registry.
    """
    dataset = Dataset.query.get_or_404(dataset_id)
    refusal = _guard_dataset(dataset, "look inside")
    if refusal:
        return refusal
    path = _dataset_file(dataset)
    if path is None:
        return _err("this dataset has no file recorded on this server")
    report = preview.describe(path, total_bytes=dataset.total_bytes or None)
    report["source"] = ("your uploaded file" if dataset.source == "upload"
                        else "consortium catalog shard")
    report["dataset"] = dataset.name
    report["sha256"] = dataset.content_sha256
    report["href"] = f"/api/datasets/{dataset_id}/file"
    return jsonify({"ok": True, "preview": report})


@bp.get("/datasets/<int:dataset_id>/file")
@login_required
def dataset_file(dataset_id: int):
    """The file itself, for the browser to render — or to download.

    Served with Range support so a video can be scrubbed rather than pulled in
    full, and always as an attachment when asked, so a holder can take their
    own data away.
    """
    from flask import send_file

    dataset = Dataset.query.get_or_404(dataset_id)
    refusal = _guard_dataset(dataset, "download")
    if refusal:
        return refusal
    path = _dataset_file(dataset)
    if path is None or not path.exists():
        return _err("the stored file is not on this server", 404)
    download = request.args.get("download") == "1"
    return send_file(path, conditional=True, as_attachment=download,
                     download_name=(dataset.original_filename or path.name))


@bp.post("/datasets/protect-all")
@login_required
def protect_all():
    """Bring every dataset that needs it back under protection, one by one.

    After a restart nothing is sealed any more — the keys were only ever in
    memory. Doing that one row at a time is busywork, so this walks the whole
    list in a single background job and reports each dataset as it goes.
    """
    hub = _hub()
    pending = [d for d in access.datasets()
               if d.id not in hub.spaces and d.source in ("upload", "catalog")]
    if not pending:
        return _err("every dataset you hold is already protected")

    app = current_app._get_current_object()
    targets = [(d.id, d.name) for d in pending]
    stages = [{"key": f"ds-{i}", "label": name, "state": "pending"}
              for i, (_, name) in enumerate(targets)]

    def run(job):
        job.result["stages"] = stages
        job.result["total"] = len(targets)
        sealed = failed = 0
        for i, (dataset_id, name) in enumerate(targets):
            stages[i]["state"] = "running"
            job.message = f"Protecting {name} ({i + 1} of {len(targets)})"
            job.progress = i / len(targets)
            t0 = time.perf_counter()
            with app.app_context():
                row = db.session.get(Dataset, dataset_id)
                if row is None:
                    stages[i].update(state="failed", detail="no longer on the register")
                    failed += 1
                    continue
                if not row.owners:
                    stages[i].update(
                        state="failed",
                        detail="no owners are named for it — protect it from its "
                               "own page and name them")
                    failed += 1
                    continue
                try:
                    hub.ensure_live(row)
                except Exception as exc:
                    stages[i].update(state="failed", detail=str(exc))
                    row.status = "stale"
                    db.session.commit()
                    job.emit(f"{name}: {exc}")
                    failed += 1
                    continue
                info = hub.space_info(dataset_id) or {}
                row.status = "active"
                row.n_blocks = info.get("n_blocks", row.n_blocks)
                db.session.commit()
                record_event("onboard",
                             f"{name} protected again: {row.n_blocks} blocks sealed "
                             f"by {len(row.owners)} owner(s)", dataset_id)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            stages[i].update(
                state="done", ms=ms,
                detail=f"{info.get('n_blocks', 0)} blocks sealed under "
                       f"{', '.join(info.get('owners', []) or row.owners)}")
            job.emit(f"{name}: sealed in {ms:.0f} ms")
            sealed += 1
        job.progress = 1.0
        job.message = (f"{sealed} dataset(s) protected"
                       + (f", {failed} could not be" if failed else ""))
        job.result.update({"sealed": sealed, "failed": failed})

    job = jobs.start(
        "protect-all", run,
        title=f"Protecting {len(targets)} dataset(s)",
        subject="Each one is sealed again with its owners' BLS12-381 keys, in turn",
        href="/console/storage",
        party_ids=(access.party_id(),), user_id=current_user.id)
    return jsonify({"ok": True, "job_id": job.id, "count": len(targets),
                    "datasets": [name for _, name in targets]})


@bp.post("/datasets/<int:dataset_id>/protect")
@login_required
def protect_dataset(dataset_id: int):
    """Bring an already-uploaded dataset under protection."""
    row = Dataset.query.get_or_404(dataset_id)
    refusal = _guard_dataset(row, "protect")
    if refusal:
        return refusal
    if row.source != "upload" or not row.storage_path:
        return _err("this dataset was protected from the catalog — use the "
                    "catalog entry to protect it again")
    if row.status == "active" and dataset_id in _hub().spaces:
        return _err("this dataset is already under protection")
    if not Path(row.storage_path).exists():
        return _err("the stored file is missing from this server")

    data = request.get_json(silent=True) or {}
    holder = db.session.get(Party, row.owner_party_id) if row.owner_party_id else None
    owners = _owner_ids_for(holder, data.get("owners", "")) or row.owners
    if not owners:
        return _err("name at least one owner for this dataset")
    if len(owners) != len(set(owners)):
        return _err("owner identifiers must be unique")

    block_size = int(data.get("block_size") or row.block_size or 262144)
    if block_size not in BLOCK_SIZES:
        return _err("unsupported block size")
    max_blocks = int(data.get("max_blocks") or 24)
    if not (4 <= max_blocks <= 200):
        return _err("blocks to protect must be between 4 and 200")

    job = _protect_job(row, owners, max_blocks, block_size)
    return jsonify({"ok": True, "job_id": job.id, "dataset_id": row.id})


@bp.delete("/datasets/<int:dataset_id>")
@login_required
def delete_dataset(dataset_id: int):
    dataset = Dataset.query.get_or_404(dataset_id)
    refusal = _guard_dataset(dataset, "remove")
    if refusal:
        return refusal
    # A dataset named on an instrument cannot simply disappear: the agreement
    # and the deed both refer to it, and a register that loses its subject is
    # worth nothing.
    if TransferAgreement.query.filter_by(dataset_id=dataset_id).first():
        return _err("this dataset is named on a transfer agreement and cannot "
                    "be removed")
    if Certificate.query.filter_by(dataset_id=dataset_id).first():
        return _err("a certificate of transfer has been issued for this dataset "
                    "— it cannot be removed")
    _hub().spaces.pop(dataset_id, None)
    AuditRow.query.filter_by(dataset_id=dataset_id).delete()
    EventRow.query.filter_by(dataset_id=dataset_id).delete()
    Listing.query.filter_by(dataset_id=dataset_id).delete()
    CustodyEvent.query.filter_by(dataset_id=dataset_id).delete()
    stored = dataset.storage_path
    db.session.delete(dataset)
    db.session.commit()
    if stored:
        # Remove the operator's own bytes as well; leaving them behind after a
        # deletion would be the opposite of what the button says.
        shutil.rmtree(Path(stored).parent, ignore_errors=True)
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Auditing + tampering
# ----------------------------------------------------------------------

@bp.post("/datasets/<int:dataset_id>/audit")
@login_required
def audit(dataset_id: int):
    refusal = _guard_dataset(Dataset.query.get_or_404(dataset_id), "verify")
    if refusal:
        return refusal
    data = request.get_json(force=True)
    c = int(data.get("c", 10))
    stale = bool(data.get("stale", False))
    try:
        result = _hub().audit(dataset_id, c, use_stale=stale)
    except (KeyError, ValueError) as exc:
        return _err(str(exc))

    note = "seller's stale parameters" if stale else ""
    db.session.add(AuditRow(
        dataset_id=dataset_id,
        passed=result["passed"],
        challenged=result["challenged"],
        total_blocks=result["total_blocks"],
        detection_pct=result["detection_pct"],
        duration_ms=result["duration_ms"],
        note=note,
    ))
    db.session.commit()
    verdict = "PASS" if result["passed"] else "FAIL"
    record_event("audit",
                 f"Verification {'passed' if result['passed'] else 'FAILED'}: "
                 f"{result['challenged']}/{result['total_blocks']} blocks in "
                 f"{result['duration_ms']:.0f} ms"
                 + (" (previous owner's credentials)" if note else ""),
                 dataset_id)
    return jsonify({"ok": True, "result": result})


@bp.post("/datasets/<int:dataset_id>/verify-run")
@login_required
def verify_run(dataset_id: int):
    """Start a verification that reports each protocol step as it happens.

    Same audit as /audit, run through the hub's staged path so the console can
    show the challenge being drawn, the provider answering and the auditor
    checking the pairing equation, with the real time each step took.
    """
    dataset = Dataset.query.get_or_404(dataset_id)
    refusal = _guard_dataset(dataset, "verify")
    if refusal:
        return refusal
    data = request.get_json(silent=True) or {}
    try:
        c = int(data.get("c", 10))
    except (TypeError, ValueError):
        return _err("the number of blocks to challenge must be a whole number")
    if c < 1:
        return _err("challenge at least one block")
    stale = bool(data.get("stale", False))
    refusal = _require_live(dataset)
    if refusal:
        return refusal
    dataset = db.session.get(Dataset, dataset_id)   # state may have been rebuilt

    app = current_app._get_current_object()
    dataset_name = dataset.name

    def run(job):
        result = _hub().audit_staged(job, dataset_id, c, use_stale=stale)
        t0 = time.perf_counter()
        with app.app_context():
            note = "previous owner's parameters" if result["stale"] else ""
            row = AuditRow(
                dataset_id=dataset_id,
                passed=result["passed"],
                challenged=result["challenged"],
                total_blocks=result["total_blocks"],
                detection_pct=result["detection_pct"],
                duration_ms=result["duration_ms"],
                note=note,
            )
            db.session.add(row)
            db.session.commit()
            record_event(
                "audit",
                f"Verification {'passed' if result['passed'] else 'FAILED'}: "
                f"{result['challenged']}/{result['total_blocks']} blocks in "
                f"{result['duration_ms']:.0f} ms"
                + (" (previous owner's credentials)" if note else ""),
                dataset_id)
            stages = job.result.get("stages") or []
            if stages:
                stages[-1].update(
                    state="done", ms=round((time.perf_counter() - t0) * 1000, 1),
                    detail=f"written to the verification log as entry #{row.id}")
            job.result["audit_id"] = row.id
            job.result["recorded_at"] = row.created_at.strftime("%d %b %Y, %H:%M")
            job.result["dataset_name"] = dataset_name

    job = jobs.start(
        "verify", run,
        title=f"Verifying {dataset_name}",
        subject=f"Challenging {c} block(s): the provider must produce a "
                f"BLS12-381 proof it still holds them",
        href=f"/console/integrity",
        party_ids=(dataset.owner_party_id,), user_id=current_user.id)
    return jsonify({"ok": True, "job_id": job.id, "dataset_name": dataset_name})


@bp.post("/datasets/<int:dataset_id>/corrupt")
@login_required
def corrupt(dataset_id: int):
    refusal = _guard_dataset(Dataset.query.get_or_404(dataset_id),
                             "run fault injection against")
    if refusal:
        return refusal
    data = request.get_json(force=True)
    try:
        result = _hub().corrupt(dataset_id, int(data.get("index", 0)))
    except (KeyError, ValueError, IndexError) as exc:
        return _err(str(exc))
    record_event("corrupt", f"Storage fault injected at block {result['index']} "
                 f"({result['corrupted_count']} block(s) currently altered)", dataset_id)
    return jsonify({"ok": True, "result": result})


@bp.post("/datasets/<int:dataset_id>/restore")
@login_required
def restore(dataset_id: int):
    refusal = _guard_dataset(Dataset.query.get_or_404(dataset_id), "restore")
    if refusal:
        return refusal
    try:
        result = _hub().restore(dataset_id)
    except KeyError as exc:
        return _err(str(exc))
    record_event("restore", f"Storage restored: {result['restored']} altered block(s) repaired",
                 dataset_id)
    return jsonify({"ok": True, "result": result})


# ----------------------------------------------------------------------
# Ownership
# ----------------------------------------------------------------------

@bp.post("/datasets/<int:dataset_id>/owners/add")
@login_required
def add_owner(dataset_id: int):
    refusal = _guard_dataset(Dataset.query.get_or_404(dataset_id),
                             "change the ownership group of")
    if refusal:
        return refusal
    data = request.get_json(force=True)
    owners = [o.strip() for o in data.get("owners", []) if o.strip()]
    if not owners:
        return _err("provide at least one owner id")
    try:
        result = _hub().add_owner(dataset_id, owners)
    except (KeyError, ValueError) as exc:
        return _err(str(exc))
    _sync_owners(dataset_id, result["owners"])
    record_event("add_owner",
                 f"Admitted {', '.join(owners)} to the ownership group "
                 f"({result['duration_ms']:.0f} ms, no re-key of existing members)", dataset_id)
    return jsonify({"ok": True, "result": result})


@bp.post("/datasets/<int:dataset_id>/owners/revoke")
@login_required
def revoke_owner(dataset_id: int):
    refusal = _guard_dataset(Dataset.query.get_or_404(dataset_id),
                             "change the ownership group of")
    if refusal:
        return refusal
    data = request.get_json(force=True)
    owners = [o.strip() for o in data.get("owners", []) if o.strip()]
    if not owners:
        return _err("provide at least one owner id")
    try:
        result = _hub().revoke_owner(dataset_id, owners)
    except (KeyError, ValueError) as exc:
        return _err(str(exc))
    _sync_owners(dataset_id, result["owners"])
    record_event("revoke_owner",
                 f"Removed {', '.join(owners)} from the ownership group — "
                 f"their authority is permanently revoked ({result['duration_ms']:.0f} ms)", dataset_id)
    return jsonify({"ok": True, "result": result})


@bp.post("/datasets/<int:dataset_id>/transfer")
@login_required
def transfer(dataset_id: int):
    refusal = _guard_dataset(Dataset.query.get_or_404(dataset_id), "transfer")
    if refusal:
        return refusal
    data = request.get_json(force=True)
    buyer_group = data.get("buyer_group", "").strip()
    buyer_owners = [o.strip() for o in data.get("buyer_owners", []) if o.strip()]
    if not buyer_group or not buyer_owners:
        return _err("buyer group name and at least one buyer owner are required")
    try:
        result = _hub().transfer(dataset_id, buyer_group, buyer_owners)
    except (KeyError, ValueError) as exc:
        return _err(str(exc))

    dataset = db.session.get(Dataset, dataset_id)
    dataset.group_name = buyer_group
    dataset.owners = result["owners"]
    db.session.commit()
    record_event("transfer",
                 f"Ownership transferred from {result['seller']} to {buyer_group} — "
                 f"storage re-sealed in place, {result['duration_ms']:,.0f} ms, "
                 "previous credentials retired", dataset_id)
    return jsonify({"ok": True, "result": result})


def _sync_owners(dataset_id: int, owners: list[str]) -> None:
    dataset = db.session.get(Dataset, dataset_id)
    dataset.owners = owners
    db.session.commit()



# ----------------------------------------------------------------------
# Parties (the legal register behind the protocol's owner ids)
# ----------------------------------------------------------------------

@bp.post("/parties")
@login_required
def create_party():
    data = request.get_json(force=True)
    name = (data.get("display_name") or "").strip()
    if not name:
        return _err("a party name is required")
    party_type = data.get("party_type", "company")
    if party_type not in PARTY_TYPE_NAMES:
        return _err("unknown party type")
    if Party.query.filter_by(slug=registry.slugify(name)).first():
        return _err(f"a party named {name!r} is already on the register")

    email = (data.get("contact_email") or "").strip().lower()
    # People are identified by an address they answer to; organizations by
    # their registration. Only the first kind is asked for an email.
    if party_type in EMAIL_REQUIRED_TYPES and not email:
        return _err("an email address is required for "
                    + ("an individual" if party_type == "individual"
                       else "the owner of a group"))
    if email and not registry.valid_email(email):
        return _err(f"{email} is not a valid email address")

    members = data.get("members") or []
    if party_type not in MEMBERSHIP_TYPES:
        members = []
    seen = {email} if email else set()
    cleaned = []
    for m in members:
        addr = (m.get("email") if isinstance(m, dict) else m or "").strip().lower()
        if not addr:
            continue
        if not registry.valid_email(addr):
            return _err(f"{addr} is not a valid email address")
        if addr in seen:
            return _err(f"{addr} is listed twice")
        seen.add(addr)
        cleaned.append({"email": addr,
                        "name": (m.get("name") if isinstance(m, dict) else "") or ""})

    party = registry.create_party(
        name, party_type,
        legal_name=data.get("legal_name", ""),
        registration_id=data.get("registration_id", ""),
        jurisdiction=data.get("jurisdiction", ""),
        contact_email=email,
    )
    if party_type in MEMBERSHIP_TYPES and email:
        # Whoever registers the group is its owner, the way the creator of a
        # shared repository is. Collaborators are admitted under them.
        registry.add_member(party, email, data.get("owner_name", ""), role="owner")
    for m in cleaned:
        registry.add_member(party, m["email"], m["name"])

    detail = (f" with {len(cleaned)} collaborator(s)" if cleaned else "")
    record_event("party", f"{party.display_name} ({party.type_label}) added to the "
                          f"ownership register as {party.party_ref}{detail}")
    return jsonify({"ok": True, "party_id": party.id, "ref": party.party_ref})


@bp.post("/parties/<int:party_id>/account")
@login_required
def issue_account(party_id: int):
    """Issue sign-in credentials to a party on the register.

    A party that cannot sign in cannot sign anything, and the registry must
    never sign in its place. So when the registry onboards a counterparty it
    issues that counterparty a login, and every signature from then on is the
    party's own act.
    """
    from models import User

    if not access.is_admin():
        return _err("only the registry can issue credentials for a party", 403)
    party = Party.query.get_or_404(party_id)
    data = request.get_json(force=True)
    email = (data.get("email") or party.contact_email or "").strip().lower()
    name = (data.get("name") or party.display_name).strip()
    password = data.get("password") or ""

    if not registry.valid_email(email):
        return _err(f"{email or 'that'} is not a valid email address")
    if User.query.filter_by(email=email).first():
        return _err(f"an account already exists for {email}")
    existing = User.query.filter_by(party_id=party.id).first()
    if existing is not None:
        return _err(f"{party.display_name} already signs in as {existing.email}")
    if len(password) < 8:
        return _err("the password must be at least 8 characters")

    user = User(name=name, email=email, organization=party.display_name,
                party_id=party.id, role="member")
    user.set_password(password)
    db.session.add(user)
    if not party.contact_email:
        party.contact_email = email
    db.session.commit()
    record_event("party", f"Sign-in credentials issued to {party.display_name} "
                          f"({email})")
    return jsonify({"ok": True, "user_id": user.id, "email": email})


# -- membership of a group ------------------------------------------------

def _member_payload(party: Party) -> list[dict]:
    return [{"id": m.id, "email": m.email, "name": m.name, "role": m.role,
             "role_label": m.role_label, "slug": m.slug,
             "since": m.created_at.strftime("%d %b %Y") if m.created_at else ""}
            for m in party.members]


@bp.get("/parties/<int:party_id>/members")
@login_required
def list_members(party_id: int):
    party = Party.query.get_or_404(party_id)
    return jsonify({"ok": True, "members": _member_payload(party),
                    "takes_members": party.takes_members,
                    "name": party.display_name})


@bp.post("/parties/<int:party_id>/members")
@login_required
def add_member(party_id: int):
    party = Party.query.get_or_404(party_id)
    if not access.can_act_for(party.id):
        return _forbidden(f"manage the members of {party.display_name}")
    data = request.get_json(force=True)
    role = "owner" if data.get("role") == "owner" else "member"
    try:
        member = registry.add_member(party, data.get("email", ""),
                                     data.get("name", ""), role=role)
    except ValueError as exc:
        return _err(str(exc))
    record_event("party", f"{member.email} added to {party.display_name} "
                          f"as {member.role_label.lower()}")
    return jsonify({"ok": True, "member": _member_payload(party),
                    "added": member.email, "slug": member.slug})


@bp.delete("/parties/<int:party_id>/members/<int:member_id>")
@login_required
def remove_member(party_id: int, member_id: int):
    party = Party.query.get_or_404(party_id)
    if not access.can_act_for(party.id):
        return _forbidden(f"manage the members of {party.display_name}")
    member = db.session.get(PartyMember, member_id)
    if member is None or member.party_id != party.id:
        return _err("no such member of this party", 404)
    # A member whose identifier is a live owner of a protected dataset cannot
    # be dropped from the register while the cryptographic owner set still
    # names them — the two would then disagree.
    holding = [d.name for d in Dataset.query.filter_by(owner_party_id=party.id).all()
               if member.slug in d.owners]
    if holding:
        return _err(f"{member.email} is a named owner of "
                    f"{', '.join(holding)} — remove them from the ownership "
                    "group of that dataset first")
    email = member.email
    try:
        registry.remove_member(party, member)
    except ValueError as exc:
        return _err(str(exc))
    record_event("party", f"{email} removed from {party.display_name}")
    return jsonify({"ok": True, "member": _member_payload(party)})


@bp.post("/parties/<int:party_id>")
@login_required
def update_party(party_id: int):
    """Amend a register entry. The slug is never changed: it is the identifier
    the protocol layer already holds, and rewriting it would silently detach a
    party from datasets and deeds that name it."""
    party = Party.query.get_or_404(party_id)
    if not access.can_act_for(party.id):
        return _forbidden(f"amend the register entry of {party.display_name}")
    data = request.get_json(force=True)
    for field in ("display_name", "legal_name", "registration_id",
                  "jurisdiction", "contact_email"):
        if field in data:
            setattr(party, field, (data.get(field) or "").strip())
    if data.get("party_type") in PARTY_TYPE_NAMES:
        party.party_type = data["party_type"]
    if not party.display_name:
        db.session.rollback()
        return _err("a party name is required")
    if party.contact_email and not registry.valid_email(party.contact_email):
        db.session.rollback()
        return _err(f"{party.contact_email} is not a valid email address")
    if party.party_type in EMAIL_REQUIRED_TYPES and not party.contact_email:
        db.session.rollback()
        return _err("an email address is required for this party type")
    if not party.legal_name:
        party.legal_name = party.display_name
    db.session.commit()
    record_event("party", f"Register entry {party.party_ref} amended "
                          f"({party.display_name})")
    return jsonify({"ok": True})


@bp.post("/parties/<int:party_id>/verify")
@login_required
def verify_party(party_id: int):
    # Identity checks are the registry's job. A party attesting to its own
    # identity would make the "Verified" mark worth nothing.
    if not access.is_admin():
        return _err("only the registry can record an identity check", 403)
    party = Party.query.get_or_404(party_id)
    data = request.get_json(force=True)
    method = (data.get("method") or "Registration document review").strip()
    registry.verify_party(party, method[:80])
    record_event("party", f"Identity of {party.display_name} verified "
                          f"({party.verification_method})")
    return jsonify({"ok": True})


@bp.delete("/parties/<int:party_id>")
@login_required
def delete_party(party_id: int):
    party = Party.query.get_or_404(party_id)
    if not access.can_act_for(party.id):
        return _forbidden(f"remove {party.display_name} from the register")
    from models import User as _User
    if _User.query.filter_by(party_id=party.id).first() and not access.is_admin():
        return _err("this entry is your account's identity on the register and "
                    "cannot be removed while the account exists")
    if TransferAgreement.query.filter(
            (TransferAgreement.seller_party_id == party_id)
            | (TransferAgreement.buyer_party_id == party_id)).first():
        return _err("this party is named on a transfer agreement and cannot be "
                    "removed from the register")
    db.session.delete(party)
    db.session.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Marketplace listings
# ----------------------------------------------------------------------

@bp.post("/listings")
@login_required
def create_listing():
    data = request.get_json(force=True)
    dataset = db.session.get(Dataset, int(data.get("dataset_id", 0)))
    if dataset is None:
        return _err("unknown dataset")
    seller = db.session.get(Party, int(data.get("seller_party_id", 0))) \
        if data.get("seller_party_id") else access.party()
    if seller is None:
        return _err("select the party that currently holds this dataset")
    if not access.can_manage_dataset(dataset):
        return _forbidden(f"list {dataset.name} for sale")
    if not access.can_act_for(seller.id):
        return _forbidden(f"list a dataset on behalf of {seller.display_name}")
    try:
        price = float(data.get("price", 0))
    except (TypeError, ValueError):
        return _err("price must be a number")
    if price < 0:
        return _err("price cannot be negative")
    listing = Listing(
        dataset_id=dataset.id,
        seller_party_id=seller.id,
        title=(data.get("title") or dataset.name).strip()[:200],
        summary=(data.get("summary") or "").strip(),
        category=(data.get("category") or "General").strip()[:60],
        price=price,
        currency=data.get("currency", "INR"),
        licence_terms=(data.get("licence_terms") or "").strip(),
        status="active",
    )
    db.session.add(listing)
    db.session.commit()
    if dataset.owner_party_id is None:
        dataset.owner_party_id = seller.id
        db.session.commit()
    record_event("listing", f"{listing.title} listed for sale at "
                            f"{listing.price_display} by {seller.display_name}",
                 dataset.id)
    return jsonify({"ok": True, "listing_id": listing.id})


@bp.post("/listings/<int:listing_id>/withdraw")
@login_required
def withdraw_listing(listing_id: int):
    listing = Listing.query.get_or_404(listing_id)
    if not access.can_act_for(listing.seller_party_id):
        return _forbidden(f"withdraw {listing.listing_ref}")
    if listing.status == "sold":
        return _err("a sold listing cannot be withdrawn")
    listing.status = "withdrawn"
    db.session.commit()
    record_event("listing", f"Listing {listing.listing_ref} withdrawn from sale",
                 listing.dataset_id)
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Transfer agreements — the two-signature instrument
# ----------------------------------------------------------------------

@bp.post("/agreements")
@login_required
def create_agreement():
    data = request.get_json(force=True)
    dataset = db.session.get(Dataset, int(data.get("dataset_id", 0)))
    if dataset is None:
        return _err("unknown dataset")
    refusal = _require_live(dataset)
    if refusal:
        return refusal

    listing = None
    if data.get("listing_id"):
        listing = db.session.get(Listing, int(data["listing_id"]))

    seller = db.session.get(Party, int(data.get("seller_party_id", 0))) if \
        data.get("seller_party_id") else None
    if seller is None and dataset.owner_party_id:
        seller = db.session.get(Party, dataset.owner_party_id)
    if seller is None and listing is not None:
        seller = listing.seller
    if seller is None:
        return _err("the transferring party must be named")
    if not access.can_act_for(seller.id):
        return _forbidden(f"raise a transfer agreement as {seller.display_name} "
                          "— bid for the dataset on the marketplace instead")

    buyer_id = data.get("buyer_party_id")
    if buyer_id:
        buyer = db.session.get(Party, int(buyer_id))
    else:
        name = (data.get("buyer_name") or "").strip()
        if not name:
            return _err("name the acquiring party, or select one from the register")
        buyer = registry.create_party(
            name, data.get("buyer_type", "company"),
            legal_name=data.get("buyer_legal_name", ""),
            registration_id=data.get("buyer_registration_id", ""),
            jurisdiction=data.get("buyer_jurisdiction", ""),
            contact_email=data.get("buyer_email", ""))
    if buyer is None:
        return _err("unknown acquiring party")
    if buyer.id == seller.id:
        return _err("a dataset cannot be sold to its current holder")

    try:
        consideration = float(data.get("consideration", 0))
    except (TypeError, ValueError):
        return _err("the consideration must be a number")
    if consideration < 0:
        return _err("the consideration cannot be negative")

    agreement = agreementsvc.create(
        dataset, seller, buyer,
        consideration=consideration,
        currency=data.get("currency", "INR"),
        payment_reference=data.get("payment_reference", ""),
        terms=data.get("terms", ""),
        listing=listing,
    )
    return jsonify({"ok": True, "agreement_id": agreement.id,
                    "ref": agreement.agreement_ref})


@bp.post("/offers")
@login_required
def make_offer():
    """A buyer bids for a dataset somebody else holds.

    This is the ordinary way a transfer starts on a real marketplace: the party
    that wants the data asks for it at a price, signs that offer, and the holder
    decides. Nothing about the dataset changes until they do.
    """
    data = request.get_json(force=True)
    buyer = access.party()
    if buyer is None:
        return _err("your account has no entry on the ownership register, so it "
                    "cannot bid for a dataset — the registry administrator "
                    "acts for the platform, not as a counterparty", 403)

    listing = None
    if data.get("listing_id"):
        listing = db.session.get(Listing, int(data["listing_id"]))
        if listing is None:
            return _err("unknown listing")
        dataset = listing.dataset
        seller = listing.seller
    else:
        dataset = db.session.get(Dataset, int(data.get("dataset_id", 0)))
        if dataset is None:
            return _err("unknown dataset")
        seller = (db.session.get(Party, dataset.owner_party_id)
                  if dataset.owner_party_id else None)
    if dataset is None or seller is None:
        return _err("this dataset has no registered holder to buy it from")
    if listing is not None and listing.status not in ("active", "under_offer"):
        return _err("this listing is no longer open to offers")
    if seller.id == buyer.id:
        return _err("you already hold this dataset")
    refusal = _require_live(dataset)
    if refusal:
        return refusal

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return _err("your offer must be an amount")
    if amount <= 0:
        return _err("your offer must be more than zero")

    # An offer you have already made is revised rather than duplicated: a
    # holder comparing bids should see one current figure per bidder, not a
    # pile of stale ones.
    open_bid = TransferAgreement.query.filter_by(
        dataset_id=dataset.id, buyer_party_id=buyer.id,
        status="awaiting_seller").first()
    if open_bid is not None:
        try:
            agreementsvc.revise_bid(open_bid, consideration=amount,
                                    terms=(data.get("terms") or open_bid.terms))
        except ValueError as exc:
            return _err(str(exc))
        return jsonify({"ok": True, "agreement_id": open_bid.id,
                        "ref": open_bid.agreement_ref, "revised": True,
                        "href": f"/console/agreements/{open_bid.id}"})

    agreement = agreementsvc.raise_bid(
        dataset, seller, buyer,
        consideration=amount,
        currency=(data.get("currency") or (listing.currency if listing else "INR")),
        terms=(data.get("terms") or "").strip(),
        listing=listing)
    return jsonify({"ok": True, "agreement_id": agreement.id,
                    "ref": agreement.agreement_ref,
                    "href": f"/console/agreements/{agreement.id}"})


@bp.get("/datasets/<int:dataset_id>/offers")
@login_required
def dataset_offers(dataset_id: int):
    """Every open offer on one asset, for whoever holds it."""
    dataset = Dataset.query.get_or_404(dataset_id)
    if not access.can_manage_dataset(dataset):
        return _forbidden(f"see the offers on {dataset.name}")
    rows = (TransferAgreement.query
            .filter(TransferAgreement.dataset_id == dataset_id,
                    TransferAgreement.status == "awaiting_seller")
            .order_by(TransferAgreement.consideration.desc()).all())
    return jsonify({"ok": True, "offers": [
        {"id": a.id, "ref": a.agreement_ref, "buyer": a.buyer.display_name,
         "buyer_type": a.buyer.type_label, "verified": bool(a.buyer.verified),
         "amount": a.consideration, "amount_display": a.consideration_display,
         "terms": a.terms, "is_bid": a.is_bid,
         "raised": a.created_at.strftime("%d %b %Y, %H:%M"),
         "href": f"/console/agreements/{a.id}"} for a in rows]})


def _execute_job(agreement_id: int, runner, kind: str):
    """Run a transfer in the background so the console can watch it happen."""
    app = current_app._get_current_object()

    def run(job):
        with app.app_context():
            row = db.session.get(TransferAgreement, agreement_id)
            cert = runner(row, job)
            job.result.update({
                "certificate_id": cert.certificate_id,
                "href": f"/certificate/{cert.certificate_id}",
                "agreement_ref": row.agreement_ref,
            })

    row = db.session.get(TransferAgreement, agreement_id)
    return jobs.start(
        kind, run,
        title=f"Transferring {row.dataset.name}",
        subject=f"{row.agreement_ref}: {row.seller.display_name} to "
                f"{row.buyer.display_name} — BLS tags are re-signed and the deed "
                f"is issued",
        href=f"/console/agreements/{agreement_id}",
        party_ids=(row.seller_party_id, row.buyer_party_id),
        user_id=current_user.id)


@bp.post("/agreements/<int:agreement_id>/accept")
@login_required
def accept_agreement(agreement_id: int):
    """The holder accepts a bid: signs, and the transfer runs immediately."""
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    if not access.is_party(agreement.seller_party_id):
        return _forbidden(f"accept an offer made to "
                          f"{agreement.seller.display_name} — a registry "
                          f"administrator approves a transfer in its own name "
                          f"instead of signing as a party")
    if agreement.status != "awaiting_seller":
        return _err(f"agreement {agreement.agreement_ref} is "
                    f"{agreement.status_label.lower()} — there is nothing to accept")
    refusal = _require_live(agreement.dataset)
    if refusal:
        return refusal
    if not agreement.buyer_signed_at:
        # A holder-raised instrument: signing hands it to the buyer instead.
        try:
            agreementsvc.sign_seller(agreement)
        except (KeyError, ValueError) as exc:
            return _err(str(exc))
        return jsonify({"ok": True, "status": agreement.status,
                        "status_label": agreement.status_label})

    job = _execute_job(agreement_id,
                       lambda row, job: agreementsvc.sign_seller(row, job=job),
                       "transfer")
    return jsonify({"ok": True, "job_id": job.id})


@bp.post("/agreements/<int:agreement_id>/approve")
@login_required
def approve_agreement(agreement_id: int):
    """The registry settles a request the parties have not closed themselves."""
    if not access.is_admin():
        return _err("only the registry administrator can approve a transfer "
                    "on the parties' behalf", 403)
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    refusal = _require_live(agreement.dataset)
    if refusal:
        return refusal
    if agreement.status not in ("awaiting_seller", "awaiting_buyer"):
        return _err(f"agreement {agreement.agreement_ref} is "
                    f"{agreement.status_label.lower()} — there is nothing to approve")
    if not (agreement.seller_signed_at or agreement.buyer_signed_at):
        return _err("neither party has signed yet — there is no request to approve")
    note = (request.get_json(silent=True) or {}).get("note", "")
    user = current_user._get_current_object()
    job = _execute_job(
        agreement_id,
        lambda row, job: agreementsvc.approve_as_registry(row, user, note, job=job),
        "transfer")
    return jsonify({"ok": True, "job_id": job.id})


@bp.post("/agreements/<int:agreement_id>/sign-seller")
@login_required
def sign_seller(agreement_id: int):
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    if not access.is_party(agreement.seller_party_id):
        return _forbidden(f"sign as {agreement.seller.display_name}")
    if agreement.buyer_signed_at:
        # The buyer already signed (this is a bid), so signing completes it —
        # that path runs as a job so the execution can be watched.
        return accept_agreement(agreement_id)
    try:
        agreementsvc.sign_seller(agreement)
    except (KeyError, ValueError) as exc:
        return _err(str(exc))
    return jsonify({"ok": True, "status": agreement.status,
                    "status_label": agreement.status_label})


@bp.post("/agreements/<int:agreement_id>/sign-buyer")
@login_required
def sign_buyer(agreement_id: int):
    """Countersign and execute. This is the call that actually moves ownership."""
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    if not access.is_party(agreement.buyer_party_id):
        return _forbidden(f"countersign as {agreement.buyer.display_name}")
    # Check the state before starting a job. The service layer guards this too,
    # but failing there would mean answering "accepted" to a request that was
    # never going to run.
    if agreement.status != "awaiting_buyer":
        return _err(f"agreement {agreement.agreement_ref} is "
                    f"{agreement.status_label.lower()} — it is not awaiting the "
                    "transferee's signature")
    refusal = _require_live(agreement.dataset)
    if refusal:
        return refusal
    app = current_app._get_current_object()

    def run(job):
        with app.app_context():
            row = db.session.get(TransferAgreement, agreement_id)
            job.emit(f"Countersigning {row.agreement_ref} for {row.buyer.display_name}")
            cert = agreementsvc.sign_buyer_and_execute(row, job=job)
            # Built as a plain path: this runs in a worker thread with no
            # request context, so url_for has no host to build against.
            job.result.update({
                "certificate_id": cert.certificate_id,
                "href": f"/certificate/{cert.certificate_id}",
                "agreement_ref": row.agreement_ref,
            })

    job = jobs.start(
        "transfer", run,
        title=f"Transferring {agreement.dataset.name}",
        subject=f"{agreement.agreement_ref}: {agreement.seller.display_name} to "
                f"{agreement.buyer.display_name} — BLS tags are re-signed and the "
                f"deed is issued",
        href=f"/console/agreements/{agreement_id}",
        party_ids=(agreement.seller_party_id, agreement.buyer_party_id),
        user_id=current_user.id)
    return jsonify({"ok": True, "job_id": job.id})


@bp.post("/agreements/<int:agreement_id>/decline")
@login_required
def decline_agreement(agreement_id: int):
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    if not access.is_party(agreement.awaiting_party_id):
        return _forbidden(f"decline {agreement.agreement_ref} — only the party "
                          "it is waiting on can turn it down")
    data = request.get_json(force=True)
    try:
        agreementsvc.decline(agreement, data.get("reason", ""))
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"ok": True})


@bp.post("/agreements/<int:agreement_id>/cancel")
@login_required
def cancel_agreement(agreement_id: int):
    """Withdraw an instrument you raised. The other side declines instead."""
    agreement = TransferAgreement.query.get_or_404(agreement_id)
    raiser = (agreement.buyer_party_id if agreement.is_bid
              else agreement.seller_party_id)
    if not access.is_party(raiser):
        return _forbidden(f"withdraw {agreement.agreement_ref} — only the party "
                          "that raised it can take it back")
    try:
        agreementsvc.withdraw(agreement)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"ok": True})

# ----------------------------------------------------------------------
# Attack demonstrations
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Analytics — live data for charts (no static images)
# ----------------------------------------------------------------------

@bp.get("/analytics/benchmarks")
@login_required
def analytics_benchmarks():
    """Measured performance series from the benchmark suite, for charting."""
    import json as _json

    from settings import CODE_DIR

    results_dir = CODE_DIR / "benchmarks" / "results"
    path = results_dir / "benchmarks_full.json"
    if not path.exists():
        path = results_dir / "benchmarks.json"
    if not path.exists():
        return _err("no benchmark results available", 404)
    payload = _json.loads(path.read_text())

    def series(section: str, label: str):
        pts = [p for p in payload["results"].get(section, []) if p["label"] == label]
        return [{"x": p["x"], "y": round(p["seconds"] * 1000, 1)} for p in sorted(pts, key=lambda p: p["x"])]

    data = {
        "meta": {
            "mode": payload["metadata"].get("mode", ""),
            "machine": payload["metadata"].get("machine", ""),
            "repeats": payload["metadata"].get("repeats", 0),
        },
        "keygen": {
            "proposed": series("keygen", "proposed"),
            "baseline": series("keygen", "baseline"),
        },
        "taggen": {
            "s1": series("taggen", "s=1"),
            "s5": series("taggen", "s=5"),
            "s10": series("taggen", "s=10"),
        },
        "audit": {
            "proofgen": series("audit", "proofgen_s1"),
            "verify": series("audit", "verify_s1"),
            "verify_s10": series("audit", "verify_s10"),
        },
        "owner_modify": {
            "add_proposed": series("owner_modify", "add_proposed"),
            "add_baseline": series("owner_modify", "add_baseline"),
            "revoke_proposed": series("owner_modify", "revoke_proposed"),
            "revoke_baseline": series("owner_modify", "revoke_baseline"),
        },
        "tag_update": {
            "s1": series("tag_update", "update_s1"),
            "s10": series("tag_update", "update_s10"),
        },
        "transfer": series("transfer", "transfer"),
    }
    return jsonify({"ok": True, "data": data})


@bp.get("/analytics/operations")
@login_required
def analytics_operations():
    """Audit history from this deployment, for the operations dashboard."""
    rows = (AuditRow.query.order_by(AuditRow.created_at.asc()).limit(200).all())
    return jsonify({
        "ok": True,
        "data": [
            {
                "t": r.created_at.strftime("%H:%M:%S"),
                "date": r.created_at.strftime("%d %b %H:%M"),
                "duration_ms": round(r.duration_ms, 1),
                "passed": r.passed,
                "challenged": r.challenged,
                "total": r.total_blocks,
            }
            for r in rows
        ],
    })


# ----------------------------------------------------------------------
# Account settings
# ----------------------------------------------------------------------

@bp.post("/settings/profile")
@login_required
def update_profile():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    organization = data.get("organization", "").strip()
    if not name:
        return _err("name is required")
    current_user.name = name
    current_user.organization = organization
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/settings/password")
@login_required
def update_password():
    data = request.get_json(force=True)
    current = data.get("current", "")
    new = data.get("new", "")
    if not current_user.check_password(current):
        return _err("current password is incorrect")
    if len(new) < 8:
        return _err("new password must be at least 8 characters")
    current_user.set_password(new)
    db.session.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Console shell: region + verification schedules
# ----------------------------------------------------------------------

@bp.post("/region")
@login_required
def set_region():
    data = request.get_json(force=True)
    region = data.get("region", "")
    if region not in REGION_NAMES:
        return _err("unknown region")
    session["region"] = region
    return jsonify({"ok": True})


@bp.post("/datasets/<int:dataset_id>/schedule")
@login_required
def set_schedule(dataset_id: int):
    dataset = Dataset.query.get_or_404(dataset_id)
    refusal = _guard_dataset(dataset, "schedule verification for")
    if refusal:
        return refusal
    data = request.get_json(force=True)
    every = data.get("every_min")
    if every in (None, "", 0, "0"):
        dataset.audit_every_min = None
        db.session.commit()
        record_event("schedule", "Continuous verification disabled", dataset_id)
        return jsonify({"ok": True, "every_min": None})
    every = int(every)
    if not (1 <= every <= 1440):
        return _err("interval must be between 1 minute and 24 hours")
    dataset.audit_every_min = every
    db.session.commit()
    record_event("schedule",
                 f"Continuous verification enabled: every {every} minute(s)",
                 dataset_id)
    return jsonify({"ok": True, "every_min": every})


# ----------------------------------------------------------------------
# Access keys
# ----------------------------------------------------------------------

@bp.post("/keys")
@login_required
def create_key():
    data = request.get_json(force=True)
    if AccessKey.query.filter_by(user_id=current_user.id).count() >= 5:
        return _err("an account can hold at most 5 access keys — delete one first")
    row, secret = AccessKey.generate(current_user.id,
                                     data.get("description", "").strip()[:160])
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "key_id": row.key_id, "secret": secret})


@bp.post("/keys/<int:key_id>/toggle")
@login_required
def toggle_key(key_id: int):
    row = AccessKey.query.filter_by(id=key_id, user_id=current_user.id).first_or_404()
    row.status = "disabled" if row.status == "active" else "active"
    db.session.commit()
    return jsonify({"ok": True, "status": row.status})


@bp.delete("/keys/<int:key_id>")
@login_required
def delete_key(key_id: int):
    row = AccessKey.query.filter_by(id=key_id, user_id=current_user.id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Support tickets
# ----------------------------------------------------------------------

@bp.post("/tickets")
@login_required
def create_ticket():
    data = request.get_json(force=True)
    subject = data.get("subject", "").strip()
    if not subject:
        return _err("a subject is required")
    row = SupportTicket(
        user_id=current_user.id,
        subject=subject[:200],
        category=data.get("category", "General guidance")[:60],
        severity=data.get("severity", "Normal")[:30],
        body=data.get("body", "").strip()[:4000],
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "id": row.id, "ticket_no": row.ticket_no})


@bp.post("/tickets/<int:ticket_id>/resolve")
@login_required
def resolve_ticket(ticket_id: int):
    row = SupportTicket.query.filter_by(id=ticket_id,
                                        user_id=current_user.id).first_or_404()
    row.status = "resolved"
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/attacks/<name>")
@login_required
def run_attack(name: str):
    if name not in {"collusion", "rogue_key", "tampering"}:
        return _err("unknown attack")
    app = current_app._get_current_object()

    def run(job):
        result = _hub().run_attack(job, name)
        with app.app_context():
            labels = {"collusion": "Collusion resistance",
                      "rogue_key": "Key-substitution resistance",
                      "tampering": "Tamper detection"}
            record_event("attack", f"Security validation completed: {labels[name]}")
        job.result.update(result)

    labels = {"collusion": "Collusion resistance",
              "rogue_key": "Key-substitution resistance",
              "tampering": "Tamper detection"}
    job = jobs.start(
        f"attack:{name}", run,
        title=f"Security validation — {labels[name]}",
        subject="Running the attack script against the live implementation",
        href="/console/security",
        party_ids=(access.party_id(),), user_id=current_user.id)
    return jsonify({"ok": True, "job_id": job.id})
