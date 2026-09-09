"""
ATTACK 1 -- COLLUSION BETWEEN THE BUYER AND THE CLOUD.

This is THE attack the base paper exists to fix. Everything else in the scheme
is optimisation; this is the security contribution.

THE SITUATION
-------------
A sale group SG owns a dataset stored at a cloud provider. SG sells PART of it
to a buy group BG. In schemes [7] and [8], completing that sale requires SG to
hand its INDEX SECRET KEY sk1 to BG in the clear.

Now BG and the cloud collude. Between them they hold:

    - sk1                (leaked by the sale, as the protocol demands)
    - every stored tag   (the cloud has them)
    - every stored block (the cloud has them)

The paper's section III.D shows this is enough to forge integrity proofs for
data SG never sold and still owns.

WHY THE FORGERY WORKS
---------------------
The prior-art tag splits cleanly into an index part and a data part:

    sigma_j = H0(j)^{sk1} * (u^{m_j})^{sk2}
              \_________/   \___________/
               position       contents

The position factor depends ONLY on j and sk1. Holding sk1, an attacker can
peel it off one tag and staple on another:

    sigma' = sigma_{j'} * H0(j')^{-sk1} * H0(j)^{sk1}
           = H0(j)^{sk1} * (u^{m_{j'}})^{sk2}

That is a valid tag binding position j to the contents of block j'. So a cloud
that has DELETED block j can answer a challenge for j using any block it still
holds. The audit passes. The owner is told their data is safe when it is gone.

THE FIX
-------
The proposed scheme masks the index exponent with a per-owner random value a_i
that is NEVER transmitted -- not during transfer, not during revocation:

    sigma_j = H3(F||j)^{H2(sk_i||a_i) + a_i} * (u^{m_j})^{sk_i*H1(pk_i)}

Only the aggregate Rs = prod g^{a_i} is published, and a discrete log cannot be
extracted from it. So even an attacker who learns every H2(sk_i||a_i) value
cannot perform the graft: the a_i residue stays welded to the original index.

This script demonstrates both halves for real -- the forgery succeeding against
the prior art, and the same forgery failing against the proposed scheme.

Run:
    python -m attacks.collusion
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crypto.baseline import PriorArtTagScheme  # noqa: E402
from crypto.hashes import H3, hash_block  # noqa: E402
from crypto.pairing import (  # noqa: E402
    g1_add,
    g1_mul,
    rand_scalar,
    scalar_mod,
)
from crypto.scheme import (  # noqa: E402
    Challenge,
    gen_proof,
    key_gen,
    sys_gen,
    tag_gen,
    verify_proof,
)
from demo.console import banner, bold, dim, expected, green, item, note, red, step  # noqa: E402

FILE_ABSTRACT = b"sale-group-confidential-dataset-v1"
LOST_INDEX = 3       # the block the cloud deletes
SUBSTITUTE_INDEX = 7  # the block it substitutes instead


def _blocks(n: int = 12) -> list[int]:
    return [hash_block(f"sensitive-record-{i}".encode()) for i in range(n)]


# ==========================================================================
# Half 1 -- the attack against the PRIOR ART
# ==========================================================================

def attack_prior_art() -> bool:
    """Returns True if the forgery SUCCEEDS (i.e. the prior art is broken)."""
    step("1a", "PRIOR ART (schemes [7], [8]) -- sigma = H0(j)^sk1 * (u^m)^sk2")

    param = sys_gen()
    scheme = PriorArtTagScheme(param)
    key = scheme.key_gen()
    blocks = _blocks()
    tags = scheme.tag_all(key, blocks)

    note(
        "The sale group has tagged and uploaded its dataset. An honest audit "
        "of the block we are about to attack passes, as it should."
    )
    coefficient = rand_scalar()
    honest_challenge = [(LOST_INDEX, coefficient)]
    dp, tp = scheme.gen_proof(honest_challenge, blocks, tags)
    honest_ok = scheme.verify(key, honest_challenge, dp, tp)
    expected(honest_ok, True, f"honest audit of block {LOST_INDEX}")

    note(
        f"Now the sale happens. Under these schemes the index secret sk1 is "
        f"transmitted to the buyer in the clear -- that is what the protocol "
        f"requires, not a mistake by the implementer. The buyer then colludes "
        f"with the cloud, which has quietly deleted block {LOST_INDEX}."
    )
    item("Leaked to the colluders", "sk1 (the index secret key)")
    item(f"Block {LOST_INDEX}", red("deleted by the cloud"))
    item(f"Block {SUBSTITUTE_INDEX}", "still held, will be substituted")

    print()
    note(
        f"The colluders graft the index component of block {LOST_INDEX} onto "
        f"the tag of block {SUBSTITUTE_INDEX}. Two exponentiations and one "
        f"multiplication -- that is the entire attack."
    )

    forged_tag = scheme.forge_by_index_swap(
        leaked_sk1=key.sk1,
        source_tag=tags[SUBSTITUTE_INDEX],
        source_index=SUBSTITUTE_INDEX,
        target_index=LOST_INDEX,
    )

    # The cloud answers a challenge for the DELETED block using the SURVIVING
    # block's contents together with the forged tag.
    forged_dp = (blocks[SUBSTITUTE_INDEX] * coefficient) % param.q
    forged_tp = g1_mul(forged_tag, coefficient)

    forgery_accepted = scheme.verify(key, honest_challenge, forged_dp, forged_tp)

    print()
    accepted_as_expected = expected(
        forgery_accepted, True,
        f"forged proof for DELETED block {LOST_INDEX} is accepted",
    )
    if forgery_accepted:
        print()
        print(red("    " + bold("The prior-art scheme is broken.")))
        note(
            f"The auditor has just certified that block {LOST_INDEX} is intact. "
            f"It does not exist. The owner has no way to tell.",
        )
    return forgery_accepted and accepted_as_expected


# ==========================================================================
# Half 2 -- the same attack against the PROPOSED SCHEME
# ==========================================================================

def attack_proposed_scheme() -> bool:
    """Returns True if the forgery FAILS (i.e. the proposed scheme resists)."""
    step("1b", "PROPOSED SCHEME -- sigma = H3(F||j)^{H2(sk||a)+a} * (u^m)^{sk*H1(pk)}")

    param = sys_gen()
    owners = [key_gen(param, f"sale-owner-{i}") for i in range(3)]
    blocks = _blocks()
    tags, state = tag_gen(param, owners, FILE_ABSTRACT, blocks)

    coefficient = rand_scalar()
    honest_challenge = Challenge(pairs=((LOST_INDEX, coefficient),))
    honest_proof = gen_proof(honest_challenge, blocks, tags)
    honest_ok = verify_proof(param, state, honest_challenge, honest_proof)
    expected(honest_ok, True, f"honest audit of block {LOST_INDEX}")

    note(
        "Now we grant the attacker MORE than the protocol would ever leak. We "
        "hand it every owner's H2(sk_i||a_i) value -- the exact analogue of the "
        "index secret that the prior art transmits in the clear. This is a "
        "deliberately generous assumption: the real protocol never reveals "
        "these at all."
    )
    leaked_index_secret = scalar_mod(sum(o.h2 for o in owners))
    item("Leaked to the colluders", "sum of every H2(sk_i||a_i)")
    item("NOT leaked (and never is)", green("the random masks a_i"))

    print()
    note(
        "The attacker attempts the identical graft: strip the index component "
        f"off block {SUBSTITUTE_INDEX}'s tag and staple it onto index "
        f"{LOST_INDEX}."
    )

    strip_old = g1_mul(H3(FILE_ABSTRACT, SUBSTITUTE_INDEX), scalar_mod(-leaked_index_secret))
    graft_new = g1_mul(H3(FILE_ABSTRACT, LOST_INDEX), leaked_index_secret)
    forged_tag = g1_add(tags[SUBSTITUTE_INDEX], g1_add(strip_old, graft_new))

    forged_dp = (blocks[SUBSTITUTE_INDEX] * coefficient) % param.q
    forged_tp = g1_mul(forged_tag, coefficient)
    forged_proof = type(honest_proof)(dp=forged_dp, tp=forged_tp)

    forgery_accepted = verify_proof(param, state, honest_challenge, forged_proof)

    print()
    rejected_as_expected = expected(
        forgery_accepted, False,
        f"forged proof for block {LOST_INDEX} is REJECTED",
    )

    if not forgery_accepted:
        print()
        print(green("    " + bold("The proposed scheme resists the attack.")))
        note(
            "The graft removed only the H2(sk_i||a_i) part of the exponent. The "
            f"masks a_i are still bound to index {SUBSTITUTE_INDEX}, and index "
            f"{LOST_INDEX} is missing them entirely. The attacker cannot correct "
            "this: recovering a_i from the published Rs = prod g^{a_i} would "
            "mean solving a discrete logarithm."
        )

    # Sanity check: the scheme must still accept HONEST proofs after all this.
    recheck = verify_proof(param, state, honest_challenge, honest_proof)
    expected(recheck, True, "honest proofs still verify (no collateral damage)")

    return (not forgery_accepted) and rejected_as_expected and recheck


def main() -> int:
    banner(
        "ATTACK 1 -- COLLUSION (buyer + cloud, using a leaked index secret)",
        "Base paper section III.D  |  the vulnerability the scheme was designed to fix",
    )

    broke_prior_art = attack_prior_art()
    resisted = attack_proposed_scheme()

    banner("RESULT")
    print(f"    Prior art [7],[8] : {red('BROKEN') if broke_prior_art else 'not broken'}"
          f"  {dim('- forged proof accepted for deleted data')}")
    print(f"    Proposed scheme   : {green('RESISTED') if resisted else red('FAILED')}"
          f"  {dim('- forged proof rejected')}")
    print()

    success = broke_prior_art and resisted
    if success:
        print(green("    Demonstration complete: the random mask is what makes the"))
        print(green("    difference, and it is not optional."))
    else:
        print(red("    Demonstration did not produce the expected outcome."))
    print()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
