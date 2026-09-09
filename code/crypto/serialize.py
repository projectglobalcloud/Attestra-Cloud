"""
Encoding of group elements and scalars for database storage and transport.

The Flask application stores tags, public keys and verification parameters in
MySQL as BLOB columns.  This module is the boundary: everything on the crypto
side is a py_ecc point, everything on the database side is bytes.

Encoding is uncompressed affine, big-endian, fixed width:
    G1  ->  96 bytes   (x, y   in Fp)
    G2  -> 192 bytes   (x, y   in Fp2, each two Fp coefficients)
    Z_q ->  32 bytes

Uncompressed rather than compressed because decompression needs a square root
in Fp (and in Fp2 for G2), which in pure Python costs more than the 48 bytes
saved.  Storage is cheap here; CPU is not.
"""

from __future__ import annotations

from py_ecc.optimized_bls12_381 import FQ, FQ2, is_on_curve, b, b2

from .pairing import CURVE_ORDER, G1Point, G2Point, g1_to_bytes, g2_to_bytes

__all__ = [
    "g1_encode",
    "g1_decode",
    "g2_encode",
    "g2_decode",
    "scalar_encode",
    "scalar_decode",
    "G1_SIZE",
    "G2_SIZE",
    "SCALAR_SIZE",
]

G1_SIZE = 96
G2_SIZE = 192
SCALAR_SIZE = 32

_G1_INFINITY_ENCODING = b"\x00" * G1_SIZE
_G2_INFINITY_ENCODING = b"\x00" * G2_SIZE


def g1_encode(point: G1Point) -> bytes:
    return g1_to_bytes(point)


def g1_decode(raw: bytes, validate: bool = True) -> G1Point:
    """
    Decode a G1 point.

    `validate` checks the point actually lies on the curve.  This matters: a
    tampered database row carrying an off-curve point could otherwise make the
    pairing produce a meaningless result rather than an honest verification
    failure.  It is on by default and only disabled in hot benchmark loops.
    """
    if len(raw) != G1_SIZE:
        raise ValueError(f"G1 encoding must be {G1_SIZE} bytes, got {len(raw)}")
    if raw == _G1_INFINITY_ENCODING:
        from .pairing import g1_identity
        return g1_identity()

    x = int.from_bytes(raw[:48], "big")
    y = int.from_bytes(raw[48:], "big")
    point = (FQ(x), FQ(y), FQ.one())
    if validate and not is_on_curve(point, b):
        raise ValueError("decoded G1 point is not on the curve")
    return point


def g2_encode(point: G2Point) -> bytes:
    return g2_to_bytes(point)


def g2_decode(raw: bytes, validate: bool = True) -> G2Point:
    if len(raw) != G2_SIZE:
        raise ValueError(f"G2 encoding must be {G2_SIZE} bytes, got {len(raw)}")
    if raw == _G2_INFINITY_ENCODING:
        from .pairing import g2_identity
        return g2_identity()

    coords = [int.from_bytes(raw[i * 48:(i + 1) * 48], "big") for i in range(4)]
    x = FQ2([coords[0], coords[1]])
    y = FQ2([coords[2], coords[3]])
    point = (x, y, FQ2.one())
    if validate and not is_on_curve(point, b2):
        raise ValueError("decoded G2 point is not on the curve")
    return point


def scalar_encode(value: int) -> bytes:
    """Encode an element of Z_q. Negative inputs are reduced first."""
    return (value % CURVE_ORDER).to_bytes(SCALAR_SIZE, "big")


def scalar_decode(raw: bytes) -> int:
    if len(raw) != SCALAR_SIZE:
        raise ValueError(f"scalar encoding must be {SCALAR_SIZE} bytes, got {len(raw)}")
    return int.from_bytes(raw, "big") % CURVE_ORDER
