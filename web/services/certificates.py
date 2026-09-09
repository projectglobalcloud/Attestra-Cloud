"""
Deeds of transfer: issuing, and verifying one without an account.

A certificate is a JSON body serialised canonically (sorted keys, no
insignificant whitespace) and signed with the platform issuing key. Because
the body carries the evidence rather than pointing at it, a verifier holding
only the document and the issuer's public key can establish:

  * which dataset moved, by fingerprint, not by name;
  * who sold and who bought, as legal parties, with what identity checks;
  * what was paid, and against which payment reference;
  * that both sides signed, and when;
  * that the data was intact and the seller genuinely held it before the sale
    (pre-transfer audit), that the buyer holds it after (post-transfer audit),
    and — the finding that settles a later dispute — that the seller's own
    credentials NO LONGER verify the dataset (revocation check).

The last of those is the point of the whole exercise. A seller who later
claims the sale never happened has to explain a signed record, issued at the
time, showing their own keys failing against data they say is still theirs.
"""

from __future__ import annotations

import hashlib
import json
import secrets

from extensions import db
from models import Certificate, TransferAgreement, utcnow
from services import registry

SPEC_VERSION = "attestra-deed/1.0"


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def new_certificate_id() -> str:
    """Human-transcribable reference, in the shape registrars actually print."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I/O/0/1
    while True:
        body = "".join(secrets.choice(alphabet) for _ in range(12))
        cid = f"ATX-{body[:4]}-{body[4:8]}-{body[8:]}"
        if Certificate.query.filter_by(certificate_id=cid).first() is None:
            return cid


def build_payload(agreement: TransferAgreement, *, certificate_id: str,
                  dataset_fingerprint: dict, evidence: dict) -> dict:
    ds = agreement.dataset
    return {
        "spec": SPEC_VERSION,
        "certificate_id": certificate_id,
        "issued_at": utcnow().isoformat(timespec="seconds"),
        "issuer": {
            "name": registry.ISSUER_NAME,
            "public_key": registry.issuer_public_b64(),
            "algorithm": "Ed25519",
        },
        "instrument": {
            "type": "Deed of dataset ownership transfer",
            "agreement_ref": agreement.agreement_ref,
            "executed_at": (agreement.completed_at or utcnow()).isoformat(timespec="seconds"),
        },
        "asset": {
            "name": ds.name,
            "region": ds.region,
            "fingerprint": dataset_fingerprint,
        },
        "parties": {
            "transferor": agreement.seller.snapshot(),
            "transferee": agreement.buyer.snapshot(),
        },
        "consideration": {
            "amount": round(agreement.consideration, 2),
            "currency": agreement.currency,
            "payment_reference": agreement.payment_reference,
        },
        "terms": agreement.terms,
        "authorisation": evidence.get("authorisation", {}),
        "signatures": {
            "transferor": {
                "signature": agreement.seller_signature,
                "signed_at": agreement.seller_signed_at.isoformat(timespec="seconds")
                if agreement.seller_signed_at else "",
                "public_key": agreement.seller.public_key,
            },
            "transferee": {
                "signature": agreement.buyer_signature,
                "signed_at": agreement.buyer_signed_at.isoformat(timespec="seconds")
                if agreement.buyer_signed_at else "",
                "public_key": agreement.buyer.public_key,
            },
            "custody_model": "Platform-custodied signing keys; the registry "
                             "attests that the authenticated account holder "
                             "applied each signature.",
        },
        "evidence": evidence,
    }


def issue(agreement: TransferAgreement, dataset_fingerprint: dict,
          evidence: dict) -> Certificate:
    cid = new_certificate_id()
    payload = build_payload(agreement, certificate_id=cid,
                            dataset_fingerprint=dataset_fingerprint,
                            evidence=evidence)
    body = canonical(payload)
    row = Certificate(
        certificate_id=cid,
        agreement_id=agreement.id,
        dataset_id=agreement.dataset_id,
        payload_json=json.dumps(payload, indent=2, sort_keys=True),
        payload_hash=hashlib.sha256(body).hexdigest(),
        signature=registry.sign_as_issuer(body),
        issuer_key=registry.issuer_public_b64(),
    )
    db.session.add(row)
    db.session.commit()
    return row


# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------

def verify(certificate: Certificate) -> dict:
    """Re-derive every check a third party would run, and report each one."""
    payload = certificate.payload
    body = canonical(payload)
    computed_hash = hashlib.sha256(body).hexdigest()

    checks = []

    intact = computed_hash == certificate.payload_hash
    checks.append({
        "label": "Document integrity",
        "passed": intact,
        "detail": ("The document body hashes to the value recorded at issue "
                   f"({computed_hash[:16]}…)." if intact else
                   "The document body does not match its recorded hash — it has "
                   "been altered since issue."),
    })

    signed = registry.verify_issuer_signature(
        body, certificate.signature, payload.get("issuer", {}).get("public_key"))
    checks.append({
        "label": "Registry signature",
        "passed": signed,
        "detail": ("Signed by " + registry.ISSUER_NAME + " under the Ed25519 key "
                   "printed on this certificate." if signed else
                   "The registry signature does not verify against the stated key."),
    })

    sigs = payload.get("signatures", {})
    agreement_body = agreement_signing_body(payload)
    authorisation = payload.get("evidence", {}).get("authorisation", {})
    by_registry = authorisation.get("basis") == "registry approval"
    for role, label in (("transferor", "Transferor signature"),
                        ("transferee", "Transferee signature")):
        entry = sigs.get(role, {})
        ok = bool(entry.get("signature")) and registry.verify_party_signature(
            entry.get("public_key", ""), agreement_body, entry.get("signature", ""))
        who = payload.get("parties", {}).get(role, {}).get("legal_name", role)
        if ok:
            detail = f"{who} signed on {entry.get('signed_at', 'an unrecorded date')}."
        elif by_registry:
            # A deed effected on the registry's approval is not defective for
            # want of the absent signature — but it must say so rather than
            # let a reader assume one was given.
            detail = (f"{who} did not sign. This transfer was effected on the "
                      f"registry's approval of the other party's application "
                      f"(see 'Authority for this transfer' below), by "
                      f"{authorisation.get('approved_by', 'the registry')}.")
        else:
            detail = f"No valid signature is present for {who}."
        checks.append({
            "label": label,
            "passed": ok or by_registry,
            "signed": ok,
            "by_registry": (not ok) and by_registry,
            "detail": detail,
        })

    if by_registry:
        approver = authorisation.get("approved_by", "the registry administrator")
        checks.append({
            "label": "Authority for this transfer",
            "passed": True,
            "detail": (f"Effected by the registry on application, approved by "
                       f"{approver}."
                       + (f" Recorded note: {authorisation['note'].rstrip('.')}."
                          if authorisation.get("note") else "")),
        })

    ev = payload.get("evidence", {})
    pre = ev.get("pre_transfer_audit", {})
    post = ev.get("post_transfer_audit", {})
    rev = ev.get("transferor_revocation_check", {})

    checks.append({
        "label": "Asset held by transferor before sale",
        "passed": bool(pre.get("passed")),
        "detail": (f"Integrity verification passed against {pre.get('challenged', 0)} "
                   f"of {pre.get('total_blocks', 0)} blocks immediately before "
                   "execution." if pre.get("passed") else
                   "No passing pre-transfer verification is recorded."),
    })
    checks.append({
        "label": "Asset held by transferee after sale",
        "passed": bool(post.get("passed")),
        "detail": (f"Integrity verification passed under the transferee's own "
                   f"credentials against {post.get('challenged', 0)} of "
                   f"{post.get('total_blocks', 0)} blocks." if post.get("passed") else
                   "No passing post-transfer verification is recorded."),
    })
    revoked_ok = rev.get("passed") is False
    checks.append({
        "label": "Transferor's credentials revoked",
        "passed": revoked_ok,
        "detail": ("Verification attempted with the transferor's retained "
                   "parameters FAILED, as it must after a completed sale — the "
                   "transferor can no longer prove possession of this asset."
                   if revoked_ok else
                   "No revocation check is recorded, or the transferor's "
                   "credentials still verify."),
    })

    revoked = certificate.status == "revoked"
    checks.append({
        "label": "Certificate standing",
        "passed": not revoked,
        "detail": "Not revoked; current on the register." if not revoked
        else "This certificate has been revoked by the registry.",
    })

    return {
        "valid": all(c["passed"] for c in checks),
        "checks": checks,
        "payload": payload,
        "payload_hash": certificate.payload_hash,
    }


def agreement_signing_body(payload: dict) -> bytes:
    """The exact bytes each party signs — the deal, not the whole document.

    Kept free of anything produced after signing (evidence, issue time, the
    certificate id) so both parties sign a stable, reviewable statement.
    """
    return canonical({
        "agreement_ref": payload["instrument"]["agreement_ref"],
        "asset": payload["asset"],
        "consideration": payload["consideration"],
        "parties": {
            "transferor": payload["parties"]["transferor"]["ref"],
            "transferee": payload["parties"]["transferee"]["ref"],
        },
        "terms": payload["terms"],
    })


def signing_body_for(agreement: TransferAgreement, dataset_fingerprint: dict) -> bytes:
    """Same statement, built before the certificate exists (used at signing)."""
    return canonical({
        "agreement_ref": agreement.agreement_ref,
        "asset": {
            "name": agreement.dataset.name,
            "region": agreement.dataset.region,
            "fingerprint": dataset_fingerprint,
        },
        "consideration": {
            "amount": round(agreement.consideration, 2),
            "currency": agreement.currency,
            "payment_reference": agreement.payment_reference,
        },
        "parties": {
            "transferor": agreement.seller.party_ref,
            "transferee": agreement.buyer.party_ref,
        },
        "terms": agreement.terms,
    })
