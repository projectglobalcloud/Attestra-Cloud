# CHAPTER 6
# TESTING

---

## 6.1 Introduction

Testing a cryptographic protocol differs from testing ordinary application software. The question is never "does this function execute without error": a broken implementation of a signature scheme executes perfectly well and simply accepts forgeries. The question is always **"does the security property still hold after this operation?"**

The test strategy therefore has two halves:

1. **Correctness testing**, 41 automated tests asserting that the audit equation holds when it should, and fails when it should.
2. **Security testing**, three executable attack demonstrations that attempt real forgeries against the implementation, and against the prior-art schemes it replaces.

The second half is the more important. A test suite proves the implementation does what the authors intended; an attack demonstration proves that what they intended was worth doing.

---

## 6.2 Test Environment

| Item | Detail |
|---|---|
| Framework | `pytest` 7.4 |
| Test cases | 41 |
| Execution time | 132 seconds |
| Result | **41 passed, 0 failed** |
| Command | `pytest tests/ -v` |

Small parameters are used throughout (6 blocks, 1–5 owners). The tests verify **algebra**, not performance, and larger parameters would only make the suite slower without testing anything further.

---

## 6.3 Correctness Testing

### 6.3.1 Test coverage by module

| Test class | Cases | What it establishes |
|---|---|---|
| `TestSysGenKeyGen` | 4 | Generators are deterministic and independent; keys and masks are distinct per owner; `APK` is order-independent |
| `TestHashes` | 4 | `H₁`, `H₂` land in `Z_q`; `H₃` is deterministic, index-sensitive and file-sensitive |
| `TestTagGenAudit` | 9 | Honest audits pass; corruption, deletion, block substitution, forged tags and wrong file abstracts all fail |
| `TestAddOwner` | 4 | Auditing survives owner addition; stale parameters are rejected; operations compose |
| `TestRevokeOwner` | 4 | Auditing survives revocation; **masks are provably retained** |
| `TestOwnerTransfer` | 5 | The buyer gains auditing ability, the seller loses it; chained resales work; the token is constant-size |
| `TestSerialisation` | 6 | Round-trips are exact and canonical; off-curve points are rejected |
| `TestEndToEnd` | 1 | Complete lifecycle, all six algorithms in sequence |

### 6.3.2 Representative negative tests

The negative tests are the substantive ones: they assert that the protocol *refuses* things it should refuse.

| Test | Attack modelled | Expected |
|---|---|---|
| `test_corrupted_block_fails` | Cloud alters one block | Audit fails |
| `test_deleted_block_fails` | Cloud loses a block and answers with zeros | Audit fails |
| `test_swapped_blocks_fail` | Cloud serves block 4's contents for a challenge on block 1 | Audit fails |
| `test_forged_tag_fails` | Tag invented using an unrelated key | Audit fails |
| `test_wrong_file_abstract_fails` | Tags from one file replayed for another | Audit fails |
| `test_stale_state_rejected_after_add` | Old verification parameters used after a membership change | Audit fails |
| `test_seller_loses_ability_to_audit` | Seller attempts to audit after a sale | Audit fails |

All behave as expected.

### 6.3.3 A defect found and fixed during testing

Three tests failed on their first run. The cause was not in the protocol but in how group elements were compared.

`py_ecc` represents curve points in **projective** coordinates `(x, y, z)`, and a single curve point has infinitely many such representations, `(x, y, 1)` and `(4x, 8y, 2)` denote the same point. Comparing the raw tuples with `==` therefore compares *representations*, not *points*.

The failure was subtle in an instructive way: the comparison happens to succeed whenever both operands were produced by identical sequences of operations, so the bug is intermittent and easy to miss. It surfaced in `test_apk_is_order_independent`, where the same aggregate public key was computed in two different orders.

The fix was made in the library layer rather than in the tests, `g1_eq()` and `g2_eq()` were added to `crypto/pairing.py`, normalising to affine coordinates before comparison, because any future code comparing group elements would hit the same trap.

Two further tests were strengthened as a result. `test_masks_are_retained_on_revoke` had been comparing object identity and therefore passing for the wrong reason; it now asserts the exact algebra, namely that `Hs` shrinks by precisely `g^{H₂(sk‖a)}` of the revoked owner. A new test, `test_encoding_is_canonical`, confirms that two projective representations of the same point serialise to identical bytes.

### 6.3.4 The security-critical test

```
tests/test_protocol.py::TestRevokeOwner::test_masks_are_retained_on_revoke
```

This test asserts that `Rs` is **unchanged** by revocation, while `Hs` shrinks by exactly the revoked owner's `H₂` term.

To a reader unfamiliar with the scheme, retaining a departed owner's random mask looks like an oversight, and "tidying it up" would appear to be a harmless simplification. It is not: removing it would silently destroy the defence against the collusion attack while leaving every other test passing and the system apparently working. The test exists to make that impossible.

---

## 6.4 Security Testing: Attack Demonstrations

Each demonstration executes the same attack twice: once against the prior-art scheme it targets, and once against the scheme implemented here. Demonstrating only the defence would prove nothing, because a scheme that rejects everything also rejects attacks.

### 6.4.1 Attack 1: Collusion between the buyer and the cloud

**Command:** `python -m attacks.collusion`

This is the vulnerability the base paper exists to fix (section III.D).

*The setting.* In schemes [7] and [8], completing a sale requires the seller to hand its index secret key `sk₁` to the buyer in the clear. The buyer then colludes with the cloud. Between them they hold the leaked key, every stored tag and every stored block.

*Why the forgery works.* The prior-art tag separates cleanly into a position factor and a contents factor:

```
σ_j = H₀(j)^{sk₁} · (u^{m_j})^{sk₂}
```

The position factor depends only on `j` and `sk₁`. Holding `sk₁`, an attacker can peel it off one tag and graft it onto another:

```
σ′ = σ_{j′} · H₀(j′)^{−sk₁} · H₀(j)^{sk₁} = H₀(j)^{sk₁} · (u^{m_{j′}})^{sk₂}
```

That is a valid tag binding position `j` to the contents of block `j′`. A cloud that has **deleted** block `j` can therefore answer a challenge for `j` using any block it still holds. The entire attack costs two exponentiations and one multiplication.

*Result.*

| Target | Outcome |
|---|---|
| Prior art [7], [8] | **BROKEN**, the forged proof for a deleted block was accepted |
| Proposed scheme | **RESISTED**, the identical forgery was rejected |

The demonstration deliberately grants the attacker *more* than the protocol would ever leak: every owner's `H₂(sk_i‖a_i)` value is handed over. The attack still fails, because the graft removes only the `H₂` component and leaves the random masks `a_i` welded to the original index. Recovering `a_i` from the published `Rs = ∏ g^{a_i}` would require solving a discrete logarithm.

A follow-up check confirms that honest proofs still verify afterwards, so the defence causes no collateral damage.

### 6.4.2 Attack 2: Rogue-key attack on aggregated ownership

**Command:** `python -m attacks.rogue_key`

*The setting.* Under naive BLS aggregation, `APK = ∏ pk_i`. An attacker who chooses its key *after* seeing an honest party's key can publish

```
pk_rogue = g^α · pk_honest^{−1}
```

so the aggregate collapses to `g^α`, which it fully controls. It then signs alone and the verification equation accepts, "proving" that the honest party co-signed a message it has never seen. In this system that means claiming co-ownership of another group's dataset.

*The defence.* Binding each key with a hash of itself, `APK = ∏ pk_i^{H₁(pk_i)}`, means a valid signature would require the exponent

```
sk·H₁(pk_honest) + sk_rogue·H₁(pk_rogue)
```

Substituting the attacker's own construction `sk_rogue = α − sk` gives

```
sk·(H₁(pk_honest) − H₁(pk_rogue)) + α·H₁(pk_rogue)
```

Because the two hash values differ, the honest secret `sk` no longer cancels, and the attacker does not know `sk`.

*Result.*

| Target | Outcome |
|---|---|
| Plain aggregate BLS | **BROKEN**, fake co-ownership accepted |
| Proposed `APK` | **RESISTED**, both the attacker's best attempt and the plain-aggregation forgery rejected |
| Genuine joint signature | **Still verifies**, the defence does not break the legitimate case |

The cost of this defence is one extra exponentiation per owner, O(s). The baseline achieves the same protection with a public-key-aggregation authenticator costing O(s²), which must additionally be rebuilt on every membership change.

### 6.4.3 Attack 3: Data tampering by a semi-trusted cloud

**Command:** `python -m attacks.tampering`

*Deterministic detection.* When a corrupted block is challenged, the audit fails without exception: corrupting `m_j` breaks the homomorphic relation between `DP` and `TP`, and the pairing equation cannot balance. Both a single altered block and a deleted block answered with zeros were detected.

*Probabilistic coverage.* The auditor samples `c` blocks rather than all `n`, so the practical question is whether the sample hits a corrupted block. The base paper uses the classical PDP bound

```
P_detect = 1 − ((n − t)/n)^c
```

to justify `c = 460`.

*A finding.* This bound models sampling **with replacement**. The implementation's auditor draws **distinct** block indices, for which the exact model is hypergeometric:

```
P_detect = 1 − C(n−t, c) / C(n, c)
```

Measured detection rates over 500 trials per configuration track the hypergeometric model closely and sit **at or above** the paper's bound in every configuration tested. Detailed figures appear in §7.4.

The conclusion is favourable to the paper: its bound is *conservative*, never optimistic, so the recommended `c = 460` is safe. It under-promises what the implementation actually delivers.

---

## 6.5 System Testing: End-to-End Walkthrough

**Command:** `python -m demo.run_demo`

A narrated walkthrough runs the complete lifecycle against a real generated dataset, checking the auditor's verdict at each stage.

| # | Step | Expected | Result |
|---|---|---|---|
| 1 | Honest cloud audited | PASS | PASS |
| 2 | Cloud corrupts a block | FAIL | FAIL |
| 3 | Block restored | PASS | PASS |
| 4 | New institution joins the consortium | PASS | PASS |
| 5 | An institution leaves | PASS | PASS |
| 6 | Dataset sold, **buyer** audits | PASS | PASS |
| 7 | Dataset sold, **seller's old parameters** | FAIL | FAIL |

**All 7 checks behaved as expected.**

Step 7 is the defining result of the project. After the sale, the seller's verification parameters are worthless against the re-tagged data. Ownership did not change merely in a database record: it changed cryptographically, which is precisely the property that ordinary access-control systems cannot provide.

---

## 6.6 Summary of Test Results

| Category | Tests | Result |
|---|---|---|
| Correctness (`pytest`) | 41 | **41 passed** |
| Collusion attack | prior art vs proposed | Prior art broken, proposed resisted |
| Rogue-key attack | plain BLS vs proposed | Plain BLS broken, proposed resisted |
| Tampering detection | deterministic + 3,500 sampling trials | All corruptions detected; bound confirmed conservative |
| End-to-end lifecycle | 7 checks | **7 as expected** |

One implementation defect was found and fixed during testing (§6.3.3), in the point-comparison layer rather than the protocol. No defect was found in the protocol implementation itself.
