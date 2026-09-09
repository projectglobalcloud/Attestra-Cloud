"""
Owner groups: the Sale Group (SG) and the Buy Group (BG).

Role, per the base paper's system model (section IV.A):

    "SG is an ad-hoc group consisting of s data owners (DO). The owners in
     group SG may be dynamically added or revoked. The ownership of the stored
     data is shared among all owners and can be transferred over time.
     BG has the same group attributes as SG [...] and also serves as the
     recipient of data ownership transferred from group SG."

SG and BG are the SAME KIND OF THING -- one class, two roles.  A group is
"selling" or "buying" only relative to a particular transaction; the medical
research institute that buys a dataset today is the sale group when it resells
tomorrow.  That symmetry is why `OwnerGroup` below has no notion of which side
it is on.

THE CENTRAL DIFFICULTY THIS CLASS EXISTS TO SOLVE
-------------------------------------------------
Ownership is SHARED.  Every owner contributes a share to every file tag, so:

  - no single owner can transfer the dataset alone;
  - no single owner can forge a proof;
  - but the group must still be able to add and remove members cheaply.

Prior schemes treated a group as a fixed unit, so adding one member meant
redoing the whole group's key material -- O(s^2) work.  The scheme implemented
here makes a membership change cost only O(number of members changing),
independent of how large the group already is.  `add_members()` and
`revoke_members()` below are where that shows up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crypto.ownership import (
    OwnershipToken,
    add_owners,
    revoke_owners,
    transfer_finalize_bg,
    transfer_initiate_sg,
)
from crypto.pairing import G1Point
from crypto.scheme import (
    GroupState,
    OwnerKey,
    SystemParams,
    key_gen,
    tag_gen,
)

__all__ = ["DataOwner", "OwnerGroup"]


@dataclass
class DataOwner:
    """
    A single data owner.

    Wraps an OwnerKey with a human-readable identity.  The secret material
    (`key.sk`, `key.a`) never leaves this object -- every other party in the
    system sees only group elements derived from it.
    """

    owner_id: str
    key: OwnerKey
    display_name: str = ""

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.owner_id.replace("-", " ").title()

    @property
    def public_key(self):
        return self.key.pk

    def __str__(self) -> str:
        return self.display_name


@dataclass
class OwnerGroup:
    """
    A group of data owners jointly owning one or more files.

    Typical lifecycle:

        sg = OwnerGroup.create(param, "hospital-consortium",
                               ["apollo", "manipal", "narayana"])
        tags, state = sg.tag_file(file_abstract, blocks)
        token, state = sg.add_members(state, ["fortis"])
        token, state = sg.transfer_to(state, bg)
    """

    param: SystemParams
    group_id: str
    members: list[DataOwner] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        param: SystemParams,
        group_id: str,
        member_ids: list[str],
    ) -> "OwnerGroup":
        """
        KeyGen for every member.

        Cost is O(s): each owner independently draws sk_i and computes
        pk_i = g^{sk_i}, with no interaction between owners at all.

        Contrast the baseline (scheme [8]): there, every owner must build a
        public-key-aggregation authenticator over ALL other owners' keys, so
        each owner does O(s) work and the group does O(s^2).  That is the gap
        benchmarks/ measures and plots.
        """
        members = [
            DataOwner(owner_id=oid, key=key_gen(param, owner_id=oid))
            for oid in member_ids
        ]
        return cls(param=param, group_id=group_id, members=members)

    @property
    def keys(self) -> list[OwnerKey]:
        return [m.key for m in self.members]

    @property
    def member_ids(self) -> list[str]:
        return [m.owner_id for m in self.members]

    @property
    def size(self) -> int:
        return len(self.members)

    def __str__(self) -> str:
        return f"{self.group_id} ({self.size} owner{'s' if self.size != 1 else ''})"

    # ------------------------------------------------------------------
    # TagGen -- preparing a file for upload
    # ------------------------------------------------------------------

    def tag_file(
        self,
        file_abstract: bytes,
        blocks: list[int],
    ) -> tuple[list[G1Point], GroupState]:
        """
        Every member computes a tag share per block; the shares are aggregated
        into one tag per block.

            sigma_ij = H3(F||j)^{H2(sk_i||a_i)+a_i} * (u^{m_j})^{sk_i*H1(pk_i)}
            sigma_j  = prod_i sigma_ij

        Two things worth noticing:

        1. Each owner uses only its OWN secrets. There is no shared secret, no
           dealer, and no trusted setup between owners.
        2. The aggregation is just a group product, so any member (or even an
           untrusted party) can perform it. A tag share is already a group
           element and reveals nothing.

        Returns the tags to upload and the public GroupState the auditor needs.
        """
        return tag_gen(self.param, self.keys, file_abstract, blocks)

    # ------------------------------------------------------------------
    # OwnerModify
    # ------------------------------------------------------------------

    def add_members(
        self,
        state: GroupState,
        new_member_ids: list[str],
    ) -> tuple[OwnershipToken, GroupState, list[DataOwner]]:
        """
        AddOwner: bring new owners into the group.

        Cost depends ONLY on how many owners are joining -- a group of 100 that
        admits 2 new members does 2 owners' worth of work, not 100.  Existing
        members are not disturbed: they do not re-key, and they do not even
        need to be online.

        Returns (token for the CSP, new public state, the newly created owners).
        """
        newcomers = [
            DataOwner(owner_id=oid, key=key_gen(self.param, owner_id=oid))
            for oid in new_member_ids
        ]
        token, new_state = add_owners(self.param, state, [m.key for m in newcomers])
        self.members.extend(newcomers)
        return token, new_state, newcomers

    def revoke_members(
        self,
        state: GroupState,
        revoke_ids: list[str],
    ) -> tuple[OwnershipToken, GroupState]:
        """
        RevokeOwner: remove owners from the group.

        After this, the revoked owners' key material no longer contributes to
        the tags, so they can no longer produce or influence a valid proof --
        they have genuinely lost ownership.

        Their random masks a_i, however, REMAIN embedded in every tag, and Rs
        is deliberately left unchanged.  A revoked owner knows its own a_i but
        can no longer act on it, and nobody else ever learns it.  That residue
        is what keeps the exposed index secret useless to an attacker -- see
        attacks/collusion.py.
        """
        targets = [m for m in self.members if m.owner_id in set(revoke_ids)]
        missing = set(revoke_ids) - {m.owner_id for m in targets}
        if missing:
            raise ValueError(f"not members of {self.group_id}: {sorted(missing)}")

        token, new_state = revoke_owners(self.param, state, [m.key for m in targets])
        self.members = [m for m in self.members if m.owner_id not in set(revoke_ids)]
        return token, new_state

    # ------------------------------------------------------------------
    # OwnerTransfer
    # ------------------------------------------------------------------

    def initiate_transfer(
        self,
        state: GroupState,
    ) -> tuple[OwnershipToken, GroupState]:
        """
        Transfer step 1, run by the SELLING group.

        The sale group cancels its own contribution and produces ODT_SG, which
        it hands to the buying group.  Note what ODT_SG is NOT: it is not a
        secret key.  It is an aggregated, blinded token from which no
        individual owner's sk_i can be recovered.

        This is precisely the point where earlier schemes leaked the index
        secret key in the clear, enabling the collusion attack the base paper
        identifies (section III.D).
        """
        return transfer_initiate_sg(self.param, state, self.keys)

    def accept_transfer(
        self,
        state: GroupState,
        sg_token: OwnershipToken,
    ) -> tuple[OwnershipToken, GroupState]:
        """
        Transfer step 2, run by the BUYING group.

        The buy group adds its own exponents and fresh masks, folds SG's token
        into its own, and sends ONE combined token to the CSP.  The cloud
        therefore rewrites each tag once for the whole sale, not twice.
        """
        return transfer_finalize_bg(self.param, state, sg_token, self.keys)

    def transfer_to(
        self,
        state: GroupState,
        buy_group: "OwnerGroup",
    ) -> tuple[OwnershipToken, GroupState]:
        """
        Convenience wrapper running both halves of a transfer.

        Real deployments call the two steps separately, since SG and BG act at
        different times and ODT_SG travels between them over the network. This
        wrapper is for demos, tests and benchmarks.
        """
        sg_token, intermediate = self.initiate_transfer(state)
        return buy_group.accept_transfer(intermediate, sg_token)
