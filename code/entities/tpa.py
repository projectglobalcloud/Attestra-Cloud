"""
Third Party Auditor (TPA).

Role, per the base paper's system model (section IV.A):

    "TPA is equipped with powerful computational and storage capabilities to
     provide reliable and convincing auditing results on behalf of group
     owners."

WHY A TPA EXISTS AT ALL.  A data owner could audit the cloud themselves, but
then every owner pays the verification cost, and a dispute becomes one party's
word against another's.  Delegating to an independent auditor means:

  - owners are freed from continuous verification work;
  - the audit result is credible to a third party (a regulator, a court);
  - the auditor can batch-audit many files and many groups.

WHAT THE TPA IS NOT TRUSTED WITH.  It never sees block contents.  Everything it
needs is public: the challenge it chose, the constant-size proof, and the
group's published verification parameters (APK, Rs, Hs).  So a curious auditor
learns nothing about the data it is auditing -- this is the "privacy-preserving
public auditing" property.

Note the asymmetry that makes the scheme practical: the TPA's verification cost
is THREE PAIRINGS, no matter whether the file is 1 KB or 1 TB.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from crypto.scheme import (
    Challenge,
    GroupState,
    Proof,
    SystemParams,
    gen_challenge,
    verify_proof,
)

__all__ = ["AuditRecord", "ThirdPartyAuditor"]


@dataclass
class AuditRecord:
    """One completed audit, retained as evidence."""

    file_id: str
    passed: bool
    challenged_blocks: int
    total_blocks: int
    detection_probability: float
    duration_ms: float
    tag_version: int
    owner_ids: list = field(default_factory=list)
    note: str = ""

    def __str__(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"[{verdict}] {self.file_id}: challenged {self.challenged_blocks}"
            f"/{self.total_blocks} blocks, "
            f"detection >= {self.detection_probability * 100:.2f}%, "
            f"{self.duration_ms:.0f} ms"
        )


class ThirdPartyAuditor:
    """
    An independent auditor issuing random challenges and verifying proofs.

    Usage:

        tpa = ThirdPartyAuditor(param)
        record = tpa.audit(csp, file_id="...", state=group_state, c=460)
    """

    def __init__(self, param: SystemParams, name: str = "tpa-independent"):
        self.param = param
        self.name = name
        self.records: list[AuditRecord] = []

    # ------------------------------------------------------------------
    # Challenge
    # ------------------------------------------------------------------

    def issue_challenge(self, n_blocks: int, c: int) -> Challenge:
        """
        Build Q = {(j, v_j)} with j sampled at random from [0, n).

        The randomness is what makes the audit meaningful.  If the CSP could
        predict which blocks would be challenged, it could keep only those and
        discard the rest.  Both the indices j and the coefficients v_j are
        drawn from a cryptographically secure source for exactly this reason.
        """
        return gen_challenge(n_blocks, c)

    @staticmethod
    def detection_probability(n_blocks: int, c: int, corruption_rate: float = 0.01) -> float:
        """
        The CLASSICAL PDP bound (Ateniese et al., 2007):

            P_detect = 1 - ((n - t)/n)^c        where t = corruption_rate * n

        This is what the base paper uses in section VII.B.3 to justify c = 460,
        and it is what we quote when comparing against the paper.

        Its striking property: it depends on the FRACTION corrupted, not on n.
        Challenging 460 blocks catches a 1% corruption with ~99% probability
        whether the file has 1,000 blocks or 1,000,000.  Auditing cost
        therefore does not grow with dataset size.

        IMPORTANT CAVEAT.  This formula models sampling WITH replacement -- it
        is the probability that c independent draws all miss the corrupted
        blocks.  Our auditor samples WITHOUT replacement (gen_challenge draws
        distinct indices), which is strictly better.  So this is a CONSERVATIVE
        LOWER BOUND on what the implementation actually achieves; use
        detection_probability_exact() for the true figure.  The gap is
        negligible when c << n and large when c approaches n.
        """
        if n_blocks <= 0 or c <= 0:
            return 0.0
        t = max(1, int(round(corruption_rate * n_blocks)))
        if t >= n_blocks:
            return 1.0
        return 1.0 - ((n_blocks - t) / n_blocks) ** min(c, n_blocks)

    @staticmethod
    def detection_probability_exact(
        n_blocks: int, c: int, corruption_rate: float = 0.01
    ) -> float:
        """
        The EXACT detection probability for sampling without replacement.

        Choosing c distinct blocks out of n, of which t are corrupted, the
        probability of missing every corrupted block is hypergeometric:

            P_miss   = C(n - t, c) / C(n, c)
                     = prod_{i=0}^{c-1}  (n - t - i) / (n - i)
            P_detect = 1 - P_miss

        Computed as a running product rather than via factorials, which would
        overflow badly for realistic n.

        This is the figure our implementation actually achieves, and it is what
        the measured rates in attacks/tampering.py are compared against.  Note
        that when c >= n - t the probability is exactly 1: challenge enough
        blocks and a corrupted one cannot be avoided.
        """
        if n_blocks <= 0 or c <= 0:
            return 0.0
        t = max(1, int(round(corruption_rate * n_blocks)))
        if t >= n_blocks:
            return 1.0
        c = min(c, n_blocks)
        if c > n_blocks - t:
            return 1.0

        p_miss = 1.0
        for i in range(c):
            p_miss *= (n_blocks - t - i) / (n_blocks - i)
        return 1.0 - p_miss

    @staticmethod
    def required_challenge_size(
        n_blocks: int, corruption_rate: float = 0.01, target: float = 0.99
    ) -> int:
        """
        Smallest c meeting a detection target -- the inverse of the bound above.

        Solving  1 - (1 - r)^c >= target  gives  c >= log(1 - target)/log(1 - r).
        """
        import math

        if not 0 < corruption_rate < 1:
            raise ValueError("corruption_rate must lie strictly between 0 and 1")
        c = math.ceil(math.log(1 - target) / math.log(1 - corruption_rate))
        return min(c, n_blocks)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(
        self,
        state: GroupState,
        challenge: Challenge,
        proof: Proof,
    ) -> bool:
        """
        Check  e(TP, g) == e(prod H3(F||j)^{v_j}, Hs*Rs) * e(u^{DP}, APK).

        A failure here means one of three things, and the auditor cannot tell
        which -- nor does it need to:
          - the cloud no longer holds the data intact;
          - the cloud tried to forge a proof;
          - the verification parameters are stale (an ownership change was
            applied to the tags but the auditor is using the old APK/Rs/Hs).
        In every case the correct action is the same: do not certify the data.
        """
        return verify_proof(self.param, state, challenge, proof)

    # ------------------------------------------------------------------
    # Full audit
    # ------------------------------------------------------------------

    def audit(
        self,
        csp,
        file_id: str,
        state: GroupState,
        c: int | None = None,
        corruption_rate: float = 0.01,
        note: str = "",
    ) -> AuditRecord:
        """
        Run one complete audit round against a CSP and record the outcome.

        The three protocol steps, in order:
          1. TPA picks a random challenge Q and sends it to the CSP.
          2. CSP replies with the constant-size proof P = (DP, TP).
          3. TPA verifies the pairing equation.

        The data itself never moves.
        """
        stored = csp.get(file_id)
        n = stored.n_blocks
        if c is None:
            c = self.required_challenge_size(n, corruption_rate)
        c = min(c, n)

        start = time.perf_counter()
        challenge = self.issue_challenge(n, c)
        proof = csp.generate_proof(file_id, challenge)
        passed = self.verify(state, challenge, proof)
        duration_ms = (time.perf_counter() - start) * 1000

        record = AuditRecord(
            file_id=file_id,
            passed=passed,
            challenged_blocks=challenge.size,
            total_blocks=n,
            detection_probability=self.detection_probability(n, c, corruption_rate),
            duration_ms=duration_ms,
            tag_version=stored.tag_version,
            owner_ids=list(state.owner_ids),
            note=note,
        )
        self.records.append(record)
        return record

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def report(self) -> str:
        """A plain-text audit report, the deliverable of functional req. #9."""
        if not self.records:
            return "No audits performed."

        passed = sum(1 for r in self.records if r.passed)
        lines = [
            "=" * 72,
            f"AUDIT REPORT  --  {self.name}",
            "=" * 72,
            f"Audits performed : {len(self.records)}",
            f"Passed           : {passed}",
            f"Failed           : {len(self.records) - passed}",
            "-" * 72,
        ]
        for i, record in enumerate(self.records, 1):
            lines.append(f"{i:3d}. {record}")
            if record.note:
                lines.append(f"      note: {record.note}")
        lines.append("=" * 72)
        return "\n".join(lines)
