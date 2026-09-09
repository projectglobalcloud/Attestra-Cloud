"""
OwnerModify (AddOwner / RevokeOwner) and OwnerTransfer.

Section V.B of the base paper.  This is where the paper's two contributions
actually live, so the reasoning is spelled out rather than assumed.

--------------------------------------------------------------------------
The Ownership Dynamic Token (ODT)
--------------------------------------------------------------------------
Every ownership operation reduces to handing the CSP a constant-size token

    ODT = (H, AUX, V)

with which it rewrites every file tag in place:

    sigma'_j = sigma_j * H3(F||j)^H * (u^{m_j})^{AUX} * V^{m_j}

Three properties make this work, and each is deliberate:

1.  CONSTANT SIZE.  H and AUX are scalars, V is one group element -- regardless
    of the number of blocks or the number of owners involved.  The CSP does the
    O(n) rewriting work; the owners do O(1) communication.  This is what the
    paper's Table III is measuring.

2.  THE BLINDING x_i.  Each owner splits its data exponent as
        aux_i = sk_i*H1(pk_i) - x_i        v_i = u^{x_i}
    so AUX alone never reveals sum_i sk_i*H1(pk_i).  The CSP recombines them
    only inside the exponent of u:
        (u^{m_j})^{AUX} * V^{m_j} = u^{m_j*(AUX + sum x_i)} = u^{m_j*sum sk_i*H1(pk_i)}
    Without this split the CSP would learn the group's aggregate signing
    exponent and could forge tags for any block it likes.

3.  MASKS ARE NEVER SUBTRACTED.  On revocation and on transfer, the leaving
    owners cancel their H2(sk_i||a_i) term but their mask a_i STAYS in the tag,
    and Rs is left untouched.  The departing owner knows a_i, but a_i is bound
    into the tag it can no longer influence, and the arriving party never learns
    it.  This is the fix for the collusion attack of section III.D -- see
    attacks/collusion.py for the executable demonstration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .hashes import H3
from .pairing import (
    G1Point,
    G2Point,
    g1_add,
    g1_identity,
    g1_mul,
    g2_add,
    g2_generator,
    g2_mul,
    g2_multi_add,
    rand_scalar,
    scalar_mod,
)
from .scheme import GroupState, OwnerKey, SystemParams

__all__ = [
    "OwnershipToken",
    "add_owners",
    "revoke_owners",
    "transfer_initiate_sg",
    "transfer_finalize_bg",
    "transfer_ownership",
    "apply_token",
]


@dataclass(frozen=True)
class OwnershipToken:
    """
    ODT = (H, AUX, V).

    Constant size: two Z_q scalars and one G1 element (~128 bytes serialised),
    independent of both n and s.
    """

    h: int       # H   = sum of index-exponent deltas
    aux: int     # AUX = sum of blinded data-exponent deltas
    v: G1Point   # V   = prod u^{x_i}, the blinding factors

    def combine(self, other: "OwnershipToken") -> "OwnershipToken":
        """Compose two tokens (used to fold ODT_SG into ODT_BG on transfer)."""
        return OwnershipToken(
            h=scalar_mod(self.h + other.h),
            aux=scalar_mod(self.aux + other.aux),
            v=g1_add(self.v, other.v),
        )


def _joining_share(param: SystemParams, owner: OwnerKey) -> tuple[int, int, G1Point, G2Point, G2Point]:
    """
    An owner JOINING (AddOwner, or the BG side of a transfer) contributes:

        h_i   = H2(sk_i||a_i) + a_i          (adds both index exponent and mask)
        aux_i = sk_i*H1(pk_i) - x_i          (blinded data exponent)
        v_i   = u^{x_i}

    and publishes h'_i = g^{H2(sk_i||a_i)} and r'_i = g^{a_i} so that Hs and Rs
    both grow to match.
    """
    x_i = rand_scalar()
    h_i = scalar_mod(owner.h2 + owner.a)
    aux_i = scalar_mod(owner.sk * owner.h1 - x_i)
    v_i = g1_mul(param.u, x_i)
    return h_i, aux_i, v_i, owner.h_share(), owner.r_share()


def _leaving_share(param: SystemParams, owner: OwnerKey) -> tuple[int, int, G1Point]:
    """
    An owner LEAVING (RevokeOwner, or the SG side of a transfer) contributes:

        h_i   = -H2(sk_i||a_i)               (cancels the index exponent ONLY --
                                              the mask a_i is deliberately kept)
        aux_i = -sk_i*H1(pk_i) - x_i         (cancels the data exponent, blinded)
        v_i   = u^{x_i}

    Note the asymmetry with _joining_share: joining adds  H2 + a , leaving
    removes only  H2 .  That asymmetry is the security property, not an
    oversight -- see the module docstring, point 3.
    """
    x_i = rand_scalar()
    h_i = scalar_mod(-owner.h2)
    aux_i = scalar_mod(-owner.sk * owner.h1 - x_i)
    v_i = g1_mul(param.u, x_i)
    return h_i, aux_i, v_i


def _aggregate(shares: Sequence[tuple[int, int, G1Point]], param: SystemParams) -> OwnershipToken:
    """
    Fold per-owner shares into one constant-size token.

    Note the output size does not depend on len(shares): two scalars and one
    group element, whether one owner is joining or eighty.
    """
    h_total = 0
    aux_total = 0
    v_total = g1_identity()
    for h_i, aux_i, v_i in shares:
        h_total = scalar_mod(h_total + h_i)
        aux_total = scalar_mod(aux_total + aux_i)
        v_total = g1_add(v_total, v_i)
    return OwnershipToken(h=h_total, aux=aux_total, v=v_total)


# ==========================================================================
# OwnerModify -- AddOwner
# ==========================================================================

def add_owners(
    param: SystemParams,
    state: GroupState,
    new_owners: Sequence[OwnerKey],
) -> tuple[OwnershipToken, GroupState]:
    """
    AddOwner: bring `new_owners` into the group holding this file.

    Cost is O(|U_in|) -- it depends only on how many owners are JOINING, not on
    how many are already in the group.  The baseline (scheme [8]) must rebuild
    its public-key-aggregation authenticator across the whole group, costing
    O(s^2).  This asymmetry is what benchmarks/ measures for Fig. 7a.

    Returns the token to hand to the CSP, and the updated public state.
    """
    shares = []
    h_shares: list[G2Point] = []
    r_shares: list[G2Point] = []
    for owner in new_owners:
        h_i, aux_i, v_i, h_pub, r_pub = _joining_share(param, owner)
        shares.append((h_i, aux_i, v_i))
        h_shares.append(h_pub)
        r_shares.append(r_pub)

    token = _aggregate(shares, param)

    new_state = GroupState(
        # APK grows by each newcomer's pk_i^{H1(pk_i)}.
        apk=g2_add(state.apk, g2_multi_add([o.apk_share() for o in new_owners])),
        rs=g2_add(state.rs, g2_multi_add(r_shares)),
        hs=g2_add(state.hs, g2_multi_add(h_shares)),
        file_abstract=state.file_abstract,
        n_blocks=state.n_blocks,
        owner_ids=list(state.owner_ids) + [o.owner_id for o in new_owners],
    )
    return token, new_state


# ==========================================================================
# OwnerModify -- RevokeOwner
# ==========================================================================

def revoke_owners(
    param: SystemParams,
    state: GroupState,
    leaving_owners: Sequence[OwnerKey],
) -> tuple[OwnershipToken, GroupState]:
    """
    RevokeOwner: remove `leaving_owners` from the group.

    Hs shrinks by g^{H2(sk_i||a_i)} for each departing owner.
    Rs is NOT touched -- the departing owner's mask a_i stays in every tag.
    APK shrinks by the departing owner's pk_i^{H1(pk_i)}.

    The audit equation still balances because Hs*Rs tracks the tag's index
    exponent exactly:  after revocation the tag holds
        sum_{i in s\\U_out} H2(sk_i||a_i)  +  sum_{i in s} a_i
    and Hs*Rs = g^{that same value}.
    """
    shares = [_leaving_share(param, o) for o in leaving_owners]
    token = _aggregate(shares, param)

    from .pairing import g2_neg

    new_state = GroupState(
        apk=g2_add(state.apk, g2_neg(g2_multi_add([o.apk_share() for o in leaving_owners]))),
        # Rs deliberately unchanged: masks are retained.
        rs=state.rs,
        hs=g2_add(state.hs, g2_mul(g2_generator(), token.h)),
        file_abstract=state.file_abstract,
        n_blocks=state.n_blocks,
        owner_ids=[
            oid for oid in state.owner_ids
            if oid not in {o.owner_id for o in leaving_owners}
        ],
    )
    return token, new_state


# ==========================================================================
# OwnerTransfer -- SG -> BG
# ==========================================================================

def transfer_initiate_sg(
    param: SystemParams,
    state: GroupState,
    sale_group: Sequence[OwnerKey],
) -> tuple[OwnershipToken, GroupState]:
    """
    Transfer step 1 (run by the Sale Group).

    SG cancels its own index and data exponents and hands ODT_SG to BG.  SG
    updates Hs (removing its H2 terms) but leaves Rs alone -- its masks stay
    embedded in the tags forever, which is exactly what stops BG and the CSP
    from later colluding to forge tags over data SG never sold.
    """
    shares = [_leaving_share(param, o) for o in sale_group]
    token = _aggregate(shares, param)

    from .pairing import g2_neg

    intermediate = GroupState(
        apk=g2_add(state.apk, g2_neg(g2_multi_add([o.apk_share() for o in sale_group]))),
        rs=state.rs,
        hs=g2_add(state.hs, g2_mul(g2_generator(), token.h)),
        file_abstract=state.file_abstract,
        n_blocks=state.n_blocks,
        owner_ids=[
            oid for oid in state.owner_ids
            if oid not in {o.owner_id for o in sale_group}
        ],
    )
    return token, intermediate


def transfer_finalize_bg(
    param: SystemParams,
    state: GroupState,
    sg_token: OwnershipToken,
    buy_group: Sequence[OwnerKey],
) -> tuple[OwnershipToken, GroupState]:
    """
    Transfer step 2 (run by the Buy Group).

    BG adds its own exponents and masks, folds SG's token into its own, and
    sends the single combined ODT_BG to the CSP.  The CSP therefore performs
    ONE tag rewrite for the whole transaction, not two.

    BG never receives any SG secret -- only the aggregated, blinded token.
    """
    shares = []
    h_shares: list[G2Point] = []
    r_shares: list[G2Point] = []
    for owner in buy_group:
        h_i, aux_i, v_i, h_pub, r_pub = _joining_share(param, owner)
        shares.append((h_i, aux_i, v_i))
        h_shares.append(h_pub)
        r_shares.append(r_pub)

    bg_token = _aggregate(shares, param)
    combined = sg_token.combine(bg_token)

    new_state = GroupState(
        apk=g2_add(state.apk, g2_multi_add([o.apk_share() for o in buy_group])),
        rs=g2_add(state.rs, g2_multi_add(r_shares)),
        hs=g2_add(state.hs, g2_multi_add(h_shares)),
        file_abstract=state.file_abstract,
        n_blocks=state.n_blocks,
        owner_ids=list(state.owner_ids) + [o.owner_id for o in buy_group],
    )
    return combined, new_state


def transfer_ownership(
    param: SystemParams,
    state: GroupState,
    sale_group: Sequence[OwnerKey],
    buy_group: Sequence[OwnerKey],
) -> tuple[OwnershipToken, GroupState]:
    """
    Convenience wrapper running both transfer steps.

    Used by tests and benchmarks.  The web application calls the two steps
    separately, because in reality SG and BG act at different times and the
    intermediate ODT_SG travels between them over the network.
    """
    sg_token, intermediate = transfer_initiate_sg(param, state, sale_group)
    return transfer_finalize_bg(param, intermediate, sg_token, buy_group)


# ==========================================================================
# CSP side -- applying a token to the stored tags
# ==========================================================================

def apply_token(
    param: SystemParams,
    token: OwnershipToken,
    file_abstract: bytes,
    blocks: Sequence[int],
    tags: Sequence[G1Point],
) -> list[G1Point]:
    """
    Run by the CSP.  Rewrites every stored tag with the token:

        sigma'_j = sigma_j * H3(F||j)^H * (u^{m_j})^{AUX} * V^{m_j}

    The CSP needs the block values m_j (which it stores anyway) but learns
    nothing about any owner's secret key: H and AUX are aggregate sums, and AUX
    is additively blinded by the x_i hidden inside V.

    This is the only O(n) step in an ownership operation, and it is borne by
    the party with "abundant storage and computational resources" -- exactly
    the design intent stated in the paper's system model.
    """
    updated: list[G1Point] = []
    for j, (m_j, tag) in enumerate(zip(blocks, tags)):
        delta_index = g1_mul(H3(file_abstract, j), token.h)
        delta_data = g1_mul(g1_mul(param.u, m_j), token.aux)
        delta_blind = g1_mul(token.v, m_j)
        updated.append(g1_add(g1_add(tag, delta_index), g1_add(delta_data, delta_blind)))
    return updated
