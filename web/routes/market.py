"""
Public marketplace and the certificate verification portal.

Both are deliberately outside the console. A deed is worth little if reading
it requires an account with the registry that issued it, so `/verify` answers
to anyone — a buyer's counsel, a regulator, an insurer, a court clerk — with
no session and no sign-up.
"""

from flask import (Blueprint, abort, redirect, render_template, request,
                   url_for)

from models import (CURRENCIES, Certificate, CustodyEvent, Dataset, Listing,
                    Party, TransferAgreement)
from services import access, certificates as certsvc

bp = Blueprint("market", __name__)


@bp.get("/marketplace")
def marketplace():
    q = (request.args.get("q") or "").strip().lower()
    category = request.args.get("category") or "all"
    listings = (Listing.query
                .filter(Listing.status.in_(["active", "under_offer"]))
                .order_by(Listing.created_at.desc()).all())
    categories = sorted({r.category for r in listings})
    if category != "all":
        listings = [r for r in listings if r.category == category]
    if q:
        listings = [r for r in listings
                    if q in r.title.lower() or q in r.summary.lower()
                    or q in r.seller.display_name.lower()]
    sold = Listing.query.filter_by(status="sold").count()
    return render_template(
        "public/marketplace.html",
        active_page="marketplace",
        listings=listings,
        categories=categories,
        category=category,
        q=request.args.get("q") or "",
        total_live=len([r for r in Listing.query
                        .filter(Listing.status.in_(["active", "under_offer"])).all()]),
        total_sold=sold,
        certificates_issued=Certificate.query.count(),
    )


@bp.get("/marketplace/<int:listing_id>")
def listing_detail(listing_id: int):
    listing = Listing.query.get_or_404(listing_id)
    chain = (CustodyEvent.query.filter_by(dataset_id=listing.dataset_id)
             .order_by(CustodyEvent.seq).all())
    # How many other parties are bidding — a fact a bidder is entitled to know.
    # What they bid is not: those figures belong to the holder alone.
    open_offers = TransferAgreement.query.filter(
        TransferAgreement.dataset_id == listing.dataset_id,
        TransferAgreement.status == "awaiting_seller").count()
    return render_template(
        "public/listing_detail.html",
        active_page="marketplace",
        l=listing,
        chain=chain,
        currencies=CURRENCIES,
        # A holder cannot bid for what it already owns; it sees that instead.
        is_holder=access.can_act_for(listing.seller_party_id),
        open_offers=open_offers,
    )


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    reference = (request.form.get("certificate_id")
                 or request.args.get("id") or "").strip().upper()
    if not reference:
        return render_template("public/verify.html", active_page="verify",
                               reference="", report=None, cert=None,
                               issued=Certificate.query.count())
    cert = Certificate.query.filter_by(certificate_id=reference).first()
    if cert is None:
        return render_template(
            "public/verify.html", active_page="verify", reference=reference,
            report=None, cert=None, not_found=True,
            issued=Certificate.query.count())
    return render_template(
        "public/verify.html", active_page="verify", reference=reference,
        cert=cert, report=certsvc.verify(cert),
        chain=(CustodyEvent.query.filter_by(dataset_id=cert.dataset_id)
               .order_by(CustodyEvent.seq).all()),
        issued=Certificate.query.count())


@bp.get("/certificate/<certificate_id>")
def certificate_document(certificate_id: str):
    """The deed itself, as a standalone printable page anyone can open."""
    cert = Certificate.query.filter_by(
        certificate_id=certificate_id.strip().upper()).first_or_404()
    return render_template(
        "certificate.html",
        cert=cert,
        p=cert.payload,
        report=certsvc.verify(cert),
    )


@bp.get("/certificate/<certificate_id>.json")
def certificate_json(certificate_id: str):
    """The signed body, for a verifier that wants to check it independently."""
    from flask import Response

    cert = Certificate.query.filter_by(
        certificate_id=certificate_id.strip().upper()).first_or_404()
    import json as _json
    doc = _json.dumps({
        "payload": cert.payload,
        "payload_hash": cert.payload_hash,
        "signature": cert.signature,
        "issuer_public_key": cert.issuer_key,
        "algorithm": "Ed25519",
        "note": "Verify: Ed25519(issuer_public_key).verify(signature, "
                "canonical_json(payload)) where canonical_json sorts keys and "
                "uses ',' and ':' separators.",
    }, indent=2)
    return Response(doc, mimetype="application/json")
