"""
Database models.

The database stores ACCOUNTS and HISTORY — things that must survive a restart.
Live cryptographic state (keys, masks, group state, stored blocks/tags) lives
in the in-process ProtocolHub (services/protocol.py) and is deliberately never
written to disk: secret keys on disk would contradict the trust model the
protocol exists to enforce. If the server restarts, onboarded datasets show as
"state lost" and can be onboarded again in seconds.
"""

import json
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db, login_manager


def utcnow():
    return datetime.now(timezone.utc)


REGIONS = [
    ("ap-south-1", "Mumbai"),
    ("ap-southeast-1", "Singapore"),
    ("eu-central-1", "Frankfurt"),
    ("us-east-1", "N. Virginia"),
]
REGION_NAMES = dict(REGIONS)
DEFAULT_REGION = "ap-south-1"


class User(UserMixin, db.Model):
    """A sign-in account.

    Every trading account is bound to one `Party` — the entry on the ownership
    register that its actions are performed as. Without that binding an account
    could sign a deed as nobody in particular, which is the exact gap the
    register exists to close. The registry's own operators hold `role="admin"`
    instead: they act for the platform, not for a counterparty.
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    organization = db.Column(db.String(160), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), index=True)
    role = db.Column(db.String(16), nullable=False, default="member")  # admin | member
    created_at = db.Column(db.DateTime, default=utcnow)

    party = db.relationship("Party", foreign_keys=[party_id])

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def acting_as(self) -> str:
        if self.is_admin:
            return "Registry administrator"
        return self.party.display_name if self.party else "No register entry"

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def account_id(self) -> str:
        """Stable display account number, in the style cloud vendors print."""
        seed = (self.id or 0) * 2_654_435_761 % 10**12
        s = f"{seed:012d}"
        return f"{s[:4]}-{s[4:8]}-{s[8:]}"


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


class Dataset(db.Model):
    """An onboarded dataset: one protected file under one owner group."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)          # catalog name
    file_id = db.Column(db.String(255), nullable=False)       # shard identifier
    group_name = db.Column(db.String(160), nullable=False)
    owners_json = db.Column(db.Text, nullable=False, default="[]")
    n_blocks = db.Column(db.Integer, nullable=False, default=0)
    block_size = db.Column(db.Integer, nullable=False, default=0)
    total_bytes = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(32), nullable=False, default="provisioning")
    # provisioning | stored | active | error | stale (in-memory state lost on restart)
    #   stored  = the file is held on this server but not yet under protection
    #   active  = protected: blocks sealed, verification parameters published
    # Where the bytes came from. A catalog dataset is read from code/datasets/;
    # an uploaded one is the operator's own file, kept under instance/uploads/.
    source = db.Column(db.String(16), nullable=False, default="catalog")
    original_filename = db.Column(db.String(255), nullable=False, default="")
    storage_path = db.Column(db.String(500), nullable=False, default="")
    content_sha256 = db.Column(db.String(80), nullable=False, default="")
    # Operator-chosen identifier, printed in place of the generated one.
    custom_ref = db.Column(db.String(80), nullable=False, default="")
    tag_ms = db.Column(db.Float, nullable=False, default=0.0)
    region = db.Column(db.String(32), nullable=False, default=DEFAULT_REGION)
    # Continuous verification: minutes between automatic audits (NULL = off).
    audit_every_min = db.Column(db.Integer)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    # Registered legal holder (Party). The protocol-level owner set lives in
    # owners_json; this is the party that signs and is named on a deed.
    owner_party_id = db.Column(db.Integer, db.ForeignKey("party.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    audits = db.relationship("AuditRow", backref="dataset", lazy="dynamic")
    events = db.relationship("EventRow", backref="dataset", lazy="dynamic")

    @property
    def dataset_ref(self) -> str:
        """The identifier shown in the console: the operator's, or a generated one."""
        return self.custom_ref or f"DS-{self.id:05d}"

    @property
    def is_upload(self) -> bool:
        return self.source == "upload"

    @property
    def owners(self) -> list[str]:
        return json.loads(self.owners_json)

    @owners.setter
    def owners(self, value: list[str]) -> None:
        self.owners_json = json.dumps(value)


class AuditRow(db.Model):
    """One completed integrity audit."""

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(db.Integer, db.ForeignKey("dataset.id"), nullable=False)
    passed = db.Column(db.Boolean, nullable=False)
    challenged = db.Column(db.Integer, nullable=False)
    total_blocks = db.Column(db.Integer, nullable=False)
    detection_pct = db.Column(db.Float, nullable=False, default=0.0)
    duration_ms = db.Column(db.Float, nullable=False, default=0.0)
    note = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow)


class EventRow(db.Model):
    """Activity feed: onboarding, membership changes, transfers, attacks."""

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(db.Integer, db.ForeignKey("dataset.id"))
    kind = db.Column(db.String(32), nullable=False)
    # onboard | audit | add_owner | revoke_owner | transfer | corrupt |
    # restore | attack | signup
    summary = db.Column(db.String(400), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class AccessKey(db.Model):
    """Programmatic credential pair. The secret is shown once and stored hashed."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    key_id = db.Column(db.String(40), unique=True, nullable=False, index=True)
    secret_hash = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(160), nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default="active")  # active | disabled
    last_used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    @staticmethod
    def generate(user_id: int, description: str = "") -> tuple["AccessKey", str]:
        key_id = "AKAT" + secrets.token_hex(8).upper()
        secret = secrets.token_urlsafe(30)
        row = AccessKey(
            user_id=user_id,
            key_id=key_id,
            secret_hash=generate_password_hash(secret),
            description=description,
        )
        return row, secret


class SupportTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(60), nullable=False, default="General guidance")
    severity = db.Column(db.String(30), nullable=False, default="Normal")
    body = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default="open")  # open | resolved
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def ticket_no(self) -> str:
        return f"T{self.created_at.strftime('%Y%m%d') if self.created_at else '00000000'}{self.id:04d}"


class LoginRow(db.Model):
    """Sign-in history shown under Account > Security."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    ip = db.Column(db.String(64), nullable=False, default="")
    agent = db.Column(db.String(200), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow)


# ======================================================================
# Ownership registry: parties, agreements, certificates, custody chain
# ======================================================================
#
# The cryptography proves that ownership MOVED and that the seller's
# credentials stopped working. It cannot, by itself, say who "Apollo
# Hospital" is in law, or that money changed hands. That is what the
# records below add: typed legal parties, a two-sided signed agreement,
# a recorded consideration, and an issued certificate that any third
# party can verify without an account — the same division of labour a
# domain registrar or a sub-registrar of land records operates under.

PARTY_TYPES = [
    ("individual", "Individual"),
    ("group", "Group of individuals"),
    ("company", "Company"),
    ("startup", "Startup"),
    ("workspace", "Workspace"),
    ("institution", "Institution"),
]
PARTY_TYPE_NAMES = dict(PARTY_TYPES)

# A person is reachable at an address; a company is identified by its
# registration. So an email is required of the party types that are made of
# people, and optional for the ones that are made of paperwork.
EMAIL_REQUIRED_TYPES = {"individual", "group"}
# Types that hold a membership roll of named people.
MEMBERSHIP_TYPES = {"group", "workspace"}

CURRENCIES = [("INR", "\u20b9"), ("USD", "$"), ("EUR", "\u20ac")]
CURRENCY_SYMBOLS = dict(CURRENCIES)


class Party(db.Model):
    """A legal party that can hold or acquire a dataset.

    `slug` is the identifier the protocol layer uses as an owner id, so the
    cryptographic owner set and the legal register never drift apart.
    """

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(160), nullable=False)
    legal_name = db.Column(db.String(200), nullable=False, default="")
    party_type = db.Column(db.String(20), nullable=False, default="company")
    registration_id = db.Column(db.String(80), nullable=False, default="")
    jurisdiction = db.Column(db.String(80), nullable=False, default="")
    contact_email = db.Column(db.String(200), nullable=False, default="")
    verified = db.Column(db.Boolean, nullable=False, default=False)
    verification_method = db.Column(db.String(80), nullable=False, default="")
    verified_at = db.Column(db.DateTime)
    # Ed25519 signing key, held in custody by the platform (see registry.py).
    public_key = db.Column(db.String(120), nullable=False, default="")
    private_key = db.Column(db.String(120), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow)

    members = db.relationship("PartyMember", back_populates="party",
                              cascade="all, delete-orphan",
                              order_by="PartyMember.id")

    @property
    def type_label(self) -> str:
        return PARTY_TYPE_NAMES.get(self.party_type, self.party_type.title())

    @property
    def takes_members(self) -> bool:
        return self.party_type in MEMBERSHIP_TYPES

    @property
    def owner_member(self) -> "PartyMember | None":
        for m in self.members:
            if m.role == "owner":
                return m
        return None

    @property
    def owner_ids(self) -> list[str]:
        """The protocol-level owner identifiers this party brings to a dataset.

        A group is its members; anything else is itself. This is what keeps the
        cryptographic owner set and the register describing the same people.
        """
        if self.takes_members and self.members:
            return [m.slug for m in self.members if m.slug]
        return [self.slug]

    @property
    def party_ref(self) -> str:
        return f"PTY-{self.id:05d}"

    def snapshot(self) -> dict:
        """Immutable record of this party as it stood when a deed was issued."""
        return {
            "ref": self.party_ref,
            "slug": self.slug,
            "display_name": self.display_name,
            "legal_name": self.legal_name or self.display_name,
            "type": self.party_type,
            "type_label": self.type_label,
            "registration_id": self.registration_id,
            "jurisdiction": self.jurisdiction,
            "verified": bool(self.verified),
            "verification_method": self.verification_method,
            "public_key": self.public_key,
            "contact_email": self.contact_email,
            "members": [
                {"name": m.name, "email": m.email, "role": m.role}
                for m in self.members
            ],
        }


class PartyMember(db.Model):
    """One named person inside a party that is made of people.

    A group of individuals works the way a shared repository does: whoever
    creates it is its owner, and collaborators are admitted by email address.
    Each member also carries a protocol-level identifier (`slug`), because when
    a group holds a dataset it is the members who are the co-owners in the
    cryptographic sense — the group is not a single key with several people
    behind it.
    """

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False,
                         index=True)
    email = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(160), nullable=False, default="")
    role = db.Column(db.String(16), nullable=False, default="member")  # owner | member
    slug = db.Column(db.String(80), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow)

    party = db.relationship("Party", back_populates="members")

    @property
    def display(self) -> str:
        return self.name or self.email.split("@")[0]

    @property
    def role_label(self) -> str:
        return "Owner" if self.role == "owner" else "Collaborator"


class Listing(db.Model):
    """A dataset offered for sale on the marketplace."""

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(db.Integer, db.ForeignKey("dataset.id"), nullable=False)
    seller_party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=False, default="")
    category = db.Column(db.String(60), nullable=False, default="General")
    price = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(8), nullable=False, default="INR")
    licence_terms = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="active")
    # draft | active | under_offer | sold | withdrawn
    created_at = db.Column(db.DateTime, default=utcnow)

    dataset = db.relationship("Dataset")
    seller = db.relationship("Party")

    @property
    def listing_ref(self) -> str:
        return f"LST-{self.id:05d}"

    @property
    def price_display(self) -> str:
        sym = CURRENCY_SYMBOLS.get(self.currency, "")
        return f"{sym}{self.price:,.0f}"


class TransferAgreement(db.Model):
    """The two-sided instrument that authorises one ownership transfer.

    Nothing moves on the protocol layer until BOTH parties have signed. This
    mirrors a domain transfer authorisation, or signatures on a sale deed.
    """

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(db.Integer, db.ForeignKey("dataset.id"), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"))
    seller_party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False)
    buyer_party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False)

    status = db.Column(db.String(24), nullable=False, default="awaiting_seller")
    # awaiting_seller | awaiting_buyer | executing | completed | declined |
    # cancelled | superseded (another offer on the same asset was accepted)

    consideration = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(8), nullable=False, default="INR")
    payment_reference = db.Column(db.String(120), nullable=False, default="")
    terms = db.Column(db.Text, nullable=False, default="")

    seller_signature = db.Column(db.String(200), nullable=False, default="")
    seller_signed_at = db.Column(db.DateTime)
    buyer_signature = db.Column(db.String(200), nullable=False, default="")
    buyer_signed_at = db.Column(db.DateTime)

    decline_reason = db.Column(db.String(300), nullable=False, default="")
    # Which side raised the instrument: the holder offering to sell, or a buyer
    # bidding for something someone else holds.
    initiated_by = db.Column(db.String(10), nullable=False, default="seller")
    # How the transfer was authorised when it completed: by the transferor's own
    # signature, or by the registry administrator acting on the request. A deed
    # must never imply the first when it was really the second.
    authorised_by = db.Column(db.String(16), nullable=False, default="")
    # transferor | registry
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approval_note = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow)
    completed_at = db.Column(db.DateTime)

    dataset = db.relationship("Dataset")
    listing = db.relationship("Listing")
    seller = db.relationship("Party", foreign_keys=[seller_party_id])
    buyer = db.relationship("Party", foreign_keys=[buyer_party_id])

    @property
    def agreement_ref(self) -> str:
        year = (self.created_at or utcnow()).year
        return f"TA-{year}-{self.id:05d}"

    @property
    def consideration_display(self) -> str:
        sym = CURRENCY_SYMBOLS.get(self.currency, "")
        return f"{sym}{self.consideration:,.2f}"

    @property
    def status_label(self) -> str:
        return {
            "awaiting_seller": ("Awaiting the holder's decision" if self.is_bid
                                else "Awaiting seller signature"),
            "awaiting_buyer": "Awaiting buyer signature",
            "executing": "Executing transfer",
            "completed": "Completed",
            "declined": "Declined",
            "superseded": "Closed — the asset was sold to another party",
            "cancelled": "Cancelled",
        }.get(self.status, self.status)

    @property
    def both_signed(self) -> bool:
        return bool(self.seller_signed_at and self.buyer_signed_at)

    @property
    def is_bid(self) -> bool:
        """Raised by the buyer — an offer for something someone else holds."""
        return self.initiated_by == "buyer"

    @property
    def awaiting_party_id(self) -> int | None:
        """Whose signature the instrument is waiting on, if anyone's."""
        if self.status == "awaiting_seller":
            return self.seller_party_id
        if self.status == "awaiting_buyer":
            return self.buyer_party_id
        return None

    def involves(self, party_id: int | None) -> bool:
        return party_id is not None and party_id in (self.seller_party_id,
                                                     self.buyer_party_id)


class Certificate(db.Model):
    """An issued deed of transfer — the artifact a court or regulator reads.

    `payload_json` is the canonical, signed body. `signature` is an Ed25519
    signature over it by the platform issuing key, so the document verifies
    even if this database is unavailable.
    """

    id = db.Column(db.Integer, primary_key=True)
    certificate_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey("transfer_agreement.id"),
                             nullable=False)
    dataset_id = db.Column(db.Integer, db.ForeignKey("dataset.id"), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    payload_hash = db.Column(db.String(80), nullable=False, index=True)
    signature = db.Column(db.String(200), nullable=False)
    issuer_key = db.Column(db.String(120), nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default="valid")  # valid | revoked
    issued_at = db.Column(db.DateTime, default=utcnow)

    agreement = db.relationship("TransferAgreement")
    dataset = db.relationship("Dataset")

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)


class CustodyEvent(db.Model):
    """One link in a dataset's chain of custody, in order."""

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(db.Integer, db.ForeignKey("dataset.id"), nullable=False)
    seq = db.Column(db.Integer, nullable=False, default=1)
    kind = db.Column(db.String(30), nullable=False)
    # registered | member_admitted | member_removed | transferred
    from_party = db.Column(db.String(200), nullable=False, default="")
    to_party = db.Column(db.String(200), nullable=False, default="")
    detail = db.Column(db.String(400), nullable=False, default="")
    certificate_id = db.Column(db.String(32), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow)


class ProcessRow(db.Model):
    """One operation the platform performed, kept after it finished.

    The live feed reads running work from the in-process job registry, which
    dies with the process. This table is the part that must not: after a
    deployment restarts, a client should still be able to scroll back through
    what was done for it, step by step, rather than find the record wiped
    because the server was redeployed.
    """

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False, default="")
    title = db.Column(db.String(200), nullable=False, default="")
    subject = db.Column(db.String(400), nullable=False, default="")
    href = db.Column(db.String(200), nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default="running")
    message = db.Column(db.String(300), nullable=False, default="")
    error = db.Column(db.String(400), nullable=False, default="")
    stages_json = db.Column(db.Text, nullable=False, default="[]")
    outcome_json = db.Column(db.Text, nullable=False, default="{}")
    party_ids_json = db.Column(db.Text, nullable=False, default="[]")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    duration_ms = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=utcnow)
    finished_at = db.Column(db.DateTime)

    @property
    def stages(self) -> list:
        try:
            return json.loads(self.stages_json)
        except ValueError:
            return []

    @property
    def outcome(self) -> dict:
        try:
            return json.loads(self.outcome_json)
        except ValueError:
            return {}

    @property
    def party_ids(self) -> list:
        try:
            return json.loads(self.party_ids_json)
        except ValueError:
            return []

    def to_dict(self) -> dict:
        return {
            "id": self.job_id,
            "kind": self.kind,
            "title": self.title,
            "subject": self.subject,
            "href": self.href,
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "stages": self.stages,
            "result": self.outcome,
            "progress": 1.0 if self.status != "running" else 0.5,
            "elapsed_s": round((self.duration_ms or 0) / 1000.0, 1),
            "started_at": (self.created_at.timestamp() if self.created_at else 0),
            "when": (self.created_at.strftime("%d %b %Y, %H:%M")
                     if self.created_at else ""),
            "persisted": True,
        }


def record_custody(dataset_id: int, kind: str, detail: str, from_party: str = "",
                   to_party: str = "", certificate_id: str = "") -> None:
    last = (CustodyEvent.query.filter_by(dataset_id=dataset_id)
            .order_by(CustodyEvent.seq.desc()).first())
    db.session.add(CustodyEvent(
        dataset_id=dataset_id,
        seq=(last.seq + 1) if last else 1,
        kind=kind, detail=detail, from_party=from_party, to_party=to_party,
        certificate_id=certificate_id,
    ))
    db.session.commit()


def record_event(kind: str, summary: str, dataset_id: int | None = None) -> None:
    db.session.add(EventRow(kind=kind, summary=summary, dataset_id=dataset_id))
    db.session.commit()


def ensure_schema() -> None:
    """Add columns introduced after the first release to an existing SQLite DB.

    create_all() creates missing tables but never alters existing ones, so the
    two dataset columns added for regions and continuous verification are
    applied here with ALTER TABLE when absent.
    """
    from sqlalchemy import text

    added = [
        ("region", f"VARCHAR(32) NOT NULL DEFAULT '{DEFAULT_REGION}'"),
        ("audit_every_min", "INTEGER"),
        ("owner_party_id", "INTEGER"),
        # Uploaded-file provenance (added when the console gained file upload).
        ("source", "VARCHAR(16) NOT NULL DEFAULT 'catalog'"),
        ("original_filename", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ("storage_path", "VARCHAR(500) NOT NULL DEFAULT ''"),
        ("content_sha256", "VARCHAR(80) NOT NULL DEFAULT ''"),
        ("custom_ref", "VARCHAR(80) NOT NULL DEFAULT ''"),
    ]
    # Columns added when accounts became parties and agreements gained a
    # provenance (who raised them, on whose authority they completed).
    user_added = [
        ("party_id", "INTEGER"),
        ("role", "VARCHAR(16) NOT NULL DEFAULT 'member'"),
    ]
    agreement_added = [
        ("initiated_by", "VARCHAR(10) NOT NULL DEFAULT 'seller'"),
        ("authorised_by", "VARCHAR(16) NOT NULL DEFAULT ''"),
        ("approved_by_user_id", "INTEGER"),
        ("approval_note", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ]
    with db.engine.connect() as conn:
        for table, columns in (("dataset", added), ("user", user_added),
                               ("transfer_agreement", agreement_added)):
            cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
            if not cols:
                continue          # table not created yet; create_all handles it
            for name, ddl in columns:
                if name not in cols:
                    conn.execute(text(
                        f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
        conn.commit()


def ensure_administrator() -> None:
    """The deployment always has at least one registry operator.

    Without this the first account created on a fresh database would have
    nobody able to approve a transfer or verify an identity.
    """
    if User.query.filter_by(role="admin").first() is not None:
        return
    first = User.query.order_by(User.id).first()
    if first is not None:
        first.role = "admin"
        db.session.commit()
