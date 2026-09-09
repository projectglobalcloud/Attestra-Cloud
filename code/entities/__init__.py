"""
The four entities of the system model (base paper, section IV.A).

    CloudStorageProvider  (CSP)  - semi-trusted storage, generates proofs
    ThirdPartyAuditor     (TPA)  - independent verifier, sees no data
    OwnerGroup            (SG)   - the group currently owning the data
    OwnerGroup            (BG)   - the group buying it

SG and BG are the same class; "sale" and "buy" are roles within a single
transaction, not permanent properties of a group.

These classes hold NO cryptography of their own -- every operation delegates to
crypto/.  They exist to make the protocol's message flow legible: who sends
what to whom, and who is allowed to know what.
"""

from .csp import CloudStorageProvider, StoredFile
from .group import DataOwner, OwnerGroup
from .tpa import AuditRecord, ThirdPartyAuditor

__all__ = [
    "CloudStorageProvider",
    "StoredFile",
    "ThirdPartyAuditor",
    "AuditRecord",
    "OwnerGroup",
    "DataOwner",
]
