"""
Who may see and do what.

Two kinds of account use this console. A **member** account is bound to one
party on the ownership register and acts only as that party: it sees the
datasets that party holds, the instruments it is named on, and the deeds
issued to it. An **admin** account is the registry operator — it sees the whole
register, because somebody has to be able to settle a request when the two
sides cannot, and because a registry that cannot see its own books is not a
registry.

Every rule here is deliberately expressed as "may this account act FOR this
party", not "is this account logged in". The difference is the whole point of
binding accounts to register entries: a signature has to belong to somebody.
"""

from __future__ import annotations

from flask_login import current_user

from models import Certificate, Dataset, Listing, TransferAgreement


def is_admin() -> bool:
    return bool(getattr(current_user, "is_authenticated", False)
                and current_user.is_admin)


def party_id() -> int | None:
    """The register entry this account acts as, if it has one."""
    if not getattr(current_user, "is_authenticated", False):
        return None
    return current_user.party_id


def party():
    return getattr(current_user, "party", None)


def can_act_for(target_party_id: int | None) -> bool:
    """May this account administer that party's holdings?

    The registry may: it has to be able to manage the register it keeps. This
    covers custody operations — protecting, verifying, listing, scheduling.
    """
    if is_admin():
        return True
    return target_party_id is not None and target_party_id == party_id()


def is_party(target_party_id: int | None) -> bool:
    """Is this account THAT party — no administrator override?

    Signing is not an administrative act. The registry can approve a transfer
    in its own name and say so on the deed, but it must never apply a party's
    signature, because a deed carrying a signature nobody gave is a forgery
    however convenient the custody arrangement makes it.
    """
    return target_party_id is not None and target_party_id == party_id()


# ----------------------------------------------------------------------
# Scoped collections
# ----------------------------------------------------------------------

def datasets() -> list[Dataset]:
    """Datasets this account is entitled to see, newest first."""
    q = Dataset.query.order_by(Dataset.created_at.desc())
    if is_admin():
        return q.all()
    pid = party_id()
    if pid is None:
        return []
    return q.filter(Dataset.owner_party_id == pid).all()


def can_manage_dataset(dataset: Dataset | None) -> bool:
    """Protect, verify, schedule, list or delete this dataset."""
    if dataset is None:
        return False
    if is_admin():
        return True
    return dataset.owner_party_id is not None \
        and dataset.owner_party_id == party_id()


def agreements() -> list[TransferAgreement]:
    q = TransferAgreement.query.order_by(TransferAgreement.created_at.desc())
    if is_admin():
        return q.all()
    pid = party_id()
    if pid is None:
        return []
    return q.filter((TransferAgreement.seller_party_id == pid)
                    | (TransferAgreement.buyer_party_id == pid)).all()


def can_see_agreement(agreement: TransferAgreement | None) -> bool:
    if agreement is None:
        return False
    return is_admin() or agreement.involves(party_id())


def certificates() -> list[Certificate]:
    rows = Certificate.query.order_by(Certificate.issued_at.desc()).all()
    if is_admin():
        return rows
    pid = party_id()
    if pid is None:
        return []
    return [c for c in rows if c.agreement is not None
            and c.agreement.involves(pid)]


def listings() -> list[Listing]:
    q = Listing.query.order_by(Listing.created_at.desc())
    if is_admin():
        return q.all()
    pid = party_id()
    if pid is None:
        return []
    return q.filter(Listing.seller_party_id == pid).all()


def visible_event_dataset_ids() -> list[int]:
    """Which datasets' activity this account is entitled to read.

    Its own holdings, plus anything it is a party to an instrument over — a
    bidder is entitled to follow what happens to the asset it has bid for, and
    to nothing else.
    """
    ids = {d.id for d in datasets()}
    ids |= {a.dataset_id for a in agreements()}
    return sorted(ids)


# ----------------------------------------------------------------------
# The work waiting on this account
# ----------------------------------------------------------------------

def inbox() -> dict:
    """Instruments that need a decision from whoever is signed in.

    `incoming` are requests waiting on this account; `outgoing` are the ones
    it is waiting on somebody else for. An admin sees everything still open,
    because it can settle any of them.
    """
    pid = party_id()
    open_states = ("awaiting_seller", "awaiting_buyer")
    rows = [a for a in agreements() if a.status in open_states]
    if is_admin():
        # The registry's queue is every request still open, since it can settle
        # any of them; a signed request is one it could act on today.
        return {"incoming": [a for a in rows
                             if a.seller_signed_at or a.buyer_signed_at],
                "outgoing": [], "pending": rows}
    incoming = [a for a in rows if a.awaiting_party_id == pid]
    outgoing = [a for a in rows if a.awaiting_party_id != pid]
    return {"incoming": incoming, "outgoing": outgoing, "pending": rows}
