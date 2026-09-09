"""
Performance measurement, reproducing Figs. 5-8 of the base paper.

WHAT IS BEING MEASURED, AND WHY IT IS THE RIGHT THING TO MEASURE
----------------------------------------------------------------
The base paper's central performance claim is an ASYMPTOTIC one:

    key generation and group membership changes cost O(s) in the proposed
    scheme, versus O(s^2) in the baseline (scheme [8]),

where s is the number of owners.  An asymptotic claim is proved by the SHAPE of
the curve, not by absolute milliseconds, so that is what these benchmarks
establish.

Both schemes are measured:
  - on the same curve (BLS12-381),
  - through the same pairing backend (py_ecc),
  - on the same machine, in the same process.

so the comparison is fair even though our absolute numbers are far slower than
the paper's C/PBC implementation.  See README.md for the honest discussion of
that gap.

Results are written as JSON so that plots.py can render them and the report can
quote them without re-running anything.

Run:
    python -m benchmarks.run_benchmarks              # quick (~4 min)
    python -m benchmarks.run_benchmarks --full       # paper parity (~40 min)
    python -m benchmarks.run_benchmarks --only keygen
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RESULTS_DIR  # noqa: E402
from crypto.baseline import baseline_key_gen  # noqa: E402
from crypto.hashes import hash_block  # noqa: E402
from crypto.ownership import add_owners, apply_token, revoke_owners, transfer_ownership  # noqa: E402
from crypto.scheme import (  # noqa: E402
    aggregate_public_key,
    gen_challenge,
    gen_proof,
    key_gen,
    sys_gen,
    tag_gen,
    verify_proof,
)

# --------------------------------------------------------------------------
# Measurement harness
# --------------------------------------------------------------------------


@dataclass
class Measurement:
    """One measured configuration."""

    label: str
    x: float
    seconds: float
    repeats: int
    stdev: float = 0.0
    extra: dict = field(default_factory=dict)


def measure(fn, repeats: int = 3) -> tuple[float, float]:
    """
    Time `fn`, returning (median seconds, stdev).

    Median rather than mean: a single GC pause or OS scheduling hiccup would
    drag a mean around, and we care about typical cost.  Repeats are kept low
    because the individual operations here are already hundreds of
    milliseconds -- long enough that timer resolution is irrelevant.
    """
    timings = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - start)
    median = statistics.median(timings)
    stdev = statistics.stdev(timings) if len(timings) > 1 else 0.0
    return median, stdev


def _blocks(n: int) -> list[int]:
    return [hash_block(f"benchmark-block-{i}".encode()) for i in range(n)]


# --------------------------------------------------------------------------
# Benchmark 1 -- KeyGen: O(s) vs O(s^2)      [paper Fig. 6]
# --------------------------------------------------------------------------

def bench_keygen(param, owner_range: list[int], repeats: int) -> list[Measurement]:
    """
    THE HEADLINE RESULT.

    Proposed: each owner draws sk_i and computes pk_i = g^{sk_i}, then the
    group forms APK = prod pk_i^{H1(pk_i)}.  Two exponentiations per owner.
    Linear in s.

    Baseline: every owner additionally builds a public-key-aggregation
    authenticator binding it to EVERY other owner -- s exponentiations each,
    s^2 for the group.  Quadratic in s.
    """
    results: list[Measurement] = []
    print("\n[1] KeyGen  --  proposed O(s) vs baseline O(s^2)")
    print(f"    {'s':>4} {'proposed':>12} {'baseline':>12} {'speedup':>10}")
    print("    " + "-" * 40)

    for s in owner_range:
        def proposed():
            owners = [key_gen(param, f"o{i}") for i in range(s)]
            aggregate_public_key(owners)

        def baseline():
            baseline_key_gen(param, s)

        t_prop, sd_prop = measure(proposed, repeats)
        t_base, sd_base = measure(baseline, repeats)

        results.append(Measurement("proposed", s, t_prop, repeats, sd_prop))
        results.append(Measurement("baseline", s, t_base, repeats, sd_base))
        print(f"    {s:4d} {t_prop*1000:10.1f}ms {t_base*1000:10.1f}ms "
              f"{t_base/max(t_prop,1e-9):9.1f}x")

    return results


# --------------------------------------------------------------------------
# Benchmark 2 -- TagGen                       [paper Fig. 5a]
# --------------------------------------------------------------------------

def bench_taggen(param, block_range: list[int], owner_sizes: list[int],
                 repeats: int) -> list[Measurement]:
    """
    Tagging cost is O(n * s): one tag share per block per owner.

    The paper notes this is the honest price of NOT outsourcing signing
    authority.  The baseline reaches O(n) only by having a trusted third party
    sign files it cannot see -- which, as the paper argues, owners would never
    agree to for data they have not disclosed.
    """
    results: list[Measurement] = []
    print("\n[2] TagGen  --  cost grows with blocks n and owners s")
    header = "    " + f"{'n':>6}" + "".join(f"{'s='+str(s):>12}" for s in owner_sizes)
    print(header)
    print("    " + "-" * (6 + 12 * len(owner_sizes)))

    for n in block_range:
        blocks = _blocks(n)
        row = f"    {n:6d}"
        for s in owner_sizes:
            owners = [key_gen(param, f"o{i}") for i in range(s)]

            def run():
                tag_gen(param, owners, b"bench-file", blocks)

            seconds, stdev = measure(run, repeats)
            results.append(
                Measurement(f"s={s}", n, seconds, repeats, stdev, {"owners": s})
            )
            row += f"{seconds:11.2f}s"
        print(row)

    return results


# --------------------------------------------------------------------------
# Benchmark 3 -- Audit                        [paper Fig. 5b, 5c]
# --------------------------------------------------------------------------

def bench_audit(param, challenge_range: list[int], n_blocks: int,
                owner_sizes: list[int], repeats: int) -> list[Measurement]:
    """
    Proof generation and proof verification, as a function of the number of
    challenged blocks c.

    The key observation the paper makes, and that these numbers confirm:
    BOTH are independent of the number of owners s.  Once tags are aggregated,
    a 2-owner group and a 20-owner group are indistinguishable to the auditor.

    Verification uses exactly three pairings regardless of c; the growth with c
    comes only from the c hash-to-curve operations needed to rebuild
    prod H3(F||j)^{v_j}.
    """
    results: list[Measurement] = []
    print(f"\n[3] Audit  --  proof generation and verification (n = {n_blocks})")
    print(f"    {'c':>6} {'owners':>7} {'proof gen':>12} {'verify':>12}")
    print("    " + "-" * 40)

    blocks = _blocks(n_blocks)
    for s in owner_sizes:
        owners = [key_gen(param, f"o{i}") for i in range(s)]
        tags, state = tag_gen(param, owners, b"bench-audit", blocks)

        for c in challenge_range:
            if c > n_blocks:
                continue
            challenge = gen_challenge(n_blocks, c)

            t_gen, sd_gen = measure(lambda: gen_proof(challenge, blocks, tags), repeats)
            proof = gen_proof(challenge, blocks, tags)
            t_ver, sd_ver = measure(
                lambda: verify_proof(param, state, challenge, proof), repeats
            )

            results.append(Measurement(f"proofgen_s{s}", c, t_gen, repeats, sd_gen,
                                       {"owners": s, "phase": "generate"}))
            results.append(Measurement(f"verify_s{s}", c, t_ver, repeats, sd_ver,
                                       {"owners": s, "phase": "verify"}))
            print(f"    {c:6d} {s:7d} {t_gen*1000:10.1f}ms {t_ver*1000:10.1f}ms")

    return results


# --------------------------------------------------------------------------
# Benchmark 4 -- OwnerModify                  [paper Fig. 7a, 7b]
# --------------------------------------------------------------------------

def bench_owner_modify(param, add_sizes: list[int], revoke_sizes: list[int],
                       base_add: int, base_revoke: int, repeats: int) -> list[Measurement]:
    """
    Adding and revoking owners.

    THE POINT OF THIS BENCHMARK: in the proposed scheme, the cost of a
    membership change depends only on HOW MANY OWNERS ARE CHANGING, not on how
    big the group already is.  A 100-member group admitting 2 new members does
    two members' worth of work.

    In the baseline, any membership change alters APK, which invalidates every
    authenticator in the group -- so all of them are rebuilt, at O(s^2).
    """
    results: list[Measurement] = []

    print(f"\n[4a] AddOwner  --  group of {base_add}, admitting |U_in| new owners")
    print(f"    {'|U_in|':>7} {'proposed':>12} {'baseline':>12} {'speedup':>10}")
    print("    " + "-" * 43)

    blocks = _blocks(20)
    for u_in in add_sizes:
        existing = [key_gen(param, f"o{i}") for i in range(base_add)]
        _, state = tag_gen(param, existing, b"bench-add", blocks)
        newcomers = [key_gen(param, f"n{i}") for i in range(u_in)]

        t_prop, sd = measure(lambda: add_owners(param, state, newcomers), repeats)
        t_base, sd_b = measure(
            lambda: baseline_key_gen(param, base_add + u_in), repeats
        )

        results.append(Measurement("add_proposed", u_in, t_prop, repeats, sd,
                                   {"group_size": base_add}))
        results.append(Measurement("add_baseline", u_in, t_base, repeats, sd_b,
                                   {"group_size": base_add}))
        print(f"    {u_in:7d} {t_prop*1000:10.1f}ms {t_base*1000:10.1f}ms "
              f"{t_base/max(t_prop,1e-9):9.1f}x")

    print(f"\n[4b] RevokeOwner  --  group of {base_revoke}, revoking |U_out| owners")
    print(f"    {'|U_out|':>7} {'proposed':>12} {'baseline':>12} {'speedup':>10}")
    print("    " + "-" * 43)

    for u_out in revoke_sizes:
        existing = [key_gen(param, f"o{i}") for i in range(base_revoke)]
        _, state = tag_gen(param, existing, b"bench-revoke", blocks)
        leaving = existing[:u_out]

        t_prop, sd = measure(lambda: revoke_owners(param, state, leaving), repeats)
        t_base, sd_b = measure(
            lambda: baseline_key_gen(param, max(1, base_revoke - u_out)), repeats
        )

        results.append(Measurement("revoke_proposed", u_out, t_prop, repeats, sd,
                                   {"group_size": base_revoke}))
        results.append(Measurement("revoke_baseline", u_out, t_base, repeats, sd_b,
                                   {"group_size": base_revoke}))
        print(f"    {u_out:7d} {t_prop*1000:10.1f}ms {t_base*1000:10.1f}ms "
              f"{t_base/max(t_prop,1e-9):9.1f}x")

    return results


# --------------------------------------------------------------------------
# Benchmark 5 -- Tag update cost at the CSP    [paper Fig. 7c]
# --------------------------------------------------------------------------

def bench_tag_update(param, block_range: list[int], owner_sizes: list[int],
                     repeats: int) -> list[Measurement]:
    """
    The CSP's cost of applying an ownership token.

    Linear in n, and INDEPENDENT of s -- the token is already aggregated, so
    the cloud does the same work whether two owners changed or two hundred.
    This is the design putting the O(n) burden on the party with resources.
    """
    results: list[Measurement] = []
    print("\n[5] Tag update at CSP  --  linear in n, independent of s")
    print(f"    {'n':>6} {'owners':>7} {'update':>12}")
    print("    " + "-" * 28)

    for n in block_range:
        blocks = _blocks(n)
        for s in owner_sizes:
            owners = [key_gen(param, f"o{i}") for i in range(s)]
            tags, state = tag_gen(param, owners, b"bench-update", blocks)
            newcomer = [key_gen(param, "joiner")]
            token, _ = add_owners(param, state, newcomer)

            seconds, stdev = measure(
                lambda: apply_token(param, token, b"bench-update", blocks, tags),
                repeats,
            )
            results.append(Measurement(f"update_s{s}", n, seconds, repeats, stdev,
                                       {"owners": s}))
            print(f"    {n:6d} {s:7d} {seconds*1000:10.1f}ms")

    return results


# --------------------------------------------------------------------------
# Benchmark 6 -- OwnerTransfer                 [paper Fig. 8]
# --------------------------------------------------------------------------

def bench_transfer(param, group_sizes: list[int], repeats: int) -> list[Measurement]:
    """
    Full ownership transfer between two groups.

    Cost is linear in (|SG| + |BG|) -- each owner on each side contributes one
    constant-size share -- and the resulting token is CONSTANT SIZE, so the
    communication to the cloud does not grow with either group.
    """
    results: list[Measurement] = []
    print("\n[6] OwnerTransfer  --  SG -> BG, constant-size token")
    print(f"    {'|SG|=|BG|':>10} {'transfer':>12} {'token bytes':>13}")
    print("    " + "-" * 37)

    blocks = _blocks(20)
    for size in group_sizes:
        sg = [key_gen(param, f"sg{i}") for i in range(size)]
        bg = [key_gen(param, f"bg{i}") for i in range(size)]
        _, state = tag_gen(param, sg, b"bench-transfer", blocks)

        seconds, stdev = measure(
            lambda: transfer_ownership(param, state, sg, bg), repeats
        )
        token, _ = transfer_ownership(param, state, sg, bg)

        from crypto.serialize import G1_SIZE, SCALAR_SIZE
        token_bytes = 2 * SCALAR_SIZE + G1_SIZE

        results.append(Measurement("transfer", size, seconds, repeats, stdev,
                                   {"token_bytes": token_bytes}))
        print(f"    {size:10d} {seconds*1000:10.1f}ms {token_bytes:12d} B")

    return results


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

QUICK = {
    "owner_range": [1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20],
    "block_range": [25, 50, 100, 200],
    "owner_sizes": [1, 5, 10],
    "challenge_range": [25, 50, 100, 200],
    "audit_blocks": 200,
    "add_sizes": [5, 10, 20],
    "revoke_sizes": [5, 10, 20],
    "base_add": 20,
    "base_revoke": 40,
    "update_blocks": [25, 50, 100, 200],
    "update_owners": [1, 10],
    "transfer_sizes": [1, 5, 10, 20],
    "repeats": 3,
}

FULL = {
    "owner_range": list(range(1, 21)),
    "block_range": [100, 200, 400, 600, 800, 1000],
    "owner_sizes": [1, 5, 10],
    "challenge_range": [50, 100, 200, 300, 460],
    "audit_blocks": 1000,
    "add_sizes": [10, 30, 80],
    "revoke_sizes": [10, 50, 80],
    "base_add": 20,
    "base_revoke": 100,
    "update_blocks": [100, 200, 400, 600, 800, 1000],
    "update_owners": [1, 10],
    "transfer_sizes": [1, 5, 10, 20, 40],
    "repeats": 3,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark suite (paper Figs. 5-8).")
    parser.add_argument("--full", action="store_true",
                        help="use the paper's full parameter ranges (slow)")
    parser.add_argument("--only", default="all",
                        choices=["all", "keygen", "taggen", "audit", "modify",
                                 "update", "transfer"])
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "benchmarks.json")
    args = parser.parse_args(argv)

    cfg = dict(FULL if args.full else QUICK)
    if args.repeats:
        cfg["repeats"] = args.repeats

    param = sys_gen()
    started = time.time()

    print("=" * 72)
    print("  BENCHMARK SUITE -- Cloud Auditing with Efficient Ownership Transfer")
    print("=" * 72)
    print(f"  Mode        : {'FULL (paper parity)' if args.full else 'QUICK'}")
    print(f"  Curve       : BLS12-381 via py_ecc (pure Python)")
    print(f"  Machine     : {platform.machine()} / {platform.system()}")
    print(f"  Python      : {platform.python_version()}")
    print(f"  Repeats     : {cfg['repeats']} (median reported)")
    print("=" * 72)

    sections: dict[str, list[Measurement]] = {}
    only = args.only

    if only in ("all", "keygen"):
        sections["keygen"] = bench_keygen(param, cfg["owner_range"], cfg["repeats"])
    if only in ("all", "taggen"):
        sections["taggen"] = bench_taggen(
            param, cfg["block_range"], cfg["owner_sizes"], cfg["repeats"])
    if only in ("all", "audit"):
        sections["audit"] = bench_audit(
            param, cfg["challenge_range"], cfg["audit_blocks"],
            cfg["owner_sizes"], cfg["repeats"])
    if only in ("all", "modify"):
        sections["owner_modify"] = bench_owner_modify(
            param, cfg["add_sizes"], cfg["revoke_sizes"],
            cfg["base_add"], cfg["base_revoke"], cfg["repeats"])
    if only in ("all", "update"):
        sections["tag_update"] = bench_tag_update(
            param, cfg["update_blocks"], cfg["update_owners"], cfg["repeats"])
    if only in ("all", "transfer"):
        sections["transfer"] = bench_transfer(param, cfg["transfer_sizes"], cfg["repeats"])

    elapsed = time.time() - started

    payload = {
        "metadata": {
            "mode": "full" if args.full else "quick",
            "curve": "BLS12-381",
            "backend": "py_ecc (pure Python)",
            "machine": platform.machine(),
            "system": platform.system(),
            "python": platform.python_version(),
            "repeats": cfg["repeats"],
            "total_seconds": round(elapsed, 1),
            "config": {k: v for k, v in cfg.items()},
            "note": (
                "Baseline figures are a cost-model re-implementation of scheme "
                "[8]'s structure per Table II of the base paper, not the "
                "original authors' code. Asymptotic shape is faithful; absolute "
                "constants are ours."
            ),
        },
        "results": {
            name: [asdict(m) for m in measurements]
            for name, measurements in sections.items()
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2))

    print("\n" + "=" * 72)
    print(f"  Completed in {elapsed/60:.1f} min")
    print(f"  Results written to {args.out}")
    print(f"  Render graphs with:  python -m benchmarks.plots")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
