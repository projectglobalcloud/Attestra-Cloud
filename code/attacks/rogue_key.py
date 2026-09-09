"""
ATTACK 2 -- ROGUE-KEY ATTACK ON THE AGGREGATED PUBLIC KEY.

THE SITUATION
-------------
BLS signatures aggregate beautifully: s owners each sign the same message, the
signatures multiply together, and one pairing check verifies all of them
against the product of their public keys:

    APK_plain = prod_i pk_i          verify:  e(sigma, g) == e(H(M), APK_plain)

That naive aggregation has a well-known flaw. An attacker who gets to choose
its public key AFTER seeing an honest party's key can pick

    pk_attacker = g^{alpha} * pk_honest^{-1}

so that the aggregate collapses to something it fully controls:

    APK_plain = pk_honest * pk_attacker = g^{alpha}

The attacker signs alone with alpha, and the verification equation accepts --
"proving" that the honest party co-signed a message it has never seen.

WHY THIS MATTERS HERE
---------------------
In this system the aggregated public key is what establishes JOINT OWNERSHIP of
a dataset. A successful rogue-key attack means an attacker can claim to co-own
data belonging to a group that never admitted it, and can produce audit proofs
in that group's name.

THE STANDARD FIX, AND ITS COST
------------------------------
Scheme [8] defends with a public-key-aggregation authenticator: each owner
proves knowledge of its secret key against the whole group. It works, but every
owner must do O(s) work, so the group does O(s^2) -- and it must all be redone
whenever membership changes.

THE PROPOSED FIX
----------------
Bind every public key with a hash OF ITSELF:

    APK = prod_i pk_i^{H1(pk_i)}

Now the attacker's chosen key appears in the exponent through H1(pk_attacker),
which it cannot control -- H1 is a random oracle, and its input is fixed the
moment the key is published. The honest party's secret no longer cancels out.

Cost: ONE exponentiation per owner. O(s), not O(s^2), and nothing to redo when
membership changes.

Run:
    python -m attacks.rogue_key
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from py_ecc.bls.hash_to_curve import hash_to_G1  # noqa: E402

from crypto.hashes import H1  # noqa: E402
from crypto.pairing import (  # noqa: E402
    g1_mul,
    g2_add,
    g2_mul,
    g2_neg,
    gt_eq,
    pair_product,
    rand_scalar,
    scalar_mod,
)
from crypto.scheme import sys_gen  # noqa: E402
from demo.console import banner, bold, dim, expected, green, item, note, red, step  # noqa: E402

MESSAGE = b"transfer-ownership-of-dataset-to-attacker"
_DST = b"ROGUEKEY_DEMO_XMD:SHA-256_SSWU_RO_"


def _hash_msg(msg: bytes):
    return hash_to_G1(msg, _DST, hashlib.sha256)


def _verify(signature, message_point, apk, param) -> bool:
    """e(sigma, g) == e(H(M), APK), batched into one final exponentiation."""
    result = pair_product([
        (signature, g2_neg(param.g)),
        (message_point, apk),
    ])
    return gt_eq(result, result.__class__.one())


# ==========================================================================
# Half 1 -- plain aggregate BLS, no binding
# ==========================================================================

def attack_plain_aggregation() -> bool:
    """Returns True if the rogue-key forgery SUCCEEDS."""
    step("2a", "PLAIN AGGREGATE BLS -- APK = prod pk_i")

    param = sys_gen()

    # An honest owner publishes its key first.
    sk_honest = rand_scalar()
    pk_honest = g2_mul(param.g, sk_honest)
    note(
        "An honest data owner publishes pk_honest. It will never see the "
        "message below, and will never sign anything."
    )

    # The attacker chooses its key AFTER seeing pk_honest.
    alpha = rand_scalar()
    pk_rogue = g2_add(g2_mul(param.g, alpha), g2_neg(pk_honest))
    note(
        "The attacker now publishes pk_rogue = g^alpha * pk_honest^{-1}. This "
        "is a perfectly well-formed public key -- nothing about it looks wrong."
    )
    item("Honest key", "pk_honest = g^sk")
    item("Rogue key", "pk_rogue = g^alpha * pk_honest^{-1}")

    apk_plain = g2_add(pk_honest, pk_rogue)
    note(
        "The aggregate collapses: pk_honest * pk_rogue = g^alpha. The honest "
        "party's secret has cancelled out completely, and the attacker knows "
        "alpha."
    )

    message_point = _hash_msg(MESSAGE)
    forged_signature = g1_mul(message_point, alpha)

    accepted = _verify(forged_signature, message_point, apk_plain, param)
    print()
    ok = expected(accepted, True, "forged 'joint' signature is accepted")
    if accepted:
        print()
        print(red("    " + bold("Plain aggregation is broken.")))
        note(
            f"The attacker signed {MESSAGE.decode()!r} entirely on its own, yet "
            "the verifier concludes that the honest owner co-signed it. In this "
            "system that means claiming co-ownership of somebody else's dataset."
        )
    return accepted and ok


# ==========================================================================
# Half 2 -- the proposed binding APK = prod pk_i^{H1(pk_i)}
# ==========================================================================

def attack_bound_aggregation() -> bool:
    """Returns True if the rogue-key forgery FAILS."""
    step("2b", "PROPOSED BINDING -- APK = prod pk_i^{H1(pk_i)}")

    param = sys_gen()

    sk_honest = rand_scalar()
    pk_honest = g2_mul(param.g, sk_honest)

    alpha = rand_scalar()
    pk_rogue = g2_add(g2_mul(param.g, alpha), g2_neg(pk_honest))

    note(
        "Exactly the same setup and exactly the same rogue key. The only "
        "change is how the aggregate is formed."
    )

    h1_honest = H1(pk_honest)
    h1_rogue = H1(pk_rogue)
    apk_bound = g2_add(g2_mul(pk_honest, h1_honest), g2_mul(pk_rogue, h1_rogue))

    item("H1(pk_honest)", f"{h1_honest % 10**12:012d}...  {dim('(attacker cannot choose)')}")
    item("H1(pk_rogue)", f"{h1_rogue % 10**12:012d}...  {dim('(fixed once published)')}")

    note(
        "A valid signature would now need the exponent "
        "sk*H1(pk_honest) + sk_rogue*H1(pk_rogue). Substituting the attacker's "
        "own construction sk_rogue = alpha - sk, this equals "
        "sk*(H1(pk_honest) - H1(pk_rogue)) + alpha*H1(pk_rogue). Because the "
        "two hash values differ, the honest secret sk no longer cancels -- and "
        "the attacker does not know sk."
    )

    message_point = _hash_msg(MESSAGE)

    # The attacker's best available attempt: compute the part it can, and hope.
    best_attempt = g1_mul(message_point, scalar_mod(alpha * h1_rogue))
    accepted = _verify(best_attempt, message_point, apk_bound, param)

    print()
    rejected_ok = expected(accepted, False, "attacker's best forgery is REJECTED")

    # Also confirm the OLD attack (which worked in 2a) fails here.
    old_style = g1_mul(message_point, alpha)
    old_accepted = _verify(old_style, message_point, apk_bound, param)
    old_rejected_ok = expected(old_accepted, False, "the 2a-style forgery is REJECTED too")

    # And confirm a GENUINE joint signature still verifies -- the defence must
    # not break the legitimate case.
    genuine = g1_mul(
        message_point,
        scalar_mod(sk_honest * h1_honest + scalar_mod(alpha - sk_honest) * h1_rogue),
    )
    genuine_ok = _verify(genuine, message_point, apk_bound, param)
    genuine_expected = expected(genuine_ok, True, "a genuine joint signature still verifies")

    if not accepted and not old_accepted:
        print()
        print(green("    " + bold("The proposed binding resists the attack.")))
        note(
            "Forging would require the attacker to find a key whose H1 value it "
            "knows in advance -- that is, to invert a random oracle."
        )

    return (not accepted) and (not old_accepted) and genuine_ok \
        and rejected_ok and old_rejected_ok and genuine_expected


def main() -> int:
    banner(
        "ATTACK 2 -- ROGUE-KEY ATTACK ON AGGREGATED OWNERSHIP",
        "Base paper section III.C  |  why APK binds each key with H1(pk_i)",
    )

    broke_plain = attack_plain_aggregation()
    resisted = attack_bound_aggregation()

    banner("RESULT")
    print(f"    Plain aggregate BLS : {red('BROKEN') if broke_plain else 'not broken'}"
          f"  {dim('- fake co-ownership accepted')}")
    print(f"    Proposed APK        : {green('RESISTED') if resisted else red('FAILED')}"
          f"  {dim('- forgery rejected, genuine signatures still work')}")
    print()

    success = broke_plain and resisted
    if success:
        print(green("    Achieved with ONE extra exponentiation per owner -- O(s)."))
        print(green("    The baseline's authenticator defence costs O(s^2)."))
    else:
        print(red("    Demonstration did not produce the expected outcome."))
    print()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
