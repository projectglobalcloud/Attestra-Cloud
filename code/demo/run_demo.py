"""
End-to-end walkthrough of the complete protocol on a real dataset.

This script IS the demonstration.  It runs every one of the six algorithms in
the order a real transaction would, on real generated data, and shows at each
step what the auditor concludes.

The story it tells:

    A consortium of hospitals jointly owns a large radiology dataset stored in
    the cloud.  An auditor confirms the cloud still holds it intact.  The cloud
    then quietly corrupts a block -- the auditor catches it.  A new hospital
    joins the consortium, and an old one leaves; auditing keeps working through
    both changes.  Finally the whole consortium SELLS the dataset to a medical
    AI institute.  After the sale the institute can audit the data and the
    hospitals cannot -- ownership really moved.

Run:
    python -m demo.run_demo
    python -m demo.run_demo --dataset financial-ledger --owners 4
    python -m demo.run_demo --blocks 50        # shorter run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEMO_CHALLENGE_SIZE  # noqa: E402
from crypto import sys_gen  # noqa: E402
from datasets.generate import SCENARIOS  # noqa: E402
from datasets.loader import (  # noqa: E402
    chunk_file,
    dataset_shards,
    list_datasets,
    load_manifest,
)
from demo.console import (  # noqa: E402
    Timer,
    banner,
    bold,
    cyan,
    dim,
    expected,
    green,
    item,
    note,
    red,
    step,
    yellow,
)
from entities import CloudStorageProvider, OwnerGroup, ThirdPartyAuditor  # noqa: E402


def run(
    dataset_name: str = "medical-imaging",
    max_blocks: int | None = 60,
    challenge_size: int = DEMO_CHALLENGE_SIZE,
    extra_owners: int = 0,
) -> bool:
    """Run the full walkthrough. Returns True if every step behaved as expected."""

    outcomes: list[bool] = []

    # ==================================================================
    banner(
        "CLOUD-BASED AUDITING WITH EFFICIENT OWNERSHIP MANAGEMENT",
        "Wu, You & Huang, IEEE TIFS vol. 20, 2025  |  Dept. of ISE, GAT",
    )

    if dataset_name not in list_datasets():
        print(red(f"\nDataset {dataset_name!r} has not been generated yet."))
        print(f"Run:  {bold('python -m datasets.generate --dataset ' + dataset_name)}")
        return False

    manifest = load_manifest(dataset_name)
    scenario = SCENARIOS[dataset_name]

    # ==================================================================
    step(1, "THE DATASET AND THE PARTIES")

    print(f"    {bold(manifest['title'])}")
    note(scenario.description)
    print()
    item("Total size", f"{manifest['total_bytes'] / 1024 / 1024:.1f} MB")
    item("Records", f"{manifest['record_count']:,}")
    item("Shards", str(manifest["shard_count"]))
    print()
    item("Sale Group (current owners)", ", ".join(scenario.sale_group))
    item("Buy Group (purchaser)", ", ".join(scenario.buy_group))

    # ==================================================================
    step(2, "SysGen  --  public system parameters")

    param = sys_gen()
    note(
        "Generates the pairing groups and two independent generators g and u. "
        "Nobody knows the discrete log of u relative to g -- we derive u by "
        "hash-to-curve from a fixed public string, so no party could know it. "
        "If anyone did, they could forge tags for arbitrary blocks."
    )
    item("Curve", "BLS12-381 (128-bit security)")
    item("Group order q", f"{param.q.bit_length()}-bit prime")

    # ==================================================================
    step(3, "KeyGen  --  every owner generates its own key pair")

    sale_ids = list(scenario.sale_group)
    sale_ids += [f"extra-owner-{i+1}" for i in range(extra_owners)]

    with Timer(f"KeyGen for {len(sale_ids)} owners"):
        sale_group = OwnerGroup.create(param, f"{dataset_name}-sale-group", sale_ids)

    note(
        "Each owner independently draws sk_i and computes pk_i = g^sk_i. There "
        "is no dealer, no shared secret and no interaction between owners. The "
        "cost is O(s). The baseline scheme this project compares against needs "
        "every owner to build an authenticator over all other owners' keys, "
        "which costs O(s^2) -- that gap is what benchmarks/ measures."
    )
    for member in sale_group.members:
        item(f"  {member.owner_id}", "sk kept private, pk published")

    # ==================================================================
    step(4, "TagGen  --  preparing and uploading the data")

    shard = next(iter(dataset_shards(dataset_name)))
    chunks = chunk_file(shard, file_id=f"{dataset_name}/{shard.name}", max_blocks=max_blocks)

    item("Shard", shard.name)
    item("Block size", f"{chunks.block_size:,} bytes")
    item("Blocks tagged", f"{chunks.n_blocks}")
    if max_blocks and chunks.n_blocks == max_blocks:
        note(
            f"Truncated to the first {max_blocks} blocks so this walkthrough "
            f"stays short. The full shard has more; use --blocks 0 for all of it."
        )
    print()

    with Timer(f"TagGen: {chunks.n_blocks} blocks x {sale_group.size} owners"):
        tags, state = sale_group.tag_file(chunks.file_abstract, chunks.blocks)

    note(
        "Every owner computed one tag share per block from its own secrets; "
        "the shares were multiplied together into a single tag per block. The "
        "tag is what proves shared ownership -- it carries a contribution from "
        "each owner, so no owner can act alone."
    )

    csp = CloudStorageProvider(param)
    csp.store(chunks.file_id, chunks.file_abstract, chunks.blocks, tags)
    tpa = ThirdPartyAuditor(param)

    item("Uploaded to CSP", f"{chunks.n_blocks} blocks + {chunks.n_blocks} tags")
    item("Published for auditing", "APK, Rs, Hs")
    note(
        "The auditor receives only those three group elements. It never sees a "
        "secret key, a random mask, or any block content."
    )

    # ==================================================================
    step(5, "Audit  --  the auditor challenges the cloud")

    c = min(challenge_size, chunks.n_blocks)
    record = tpa.audit(csp, chunks.file_id, state, c=c, note="baseline audit, honest cloud")

    note(
        "The auditor picks random block indices and random coefficients, and "
        "the cloud must answer with DP (one scalar) and TP (one group "
        "element). The response is CONSTANT SIZE no matter how many blocks "
        "were challenged, and the data itself never moves."
    )
    item("Blocks challenged", f"{record.challenged_blocks} of {record.total_blocks}")
    item("Detection probability", f">= {record.detection_probability * 100:.2f}% for 1% corruption")
    item("Verification cost", "3 pairings (independent of file size)")
    print()
    outcomes.append(expected(record.passed, True, "honest cloud verifies"))

    # ==================================================================
    step(6, "Audit under attack  --  the cloud corrupts a block")

    target = 0
    original = csp.corrupt_block(chunks.file_id, target)
    note(
        f"The cloud has silently altered block {target} -- modelling either "
        f"malice (deleting cold data to save cost, then bluffing) or accident "
        f"(bit rot). The auditor is not told, and challenges as usual."
    )

    # Challenge that block specifically, so the demo is deterministic rather
    # than relying on the random challenge happening to select it.
    from crypto.scheme import Challenge, gen_proof
    from crypto.pairing import rand_scalar

    forced = Challenge(pairs=((target, rand_scalar()),))
    proof = csp.generate_proof(chunks.file_id, forced)
    detected = not tpa.verify(state, forced, proof)
    outcomes.append(expected(not detected, False, "corrupted block detected"))

    csp.restore_block(chunks.file_id, target, original)
    record = tpa.audit(csp, chunks.file_id, state, c=c, note="after restoring the block")
    outcomes.append(expected(record.passed, True, "restored data verifies again"))

    # ==================================================================
    step(7, "AddOwner  --  a new institution joins the consortium")

    joiner = "fortis-healthcare-network"
    with Timer(f"AddOwner ({joiner})"):
        token, state, newcomers = sale_group.add_members(state, [joiner])
        csp.apply_ownership_token(chunks.file_id, token)

    note(
        "The joining owner produced a constant-size token; the cloud used it to "
        "rewrite every tag in place. The cost to the owners is O(1) in "
        "communication and depends only on how many owners are JOINING -- not "
        "on how many were already in the group. Existing owners did not re-key "
        "and did not even need to be online."
    )
    item("Group size now", f"{sale_group.size} owners")

    record = tpa.audit(csp, chunks.file_id, state, c=c, note="after AddOwner")
    outcomes.append(expected(record.passed, True, "auditing survives a new owner joining"))

    # ==================================================================
    step(8, "RevokeOwner  --  an institution leaves")

    leaver = sale_group.members[0].owner_id
    with Timer(f"RevokeOwner ({leaver})"):
        token, state = sale_group.revoke_members(state, [leaver])
        csp.apply_ownership_token(chunks.file_id, token)

    note(
        "The departing owner's key material no longer contributes to any tag, "
        "so it has genuinely lost ownership. But note what is deliberately NOT "
        "removed: its random mask a_i stays embedded in every tag forever, and "
        "Rs is left untouched. That residue is what makes a leaked index "
        "secret useless to an attacker -- see attacks/collusion.py."
    )
    item("Group size now", f"{sale_group.size} owners")

    record = tpa.audit(csp, chunks.file_id, state, c=c, note="after RevokeOwner")
    outcomes.append(expected(record.passed, True, "auditing survives an owner leaving"))

    # ==================================================================
    step(9, "OwnerTransfer  --  the dataset is SOLD to the buy group")

    buy_group = OwnerGroup.create(
        param, f"{dataset_name}-buy-group", list(scenario.buy_group)
    )
    print(f"    {bold('Seller')}: {sale_group}")
    print(f"    {bold('Buyer')} : {buy_group}")
    print()

    stale_state = state  # keep the pre-sale parameters to test them afterwards

    with Timer("OwnerTransfer (SG -> BG -> CSP)"):
        sg_token, intermediate = sale_group.initiate_transfer(state)
        combined, state = buy_group.accept_transfer(intermediate, sg_token)
        csp.apply_ownership_token(chunks.file_id, combined)

    note(
        "Step 1: the sale group cancelled its own contribution and produced "
        "ODT_SG. Step 2: the buy group added its own exponents and fresh masks, "
        "folded ODT_SG into its own token, and sent ONE combined token to the "
        "cloud. The cloud rewrote each tag once for the whole sale."
    )
    note(
        "Critically, the buy group never received any seller secret key -- only "
        "an aggregated, blinded token. Earlier schemes handed over the index "
        "secret key in the clear here, which is exactly the flaw the base paper "
        "set out to fix."
    )

    record = tpa.audit(csp, chunks.file_id, state, c=c, note="buyer audits after purchase")
    outcomes.append(expected(record.passed, True, "BUYER can now audit the dataset"))

    record = tpa.audit(csp, chunks.file_id, stale_state, c=c, note="seller's old parameters")
    outcomes.append(expected(record.passed, False, "SELLER's old parameters no longer verify"))

    note(
        "That second result is the whole point of ownership transfer. The "
        "seller's verification parameters are now worthless against the "
        "re-tagged data: ownership did not just change in a database row, it "
        "changed cryptographically."
    )

    # ==================================================================
    step(10, "AUDIT REPORT")

    print(tpa.report())

    print()
    print("    " + bold("CSP statistics"))
    for key, value in csp.stats().items():
        item(f"  {key}", str(value))

    # ==================================================================
    banner("RESULT")
    all_ok = all(outcomes)
    passed_n = sum(outcomes)
    if all_ok:
        print(green(f"  All {len(outcomes)} protocol checks behaved exactly as expected."))
        print(dim("  Six algorithms exercised: SysGen, KeyGen, TagGen, Audit,"))
        print(dim("  OwnerModify (Add + Revoke), OwnerTransfer."))
    else:
        print(red(f"  {passed_n}/{len(outcomes)} checks behaved as expected."))
    print()
    return all_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dataset", default="medical-imaging", choices=sorted(SCENARIOS))
    parser.add_argument(
        "--blocks", type=int, default=60,
        help="max blocks to tag (0 = the whole shard). Default 60, for a short run.",
    )
    parser.add_argument("--challenge", type=int, default=DEMO_CHALLENGE_SIZE)
    parser.add_argument(
        "--owners", type=int, default=0,
        help="extra synthetic owners to add to the sale group",
    )
    args = parser.parse_args(argv)

    ok = run(
        dataset_name=args.dataset,
        max_blocks=args.blocks if args.blocks > 0 else None,
        challenge_size=args.challenge,
        extra_owners=args.owners,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
