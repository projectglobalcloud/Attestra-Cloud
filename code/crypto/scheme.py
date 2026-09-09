"""
The proposed scheme: SysGen, KeyGen, TagGen, Audit.

Direct implementation of section V.B of

    C. Wu, W. You, X. Huang, "Scalable Cloud Auditing With Efficient Ownership
    Transfer for Group Transactions", IEEE TIFS vol. 20, 2025.
    https://doi.org/10.1109/TIFS.2025.3636056

ported to BLS12-381 (see pairing.py for the Type-1 -> Type-3 placement rule).

Ownership modification and transfer live in ownership.py.

Reading guide -- the paper writes groups multiplicatively, the code writes them
additively.  Every g1_mul/g2_mul below is the paper's exponentiation, and every
g1_add/g2_add is the paper's product.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Sequence

from .hashes import H1, H2, H3
from .pairing import (
    CURVE_ORDER,
    G1Point,
    G2Point,
    g1_add,
    g1_generator,
    g1_identity,
    g1_mul,
    g1_multi_add,
    g2_add,
    g2_generator,
    g2_identity,
    g2_mul,
    g2_multi_add,
    gt_eq,
    pair_product,
    rand_scalar,
    scalar_mod,
)

__all__ = [
    "SystemParams",
    "OwnerKey",
    "GroupState",
    "Challenge",
    "Proof",
    "sys_gen",
    "key_gen",
    "aggregate_public_key",
    "tag_gen",
    "gen_challenge",
    "gen_proof",
    "verify_proof",
]


# ==========================================================================
# SysGen
# ==========================================================================

@dataclass(frozen=True)
class SystemParams:
    """
    param <- SysGen(1^lambda)

    The paper's param = {q, G1, GT, e, g, u, H1, H2}.  Under the Type-3 port the
    generator g lives in G2 and u lives in G1; the groups, the pairing and the
    hash functions are fixed by the curve choice and by hashes.py, so the only
    runtime state is (g, u).
    """

    q: int
    g: G2Point   # generator of G2 -- base for all published key material
    u: G1Point   # independent generator of G1 -- base for the data exponent

    @property
    def order(self) -> int:
        return self.q


def sys_gen(seed: bytes | None = None) -> SystemParams:
    """
    SysGen(1^lambda) -> param.

    u must be an *independent* generator: nobody may know log_g(u), or the
    unforgeability reduction (Theorem 2) breaks -- an owner who knew that
    discrete log could forge tags for arbitrary blocks.  We derive u by
    hash-to-curve from a fixed public string ("nothing up my sleeve"), so no
    party ever learns the relation.
    """
    from .hashes import H3 as _hash_to_g1

    label = b"GAT-CLOUDAUDIT-V1-INDEPENDENT-GENERATOR-U"
    if seed is not None:
        label = label + b"|" + seed
    u = _hash_to_g1(label, 0)
    return SystemParams(q=CURVE_ORDER, g=g2_generator(), u=u)


# ==========================================================================
# KeyGen
# ==========================================================================

@dataclass
class OwnerKey:
    """
    An owner's key pair plus the per-owner random mask used in TagGen.

    `a` is the random mask a_i.  It is generated once per owner and never
    published on its own -- only g^{a_i} contributes to the aggregate Rs.
    Its secrecy is what defeats the collusion attack of section III.D.
    """

    sk: int
    pk: G2Point
    a: int
    owner_id: str = ""

    # Both hashes below are computed once and reused. They are needed for every
    # block during TagGen, and H1 in particular hashes a 192-byte G2 encoding,
    # so recomputing them per block would dominate tagging time for large files.
    _h1_cache: int | None = field(default=None, repr=False, compare=False)
    _h2_cache: int | None = field(default=None, repr=False, compare=False)

    @property
    def h1(self) -> int:
        """H1(pk_i) -- the exponent that binds this owner's key pair."""
        if self._h1_cache is None:
            self._h1_cache = H1(self.pk)
        return self._h1_cache

    @property
    def h2(self) -> int:
        """H2(sk_i || a_i) -- the masked index exponent."""
        if self._h2_cache is None:
            self._h2_cache = H2(self.sk, self.a)
        return self._h2_cache

    def apk_share(self) -> G2Point:
        """apk_i = pk_i^{H1(pk_i)}."""
        return g2_mul(self.pk, self.h1)

    def h_share(self) -> G2Point:
        """h'_i = g^{H2(sk_i || a_i)}  -- this owner's contribution to Hs."""
        return g2_mul(g2_generator(), self.h2)

    def r_share(self) -> G2Point:
        """r'_i = g^{a_i}  -- this owner's contribution to Rs."""
        return g2_mul(g2_generator(), self.a)


def key_gen(param: SystemParams, owner_id: str = "") -> OwnerKey:
    """
    KeyGen(param) -> (sk_i, pk_i).

    Cost is O(1) per owner, hence O(s) for a group of s owners.  This is the
    scheme's headline improvement: the baseline (scheme [8]) has every owner
    build a public-key-aggregation authenticator over all other owners, costing
    O(s) each and therefore O(s^2) overall.
    """
    sk = rand_scalar()
    pk = g2_mul(param.g, sk)
    a = rand_scalar()
    return OwnerKey(sk=sk, pk=pk, a=a, owner_id=owner_id)


def aggregate_public_key(owners: Sequence[OwnerKey]) -> G2Point:
    """
    APK = prod_i pk_i^{H1(pk_i)}.

    Binding each public key with a hash *of itself* is what makes the scheme
    rogue-key resistant without the expensive commitment / proof-of-possession
    machinery the baseline needs.  An attacker who publishes
    pk_2 = g^{alpha}*pk_1^{-1} cannot control H1(pk_2), so it cannot force the
    aggregate to collapse to a value it can sign under.
    """
    return g2_multi_add([o.apk_share() for o in owners])


# ==========================================================================
# TagGen
# ==========================================================================

@dataclass
class GroupState:
    """
    The public, auditable state of a group holding one file.

    These four values are exactly what the TPA needs to verify a proof; the TPA
    never sees a secret key, a mask, or any block content.
    """

    apk: G2Point   # APK = prod pk_i^{H1(pk_i)}
    rs: G2Point    # Rs  = prod g^{a_i}
    hs: G2Point    # Hs  = prod g^{H2(sk_i||a_i)}
    file_abstract: bytes
    n_blocks: int
    owner_ids: list = field(default_factory=list)


def tag_share(
    param: SystemParams,
    owner: OwnerKey,
    file_abstract: bytes,
    index: int,
    m_j: int,
) -> G1Point:
    """
    One owner's tag share for one block:

        sigma_ij = H3(F||j)^{H2(sk_i||a_i) + a_i} * (u^{m_j})^{sk_i * H1(pk_i)}

    Each owner computes this independently from its own secrets -- no owner
    ever learns another's sk or a.
    """
    h3 = H3(file_abstract, index)
    index_exp = scalar_mod(owner.h2 + owner.a)
    data_exp = scalar_mod(owner.sk * owner.h1)

    left = g1_mul(h3, index_exp)
    right = g1_mul(g1_mul(param.u, m_j), data_exp)
    return g1_add(left, right)


def tag_gen(
    param: SystemParams,
    owners: Sequence[OwnerKey],
    file_abstract: bytes,
    blocks: Sequence[int],
) -> tuple[list[G1Point], GroupState]:
    """
    TagGen({sk_i}, F, m_j, param) -> (sigma_j, Rs, Hs)

    Each owner produces a share per block; any owner may act as aggregator and
    multiply the shares together.  The aggregator learns nothing: a share is
    already a group element, not a secret.

        sigma_j = prod_i sigma_ij
                = H3(F||j)^{sum_i H2(sk_i||a_i)+a_i} * (u^{m_j})^{sum_i sk_i*H1(pk_i)}

    Cost is O(n*s).  The paper notes (section VII.B) that this is the accepted
    price of *not* outsourcing signing authority to a trusted party: the
    baseline achieves O(n) only by having a third party sign files it cannot
    see, which the paper argues owners would never agree to.
    """
    tags: list[G1Point] = []
    for j, m_j in enumerate(blocks):
        shares = [tag_share(param, o, file_abstract, j, m_j) for o in owners]
        tags.append(g1_multi_add(shares))

    state = GroupState(
        apk=aggregate_public_key(owners),
        rs=g2_multi_add([o.r_share() for o in owners]),
        hs=g2_multi_add([o.h_share() for o in owners]),
        file_abstract=file_abstract,
        n_blocks=len(blocks),
        owner_ids=[o.owner_id for o in owners],
    )
    return tags, state


# ==========================================================================
# Audit
# ==========================================================================

@dataclass(frozen=True)
class Challenge:
    """Q = {(j, v_j)} -- a c-element challenge set issued by the TPA."""

    pairs: tuple  # tuple of (index j, coefficient v_j)

    @property
    def size(self) -> int:
        return len(self.pairs)


@dataclass(frozen=True)
class Proof:
    """P = (DP, TP) -- the CSP's response. Constant size, independent of c."""

    dp: int      # DP = sum_{(j,v_j) in Q} m_j * v_j   (in Z_q)
    tp: G1Point  # TP = prod_{(j,v_j) in Q} sigma_j^{v_j}


def gen_challenge(n_blocks: int, c: int) -> Challenge:
    """
    TPA step 1: sample a c-element challenge Q = {(j, v_j)}, j sampled without
    replacement from [0, n).

    On the choice of c -- the paper (section VII.B.3) uses the standard PDP
    detection bound

        P_detect = 1 - ((n - t)/n)^c

    With n = 1000 blocks, a 1% corruption rate (t = 10) and a 99% detection
    target this gives c = 460.  Note this is *independent of n* for a fixed
    corruption *fraction*, which is why auditing stays cheap for large files.
    """
    if c > n_blocks:
        c = n_blocks
    indices = _sample_without_replacement(n_blocks, c)
    pairs = tuple((j, rand_scalar()) for j in indices)
    return Challenge(pairs=pairs)


def _sample_without_replacement(n: int, k: int) -> list[int]:
    """Cryptographically secure sample of k distinct indices from [0, n)."""
    if k >= n:
        return list(range(n))
    chosen: set[int] = set()
    while len(chosen) < k:
        chosen.add(secrets.randbelow(n))
    return sorted(chosen)


def gen_proof(
    challenge: Challenge,
    blocks: Sequence[int],
    tags: Sequence[G1Point],
) -> Proof:
    """
    CSP step: compute the proof of storage.

        DP = sum m_j * v_j        TP = prod sigma_j^{v_j}

    Both are constant size no matter how many blocks were challenged, which is
    the whole point of a homomorphic authenticator -- the CSP cannot answer
    without actually holding the challenged blocks and their tags.
    """
    dp = 0
    tp = g1_identity()
    for j, v_j in challenge.pairs:
        dp = (dp + blocks[j] * v_j) % CURVE_ORDER
        tp = g1_add(tp, g1_mul(tags[j], v_j))
    return Proof(dp=dp, tp=tp)


def verify_proof(
    param: SystemParams,
    state: GroupState,
    challenge: Challenge,
    proof: Proof,
) -> bool:
    """
    TPA step 2: verify

        e(TP, g) == e(prod H3(F||j)^{v_j}, Hs * Rs) * e(u^{DP}, APK)

    Correctness (paper Theorem 5, re-derived for the Type-3 port).  Write
        A = sum_i (H2(sk_i||a_i) + a_i)   and   B = sum_i sk_i * H1(pk_i),
    so that sigma_j = H3(F||j)^A * (u^{m_j})^B.  Then

        TP = prod_j sigma_j^{v_j}
           = (prod_j H3(F||j)^{v_j})^A * u^{B * sum_j m_j v_j}
           = (prod_j H3(F||j)^{v_j})^A * u^{B * DP}

        e(TP, g) = e(prod_j H3(F||j)^{v_j}, g^A) * e(u^{DP}, g^B)
                 = e(prod_j H3(F||j)^{v_j}, Hs*Rs) * e(u^{DP}, APK)

    since Hs*Rs = g^{sum_i H2(sk_i||a_i)} * g^{sum_i a_i} = g^A and APK = g^B.

    Cost: exactly THREE pairings regardless of file size or challenge size, and
    they are batched into one final exponentiation by pair_product().  The only
    part that grows with c is the c hash-to-curve operations.
    """
    # Left of the first pairing: prod_j H3(F||j)^{v_j}
    h_acc = g1_identity()
    for j, v_j in challenge.pairs:
        h_acc = g1_add(h_acc, g1_mul(H3(state.file_abstract, j), v_j))

    lhs_g1 = proof.tp
    rhs_1_g1, rhs_1_g2 = h_acc, g2_add(state.hs, state.rs)
    rhs_2_g1, rhs_2_g2 = g1_mul(param.u, proof.dp), state.apk

    # Verify  e(TP, g) == e(h_acc, Hs*Rs) * e(u^DP, APK)  as the equivalent
    # one-sided check  e(TP, g^{-1}) * e(h_acc, Hs*Rs) * e(u^DP, APK) == 1,
    # so all three pairings share a single final exponentiation.
    from .pairing import g2_neg

    result = pair_product(
        [
            (lhs_g1, g2_neg(param.g)),
            (rhs_1_g1, rhs_1_g2),
            (rhs_2_g1, rhs_2_g2),
        ]
    )
    return gt_eq(result, _gt_one(result))


def _gt_one(sample):
    """The identity of GT, built from a sample element's own type."""
    return sample.__class__.one()
