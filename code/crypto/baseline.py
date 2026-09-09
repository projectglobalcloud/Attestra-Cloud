"""
Prior-art schemes, implemented for comparison.

Two separate things live here, both needed to make the base paper's claims
demonstrable rather than merely asserted:

  1. BaselineKeyGen        - the O(s^2) key-generation structure of scheme [8]
                             (Shen et al.), used by benchmarks/ to produce the
                             comparison curves of Figs. 6 and 7.

  2. PriorArtTagScheme     - the tag construction sigma = H0(j)^{sk1}(u^m)^{sk2}
                             used by schemes [7] and [8], where the index secret
                             key sk1 is handed over in the clear during a data
                             transaction. Used by attacks/collusion.py to show
                             the attack the base paper set out to fix.

HONESTY NOTE -- PLEASE READ BEFORE QUOTING ANY NUMBER FROM HERE
---------------------------------------------------------------
This is NOT the original authors' code for scheme [8]. It is a re-implementation
of the COST STRUCTURE that the base paper attributes to scheme [8] in its
Table II, built from the same primitives on the same curve so the comparison is
apples-to-apples.

What that means in practice:
  - The asymptotic shape (O(s^2) vs O(s)) is faithful and is what the graphs
    demonstrate.
  - The absolute constants are ours, not Shen et al.'s.
Anywhere these numbers appear, they are labelled as a cost-model baseline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from .pairing import (
    CURVE_ORDER,
    G1Point,
    G2Point,
    g1_add,
    g1_identity,
    g1_mul,
    g2_add,
    g2_identity,
    g2_mul,
    g2_multi_add,
    g2_to_bytes,
    gt_eq,
    pair_product,
    rand_scalar,
    scalar_mod,
)
from .scheme import SystemParams

__all__ = [
    "BaselineOwner",
    "baseline_key_gen",
    "baseline_add_owners",
    "baseline_revoke_owners",
    "PriorArtKey",
    "PriorArtTagScheme",
]


# ==========================================================================
# PART 1 -- Scheme [8]'s O(s^2) key generation
# ==========================================================================

@dataclass
class BaselineOwner:
    """An owner under the baseline scheme, carrying its aggregation authenticator."""

    sk: int
    pk: G2Point
    auth: G2Point   # auth_i = prod_{j in s} f_{sk_i}(pk_j, APK)


def _auth_exponent(pk_i_bytes: bytes, pk_j_bytes: bytes, apk_bytes: bytes) -> int:
    """
    One term of the public-key-aggregation authenticator.

    The defining feature -- and the source of the quadratic cost -- is that this
    exponent depends on the aggregated public key APK, so it cannot be computed
    until every owner's key is known, and it must be recomputed for every pair
    of owners.
    """
    digest = hashlib.sha512(
        b"BASELINE-PKAGG" + pk_i_bytes + pk_j_bytes + apk_bytes
    ).digest()
    return int.from_bytes(digest, "big") % CURVE_ORDER


def baseline_key_gen(param: SystemParams, s: int) -> tuple[list[BaselineOwner], G2Point]:
    """
    KeyGen for a group of s owners under scheme [8].

    Why this is O(s^2), stated plainly:

        for each owner i in 1..s:              <- s iterations
            for each owner j in 1..s:          <- s iterations
                compute pk_j ^ f(pk_i, pk_j, APK)

    Every owner must build an authenticator that binds it to EVERY other owner
    in the group, so each owner performs s exponentiations and the group as a
    whole performs s^2.

    The proposed scheme replaces this entirely with APK = prod pk_i^{H1(pk_i)},
    where each owner's exponent depends only on its OWN public key. That is one
    exponentiation per owner -- O(s) -- and it still resists rogue-key attacks,
    because an attacker cannot control H1 of a key it must publish first.
    """
    # Stage 1: plain key pairs -- O(s).
    keys = []
    for _ in range(s):
        sk = rand_scalar()
        keys.append((sk, g2_mul(param.g, sk)))

    # Stage 2: the aggregated public key -- O(s).
    apk = g2_multi_add([pk for _, pk in keys])
    apk_bytes = g2_to_bytes(apk)
    pk_bytes = [g2_to_bytes(pk) for _, pk in keys]

    # Stage 3: the authenticators -- O(s^2). This is the bottleneck.
    owners: list[BaselineOwner] = []
    for i, (sk_i, pk_i) in enumerate(keys):
        auth = g2_identity()
        for j, (_, pk_j) in enumerate(keys):
            exponent = _auth_exponent(pk_bytes[i], pk_bytes[j], apk_bytes)
            auth = g2_add(auth, g2_mul(pk_j, scalar_mod(exponent * sk_i)))
        owners.append(BaselineOwner(sk=sk_i, pk=pk_i, auth=auth))

    return owners, apk


def baseline_add_owners(
    param: SystemParams,
    existing: Sequence[BaselineOwner],
    n_new: int,
) -> list[BaselineOwner]:
    """
    AddOwner under the baseline.

    Because APK changes when anyone joins, EVERY authenticator in the group is
    invalidated and must be rebuilt -- including those of owners who were not
    involved and may not even be online. Cost is O((s + |U_in|)^2).

    The proposed scheme's AddOwner costs O(|U_in|) and leaves existing owners
    entirely undisturbed. This is the gap plotted in Fig. 7a.
    """
    total = len(existing) + n_new
    owners, _ = baseline_key_gen(param, total)
    return owners


def baseline_revoke_owners(
    param: SystemParams,
    existing: Sequence[BaselineOwner],
    n_revoked: int,
) -> list[BaselineOwner]:
    """
    RevokeOwner under the baseline -- same story as AddOwner: APK changes, so
    every remaining authenticator is rebuilt. Cost is O((s - |U_out|)^2).
    """
    remaining = max(1, len(existing) - n_revoked)
    owners, _ = baseline_key_gen(param, remaining)
    return owners


# ==========================================================================
# PART 2 -- The vulnerable tag construction of schemes [7] and [8]
# ==========================================================================

@dataclass
class PriorArtKey:
    """
    A key pair under schemes [7]/[8], split into two independent halves.

        sk1 / pk1   the INDEX key   -- signs the block position H0(j)
        sk2 / pk2   the DATA key    -- signs the block content u^{m_j}

    Splitting them is what makes the ownership transfer token constant-size
    (a genuine improvement at the time). The fatal detail is what happens next:
    during a data transaction, sk1 is transmitted to the buyer IN THE CLEAR.
    """

    sk1: int
    pk1: G2Point
    sk2: int
    pk2: G2Point


class PriorArtTagScheme:
    """
    The tag construction  sigma_j = H0(j)^{sk1} * (u^{m_j})^{sk2}.

    Verification:
        e(TP, g) == e(prod H0(j)^{v_j}, pk1) * e(u^{DP}, pk2)

    This class exists solely so attacks/collusion.py can demonstrate a REAL
    forgery against it -- not a described one. The vulnerability is structural
    and is analysed in section III.D of the base paper:

        The index component H0(j)^{sk1} depends only on the position j and the
        index secret. Anyone holding sk1 can therefore STRIP the index
        component off one tag and GRAFT it onto another position, producing a
        tag that is valid for the wrong block.

    The proposed scheme blocks this by masking the index exponent with a secret
    random value a_i that is never transmitted:

        sigma_j = H3(F||j)^{H2(sk_i||a_i) + a_i} * (u^{m_j})^{sk_i*H1(pk_i)}

    Even a party who learns every H2(sk_i||a_i) cannot perform the graft,
    because the a_i residue would be left attached to the wrong index.
    """

    def __init__(self, param: SystemParams):
        self.param = param

    # -- key generation ------------------------------------------------

    def key_gen(self) -> PriorArtKey:
        sk1, sk2 = rand_scalar(), rand_scalar()
        return PriorArtKey(
            sk1=sk1, pk1=g2_mul(self.param.g, sk1),
            sk2=sk2, pk2=g2_mul(self.param.g, sk2),
        )

    # -- hashing -------------------------------------------------------

    @staticmethod
    def h0(index: int) -> G1Point:
        """
        H0(j) -> G1.

        Note what it does NOT depend on: the file. In these schemes the index
        hash is a function of position alone, which is precisely what lets an
        attacker move a tag between positions.
        """
        from py_ecc.bls.hash_to_curve import hash_to_G1

        return hash_to_G1(
            b"PRIORART-H0" + index.to_bytes(8, "big"),
            b"PRIORART_H0_XMD:SHA-256_SSWU_RO_",
            hashlib.sha256,
        )

    # -- tagging -------------------------------------------------------

    def tag(self, key: PriorArtKey, index: int, m_j: int) -> G1Point:
        """sigma_j = H0(j)^{sk1} * (u^{m_j})^{sk2}"""
        index_part = g1_mul(self.h0(index), key.sk1)
        data_part = g1_mul(g1_mul(self.param.u, m_j), key.sk2)
        return g1_add(index_part, data_part)

    def tag_all(self, key: PriorArtKey, blocks: Sequence[int]) -> list[G1Point]:
        return [self.tag(key, j, m_j) for j, m_j in enumerate(blocks)]

    # -- auditing ------------------------------------------------------

    def gen_proof(
        self,
        challenge_pairs: Sequence[tuple[int, int]],
        blocks: Sequence[int],
        tags: Sequence[G1Point],
    ) -> tuple[int, G1Point]:
        dp = 0
        tp = g1_identity()
        for j, v_j in challenge_pairs:
            dp = (dp + blocks[j] * v_j) % CURVE_ORDER
            tp = g1_add(tp, g1_mul(tags[j], v_j))
        return dp, tp

    def verify(
        self,
        key: PriorArtKey,
        challenge_pairs: Sequence[tuple[int, int]],
        dp: int,
        tp: G1Point,
    ) -> bool:
        """e(TP, g) == e(prod H0(j)^{v_j}, pk1) * e(u^{DP}, pk2)"""
        from .pairing import g2_neg

        h_acc = g1_identity()
        for j, v_j in challenge_pairs:
            h_acc = g1_add(h_acc, g1_mul(self.h0(j), v_j))

        result = pair_product([
            (tp, g2_neg(self.param.g)),
            (h_acc, key.pk1),
            (g1_mul(self.param.u, dp), key.pk2),
        ])
        return gt_eq(result, result.__class__.one())

    # -- the attack ----------------------------------------------------

    def forge_by_index_swap(
        self,
        leaked_sk1: int,
        source_tag: G1Point,
        source_index: int,
        target_index: int,
    ) -> G1Point:
        """
        THE COLLUSION FORGERY (base paper, section III.D).

        Given the leaked index secret sk1, rewrite a tag so that it validates
        at a DIFFERENT position:

            sigma' = sigma_{j'} * H0(j')^{-sk1} * H0(j)^{sk1}
                   = H0(j)^{sk1} * (u^{m_{j'}})^{sk2}

        The result is a perfectly valid tag binding position j to the CONTENTS
        of block j'. A cloud that has lost block j can therefore answer a
        challenge for j using whatever block it still happens to hold.

        Nothing here is exotic: it is two exponentiations and a multiplication.
        That is how cheap the attack is once sk1 leaks.
        """
        strip_old = g1_mul(self.h0(source_index), scalar_mod(-leaked_sk1))
        graft_new = g1_mul(self.h0(target_index), leaked_sk1)
        return g1_add(source_tag, g1_add(strip_old, graft_new))
