"""
Cloud Storage Provider (CSP).

Role, per the base paper's system model (section IV.A):

    "CSP is a semi-trusted entity equipped with abundant storage and
     computational resources. Its primary responsibility is to store
     owner-uploaded data and facilitate external integrity auditing."

SEMI-TRUSTED is the important word.  The CSP is trusted to be *available* but
NOT trusted to be *honest*.  The threat model assumes it may:

  - silently delete or corrupt blocks (to save storage), then lie about it;
  - collude with a buyer to forge proofs over data that was never sold.

So this class is deliberately written as an untrusted server.  Note what it
never receives:

  - no owner's secret key sk_i
  - no owner's random mask a_i
  - no un-blinded aggregate signing exponent

It holds only blocks, tags, and the tokens it is told to apply.  Everything it
can do here, a real malicious cloud could also do -- which is why the
`corrupt_block()` method exists: it is not a bug, it is the attack surface we
demonstrate against in attacks/tampering.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crypto.ownership import OwnershipToken, apply_token
from crypto.pairing import G1Point
from crypto.scheme import Challenge, Proof, SystemParams, gen_proof

__all__ = ["StoredFile", "CloudStorageProvider"]


@dataclass
class StoredFile:
    """
    One file as the CSP holds it.

    `blocks` are the Z_q-reduced block values, `tags` the homomorphic
    authenticators. The CSP needs both: blocks to compute DP, tags to compute
    TP, and blocks again to apply an ownership token.
    """

    file_id: str
    file_abstract: bytes
    blocks: list[int]
    tags: list[G1Point]
    tag_version: int = 0          # bumped every time an ODT is applied
    original_blocks: list[int] = field(default_factory=list)

    @property
    def n_blocks(self) -> int:
        return len(self.blocks)


class CloudStorageProvider:
    """
    An honest-by-default but corruptible cloud.

    Usage mirrors the protocol exactly:

        csp.store(file_id, F, blocks, tags)          # owners upload
        proof = csp.generate_proof(file_id, chal)    # answer a TPA challenge
        csp.apply_ownership_token(file_id, token)    # rewrite tags after a sale
    """

    def __init__(self, param: SystemParams, name: str = "csp-primary"):
        self.param = param
        self.name = name
        self._files: dict[str, StoredFile] = {}
        self.proof_count = 0
        self.token_applications = 0

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def store(
        self,
        file_id: str,
        file_abstract: bytes,
        blocks: list[int],
        tags: list[G1Point],
    ) -> StoredFile:
        """Accept an upload from a group of owners."""
        if len(blocks) != len(tags):
            raise ValueError(
                f"block/tag count mismatch: {len(blocks)} blocks vs {len(tags)} tags"
            )
        stored = StoredFile(
            file_id=file_id,
            file_abstract=file_abstract,
            blocks=list(blocks),
            tags=list(tags),
            original_blocks=list(blocks),
        )
        self._files[file_id] = stored
        return stored

    def get(self, file_id: str) -> StoredFile:
        if file_id not in self._files:
            raise KeyError(f"CSP is not storing any file with id {file_id!r}")
        return self._files[file_id]

    def has(self, file_id: str) -> bool:
        return file_id in self._files

    @property
    def file_ids(self) -> list[str]:
        return list(self._files)

    # ------------------------------------------------------------------
    # Auditing -- responding to a TPA challenge
    # ------------------------------------------------------------------

    def generate_proof(self, file_id: str, challenge: Challenge) -> Proof:
        """
        Compute the proof of storage P = (DP, TP) for a challenge.

            DP = sum_{(j,v_j) in Q} m_j * v_j
            TP = prod_{(j,v_j) in Q} sigma_j^{v_j}

        The response is CONSTANT SIZE -- one scalar and one group element --
        however many blocks were challenged.  That is the practical payoff of
        the whole scheme: verifying a 100 MB dataset costs the same bandwidth
        as verifying a 1 KB one, and the data never leaves the cloud.

        A cloud that has discarded the challenged blocks cannot fake this: it
        would have to produce a TP consistent with a DP it cannot compute.
        """
        stored = self.get(file_id)
        self.proof_count += 1
        return gen_proof(challenge, stored.blocks, stored.tags)

    # ------------------------------------------------------------------
    # Ownership -- applying a token
    # ------------------------------------------------------------------

    def apply_ownership_token(self, file_id: str, token: OwnershipToken) -> StoredFile:
        """
        Rewrite every tag with an ODT handed over by the owners.

            sigma'_j = sigma_j * H3(F||j)^H * (u^{m_j})^{AUX} * V^{m_j}

        This is the ONLY O(n) step in an ownership change, and the design puts
        it here on purpose: the CSP is the party with "abundant computational
        resources", while the owners exchange only a constant-size token.

        The CSP learns nothing usable from the token. H and AUX are aggregate
        sums over the group, and AUX is additively blinded by random x_i values
        that are only recoverable inside the exponent of u.
        """
        stored = self.get(file_id)
        stored.tags = apply_token(
            self.param,
            token,
            stored.file_abstract,
            stored.blocks,
            stored.tags,
        )
        stored.tag_version += 1
        self.token_applications += 1
        return stored

    # ------------------------------------------------------------------
    # Adversarial behaviour -- used by the attack demonstrations
    # ------------------------------------------------------------------

    def corrupt_block(self, file_id: str, index: int, new_value: int | None = None) -> int:
        """
        Simulate a dishonest or failing cloud silently altering a block.

        This models both malice (deleting rarely-read data to save cost, then
        bluffing) and accident (bit rot, a bad disk).  The auditing protocol
        cannot tell the two apart -- and should not have to.

        Returns the old value so the caller can restore it.
        """
        stored = self.get(file_id)
        if not 0 <= index < stored.n_blocks:
            raise IndexError(f"block index {index} out of range for {file_id!r}")

        old = stored.blocks[index]
        if new_value is None:
            # Flip to a different value in Z_q; +1 is enough to break the
            # homomorphic relation, and keeps the demo's output readable.
            from crypto.pairing import CURVE_ORDER
            new_value = (old + 1) % CURVE_ORDER
        stored.blocks[index] = new_value
        return old

    def restore_block(self, file_id: str, index: int, value: int) -> None:
        """Undo corrupt_block(), so one demo does not poison the next."""
        self.get(file_id).blocks[index] = value

    def delete_block(self, file_id: str, index: int) -> int:
        """
        Simulate the cloud discarding a block entirely and substituting zero.

        A real cloud that lost data would have to invent *something* to answer
        a challenge; zero is the cheapest lie available to it.
        """
        return self.corrupt_block(file_id, index, new_value=0)

    def stats(self) -> dict:
        """Summary for demo output."""
        return {
            "name": self.name,
            "files_stored": len(self._files),
            "total_blocks": sum(f.n_blocks for f in self._files.values()),
            "proofs_generated": self.proof_count,
            "tokens_applied": self.token_applications,
        }
