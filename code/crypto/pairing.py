"""
Pairing backend for the cloud auditing scheme.

The base paper (Wu, You & Huang, IEEE TIFS vol. 20, 2025) assumes a *symmetric*
Type-1 pairing  e: G1 x G1 -> GT , as supplied by the PBC library's Type-A
curves.  Type-1 curves are obsolete and unavailable in maintained Python
libraries, so we port the scheme to BLS12-381, an *asymmetric* Type-3 curve
  e: G1 x G2 -> GT .

Placement rule for the port (see PROJECT_PLAN.md section 3.2):

    G1  -  u, H3(F||j), file tags sigma_j, the proof TP
    G2  -  the generator g, public keys pk_i, APK, Rs, Hs

Every element that is raised to a secret and then *published* as a verification
parameter lives in G2; the tag and its bases live in G1.  With that placement
every pairing in the scheme is a well-formed G1 x G2 pairing and the audit
equation is preserved exactly (proved algebraically in PROJECT_PLAN.md 3.3).

This module is the ONLY place that touches py_ecc.  Swapping in a faster
native backend later means rewriting this file and nothing else.
"""

from __future__ import annotations

import secrets
from typing import Iterable

from py_ecc.optimized_bls12_381 import (
    G1 as _G1_GEN,
    G2 as _G2_GEN,
    Z1 as _G1_INF,
    Z2 as _G2_INF,
    add as _add,
    curve_order as CURVE_ORDER,
    final_exponentiate as _final_exponentiate,
    multiply as _multiply,
    neg as _neg,
    normalize as _normalize,
    pairing as _pairing,
)

__all__ = [
    "CURVE_ORDER",
    "G1Point",
    "G2Point",
    "g1_generator",
    "g2_generator",
    "g1_identity",
    "g2_identity",
    "g1_mul",
    "g2_mul",
    "g1_add",
    "g2_add",
    "g1_neg",
    "g2_neg",
    "g1_multi_add",
    "g2_multi_add",
    "pair",
    "pair_product",
    "gt_eq",
    "g1_eq",
    "g2_eq",
    "rand_scalar",
    "scalar_mod",
]

# Type aliases: py_ecc optimised points are 3-tuples of field elements.
G1Point = tuple
G2Point = tuple


# --------------------------------------------------------------------------
# Scalars (Z_q)
# --------------------------------------------------------------------------

def rand_scalar() -> int:
    """Uniform random element of Z_q^* (cryptographically secure)."""
    return 1 + secrets.randbelow(CURVE_ORDER - 1)


def scalar_mod(x: int) -> int:
    """Reduce an integer into Z_q. Handles negatives (Python % is non-negative)."""
    return x % CURVE_ORDER


# --------------------------------------------------------------------------
# Group generators / identities
# --------------------------------------------------------------------------

def g1_generator() -> G1Point:
    return _G1_GEN


def g2_generator() -> G2Point:
    return _G2_GEN


def g1_identity() -> G1Point:
    """Identity of G1 (written multiplicatively in the paper, additively here)."""
    return _G1_INF


def g2_identity() -> G2Point:
    return _G2_INF


# --------------------------------------------------------------------------
# Group operations
#
# The paper writes the groups multiplicatively (x^a, x*y); py_ecc writes them
# additively (multiply(x, a), add(x, y)).  These wrappers keep the naming
# honest: `mul` = the paper's exponentiation, `add` = the paper's product.
# --------------------------------------------------------------------------

def g1_mul(point: G1Point, scalar: int) -> G1Point:
    """Paper's  point^scalar  in G1."""
    s = scalar_mod(scalar)
    if s == 0:
        return _G1_INF
    return _multiply(point, s)


def g2_mul(point: G2Point, scalar: int) -> G2Point:
    """Paper's  point^scalar  in G2."""
    s = scalar_mod(scalar)
    if s == 0:
        return _G2_INF
    return _multiply(point, s)


def g1_add(a: G1Point, b: G1Point) -> G1Point:
    """Paper's  a * b  in G1."""
    return _add(a, b)


def g2_add(a: G2Point, b: G2Point) -> G2Point:
    """Paper's  a * b  in G2."""
    return _add(a, b)


def g1_neg(a: G1Point) -> G1Point:
    """Paper's  a^{-1}  in G1."""
    return _neg(a)


def g2_neg(a: G2Point) -> G2Point:
    """Paper's  a^{-1}  in G2."""
    return _neg(a)


def g1_multi_add(points: Iterable[G1Point]) -> G1Point:
    """Paper's  prod_i point_i  in G1."""
    acc = _G1_INF
    for p in points:
        acc = _add(acc, p)
    return acc


def g2_multi_add(points: Iterable[G2Point]) -> G2Point:
    """Paper's  prod_i point_i  in G2."""
    acc = _G2_INF
    for p in points:
        acc = _add(acc, p)
    return acc


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------

def pair(p1: G1Point, p2: G2Point):
    """e(p1, p2) with p1 in G1, p2 in G2. Returns a GT element."""
    # py_ecc's pairing() signature is pairing(Q in G2, P in G1).
    return _pairing(p2, p1)


def pair_product(pairs: Iterable[tuple]) -> object:
    """
    Product of pairings  prod_k e(p1_k, p2_k)  computed with a SINGLE final
    exponentiation instead of one per pairing.

    The final exponentiation dominates the cost of a BLS12-381 pairing, so this
    turns the audit's three pairings into roughly the cost of one.  Used by the
    verifier, which is the latency the user actually feels.
    """
    acc = None
    for p1, p2 in pairs:
        f = _pairing(p2, p1, final_exponentiate=False)
        acc = f if acc is None else acc * f
    if acc is None:
        raise ValueError("pair_product() requires at least one pair")
    return _final_exponentiate(acc)


def gt_eq(a, b) -> bool:
    """Equality test for GT elements."""
    return a == b


# --------------------------------------------------------------------------
# Point equality
#
# IMPORTANT: py_ecc's optimised points are PROJECTIVE triples (x, y, z), and
# the same curve point has infinitely many such representations -- (x, y, 1)
# and (4x, 8y, 2) are the same point.  So `point_a == point_b` on the raw
# tuples is WRONG: it compares representations, not points.
#
# It happens to work when both sides were built by identical operation
# sequences, which makes the bug intermittent and easy to miss.  Anything that
# compares group elements must go through these helpers.
# --------------------------------------------------------------------------

def g1_eq(a: G1Point, b: G1Point) -> bool:
    """True iff a and b are the same point of G1, whatever their representation."""
    a_inf, b_inf = _is_infinity(a), _is_infinity(b)
    if a_inf or b_inf:
        return a_inf and b_inf
    return _normalize(a) == _normalize(b)


def g2_eq(a: G2Point, b: G2Point) -> bool:
    """True iff a and b are the same point of G2, whatever their representation."""
    a_inf, b_inf = _is_infinity(a), _is_infinity(b)
    if a_inf or b_inf:
        return a_inf and b_inf
    return _normalize(a) == _normalize(b)


# --------------------------------------------------------------------------
# Canonical encoding (used by serialize.py and by the hash functions)
# --------------------------------------------------------------------------

def g1_to_bytes(point: G1Point) -> bytes:
    """Uncompressed affine encoding of a G1 point: 48-byte x || 48-byte y."""
    if _is_infinity(point):
        return b"\x00" * 96
    x, y = _normalize(point)
    return int(x).to_bytes(48, "big") + int(y).to_bytes(48, "big")


def g2_to_bytes(point: G2Point) -> bytes:
    """Uncompressed affine encoding of a G2 point: 4 x 48-byte Fp2 coordinates."""
    if _is_infinity(point):
        return b"\x00" * 192
    x, y = _normalize(point)
    out = b""
    for coord in (x, y):
        for c in coord.coeffs:
            out += int(c).to_bytes(48, "big")
    return out


def _is_infinity(point) -> bool:
    """
    A projective point is the identity iff its Z coordinate is zero.

    G1 coordinates are FQ (which compares against int); G2 coordinates are FQ2,
    which raises TypeError on int comparison, so we inspect its coefficients.
    """
    z = point[2]
    coeffs = getattr(z, "coeffs", None)
    if coeffs is None:
        return z == 0
    return all(int(c) == 0 for c in coeffs)
