"""
Cryptographic core of the cloud auditing scheme.

Implements Wu, You & Huang, "Scalable Cloud Auditing With Efficient Ownership
Transfer for Group Transactions", IEEE TIFS vol. 20, 2025, ported to BLS12-381.

This package has no web or database dependencies -- it is the protocol, and
nothing else.  The Flask application in ../app/ consumes it through serialize.py.
"""

from .hashes import H1, H2, H3, hash_block
from .ownership import (
    OwnershipToken,
    add_owners,
    apply_token,
    revoke_owners,
    transfer_finalize_bg,
    transfer_initiate_sg,
    transfer_ownership,
)
from .pairing import CURVE_ORDER
from .scheme import (
    Challenge,
    GroupState,
    OwnerKey,
    Proof,
    SystemParams,
    aggregate_public_key,
    gen_challenge,
    gen_proof,
    key_gen,
    sys_gen,
    tag_gen,
    verify_proof,
)

__all__ = [
    "CURVE_ORDER",
    "SystemParams",
    "OwnerKey",
    "GroupState",
    "Challenge",
    "Proof",
    "OwnershipToken",
    "sys_gen",
    "key_gen",
    "aggregate_public_key",
    "tag_gen",
    "gen_challenge",
    "gen_proof",
    "verify_proof",
    "add_owners",
    "revoke_owners",
    "transfer_initiate_sg",
    "transfer_finalize_bg",
    "transfer_ownership",
    "apply_token",
    "H1",
    "H2",
    "H3",
    "hash_block",
]
