"""
Correctness tests for the six protocol algorithms.

Organised by what each test PROVES, not by which function it calls, because the
interesting question is never "does tag_gen() run" but "does the audit equation
still hold after this operation".

Run:
    pytest tests/ -v
    pytest tests/ -v -k transfer
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crypto.hashes import H1, H2, H3, hash_block  # noqa: E402
from crypto.ownership import (  # noqa: E402
    add_owners,
    apply_token,
    revoke_owners,
    transfer_finalize_bg,
    transfer_initiate_sg,
    transfer_ownership,
)
from crypto.pairing import (  # noqa: E402
    CURVE_ORDER,
    g1_add,
    g1_eq,
    g1_mul,
    g2_eq,
    g2_mul,
    rand_scalar,
)
from crypto.scheme import (  # noqa: E402
    Challenge,
    Proof,
    aggregate_public_key,
    gen_challenge,
    gen_proof,
    key_gen,
    sys_gen,
    tag_gen,
    verify_proof,
)
from crypto.serialize import (  # noqa: E402
    g1_decode,
    g1_encode,
    g2_decode,
    g2_encode,
    scalar_decode,
    scalar_encode,
)

# Small sizes throughout: these tests check ALGEBRA, not performance, and
# py_ecc is slow enough that large n would make the suite unusable.
N_BLOCKS = 6
CHALLENGE_SIZE = 4


# ==========================================================================
# Fixtures
# ==========================================================================

@pytest.fixture(scope="module")
def param():
    return sys_gen()


@pytest.fixture
def blocks():
    return [hash_block(f"test-block-{i}".encode()) for i in range(N_BLOCKS)]


@pytest.fixture
def abstract():
    return b"test-file-abstract-v1"


def _owners(param, n, prefix="owner"):
    return [key_gen(param, f"{prefix}-{i}") for i in range(n)]


def _audit_passes(param, state, blocks, tags, c=CHALLENGE_SIZE) -> bool:
    """Run one full audit round and return the verdict."""
    challenge = gen_challenge(len(blocks), c)
    proof = gen_proof(challenge, blocks, tags)
    return verify_proof(param, state, challenge, proof)


# ==========================================================================
# SysGen and KeyGen
# ==========================================================================

class TestSysGenKeyGen:

    def test_generators_are_independent(self, param):
        """
        u must not be a known power of g, or unforgeability collapses.

        We cannot test "nobody knows the discrete log" directly, but we can
        test the property that makes it true: u is derived by hash-to-curve
        from a fixed public string, so it is reproducible and nobody chose it.
        """
        again = sys_gen()
        assert g1_eq(param.u, again.u), "u must be deterministic (nothing-up-my-sleeve)"
        assert g2_eq(param.g, again.g)

    def test_keys_are_distinct(self, param):
        owners = _owners(param, 5)
        assert len({o.sk for o in owners}) == 5
        assert len({o.a for o in owners}) == 5, "random masks must differ per owner"

    def test_public_key_matches_secret(self, param):
        owner = key_gen(param, "solo")
        assert g2_eq(owner.pk, g2_mul(param.g, owner.sk))

    def test_apk_is_order_independent(self, param):
        """
        APK = prod pk_i^{H1(pk_i)} is a product, so owner ordering must not
        matter -- groups are sets, not lists.

        Note this MUST use g2_eq: the two products are built by different
        operation sequences, so their projective representations differ even
        though they are the same point.
        """
        owners = _owners(param, 4)
        assert g2_eq(
            aggregate_public_key(owners),
            aggregate_public_key(list(reversed(owners))),
        )


# ==========================================================================
# Hash functions
# ==========================================================================

class TestHashes:

    def test_h1_h2_land_in_zq(self, param):
        owner = key_gen(param, "h")
        assert 0 <= H1(owner.pk) < CURVE_ORDER
        assert 0 <= H2(owner.sk, owner.a) < CURVE_ORDER

    def test_h3_is_deterministic_and_index_sensitive(self, abstract):
        assert H3(abstract, 5) == H3(abstract, 5)
        assert H3(abstract, 5) != H3(abstract, 6)

    def test_h3_is_file_sensitive(self):
        """
        Binding F into H3 is what stops a tag from one file being replayed as
        a tag for another at the same index.
        """
        assert H3(b"file-A", 3) != H3(b"file-B", 3)

    def test_h2_depends_on_both_inputs(self):
        assert H2(111, 222) != H2(111, 223)
        assert H2(111, 222) != H2(112, 222)


# ==========================================================================
# TagGen and Audit
# ==========================================================================

class TestTagGenAudit:

    @pytest.mark.parametrize("n_owners", [1, 2, 5])
    def test_honest_audit_passes(self, param, blocks, abstract, n_owners):
        owners = _owners(param, n_owners)
        tags, state = tag_gen(param, owners, abstract, blocks)
        assert _audit_passes(param, state, blocks, tags)

    def test_full_challenge_passes(self, param, blocks, abstract):
        """Challenging every block must verify, not just a sample."""
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)
        assert _audit_passes(param, state, blocks, tags, c=N_BLOCKS)

    def test_corrupted_block_fails(self, param, blocks, abstract):
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        corrupted = list(blocks)
        corrupted[2] = (corrupted[2] + 1) % CURVE_ORDER

        challenge = Challenge(pairs=((2, rand_scalar()),))
        proof = gen_proof(challenge, corrupted, tags)
        assert not verify_proof(param, state, challenge, proof)

    def test_deleted_block_fails(self, param, blocks, abstract):
        """A cloud answering with zeros for a lost block must be caught."""
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        lost = list(blocks)
        lost[0] = 0

        challenge = Challenge(pairs=((0, rand_scalar()),))
        proof = gen_proof(challenge, lost, tags)
        assert not verify_proof(param, state, challenge, proof)

    def test_swapped_blocks_fail(self, param, blocks, abstract):
        """
        Serving block 4's contents in answer to a challenge for block 1 must
        fail -- the index is bound into the tag through H3(F||j).
        """
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        challenge = Challenge(pairs=((1, rand_scalar()),))
        honest = gen_proof(challenge, blocks, tags)
        swapped = Proof(dp=(blocks[4] * challenge.pairs[0][1]) % CURVE_ORDER, tp=honest.tp)
        assert not verify_proof(param, state, challenge, swapped)

    def test_forged_tag_fails(self, param, blocks, abstract):
        """A tag invented from an unrelated key must not verify."""
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        outsider = key_gen(param, "outsider")
        fake = g1_add(
            g1_mul(H3(abstract, 0), outsider.h2),
            g1_mul(g1_mul(param.u, blocks[0]), outsider.sk * outsider.h1),
        )
        forged = list(tags)
        forged[0] = fake

        challenge = Challenge(pairs=((0, rand_scalar()),))
        proof = gen_proof(challenge, blocks, forged)
        assert not verify_proof(param, state, challenge, proof)

    def test_wrong_file_abstract_fails(self, param, blocks, abstract):
        """Auditing with the wrong F must fail, even with the right tags."""
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        from dataclasses import replace
        wrong = replace(state, file_abstract=b"a-completely-different-file")
        assert not _audit_passes(param, wrong, blocks, tags)

    def test_tag_count_matches_blocks(self, param, blocks, abstract):
        owners = _owners(param, 2)
        tags, state = tag_gen(param, owners, abstract, blocks)
        assert len(tags) == len(blocks) == state.n_blocks

    def test_challenge_is_clamped_and_distinct(self, param):
        challenge = gen_challenge(N_BLOCKS, c=999)
        indices = [j for j, _ in challenge.pairs]
        assert len(indices) == N_BLOCKS
        assert len(set(indices)) == len(indices), "indices must be distinct"


# ==========================================================================
# OwnerModify -- AddOwner
# ==========================================================================

class TestAddOwner:

    @pytest.mark.parametrize("n_new", [1, 3])
    def test_audit_survives_add(self, param, blocks, abstract, n_new):
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)
        assert _audit_passes(param, state, blocks, tags)

        newcomers = _owners(param, n_new, prefix="new")
        token, state2 = add_owners(param, state, newcomers)
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert _audit_passes(param, state2, blocks, tags2)

    def test_stale_state_rejected_after_add(self, param, blocks, abstract):
        """
        The OLD verification parameters must stop working once tags are
        rewritten -- otherwise ownership changes would be cosmetic.
        """
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        token, state2 = add_owners(param, state, _owners(param, 1, "new"))
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert not _audit_passes(param, state, blocks, tags2)

    def test_old_tags_rejected_by_new_state(self, param, blocks, abstract):
        """The converse: new parameters must not accept un-updated tags."""
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)
        _, state2 = add_owners(param, state, _owners(param, 1, "new"))
        assert not _audit_passes(param, state2, blocks, tags)

    def test_sequential_adds(self, param, blocks, abstract):
        """Ownership changes must compose; state cannot drift over time."""
        owners = _owners(param, 2)
        tags, state = tag_gen(param, owners, abstract, blocks)

        for round_no in range(3):
            token, state = add_owners(param, state, _owners(param, 1, f"r{round_no}"))
            tags = apply_token(param, token, abstract, blocks, tags)
            assert _audit_passes(param, state, blocks, tags), f"failed at round {round_no}"


# ==========================================================================
# OwnerModify -- RevokeOwner
# ==========================================================================

class TestRevokeOwner:

    def test_audit_survives_revoke(self, param, blocks, abstract):
        owners = _owners(param, 4)
        tags, state = tag_gen(param, owners, abstract, blocks)

        token, state2 = revoke_owners(param, state, [owners[0]])
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert _audit_passes(param, state2, blocks, tags2)

    def test_revoke_multiple(self, param, blocks, abstract):
        owners = _owners(param, 5)
        tags, state = tag_gen(param, owners, abstract, blocks)

        token, state2 = revoke_owners(param, state, owners[:3])
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert _audit_passes(param, state2, blocks, tags2)
        assert len(state2.owner_ids) == 2

    def test_masks_are_retained_on_revoke(self, param, blocks, abstract):
        """
        THE SECURITY-CRITICAL DETAIL.

        Rs must NOT change when an owner is revoked. The departing owner's
        random mask a_i stays embedded in every tag forever, which is what
        keeps a leaked index secret useless. If a future refactor "tidied this
        up" by also subtracting the mask, the collusion defence would silently
        disappear -- so it is pinned here.
        """
        owners = _owners(param, 3)
        _, state = tag_gen(param, owners, abstract, blocks)
        _, state2 = revoke_owners(param, state, [owners[0]])

        assert g2_eq(state2.rs, state.rs), "Rs must be unchanged by revocation"
        assert not g2_eq(state2.hs, state.hs), "Hs must shrink by the revoked H2 term"

        # Pin the exact algebra, not just "it changed": Hs must shrink by
        # precisely g^{H2(sk||a)} of the revoked owner.
        from crypto.pairing import g2_add, g2_generator, g2_neg
        expected_hs = g2_add(state.hs, g2_neg(g2_mul(g2_generator(), owners[0].h2)))
        assert g2_eq(state2.hs, expected_hs), "Hs must shrink by exactly the revoked term"

    def test_add_then_revoke_returns_to_working_state(self, param, blocks, abstract):
        owners = _owners(param, 3)
        tags, state = tag_gen(param, owners, abstract, blocks)

        newcomers = _owners(param, 2, "new")
        token, state = add_owners(param, state, newcomers)
        tags = apply_token(param, token, abstract, blocks, tags)

        token, state = revoke_owners(param, state, newcomers)
        tags = apply_token(param, token, abstract, blocks, tags)

        assert _audit_passes(param, state, blocks, tags)


# ==========================================================================
# OwnerTransfer
# ==========================================================================

class TestOwnerTransfer:

    def test_buyer_can_audit_after_transfer(self, param, blocks, abstract):
        sg = _owners(param, 3, "sg")
        bg = _owners(param, 2, "bg")
        tags, state = tag_gen(param, sg, abstract, blocks)

        token, state2 = transfer_ownership(param, state, sg, bg)
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert _audit_passes(param, state2, blocks, tags2)

    def test_seller_loses_ability_to_audit(self, param, blocks, abstract):
        """
        THE DEFINING PROPERTY OF OWNERSHIP TRANSFER.

        After the sale, the seller's verification parameters must be worthless
        against the re-tagged data. Ownership changed cryptographically, not
        just in a database row.
        """
        sg = _owners(param, 3, "sg")
        bg = _owners(param, 2, "bg")
        tags, state = tag_gen(param, sg, abstract, blocks)

        token, _ = transfer_ownership(param, state, sg, bg)
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert not _audit_passes(param, state, blocks, tags2)

    def test_two_step_matches_wrapper(self, param, blocks, abstract):
        """The split SG/BG flow must be equivalent to the convenience wrapper."""
        sg = _owners(param, 2, "sg")
        bg = _owners(param, 2, "bg")
        tags, state = tag_gen(param, sg, abstract, blocks)

        sg_token, intermediate = transfer_initiate_sg(param, state, sg)
        token, final_state = transfer_finalize_bg(param, intermediate, sg_token, bg)
        tags2 = apply_token(param, token, abstract, blocks, tags)

        assert _audit_passes(param, final_state, blocks, tags2)

    def test_chained_transfers(self, param, blocks, abstract):
        """A dataset resold twice must remain auditable by its latest owner."""
        g1 = _owners(param, 2, "g1")
        g2 = _owners(param, 2, "g2")
        g3 = _owners(param, 3, "g3")

        tags, state = tag_gen(param, g1, abstract, blocks)

        token, state = transfer_ownership(param, state, g1, g2)
        tags = apply_token(param, token, abstract, blocks, tags)
        assert _audit_passes(param, state, blocks, tags), "after first sale"

        token, state = transfer_ownership(param, state, g2, g3)
        tags = apply_token(param, token, abstract, blocks, tags)
        assert _audit_passes(param, state, blocks, tags), "after second sale"

    def test_transfer_token_is_constant_size(self, param, blocks, abstract):
        """
        The ODT must not grow with group size -- that is the communication
        claim of the paper's Table III.
        """
        from crypto.serialize import G1_SIZE, SCALAR_SIZE

        sizes = []
        for s in (1, 5, 10):
            sg = _owners(param, s, f"sg{s}")
            bg = _owners(param, s, f"bg{s}")
            _, state = tag_gen(param, sg, abstract, blocks)
            token, _ = transfer_ownership(param, state, sg, bg)
            sizes.append(len(scalar_encode(token.h)) + len(scalar_encode(token.aux))
                         + len(g1_encode(token.v)))

        assert len(set(sizes)) == 1, f"token size varied with group size: {sizes}"
        assert sizes[0] == 2 * SCALAR_SIZE + G1_SIZE


# ==========================================================================
# Serialisation
# ==========================================================================

class TestSerialisation:

    def test_g1_roundtrip(self, param, abstract):
        point = H3(abstract, 42)
        assert g1_eq(g1_decode(g1_encode(point)), point)

    def test_g2_roundtrip(self, param):
        owner = key_gen(param, "ser")
        assert g2_eq(g2_decode(g2_encode(owner.pk)), owner.pk)

    def test_encoding_is_canonical(self, param, abstract):
        """
        Encoding normalises to affine, so two projective representations of the
        same point must encode to identical bytes. Without this, a tag stored
        and reloaded could compare unequal to itself.
        """
        point = H3(abstract, 7)
        doubled = g1_add(point, point)
        halved_back = g1_add(doubled, g1_mul(point, CURVE_ORDER - 1))
        assert g1_encode(point) == g1_encode(halved_back)

    def test_scalar_roundtrip(self):
        value = rand_scalar()
        assert scalar_decode(scalar_encode(value)) == value

    def test_negative_scalar_is_reduced(self):
        assert scalar_decode(scalar_encode(-5)) == CURVE_ORDER - 5

    def test_off_curve_point_is_rejected(self):
        """
        A tampered database row must produce a clean error, not a silently
        meaningless pairing result.
        """
        garbage = (1).to_bytes(48, "big") + (2).to_bytes(48, "big")
        with pytest.raises(ValueError, match="not on the curve"):
            g1_decode(garbage)

    def test_wrong_length_rejected(self):
        with pytest.raises(ValueError, match="96 bytes"):
            g1_decode(b"\x00" * 10)


# ==========================================================================
# Full lifecycle
# ==========================================================================

class TestEndToEnd:

    def test_complete_dataset_lifecycle(self, param, blocks, abstract):
        """
        Every algorithm, in the order a real transaction would use them, with
        an audit after each step.

        This is the test that would catch a regression anywhere in the system.
        """
        sale = _owners(param, 3, "sale")
        tags, state = tag_gen(param, sale, abstract, blocks)
        assert _audit_passes(param, state, blocks, tags), "1. initial upload"

        # 2. a new member joins
        joiners = _owners(param, 2, "joiner")
        token, state = add_owners(param, state, joiners)
        tags = apply_token(param, token, abstract, blocks, tags)
        assert _audit_passes(param, state, blocks, tags), "2. after AddOwner"

        # 3. an original member leaves
        token, state = revoke_owners(param, state, [sale[0]])
        tags = apply_token(param, token, abstract, blocks, tags)
        assert _audit_passes(param, state, blocks, tags), "3. after RevokeOwner"

        # 4. the dataset is sold
        remaining = sale[1:] + joiners
        buyers = _owners(param, 2, "buyer")
        pre_sale_state = state
        token, state = transfer_ownership(param, state, remaining, buyers)
        tags = apply_token(param, token, abstract, blocks, tags)
        assert _audit_passes(param, state, blocks, tags), "4. buyer audits"

        # 5. the seller can no longer audit
        assert not _audit_passes(param, pre_sale_state, blocks, tags), \
            "5. seller must lose auditing ability"

        # 6. tampering is still caught after all those ownership changes
        damaged = list(blocks)
        damaged[1] = (damaged[1] + 1) % CURVE_ORDER
        challenge = Challenge(pairs=((1, rand_scalar()),))
        proof = gen_proof(challenge, damaged, tags)
        assert not verify_proof(param, state, challenge, proof), \
            "6. tampering must still be detected"
