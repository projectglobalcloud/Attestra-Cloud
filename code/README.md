# Cloud-Based Auditing System with Efficient Ownership Management for Group Data Transactions

Implementation of

> C. Wu, W. You and X. Huang, **"Scalable Cloud Auditing With Efficient Ownership Transfer for Group Transactions"**, *IEEE Transactions on Information Forensics and Security*, vol. 20, 2025. [doi:10.1109/TIFS.2025.3636056](https://doi.org/10.1109/TIFS.2025.3636056)

Major Project Phase I (ISEP23605) · Dept. of Information Science & Engineering, Global Academy of Technology · AY 2026-27

---

## What problem this solves

You store a large dataset in the cloud. Two questions follow, and neither has an obvious answer:

1. **Is it still there, and still correct?** You cannot download 100 GB every week to check, and the cloud has a financial incentive to quietly discard data nobody reads.
2. **What happens when you sell it?** Datasets are traded on Kaggle, on Zenodo, and through commercial data vendors. The buyer must genuinely acquire the ability to verify the data, and the seller must genuinely lose it.

**Provable Data Possession (PDP)** answers the first. **DT-PDP** extends it to answer the second. This project implements a scheme that fixes two failures in the existing DT-PDP literature:

| Problem in prior work | What it costs |
|---|---|
| A group of owners is treated as one fixed unit, so admitting or removing one member is handled as a whole-group transfer | Key generation and every membership change cost **O(s²)** in the number of owners |
| The index secret key is handed to the buyer **in the clear** during a sale | A buyer colluding with the cloud can forge integrity proofs for data that was **never sold** |

The scheme implemented here reduces the first to **O(s)** and eliminates the second entirely. Both claims are demonstrated by running code in this repository, not asserted.

---

## Quick start

```bash
cd code
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

python -m datasets.generate --dataset all --size medium   # ~1 s, makes 125 MB
python -m demo.run_demo                                   # the full walkthrough
```

Then, in order of how convincing they are:

```bash
python -m attacks.collusion            # the attack the paper exists to fix
python -m attacks.rogue_key            # fake co-ownership, blocked
python -m attacks.tampering            # corruption detection + sampling analysis
pytest tests/ -v                       # 41 correctness tests
python -m benchmarks.run_benchmarks    # measured O(s) vs O(s²)
python -m benchmarks.plots             # renders the report figures
```

---

## Layout

```
code/
├── crypto/                 THE PROTOCOL: no web, no database, no I/O
│   ├── pairing.py            BLS12-381 backend; the only file that touches py_ecc
│   ├── hashes.py             H1, H2, H3
│   ├── scheme.py             SysGen, KeyGen, TagGen, Audit
│   ├── ownership.py          AddOwner, RevokeOwner, OwnerTransfer, the ODT
│   ├── serialize.py          group elements <-> bytes
│   └── baseline.py           prior-art schemes, for comparison and for attacks
├── entities/               THE FOUR ACTORS from the paper's system model
│   ├── csp.py                Cloud Storage Provider (semi-trusted, corruptible)
│   ├── tpa.py                Third Party Auditor (sees no data)
│   └── group.py              OwnerGroup, used as Sale Group and Buy Group
├── datasets/               large synthetic datasets with ownership scenarios
│   ├── generate.py           five scenarios, four size tiers
│   └── loader.py             file -> blocks -> Z_q
├── demo/run_demo.py        narrated end-to-end walkthrough
├── attacks/                three executable attack demonstrations
├── benchmarks/             measurement harness + figure rendering
└── tests/                  41 correctness tests
```

The dependency direction is strictly one way: `crypto/` knows nothing about `entities/`, which knows nothing about `demo/`. You can read `crypto/` on its own and it is complete.

---

## The scheme: in one page

**Setup.** Two independent generators, `g ∈ G₂` and `u ∈ G₁`. Nobody knows `log_g(u)`, because `u` is derived by hash-to-curve from a fixed public string, so no party could have chosen it. If anyone knew that discrete log, they could forge tags for arbitrary blocks.

**Keys.** Each owner draws `sk_i` and publishes `pk_i = g^{sk_i}`. The group's aggregated public key is

```
APK = ∏ᵢ pk_i^{H₁(pk_i)}
```

Binding each key with a hash **of itself** is what defeats rogue-key attacks, and it costs one exponentiation per owner. The baseline needs every owner to build an authenticator against every other owner: hence O(s²).

**Tags.** Each owner draws a secret random mask `a_i` and contributes

```
σ_ij = H₃(F‖j)^{H₂(sk_i‖a_i) + a_i} · (u^{m_j})^{sk_i·H₁(pk_i)}
```

The shares multiply into one tag per block. Only the aggregates `Rs = ∏ g^{a_i}` and `Hs = ∏ g^{H₂(sk_i‖a_i)}` are published: never `a_i` itself.

**Audit.** The auditor sends random `Q = {(j, v_j)}`; the cloud returns `DP = Σ m_j v_j` and `TP = ∏ σ_j^{v_j}`; the auditor checks

```
e(TP, g) ?= e(∏ H₃(F‖j)^{v_j}, Hs·Rs) · e(u^{DP}, APK)
```

Three pairings. Always three, whether the dataset is 1 KB or 1 TB.

**Ownership changes.** Every change (join, leave, sale) reduces to a constant-size token `ODT = (H, AUX, V)` that the cloud uses to rewrite tags in place:

```
σ'_j = σ_j · H₃(F‖j)^H · (u^{m_j})^{AUX} · V^{m_j}
```

The `V = ∏ u^{x_i}` term blinds `AUX` so the cloud never learns the group's aggregate signing exponent.

**The security-critical asymmetry.** A joining owner adds `H₂(sk_i‖a_i) + a_i`. A leaving owner removes only `H₂(sk_i‖a_i)`, **their mask stays in the tag forever**, and `Rs` is never decreased. That residue is exactly what makes a leaked index secret useless. It looks like an inconsistency; it is the entire security argument. `tests/test_protocol.py::test_masks_are_retained_on_revoke` pins it so a future cleanup cannot silently remove it.

---

## Deliberate deviations from the paper

These are documented rather than hidden, because an examiner will ask.

**1. Notation.** The paper declares `H₁: {0,1}* → G₁` but uses `H₁(pk_i)` as an exponent, and writes the same value as both `H₂(F‖j)` and `H₃(F‖j)` in different sections. We use the assignment the equations actually require: `H₁, H₂ → Z_q` and `H₃ → G₁`. This is a notation fix; every equation is reproduced exactly.

**2. Type-1 → Type-3 pairing.** The paper assumes a symmetric pairing `e: G₁×G₁ → G_T` (PBC Type-A curves). Those are obsolete and unavailable in maintained Python libraries. We port to BLS12-381, `e: G₁×G₂ → G_T`, placing published key material in G₂ and tags in G₁. The audit equation was re-derived under the port before implementation and is covered by tests.

**3. Block encoding.** A BLS12-381 scalar holds ~255 bits, so a real block does not fit. Default mode hashes each block into `Z_q`; possession of the block is still required to compute its hash, so soundness is unaffected. The paper's 8-byte raw mode is retained for benchmark parity.

**4. Detection probability.** The paper uses the classical bound `1 − ((n−t)/n)^c`, which models sampling **with** replacement. Our auditor samples **without** replacement, so the exact model is hypergeometric. `attacks/tampering.py` measures both: the classical bound turns out to be **conservative**: the implementation detects corruption at or above the promised rate in every configuration tested. The paper's choice of `c = 460` is therefore safe.

---

## On performance: read this before quoting a number

`py_ecc` is pure Python. On the development machine (Apple M-series, arm64):

| Operation | Time |
|---|---|
| hash-to-G₁ | 4.8 ms |
| G₁ scalar multiplication | 10.3 ms |
| G₂ scalar multiplication | 37.7 ms |
| pairing | 578 ms |

The paper's authors used C with the PBC library and report microseconds. **Our absolute numbers are roughly two orders of magnitude slower and are not comparable to theirs.**

That does not weaken the result, because the paper's claim is asymptotic, O(s) versus O(s²). Both schemes here are measured on the same curve, through the same backend, in the same process, so the *shape* of the curves is a fair test, and the shape is what the claim is about.

Measured `KeyGen`, in milliseconds:

| owners `s` | proposed | baseline [8] | speedup |
|---:|---:|---:|---:|
| 1 | 73 | 77 | 1.1× |
| 4 | 298 | 745 | 2.5× |
| 8 | 594 | 2,658 | 4.5× |
| 12 | 890 | 5,765 | 6.5× |
| 16 | 1,193 | 10,660 | 8.9× |
| 20 | 1,486 | 15,540 | **10.5×** |

<sub>Every figure measured directly; median of 3 repeats.</sub>

The speedup grows **linearly** with `s`, which is precisely the signature of quadratic-over-linear. Doubling the group from 10 to 20 owners doubles the proposed cost (746 → 1,486 ms) but nearly quadruples the baseline (4,076 → 15,540 ms).

Two further measured results confirm the design's claims:

- **Verification is independent of group size.** At `c = 25`, verification took 692.6 ms with 1 owner and 692.1 ms with 10: a 0.07 % difference. Once tags are aggregated, the auditor cannot tell a 1-owner group from a 10-owner one.
- **The ownership token is constant size** at **160 bytes** for every group size tested (1 to 20 owners), confirming the communication claim of the paper's Table III.

Two engineering mitigations are in the code:

- **Batched final exponentiation.** `pair_product()` computes the audit's three pairings with one final exponentiation instead of three, cutting verification from ~1.7 s to ~0.4 s.
- **A swappable backend.** `crypto/pairing.py` is the only file that imports `py_ecc`. Replacing it with native bindings is a single-file change; nothing else in the codebase would move.

The baseline figures are a **cost-model re-implementation** of scheme [8]'s structure per Table II of the paper, not the original authors' code. Asymptotic shape is faithful; absolute constants are ours. This is restated wherever those numbers appear.

---

## The datasets

Five scenarios, each a plausible real sale, each owned by a **consortium** rather than one party, because multi-owner is the case the paper addresses:

| Dataset | Sale Group → Buy Group |
|---|---|
| `medical-imaging` | 3 hospitals → medical-AI research institute |
| `iot-telemetry` | city transport dept + 2 agencies → urban analytics firm |
| `financial-ledger` | regional bank → statutory audit firm |
| `genomics-variants` | 3 sequencing labs → pharmaceutical company |
| `ai-training-corpus` | data-labelling vendor → autonomous-systems AI lab |

Sizes: `small` 2 MB · `medium` 25 MB · `large` 100 MB · `xlarge` 512 MB, sharded into 8 MB parts like a real data lake.

**Everything is synthetic.** No real patient, customer, or personal data is present or reproduced: the generators emit statistically plausible but entirely fabricated records. Generation is deterministic: the same `--seed` reproduces a dataset byte for byte, so audits and benchmarks stay reproducible.

> **Note on block size.** Protocol cost is driven by the *number of blocks* `n`, not file size. At the default 256 KB block, a 100 MB dataset is 400 blocks. Security is unaffected: the detection bound depends on the *fraction* of blocks corrupted, not their size. See `config.py`.

---

## Reference

The paper's authors published reference code at
`github.com/chencwu/Scalable-Cloud-Auditing-with-Efficient-Ownership-Transfer-for-Group-Transactions`.

This implementation was written independently from the equations in the paper. That repository is cited as related work, not copied.
