# Project Plan: Cloud-Based Auditing System with Efficient Ownership Management for Group Data Transactions

**Institution:** Global Academy of Technology (Autonomous, affiliated to VTU), Dept. of Information Science & Engineering
**Course:** Major Project Phase I (ISEP23605), AY 2026-27
**Classification:** Research Project
**Team:** Sanjay GP (1GA23IS141), Shashank Gowda TK (1GA23IS144), Srujangouda Malipatil (1GA23IS165), Yashwanth Gowda DK (1GA23IS188)

**Base paper:** Chenchen Wu, Weijing You, Xinyi Huang, *"Scalable Cloud Auditing With Efficient Ownership Transfer for Group Transactions"*, IEEE Transactions on Information Forensics and Security, vol. 20, 2025. DOI [10.1109/TIFS.2025.3636056](https://doi.org/10.1109/TIFS.2025.3636056)

---

## 1. Context: what this project actually is

Cloud providers store data on behalf of users, but users cannot trust them blindly. **Provable Data Possession (PDP)** lets a user verify that the cloud still holds their file *without downloading it*. **DT-PDP** extends this so that data *ownership* can be sold/transferred between parties without re-uploading the file.

The base paper identifies two unsolved problems in existing DT-PDP work:

| Problem | Consequence |
|---|---|
| Prior multi-owner schemes treat a group as one fixed unit. Adding/revoking one member is handled as a full group-to-group transfer. | Key generation and membership changes cost **O(s²)** in the number of owners `s`. Does not scale. |
| Prior schemes transfer the *index secret key* in the clear during a transaction. | A buyer who colludes with the cloud can forge tags for data that was **never sold**, breaking integrity auditing for the honest seller. |

The paper's fix, and therefore this project's core contribution:

1. Bind each owner's key pair with a hash of their own public key inside the tag, `σ = (...)^{sk·H₁(pk)}`, which kills **rogue-key attacks** at **O(s)** instead of O(s²).
2. Add a per-owner **random mask** `a_i` to the index exponent, and publish only the *aggregate* `Rs = ∏ g^{a_i}`. Even if the index secret leaks, the mask is unknown, so forgery fails. This defeats the **collusion attack**.

**Deliverable of this project:** a real, working implementation of that protocol (not a mock), with measured benchmarks proving the O(s) vs O(s²) claim, live attack demonstrations, and the matching report chapters.

---

## 2. What was supplied, and what I concluded

| File | Role |
|---|---|
| `Major Project Synoposis final.pdf` | Approved synopsis: title, objectives, methodology, SDG alignment. Defines scope. |
| `Scalable_Cloud_Auditing_...pdf` | The IEEE base paper. Source of all protocol equations, security proofs, and the benchmark methodology. |
| `Template Report contents AY 2024-25.pdf` | Report already drafted through Chapter 5. Fixes the required stack and the functional/non-functional requirements. |
| `WhatsApp Image ... .jpeg` | A student-grievance table. **Unrelated to this project, excluded by client instruction.** |

**Scope decision (client instruction, later amended):** originally **backend only, no frontend**; the client subsequently requested a web interface, now implemented in `web/` (Flask + SQLAlchemy + vanilla JS — the stack named in report §3.2) as a company-style console over the entity layer. The statement below stands for the protocol deliverable itself: The deliverable is the cryptographic core, the four-entity backend, large sample datasets, executable attack demonstrations, benchmarks and the report chapters. The code is written to be *read* (by examiners and judges) so every non-obvious design decision carries its reasoning inline.

Chapter 3 of the report names "Java / Python"; we use **Python 3.12**, which the report already permits, so no report edit is required. The Flask/MySQL rows of §3.2 describe a web deployment that is out of scope for this phase and should be marked as future work in the report.

---

## 3. Cryptographic design

### 3.1 Corrections to the paper's notation

The paper is internally inconsistent about its hash functions, it declares `H₁: {0,1}* → G₁` but then uses `H₁(pk_i)` as an **exponent**, and it uses both `H₂(F‖j)` and `H₃(F‖j)` for the same value. We fix the notation to what the equations actually require, and document it:

| Ours | Type | Used for |
|---|---|---|
| `H₁` | `{0,1}* → Z_q` | `H₁(pk_i)`, exponent binding a key pair |
| `H₂` | `{0,1}* → Z_q` | `H₂(sk_i‖a_i)`, masked index exponent |
| `H₃` | `{0,1}* → G₁` | `H₃(F‖j)`, hash-to-curve of the block index |

### 3.2 Pairing curve: the Type-1 to Type-3 port

The paper assumes a **symmetric (Type-1)** pairing `e: G₁×G₁ → G_T`, as provided by the PBC library's Type-A curves. Type-1 curves are today considered obsolete and are not available in any maintained Python library.

We port the scheme to **BLS12-381**, an **asymmetric (Type-3)** curve `e: G₁×G₂ → G_T`, via `py_ecc` (pure Python, so no native build, verified working on the target arm64 macOS machine).

Placement rule: everything raised to a **secret** and published as a verification parameter goes in **G₂**; the tag and its bases go in **G₁**.

| Element | Group |
|---|---|
| `u`, `H₃(F‖j)`, tag `σ_j`, proof `TP` | **G₁** |
| `g`, `pk_i`, `APK`, `Rs`, `Hs` | **G₂** |

Measured on the target machine (`py_ecc.optimized_bls12_381`): hash-to-G₁ 4.8 ms · G₁ scalar-mult 10.3 ms · G₂ scalar-mult 37.7 ms · pairing 578 ms. Verification uses **3 pairings total regardless of file size**, so this is comfortably fast enough for a live demo.

### 3.3 The six algorithms (as implemented)

```
SysGen(1^λ) → param
    param = { q, G₁, G₂, G_T, e, g∈G₂, u∈G₁, H₁, H₂, H₃ }

KeyGen(param) → ({sk_i, pk_i}, APK)
    sk_i ←$ Z_q ,  pk_i = g^{sk_i}
    APK  = ∏_i pk_i^{H₁(pk_i)}                        ← O(s), not O(s²)

TagGen({sk_i}, F, m_j, param) → (σ_j, Rs, Hs)
    each owner i:  a_i ←$ Z_q
                   h'_i = g^{H₂(sk_i‖a_i)} ,  r'_i = g^{a_i}
                   σ_ij = H₃(F‖j)^{H₂(sk_i‖a_i)+a_i} · (u^{m_j})^{sk_i·H₁(pk_i)}
    aggregator:    σ_j = ∏_i σ_ij ,  Rs = ∏_i r'_i ,  Hs = ∏_i h'_i

Audit(Q, {m_j,σ_j}, APK, Rs, Hs, param) → {0,1}
    TPA  → CSP :  Q = {(j, v_j)} , c elements
    CSP  → TPA :  DP = Σ m_j·v_j ,  TP = ∏ σ_j^{v_j}
    TPA verifies:  e(TP, g) ?= e(∏ H₃(F‖j)^{v_j}, Hs·Rs) · e(u^{DP}, APK)

OwnerModify, AddOwner(U_in)
    h_i = H₂(sk_i‖a_i)+a_i ,  aux_i = sk_i·H₁(pk_i) − x_i ,  v_i = u^{x_i}
    ODT = (H=Σh_i, AUX=Σaux_i, V=∏v_i) ;  Rs·=∏r'_i ,  Hs·=∏h'_i
    CSP:  σ'_j = σ_j · H₃(F‖j)^H · (u^{m_j})^{AUX} · V^{m_j}

OwnerModify, RevokeOwner(U_out)
    h_i = −H₂(sk_i‖a_i) ,  aux_i = −sk_i·H₁(pk_i) − x_i ,  v_i = u^{x_i}
    Hs ·= g^H       ← Rs is deliberately NOT updated: the revoked owner's
                      mask a_i stays in the tag. This is what protects the
                      exposed index secret.
    CSP updates σ'_j as above.

OwnerTransfer(SG → BG)
    SG:  h_i = −H₂(sk_i‖a_i), aux_i = −sk_i·H₁(pk_i) − x_i, v_i = u^{x_i}
         Hs ·= g^{H_SG} ;  send ODT_SG to BG
    BG:  h_i = H₂(sk_i‖a_i)+a_i, aux_i = sk_i·H₁(pk_i) − x'_i, v_i = u^{x'_i}
         H = H_SG + Σh_i ; AUX = AUX_SG + Σaux_i ; V = V_SG·∏v_i
         Rs ·= ∏r'_i ; Hs ·= ∏h'_i ;  send ODT_BG to CSP
    CSP: σ'_j = σ_j · H₃(F‖j)^H · (u^{m_j})^{AUX} · V^{m_j}
```

The `x_i` blinding in every ODT is not cosmetic: it stops the CSP from learning `Σ sk_i·H₁(pk_i)` while still letting it update tags. Verified by hand: all four update paths preserve the audit equation.

### 3.4 Blocks → `Z_q` elements

A BLS12-381 scalar holds ~255 bits, so a real file block does not fit. Two modes:

- **`raw` mode**, 31-byte blocks mapped directly to `Z_q`. Paper-faithful; used for benchmarks.
- **`hashed` mode (the default for real datasets)**: `m_j = SHA-512(block) mod q` with a configurable block size (default 4 KB). Standard adaptation; possession of the block is still required to compute its hash, so the soundness argument is unaffected. Documented in the report.

### 3.5 Baseline for comparison

To produce the paper's comparison graphs we implement the cost structure of **scheme [8]** (Shen et al., public-key-aggregation authenticator, `auth_i = ∏_{i∈s} f_{sk_i}(pk_i, APK)`), where every owner performs O(s) work during KeyGen and every membership change → **O(s²)**. This is a re-implementation of the baseline's cost model per Table II of the paper, clearly labelled as such, not the original authors' code.

---

## 4. Repository layout

```
project 2 global/
├── PROJECT_PLAN.md              ← this file
├── code/
│   ├── README.md                   setup + run instructions + honest caveats
│   ├── requirements.txt
│   ├── config.py                   block size/mode, challenge size, paths
│   ├── crypto/                     ← THE PROTOCOL. no I/O, no framework.
│   │   ├── pairing.py              BLS12-381 backend; only file importing py_ecc
│   │   ├── hashes.py               H₁, H₂, H₃
│   │   ├── scheme.py               SysGen, KeyGen, TagGen, Audit
│   │   ├── ownership.py            AddOwner, RevokeOwner, OwnerTransfer, ODT
│   │   ├── serialize.py            group elements ↔ bytes
│   │   └── baseline.py             prior-art schemes (comparison + attack target)
│   ├── entities/                   ← the paper's four actors, as readable classes
│   │   ├── csp.py                  Cloud Storage Provider (semi-trusted)
│   │   ├── tpa.py                  Third Party Auditor (sees no data)
│   │   └── group.py                OwnerGroup, serves as both SG and BG
│   ├── datasets/                   ← large synthetic datasets + ownership scenarios
│   │   ├── generate.py             5 scenarios × 4 size tiers, deterministic
│   │   ├── loader.py               file → blocks → Z_q
│   │   └── data/                   generated output (git-ignored)
│   ├── demo/run_demo.py            narrated end-to-end CLI walkthrough
│   ├── attacks/                    collusion, rogue-key, tampering
│   ├── benchmarks/                 measurement harness → JSON → matplotlib PNGs
│   └── tests/                      41 correctness tests
└── report/
    ├── diagrams/                   architecture, DFD 0/1/2, class, sequence, activity
    └── chapters/                   Implementation, Testing, Results
```

Dependency direction is strictly one-way: `crypto/` knows nothing about `entities/`, which knows nothing about `demo/`. `crypto/` can be read standalone and is complete.

---

## 5. The entity layer: the four actors, made legible

Rather than a UI, the four entities of the paper's system model are plain Python classes. A reader can follow exactly who sends what to whom, and (more importantly) who is **not** allowed to know what.

| Class | Trust | Holds | Never receives |
|---|---|---|---|
| `CloudStorageProvider` | semi-trusted | blocks, tags, ODTs | secret keys, masks, un-blinded exponents |
| `ThirdPartyAuditor` | independent | challenges, proofs, APK/Rs/Hs | block contents, any secret |
| `OwnerGroup` (SG / BG) | owners | own `sk_i`, own `a_i` | other owners' secrets |

`CloudStorageProvider` deliberately exposes `corrupt_block()` and `delete_block()`, not bugs, but the attack surface the demonstrations exercise.

This covers the substance of the ten functional requirements in report §3.3; the registration/login and report-export items become a thin presentation layer in a later phase.

---

## 6. Benchmarks (report Results chapter)

Reproducing Figs. 5–8 of the paper, with our own measured numbers:

| Graph | X axis | Compares |
|---|---|---|
| KeyGen | owners `s` = 1…20 | **ours O(s)** vs baseline **O(s²)** |
| TagGen | blocks `n` = 100…1000, `s` = {1,5,10} | scaling in `n` and `s` |
| Proof generation | challenged blocks `c` = 1…460 | linear in `c`, independent of `s` |
| Proof verification | challenged blocks `c` | constant 3 pairings + `c` hashes |
| AddOwner / RevokeOwner | `U_in`={10,30,80} at s=20 · `U_out`={10,50,80} at s=100 | ours depends only on *pending* owners |
| OwnerTransfer | group sizes | constant-size ODT |

`c = 460` comes from the paper's own detection-probability formula `1 − ((n−t)/n)^c ≥ 99%` at `n=1000`, 1% corruption.

---

## 7. Attack demonstrations

Each demo shows the attack **succeeding against the vulnerable prior scheme** and then **failing against ours**: this is what makes the security claims concrete in a viva.

1. **Data tampering**, CSP flips one byte → audit equation fails.
2. **Rogue-key attack**: attacker publishes `pk₂ = g^{α₂}·pk₁⁻¹` to fake a signature "jointly" made with an honest non-participant. Succeeds under plain aggregate BLS; **fails** under `APK = ∏ pk^{H₁(pk)}` because the attacker cannot produce a tag consistent with a `H₁(pk)` exponent for a key it does not hold.
3. **Collusion attack**: buyer + CSP, holding the leaked index secret, forge a tag for **untransacted** data by swapping index `j → j'`. Succeeds against the scheme-[7]/[8] tag `σ = H₀(j)^{sk₁}(u^m)^{sk₂}`; **fails** against ours because the mask `a_i` is never revealed.

---

## 8. Execution order

| # | Task | Output | Status |
|---|---|---|---|
| 1 | Crypto core | All six algorithms, verified correct | done |
| 2 | Dataset generator | 5 scenarios × 4 tiers; 500 MB generated | done |
| 3 | Entity layer | CSP / TPA / OwnerGroup | done |
| 4 | CLI demo | Full lifecycle, 7/7 checks as expected | done |
| 5 | Attack demos | Collusion, rogue-key, tampering, all 3 land | done |
| 6 | Tests | 41 passing | done |
| 7 | Benchmark harness | JSON results + PNG figures | done |
| 8 | Report chapters + diagrams | Ch. 4 diagrams, Implementation, Testing, Results | done |

---

## 9. Honest notes / risks

- **`py_ecc` is pure Python.** Correct and portable, but ~100× slower than the C PBC library the paper's authors used. Absolute timings will exceed the paper's; the **relative** O(s) vs O(s²) shape (which is the actual claim) reproduces correctly, and both schemes are measured on the same primitives so the comparison is fair. If native speed is needed later, `crypto/pairing.py` is a single swappable module. *(Native alternatives were tested and rejected: `blspy` installs but exposes no scalar multiplication, making it unusable for a custom scheme; `petrelic` has no arm64 macOS wheel.)*

- **The paper's detection bound is conservative, and we say so.** It uses `1 − ((n−t)/n)^c`, which models sampling *with* replacement; our auditor samples *without* replacement, whose exact model is hypergeometric. Measured detection meets or beats the paper's bound in every configuration: a small original finding, documented in `attacks/tampering.py` and the Results chapter.
- **Type-1 → Type-3 port** is a deliberate, documented deviation. Correctness of the audit equation under the port was verified algebraically before implementation and is covered by tests.
- The **paper's authors published reference code** at `github.com/chencwu/Scalable-Cloud-Auditing-with-Efficient-Ownership-Transfer-for-Group-Transactions`. We implement independently from the paper's equations; the repo is cited as a reference, not copied.
- The baseline is a **cost-model re-implementation**, not the original scheme-[8] code. Stated plainly wherever its numbers appear.
