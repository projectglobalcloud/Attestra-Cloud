"""
The three hash functions of the scheme.

The base paper's notation is internally inconsistent: section V.B declares
    H1 : {0,1}* -> G1     and    H2 : {0,1}* -> Zp
but then uses H1(pk_i) as an *exponent* (pk_i^{H1(pk_i)}, APK = prod pk_i^{H1(pk_i)}),
which requires H1 to land in Zp, not G1.  It also uses H2(F||j) as a *group
element* in TagGen while writing the same value as H3(F||j) in the verification
equation of Audit and in the correctness proof (Theorem 5).

We use the assignment the equations actually require, and name them distinctly:

    H1 : {0,1}* -> Z_q     H1(pk_i)        key-pair binding exponent
    H2 : {0,1}* -> Z_q     H2(sk_i||a_i)   masked index exponent
    H3 : {0,1}* -> G1      H3(F||j)        hash-to-curve of the block index

This is a notation fix, not a change of scheme: every equation in the paper is
reproduced exactly under this reading.  Documented in PROJECT_PLAN.md 3.1.

Security note on H1: it must be bound to the *encoding of the public key* so
that an attacker cannot choose a key pair after seeing the exponent.  This is
precisely what defeats the rogue-key attack of the paper's section III.C.
"""

from __future__ import annotations

import hashlib

from py_ecc.bls.hash_to_curve import hash_to_G1 as _hash_to_G1

from .pairing import CURVE_ORDER, G1Point, G2Point, g2_to_bytes

__all__ = ["H1", "H2", "H3", "H1_bytes", "hash_block"]

# Domain separation tags.  Distinct tags stop a digest produced for one purpose
# from being replayed as another (a standard requirement, and necessary here
# because H1 and H2 share a codomain).
_DST_H1 = b"GAT-CLOUDAUDIT-V1-H1-PKBIND"
_DST_H2 = b"GAT-CLOUDAUDIT-V1-H2-INDEXKEY"
_DST_H3 = b"GAT-CLOUDAUDIT-V1-H3-BLOCKINDEX_XMD:SHA-256_SSWU_RO_"
_DST_BLOCK = b"GAT-CLOUDAUDIT-V1-BLOCK"


def _hash_to_zq(dst: bytes, *parts: bytes) -> int:
    """
    Map arbitrary input into Z_q.

    Uses SHA-512 and reduces mod q.  With a 512-bit digest against a 255-bit
    q, the modular bias is below 2^-255 and therefore negligible -- taking
    SHA-256 mod q would be measurably biased.
    """
    h = hashlib.sha512()
    h.update(len(dst).to_bytes(4, "big"))
    h.update(dst)
    for part in parts:
        # Length-prefix every field so that H(a||b) is unambiguous.
        h.update(len(part).to_bytes(8, "big"))
        h.update(part)
    return int.from_bytes(h.digest(), "big") % CURVE_ORDER


def H1(pk: G2Point) -> int:
    """
    H1(pk_i) -> Z_q.  The exponent that binds an owner's public key into both
    the aggregated public key APK and the owner's own tag share.
    """
    return _hash_to_zq(_DST_H1, g2_to_bytes(pk))


def H1_bytes(pk_bytes: bytes) -> int:
    """H1 over an already-serialised public key (used when reading from the DB)."""
    return _hash_to_zq(_DST_H1, pk_bytes)


def H2(sk: int, a: int) -> int:
    """
    H2(sk_i || a_i) -> Z_q.  The index exponent, masked by the owner's secret
    random value a_i.

    a_i is never published on its own -- only the aggregate Rs = prod g^{a_i}
    is.  That is what makes the leaked index secret useless to a colluding
    buyer, defeating the attack in the paper's section III.D.
    """
    return _hash_to_zq(
        _DST_H2,
        sk.to_bytes(32, "big"),
        a.to_bytes(32, "big"),
    )


def H3(file_abstract: bytes, index: int) -> G1Point:
    """
    H3(F || j) -> G1.  Hash-to-curve of a block index, bound to the file
    abstract F so that a tag from one file cannot be replayed for another.

    Uses the standard RFC 9380 SSWU hash-to-curve, which is constant-time and
    indifferentiable from a random oracle -- the assumption the security proofs
    (Theorems 1-4) are stated under.
    """
    msg = (
        len(file_abstract).to_bytes(8, "big")
        + file_abstract
        + index.to_bytes(8, "big")
    )
    return _hash_to_G1(msg, _DST_H3, hashlib.sha256)


def hash_block(block: bytes) -> int:
    """
    Compress a file block to an element of Z_q  (the 'hashed' block mode).

    A BLS12-381 scalar holds ~255 bits, so a real 4 KB block cannot be embedded
    directly.  Hashing it preserves soundness: computing SHA-256(block) still
    requires possessing the block, so a cloud that has discarded the data
    cannot produce a valid proof.

    The paper's 8-byte blocks fit in Z_q directly; that mode is available as
    'raw' in config.py and is what the benchmarks use for parity with the paper.
    """
    return _hash_to_zq(_DST_BLOCK, block)
