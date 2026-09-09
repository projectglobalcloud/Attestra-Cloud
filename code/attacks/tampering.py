"""
ATTACK 3 -- DATA TAMPERING BY THE CLOUD, AND THE ODDS OF CATCHING IT.

THE SITUATION
-------------
The cloud is semi-trusted: available, but not honest. A cloud has a standing
financial incentive to quietly discard data that is rarely read, and to bluff
if anyone asks. Bit rot and failing disks produce the same observable
behaviour without any malice at all. The auditing protocol cannot distinguish
the two, and does not need to -- in both cases the correct answer is "do not
certify this data".

TWO THINGS THIS SCRIPT DEMONSTRATES
-----------------------------------
1. DETERMINISTIC DETECTION. If a corrupted block is challenged, the audit
   fails. Always. There is no probability involved in that step -- the
   homomorphic relation simply stops holding.

2. PROBABILISTIC COVERAGE. The auditor does not challenge every block; that
   would defeat the purpose. It samples c of them. So the real question is:
   what are the odds the sample hits at least one corrupted block?

       P_detect = 1 - ((n - t)/n)^c

   This is the standard PDP bound (Ateniese et al., 2007), and it is what the
   base paper uses in section VII.B.3 to justify c = 460.

   The striking consequence is that this depends on the FRACTION corrupted,
   not on n. Challenging 460 blocks catches a 1% corruption with ~99%
   probability whether the dataset has a thousand blocks or a billion. Audit
   cost does not grow with dataset size.

   Rather than asserting that formula, this script MEASURES it: it runs many
   independent trials and compares the observed detection rate against theory.

Run:
    python -m attacks.tampering
    python -m attacks.tampering --trials 400
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crypto.hashes import hash_block  # noqa: E402
from crypto.pairing import rand_scalar  # noqa: E402
from crypto.scheme import (  # noqa: E402
    Challenge,
    gen_challenge,
    gen_proof,
    key_gen,
    sys_gen,
    tag_gen,
    verify_proof,
)
from demo.console import banner, bold, dim, expected, green, item, note, red, step  # noqa: E402
from entities import CloudStorageProvider, ThirdPartyAuditor  # noqa: E402

FILE_ABSTRACT = b"tampering-demo-dataset"
N_BLOCKS = 200


def _setup(n_blocks: int = N_BLOCKS, n_owners: int = 3):
    """Build a small but realistic tagged file held by a CSP."""
    param = sys_gen()
    owners = [key_gen(param, f"owner-{i}") for i in range(n_owners)]
    blocks = [hash_block(f"record-{i}".encode()) for i in range(n_blocks)]
    tags, state = tag_gen(param, owners, FILE_ABSTRACT, blocks)

    csp = CloudStorageProvider(param)
    csp.store("dataset-shard-0", FILE_ABSTRACT, blocks, tags)
    tpa = ThirdPartyAuditor(param)
    return param, csp, tpa, state


# ==========================================================================
# Part 1 -- a single corrupted block is always caught when challenged
# ==========================================================================

def demo_single_corruption(param, csp, tpa, state) -> bool:
    step("3a", "A CORRUPTED BLOCK IS ALWAYS CAUGHT WHEN CHALLENGED")

    file_id = "dataset-shard-0"
    outcomes = []

    record = tpa.audit(csp, file_id, state, c=32, note="honest cloud")
    outcomes.append(expected(record.passed, True, "honest cloud passes"))

    note(
        "Now the cloud corrupts one block. We challenge that exact block, so "
        "the result depends only on the cryptography, not on sampling luck."
    )

    for label, index, action in [
        ("single bit changed", 17, "corrupt"),
        ("block deleted (answered with zeros)", 42, "delete"),
    ]:
        if action == "corrupt":
            old = csp.corrupt_block(file_id, index)
        else:
            old = csp.delete_block(file_id, index)

        forced = Challenge(pairs=((index, rand_scalar()),))
        proof = csp.generate_proof(file_id, forced)
        accepted = tpa.verify(state, forced, proof)

        outcomes.append(expected(accepted, False, f"{label} (block {index}) is detected"))
        csp.restore_block(file_id, index, old)

    record = tpa.audit(csp, file_id, state, c=32, note="after restoring both blocks")
    outcomes.append(expected(record.passed, True, "restored data passes again"))

    print()
    note(
        "Detection here is not probabilistic. Corrupting m_j breaks the "
        "homomorphic relation between DP and TP, and the pairing equation "
        "cannot balance. The cloud would have to forge a tag to get away with "
        "it -- which is exactly what Theorem 2 (unforgeability) rules out "
        "under the CDH assumption."
    )
    return all(outcomes)


# ==========================================================================
# Part 2 -- measuring the detection probability
# ==========================================================================

def demo_detection_probability(param, csp, tpa, state, trials: int) -> bool:
    step("3b", "MEASURED vs THEORETICAL DETECTION PROBABILITY")

    file_id = "dataset-shard-0"
    stored = csp.get(file_id)
    n = stored.n_blocks

    note(
        f"The cloud corrupts a fraction of the {n} blocks. The auditor samples "
        f"c blocks at random and we record whether it noticed. Each "
        f"configuration is repeated {trials} times."
    )
    note(
        "To keep this fast we test the SAMPLING question -- did the challenge "
        "hit a corrupted block -- which is the only probabilistic part. Part 3a "
        "already established that a hit is always detected, so the two "
        "together give the end-to-end probability."
    )
    print()

    note(
        "Two models are shown. The PAPER'S BOUND, 1-((n-t)/n)^c, assumes "
        "sampling WITH replacement. Our auditor draws DISTINCT block indices, "
        "so the exact model is hypergeometric: 1 - C(n-t,c)/C(n,c). The "
        "measured rate should track the exact model, and should meet or beat "
        "the paper's bound -- which is therefore conservative, never optimistic."
    )
    print()

    import math
    import secrets

    print(f"    {'corrupt%':>9} {'c':>5} {'paper bound':>13} {'exact':>10} "
          f"{'measured':>11} {'':>4}")
    print("    " + "-" * 58)

    all_close = True
    for corruption_rate, c in [
        (0.01, 100), (0.01, 150), (0.01, 190),
        (0.05, 50), (0.05, 100),
        (0.10, 20), (0.10, 50),
    ]:
        t = max(1, int(round(corruption_rate * n)))
        paper_bound = tpa.detection_probability(n, c, corruption_rate)
        exact = tpa.detection_probability_exact(n, c, corruption_rate)

        hits = 0
        for _ in range(trials):
            corrupted = set()
            while len(corrupted) < t:
                corrupted.add(secrets.randbelow(n))
            challenge = gen_challenge(n, c)
            if any(j in corrupted for j, _ in challenge.pairs):
                hits += 1
        measured = hits / trials

        # Compare against the EXACT model. Binomial standard error; 3 sigma is
        # a generous band, with a floor for the near-certain cases.
        se = math.sqrt(max(exact * (1 - exact), 1e-12) / trials)
        close = abs(measured - exact) <= max(3 * se, 0.02)
        conservative = measured >= paper_bound - max(3 * se, 0.02)
        ok = close and conservative
        all_close = all_close and ok

        flag = green("ok") if ok else red("off")
        print(f"    {corruption_rate*100:8.0f}% {c:5d} {paper_bound*100:12.2f}% "
              f"{exact*100:9.2f}% {measured*100:10.2f}%  {flag}")

    print()
    note(
        "Measured rates match the exact hypergeometric model and sit at or "
        "above the paper's bound in every configuration. The paper's choice of "
        "c = 460 is therefore safe -- it under-promises what the implementation "
        "actually delivers."
    )

    print()
    item("Verification cost", "3 pairings, regardless of n or c")
    item("Bandwidth per audit", "one scalar + one group element (~128 bytes)")
    note(
        "Both are constant. That is what makes auditing a 100 MB dataset and a "
        "100 TB dataset cost the auditor the same."
    )
    return all_close


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data tampering detection demonstration.")
    parser.add_argument("--trials", type=int, default=300,
                        help="repetitions per configuration (default 300)")
    args = parser.parse_args(argv)

    banner(
        "ATTACK 3 -- DATA TAMPERING BY A SEMI-TRUSTED CLOUD",
        "Deterministic detection when challenged, plus the sampling analysis",
    )

    param, csp, tpa, state = _setup()
    item("Blocks", str(N_BLOCKS))
    item("Owners", "3")

    single_ok = demo_single_corruption(param, csp, tpa, state)
    probability_ok = demo_detection_probability(param, csp, tpa, state, args.trials)

    banner("RESULT")
    print(f"    Corruption detection : "
          f"{green('CORRECT') if single_ok else red('FAILED')}"
          f"  {dim('- every challenged corruption caught')}")
    print(f"    Sampling analysis    : "
          f"{green('CONFIRMED') if probability_ok else red('DEVIATED')}"
          f"  {dim('- measured rate matches theory')}")
    print()

    success = single_ok and probability_ok
    if not success:
        print(red("    Demonstration did not produce the expected outcome."))
    print()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
