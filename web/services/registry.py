"""
Party registry and signing keys.

Two kinds of key live here:

* The **issuing key** — one Ed25519 key pair per deployment, held in
  `instance/issuer_ed25519`, or in ATTESTRA_ISSUER_KEY where there is no
  persistent disk. Every certificate this platform issues is signed
  with it, and the public half is printed on the certificate so a verifier can
  check the document without trusting (or even reaching) this database.

* **Party keys** — one Ed25519 key pair per registered party, used to sign
  transfer agreements. They are held in custody by the platform, which is the
  same model an electronic-signature provider operates: the platform attests
  that the authenticated account holder applied the signature. That is an
  honest description of the guarantee, and the certificate says so in as many
  words rather than implying the party generated the key on its own hardware.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from extensions import db
from models import MEMBERSHIP_TYPES, Party, PartyMember, utcnow
from settings import INSTANCE_DIR

ISSUER_NAME = "Attestra Registry Authority"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode())


# ----------------------------------------------------------------------
# Issuing key
# ----------------------------------------------------------------------

def _issuer_path() -> Path:
    return Path(INSTANCE_DIR) / "issuer_ed25519"


def issuer_private() -> Ed25519PrivateKey:
    # A deployment without a persistent disk cannot keep this in a file: the
    # filesystem is rebuilt on every restart, and a freshly generated key would
    # silently invalidate every certificate already issued — the public half is
    # printed on each deed, and a verifier checks against it. ATTESTRA_ISSUER_KEY
    # carries the same base64 the file would, so the registry keeps one identity
    # for the life of the deployment.
    from_env = os.environ.get("ATTESTRA_ISSUER_KEY", "").strip()
    if from_env:
        return Ed25519PrivateKey.from_private_bytes(_unb64(from_env))

    path = _issuer_path()
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_b64(key.private_bytes_raw()))
        path.chmod(0o600)
        return key
    return Ed25519PrivateKey.from_private_bytes(_unb64(path.read_text().strip()))


def issuer_public_b64() -> str:
    return _b64(issuer_private().public_key().public_bytes_raw())


def sign_as_issuer(message: bytes) -> str:
    return _b64(issuer_private().sign(message))


def verify_issuer_signature(message: bytes, signature_b64: str,
                            public_b64: str | None = None) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            _unb64(public_b64 or issuer_public_b64()))
        pub.verify(_unb64(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ----------------------------------------------------------------------
# Party keys and signatures
# ----------------------------------------------------------------------

def ensure_party_keys(party: Party) -> None:
    if party.public_key and party.private_key:
        return
    key = Ed25519PrivateKey.generate()
    party.private_key = _b64(key.private_bytes_raw())
    party.public_key = _b64(key.public_key().public_bytes_raw())


def sign_as_party(party: Party, message: bytes) -> str:
    ensure_party_keys(party)
    key = Ed25519PrivateKey.from_private_bytes(_unb64(party.private_key))
    return _b64(key.sign(message))


def verify_party_signature(public_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(public_b64)).verify(
            _unb64(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ----------------------------------------------------------------------
# Party records
# ----------------------------------------------------------------------

def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "party"


def party_exists(display_name: str) -> bool:
    return Party.query.filter_by(slug=slugify(display_name)).first() is not None


def unique_slug(base: str) -> str:
    slug = slugify(base)
    if Party.query.filter_by(slug=slug).first() is None:
        return slug
    n = 2
    while Party.query.filter_by(slug=f"{slug}-{n}").first() is not None:
        n += 1
    return f"{slug}-{n}"


def create_party(display_name: str, party_type: str = "company", *,
                 legal_name: str = "", registration_id: str = "",
                 jurisdiction: str = "", contact_email: str = "",
                 slug: str | None = None, verified: bool = False,
                 verification_method: str = "") -> Party:
    party = Party(
        slug=slug or unique_slug(display_name),
        display_name=display_name.strip(),
        legal_name=legal_name.strip() or display_name.strip(),
        party_type=party_type,
        registration_id=registration_id.strip(),
        jurisdiction=jurisdiction.strip(),
        contact_email=contact_email.strip(),
        verified=verified,
        verification_method=verification_method,
        verified_at=utcnow() if verified else None,
    )
    ensure_party_keys(party)
    db.session.add(party)
    db.session.commit()
    return party


def get_or_create_by_slug(slug: str, party_type: str = "company") -> Party:
    """Used when a protocol-level owner id has no register entry yet."""
    party = Party.query.filter_by(slug=slug).first()
    if party is not None:
        return party
    display = slug.replace("-", " ").title()
    return create_party(display, party_type, slug=slug)


def verify_party(party: Party, method: str) -> Party:
    party.verified = True
    party.verification_method = method
    party.verified_at = utcnow()
    db.session.commit()
    return party


# ----------------------------------------------------------------------
# Membership: the people inside a party that is made of people
# ----------------------------------------------------------------------
#
# A company is one legal person and signs as one. A group of individuals is
# not: it is several people who agreed to hold something together, and the
# register has to say who they are. Members are admitted by email address —
# the address is the identity a person actually answers to — and each one is
# given a protocol-level identifier so that when the group holds a dataset,
# the cryptographic owner set is the members themselves.

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match((value or "").strip()))


def member_slug(party: Party, email: str, name: str = "") -> str:
    """A stable protocol owner id for one member, unique across the register."""
    base = slugify(name) if name else slugify((email or "").split("@")[0])
    base = base or "member"
    taken = {m.slug for m in PartyMember.query.all() if m.slug}
    taken |= {p.slug for p in Party.query.all()}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def add_member(party: Party, email: str, name: str = "",
               role: str = "member") -> PartyMember:
    email = (email or "").strip().lower()
    if not valid_email(email):
        raise ValueError(f"{email or 'that'} is not a valid email address")
    if party.party_type not in MEMBERSHIP_TYPES:
        raise ValueError(f"a {party.type_label.lower()} does not hold a member list")
    if any(m.email.lower() == email for m in party.members):
        raise ValueError(f"{email} is already a member of {party.display_name}")
    if role == "owner" and party.owner_member is not None:
        raise ValueError(f"{party.display_name} already has an owner")
    member = PartyMember(
        party_id=party.id, email=email, name=(name or "").strip()[:160],
        role="owner" if role == "owner" else "member",
        slug=member_slug(party, email, name),
    )
    db.session.add(member)
    db.session.commit()
    return member


def remove_member(party: Party, member: PartyMember) -> None:
    if member.party_id != party.id:
        raise ValueError("that member does not belong to this party")
    if member.role == "owner":
        raise ValueError("the owner of a group cannot be removed from it — "
                         "the group would be left with nobody accountable for it")
    db.session.delete(member)
    db.session.commit()
