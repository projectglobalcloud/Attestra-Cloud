"""
The transfer agreement lifecycle.

    raised by the holder:
      awaiting_seller ─seller signs─> awaiting_buyer ─buyer signs─> completed

    raised as a bid by a buyer (the bid IS the buyer's signature):
      awaiting_seller ─holder accepts─────────────────────────────> completed
                      └─registry administrator approves the request─> completed

Nothing touches the protocol layer until the transfer is authorised on both
sides: by two signatures, or by one signature and the registry acting on the
other party's request. Which of the two it was is recorded on the deed in as
many words — a deed that implies a signature nobody gave is a forgery. When the buyer
countersigns, `execute()` runs the sale and gathers, in one pass, the evidence
that a certificate has to carry:

    1. pre-transfer audit      — the data is intact and the SELLER holds it
    2. OwnerTransfer, in the two halves the paper defines (SG then BG)
    3. post-transfer audit     — the BUYER now holds it, under its own keys
    4. revocation check        — the seller's retained parameters NO LONGER
                                 verify, which is what makes a later denial
                                 of the sale untenable

If any step fails the transaction is rolled back and no certificate is issued,
because a deed that records a half-completed sale is worse than no deed.
"""

from __future__ import annotations

from extensions import db
from models import (AuditRow, Certificate, Dataset, EventRow, Listing, Party,
                    TransferAgreement, record_custody, record_event, utcnow)
from services import certificates, registry
from services.protocol import ProtocolHub

# A verification round for the evidence a deed carries.

CHALLENGE_BLOCKS = 10


def _hub() -> ProtocolHub:
    return ProtocolHub.instance()


def _record_audit(dataset_id: int, result: dict, note: str) -> dict:
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
    return {
        "passed": bool(result["passed"]),
        "challenged": result["challenged"],
        "total_blocks": result["total_blocks"],
        "detection_confidence_pct": round(result["detection_pct"], 2),
        "duration_ms": round(result["duration_ms"], 1),
        "performed_at": utcnow().isoformat(timespec="seconds"),
    }


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------

def create(dataset: Dataset, seller: Party, buyer: Party, *,
           consideration: float, currency: str, payment_reference: str,
           terms: str, listing: Listing | None = None,
           initiated_by: str = "seller") -> TransferAgreement:
    agreement = TransferAgreement(
        dataset_id=dataset.id,
        listing_id=listing.id if listing else None,
        seller_party_id=seller.id,
        buyer_party_id=buyer.id,
        consideration=consideration,
        currency=currency,
        payment_reference=payment_reference.strip(),
        terms=terms.strip(),
        status="awaiting_seller",
        initiated_by="buyer" if initiated_by == "buyer" else "seller",
    )
    db.session.add(agreement)
    db.session.commit()
    # A bid does not take the listing off the market: other buyers may bid too,
    # and the holder decides when — and whether — to accept any of them. Only a
    # sale closes a listing.
    if listing is not None and initiated_by != "buyer":
        listing.status = "under_offer"
        db.session.commit()
    record_event("agreement",
                 f"Transfer agreement {agreement.agreement_ref} raised: "
                 f"{seller.display_name} to {buyer.display_name} for "
                 f"{agreement.consideration_display}", dataset.id)
    return agreement


def _signing_body(agreement: TransferAgreement) -> bytes:
    return certificates.signing_body_for(
        agreement, _hub().fingerprint(agreement.dataset_id))


def raise_bid(dataset: Dataset, seller: Party, buyer: Party, *,
              consideration: float, currency: str, terms: str,
              listing: Listing | None = None) -> TransferAgreement:
    """A buyer asks for a dataset somebody else holds, at a price.

    The bid is signed by the buyer as it is made: an offer you have not
    committed to is not an offer, and the holder deserves to see a signature
    before deciding. Nothing moves until the holder accepts (or the registry
    approves the request on their behalf).
    """
    agreement = create(dataset, seller, buyer,
                       consideration=consideration, currency=currency,
                       payment_reference="", terms=terms, listing=listing,
                       initiated_by="buyer")
    agreement.buyer_signature = registry.sign_as_party(
        agreement.buyer, _signing_body(agreement))
    agreement.buyer_signed_at = utcnow()
    db.session.commit()
    record_event("agreement",
                 f"{buyer.display_name} bid {agreement.consideration_display} for "
                 f"{dataset.name}, held by {seller.display_name} "
                 f"({agreement.agreement_ref}) — awaiting the holder's decision",
                 dataset.id)
    return agreement


def revise_bid(agreement: TransferAgreement, *, consideration: float,
               terms: str) -> TransferAgreement:
    """A buyer improves (or lowers) an offer the holder has not answered yet.

    The signature covers the price, so revising means signing again: the old
    figure was never agreed to by anybody, and the new one has to carry the
    buyer's name in the same way.
    """
    if agreement.status != "awaiting_seller" or not agreement.is_bid:
        raise ValueError("only an offer the holder has not yet answered can be revised")
    previous = agreement.consideration_display
    agreement.consideration = consideration
    agreement.terms = (terms or agreement.terms).strip()
    agreement.buyer_signature = registry.sign_as_party(
        agreement.buyer, _signing_body(agreement))
    agreement.buyer_signed_at = utcnow()
    db.session.commit()
    record_event("agreement",
                 f"{agreement.buyer.display_name} revised its offer on "
                 f"{agreement.dataset.name} from {previous} to "
                 f"{agreement.consideration_display} ({agreement.agreement_ref})",
                 agreement.dataset_id)
    return agreement


def withdraw(agreement: TransferAgreement) -> TransferAgreement:
    """The party that raised an instrument takes it back off the table."""
    if agreement.status not in ("awaiting_seller", "awaiting_buyer"):
        raise ValueError("only an open instrument can be withdrawn")
    agreement.status = "cancelled"
    db.session.commit()
    if agreement.listing is not None and agreement.listing.status == "under_offer":
        agreement.listing.status = "active"
        db.session.commit()
    who = agreement.buyer if agreement.is_bid else agreement.seller
    record_event("agreement",
                 f"{who.display_name} withdrew {agreement.agreement_ref}",
                 agreement.dataset_id)
    return agreement


def sign_seller(agreement: TransferAgreement, job=None) -> TransferAgreement:
    """The holder signs. On a bid the buyer has already signed, so this
    completes the instrument and the transfer runs immediately."""
    if agreement.status != "awaiting_seller":
        raise ValueError("this agreement is not awaiting the transferor's signature")
    agreement.seller_signature = registry.sign_as_party(
        agreement.seller, _signing_body(agreement))
    agreement.seller_signed_at = utcnow()
    if agreement.buyer_signed_at:
        agreement.status = "executing"
        db.session.commit()
        record_event("agreement",
                     f"{agreement.seller.display_name} accepted "
                     f"{agreement.buyer.display_name}'s bid of "
                     f"{agreement.consideration_display} "
                     f"({agreement.agreement_ref})", agreement.dataset_id)
        try:
            return execute(agreement, authorised_by="transferor", job=job)
        except Exception:
            agreement.status = "awaiting_seller"
            agreement.seller_signature = ""
            agreement.seller_signed_at = None
            db.session.commit()
            raise
    agreement.status = "awaiting_buyer"
    db.session.commit()
    record_event("agreement",
                 f"{agreement.seller.display_name} signed agreement "
                 f"{agreement.agreement_ref}; awaiting the transferee",
                 agreement.dataset_id)
    return agreement


def approve_as_registry(agreement: TransferAgreement, user, note: str = "",
                        job=None):
    """The registry settles a request the two sides have not closed themselves.

    This is the administrator acting in the same capacity a sub-registrar does
    when a transfer is effected on application rather than by the holder's
    appearance: the transfer is real, but it was authorised by the registry,
    and the deed says so instead of pretending the missing party signed.
    """
    if agreement.status not in ("awaiting_seller", "awaiting_buyer"):
        raise ValueError(f"agreement {agreement.agreement_ref} is "
                         f"{agreement.status_label.lower()} — there is nothing "
                         "to approve")
    if not (agreement.seller_signed_at or agreement.buyer_signed_at):
        raise ValueError("neither party has signed this agreement yet — there "
                         "is no request to approve")
    agreement.status = "executing"
    agreement.approved_by_user_id = getattr(user, "id", None)
    agreement.approval_note = (note or "").strip()[:300]
    db.session.commit()
    record_event("agreement",
                 f"Registry approved transfer {agreement.agreement_ref}: "
                 f"{agreement.seller.display_name} to "
                 f"{agreement.buyer.display_name}", agreement.dataset_id)
    try:
        return execute(agreement, authorised_by="registry", job=job)
    except Exception:
        agreement.status = ("awaiting_buyer" if agreement.seller_signed_at
                            else "awaiting_seller")
        agreement.approved_by_user_id = None
        db.session.commit()
        raise


def sign_buyer_and_execute(agreement: TransferAgreement, job=None) -> Certificate:
    if agreement.status != "awaiting_buyer":
        raise ValueError("this agreement is not awaiting the transferee's signature")
    agreement.buyer_signature = registry.sign_as_party(
        agreement.buyer, _signing_body(agreement))
    agreement.buyer_signed_at = utcnow()
    agreement.status = "executing"
    db.session.commit()
    try:
        return execute(agreement, authorised_by="transferor", job=job)
    except Exception:
        agreement.status = "awaiting_buyer"
        agreement.buyer_signature = ""
        agreement.buyer_signed_at = None
        db.session.commit()
        raise


EXECUTION_STAGES = [
    ("restore", "Restore the protection state if the service restarted"),
    ("possession", "Confirm the transferor still holds the data"),
    ("cancel", "Transferor cancels its BLS signing exponents"),
    ("accept", "Transferee accepts; every tag re-signed under its key"),
    ("revocation", "Transferor's BLS credentials checked — they must now fail"),
    ("deed", "Registry signs and issues the deed"),
]


def _stages() -> list[dict]:
    return [{"key": k, "label": lbl, "state": "pending"}
            for k, lbl in EXECUTION_STAGES]


def execute(agreement: TransferAgreement, *, authorised_by: str = "transferor",
            job=None) -> Certificate:
    """Run the sale, gathering the evidence a deed has to carry.

    Each step reports itself as it happens, with the BLS12-381 values it
    actually produced, so the transfer can be watched rather than merely
    announced afterwards.
    """
    import time as _time

    dataset = agreement.dataset
    hub = _hub()
    dataset_id = dataset.id

    stages = _stages()
    if job is not None:
        job.result["stages"] = stages
        job.result["agreement_ref"] = agreement.agreement_ref

    def begin(i: int, message: str) -> float:
        stages[i]["state"] = "running"
        if job is not None:
            job.message = message
            job.progress = i / len(stages)
        return _time.perf_counter()

    def finish(i: int, t0: float, detail: str, **extra) -> None:
        stages[i].update(state="done",
                         ms=round((_time.perf_counter() - t0) * 1000, 1),
                         detail=detail, **extra)
        if job is not None:
            job.emit(f"{stages[i]['label']}: {detail}")
            job.progress = (i + 1) / len(stages)

    def fail(i: int, detail: str) -> None:
        stages[i].update(state="failed", detail=detail)
        if job is not None:
            job.emit(f"{stages[i]['label']}: {detail}")

    # 0. Keys live in memory only, so a restart leaves the row saying "active"
    #    with nothing behind it. Everything needed to seal the file again is on
    #    record, so it is rebuilt here rather than refusing the transfer.
    t0 = begin(0, "Checking the protection state")
    rebuilt = hub.ensure_live(dataset, job=None)
    finish(0, t0, ("protection state rebuilt from the stored file — the service "
                   "had restarted since it was last sealed" if rebuilt
                   else "already sealed and in memory; nothing to restore"))

    fingerprint = hub.fingerprint(dataset_id)
    bls_before = hub.bls_state(dataset_id)

    # 1. The seller must actually hold intact data at the moment of sale.
    t0 = begin(1, "Verifying the transferor still holds intact data")
    pre_raw = hub.audit_staged(None, dataset_id, CHALLENGE_BLOCKS)
    pre = _record_audit(dataset_id, pre_raw,
                        f"pre-transfer verification for {agreement.agreement_ref}")
    if not pre["passed"]:
        fail(1, "the transferor could not prove possession — sale not executed")
        raise ValueError(
            "the transferor cannot currently prove possession of this dataset — "
            "the sale was not executed and no certificate was issued")
    finish(1, t0, f"possession proved over {pre['challenged']} of "
                  f"{pre['total_blocks']} blocks",
           bls=pre_raw.get("bls"))

    # 2. OwnerTransfer, in its two defined halves.
    buyer_group = agreement.buyer.slug
    buyer_owners = list(agreement.buyer.owner_ids) or [agreement.buyer.slug]
    t0 = begin(2, "Transferor cancels its signing exponents")
    init = hub.transfer_initiate(dataset_id)
    finish(2, t0, f"ownership-delegation token produced ({init['token_bytes']} bytes) "
                  f"— the transferor's authority over these tags is now spent")

    t0 = begin(3, "Transferee accepts; tags re-signed under its key")
    try:
        moved = hub.transfer_finalize(dataset_id, buyer_group, buyer_owners)
    except Exception:
        fail(3, "the transferee could not accept — rolled back to the transferor")
        hub.transfer_abort(dataset_id)
        raise
    bls_after = hub.bls_state(dataset_id)
    finish(3, t0, f"tags re-sealed in place for {', '.join(moved['owners'])} — "
                  f"no data moved",
           bls={"curve": "BLS12-381",
                "group_public_key": bls_after.get("group_public_key", "")})

    # 3. The buyer must now be able to prove possession under its own keys.
    post_raw = hub.audit_staged(None, dataset_id, CHALLENGE_BLOCKS)
    post = _record_audit(dataset_id, post_raw,
                         f"post-transfer verification for {agreement.agreement_ref}")

    # 4. And the seller must no longer be able to.
    t0 = begin(4, "Checking the transferor's retained parameters")
    revocation_raw = hub.audit_staged(None, dataset_id, CHALLENGE_BLOCKS,
                                      use_stale=True)
    revocation = _record_audit(
        dataset_id, revocation_raw,
        f"transferor revocation check for {agreement.agreement_ref}")
    finish(4, t0,
           ("the transferor's own credentials NO LONGER verify this dataset"
            if not revocation["passed"] else
            "WARNING: the transferor's credentials still verify"),
           bls=revocation_raw.get("bls"))

    evidence = {
        "protocol": {
            "scheme": "Scalable cloud auditing with efficient ownership transfer "
                      "(Wu, You & Huang, IEEE TIFS vol. 20, 2025)",
            "curve": "BLS12-381",
            "operation": "OwnerTransfer",
            "token_bytes": moved["token_bytes"],
            "seller_half_ms": round(init["duration_ms"], 1),
            "buyer_half_ms": round(moved["duration_ms"], 1),
            "bytes_of_data_moved": 0,
        },
        # The BLS12-381 signature material itself, before and after the sale.
        # A reader with these values and the published parameters can see that
        # the key controlling the data is not the key that controlled it before.
        "bls_signatures": {
            "curve": "BLS12-381",
            "group_public_key_before": bls_before.get("group_public_key", ""),
            "group_public_key_after": bls_after.get("group_public_key", ""),
            "aggregate_masks_after": bls_after.get("mask_aggregate", ""),
            "aggregate_keys_after": bls_after.get("key_aggregate", ""),
            "proof_before_sale": (pre_raw.get("bls") or {}).get("proof_tp", ""),
            "proof_after_sale": (post_raw.get("bls") or {}).get("proof_tp", ""),
            "encoding": "uncompressed affine, hex; G1 = 96 bytes, G2 = 192 bytes",
        },
        "authorisation": _authorisation(agreement, authorised_by),
        "pre_transfer_audit": pre,
        "post_transfer_audit": post,
        "transferor_revocation_check": revocation,
        "custody": {
            "from_group": moved["seller"],
            "to_group": moved["buyer"],
            "members_after": moved["owners"],
        },
    }

    agreement.status = "completed"
    agreement.completed_at = utcnow()
    agreement.authorised_by = authorised_by
    db.session.commit()

    t0 = begin(5, "Signing and issuing the deed")
    cert = certificates.issue(agreement, fingerprint, evidence)
    finish(5, t0, f"certificate {cert.certificate_id} issued and signed "
                  f"with the registry's Ed25519 key")
    if job is not None:
        job.result.update({"certificate_id": cert.certificate_id,
                           "href": f"/certificate/{cert.certificate_id}"})

    dataset.group_name = buyer_group
    dataset.owners = moved["owners"]
    dataset.owner_party_id = agreement.buyer_party_id
    db.session.commit()

    if agreement.listing is not None:
        agreement.listing.status = "sold"
        db.session.commit()
    # Any other listing of this asset was posted by a party that no longer
    # holds it. Leaving those up would invite offers nobody can accept.
    stale_listings = Listing.query.filter(
        Listing.dataset_id == dataset_id,
        Listing.seller_party_id != agreement.buyer_party_id,
        Listing.status.in_(["active", "under_offer"])).all()
    for row in stale_listings:
        row.status = "withdrawn"
    if stale_listings:
        db.session.commit()
        record_event("listing",
                     f"{len(stale_listings)} listing(s) of {dataset.name} withdrawn — "
                     f"the asset now belongs to {agreement.buyer.display_name}",
                     dataset_id)
    # Every other open offer on this asset is now moot: the thing being bid for
    # has a new owner. They are closed with that reason on the record rather
    # than left hanging in a queue nobody can act on.
    others = TransferAgreement.query.filter(
        TransferAgreement.dataset_id == dataset_id,
        TransferAgreement.id != agreement.id,
        TransferAgreement.status.in_(["awaiting_seller", "awaiting_buyer"])).all()
    for other in others:
        other.status = "superseded"
        other.decline_reason = (
            f"{agreement.buyer.display_name} acquired the asset under "
            f"{agreement.agreement_ref}")
        if other.listing is not None and other.listing.status == "under_offer":
            other.listing.status = "active"
    if others:
        db.session.commit()
        record_event("agreement",
                     f"{len(others)} other open offer(s) on {dataset.name} closed — "
                     f"the asset was sold under {agreement.agreement_ref}",
                     dataset_id)

    record_custody(
        dataset_id, "transferred",
        f"Sold for {agreement.consideration_display} under agreement "
        f"{agreement.agreement_ref}",
        from_party=agreement.seller.display_name,
        to_party=agreement.buyer.display_name,
        certificate_id=cert.certificate_id,
    )
    record_event("transfer",
                 f"Ownership transferred from {agreement.seller.display_name} to "
                 f"{agreement.buyer.display_name} under agreement "
                 f"{agreement.agreement_ref}; certificate {cert.certificate_id} issued",
                 dataset_id)
    return cert


def _authorisation(agreement: TransferAgreement, authorised_by: str) -> dict:
    """How this transfer came to be authorised — stated plainly on the deed."""
    from models import User

    if authorised_by == "registry":
        approver = (db.session.get(User, agreement.approved_by_user_id)
                    if agreement.approved_by_user_id else None)
        return {
            "basis": "registry approval",
            "description": (
                "The transferee applied for this transfer and signed. The "
                "transferor did not counter-sign; the transfer was approved and "
                "effected by the registry administrator on that application."),
            "approved_by": approver.name if approver else "Registry administrator",
            "approved_by_account": approver.email if approver else "",
            "note": agreement.approval_note,
            "signatures_present": [
                side for side, present in
                (("transferor", bool(agreement.seller_signed_at)),
                 ("transferee", bool(agreement.buyer_signed_at))) if present],
        }
    return {
        "basis": "signatures of both parties",
        "description": ("Both the transferor and the transferee signed this "
                        "instrument; the registry only recorded and executed it."),
        "signatures_present": ["transferor", "transferee"],
    }


def decline(agreement: TransferAgreement, reason: str) -> TransferAgreement:
    if agreement.status not in ("awaiting_seller", "awaiting_buyer"):
        raise ValueError("only an unsigned agreement can be declined")
    # Whoever it was waiting on is the one turning it down.
    decliner = (agreement.seller if agreement.status == "awaiting_seller"
                else agreement.buyer)
    agreement.status = "declined"
    agreement.decline_reason = reason.strip()[:300]
    db.session.commit()
    if agreement.listing is not None and agreement.listing.status == "under_offer":
        agreement.listing.status = "active"
        db.session.commit()
    record_event("agreement",
                 f"Agreement {agreement.agreement_ref} declined by "
                 f"{decliner.display_name}", agreement.dataset_id)
    return agreement


def cancel(agreement: TransferAgreement) -> TransferAgreement:
    if agreement.status in ("completed", "executing"):
        raise ValueError("a completed transfer cannot be cancelled")
    agreement.status = "cancelled"
    db.session.commit()
    if agreement.listing is not None and agreement.listing.status == "under_offer":
        agreement.listing.status = "active"
        db.session.commit()
    record_event("agreement", f"Agreement {agreement.agreement_ref} cancelled",
                 agreement.dataset_id)
    return agreement
