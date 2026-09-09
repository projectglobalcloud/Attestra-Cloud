# CHAPTER 5
# IMPLEMENTATION

> **Note on chapter numbering.** The drafted report ends at Chapter 5 (Conclusion). This chapter and the two that follow are inserted as Chapters 5, 6 and 7, and the existing Conclusion becomes Chapter 8.

---

## 5.1 Introduction

The implementation phase translates the design of Chapter 4 into working software. The objective was not to produce a demonstration that merely *looks* like cloud auditing, but to implement the cryptographic protocol of the base paper faithfully enough that its security claims can be tested by running code.

The system implements all six algorithms of the base paper, `SysGen`, `KeyGen`, `TagGen`, `Audit`, `OwnerModify` (comprising `AddOwner` and `RevokeOwner`) and `OwnerTransfer`, together with the four entities of its system model, five large sample datasets, a benchmark suite and three executable attack demonstrations.

---

## 5.2 Implementation Environment

| Item | Detail |
|---|---|
| Programming language | Python 3.12 |
| Cryptographic library | `py_ecc` 7.0 (BLS12-381 pairings) |
| Plotting | `matplotlib` 3.11 |
| Testing framework | `pytest` 7.4 |
| Version control | Git |
| Development machine | Apple silicon (arm64), macOS |
| Total dependencies | 3 |

Python was chosen from the two languages permitted by the requirements in §3.2. The dependency count is deliberately small: the protocol itself is implemented from first principles, and no library performs any part of the scheme on the project's behalf.

### 5.2.1 Selection of the cryptographic library

Three candidate libraries were evaluated on the target machine before selecting one.

| Library | Outcome |
|---|---|
| `blspy` (Chia) | Installs successfully, but exposes **no scalar multiplication** on G₁ or G₂ (`G1Element.__mul__` does not exist). It is a signature-scheme API, not a general pairing library, and therefore cannot implement a custom scheme. **Rejected.** |
| `petrelic` (RELIC bindings) | No distribution available for arm64 macOS. **Rejected.** |
| `py_ecc` | Pure Python; installs with no compiler and no system libraries; exposes generic group arithmetic and a raw pairing. **Selected.** |

The trade-off is performance, discussed honestly in §7.5.

---

## 5.3 Modular Organisation

The codebase is organised so that the protocol can be read on its own, with no framework or storage concerns interleaved.

```
code/
├── crypto/        the protocol, no I/O, no framework, no dependencies beyond py_ecc
├── entities/      the four actors of the system model, as readable classes
├── datasets/      large synthetic datasets with ownership scenarios
├── demo/          narrated end-to-end walkthrough
├── attacks/       three executable attack demonstrations
├── benchmarks/    measurement harness and figure rendering
└── tests/         41 correctness tests
```

The dependency direction is strictly one-way, `crypto/` does not know that `entities/` exists, and `entities/` does not know that `demo/` exists. A reader can therefore study the cryptography in isolation and find it complete.

---

## 5.4 Implementation of the Cryptographic Core

### 5.4.1 Pairing backend and the Type-1 → Type-3 port

The base paper assumes a **symmetric (Type-1)** bilinear pairing `e: G₁ × G₁ → G_T`, as provided by the PBC library's Type-A curves. Type-1 curves are now considered obsolete and are not available in any maintained Python library.

The scheme was therefore ported to **BLS12-381**, an **asymmetric (Type-3)** curve `e: G₁ × G₂ → G_T`. The port follows a single placement rule:

> Every element that is raised to a secret and then published as a verification parameter is placed in **G₂**; the tag and its bases are placed in **G₁**.

| Element | Group |
|---|---|
| `u`, `H₃(F‖j)`, tag `σ_j`, proof `TP` | G₁ |
| generator `g`, `pk_i`, `APK`, `Rs`, `Hs` | G₂ |

Under this placement every pairing in the scheme is a well-formed G₁ × G₂ pairing. The correctness of the audit equation was re-derived algebraically before any code was written, and is reproduced in §5.4.4.

All pairing operations are confined to a single module, `crypto/pairing.py`. No other file imports `py_ecc`. Replacing the backend with a native implementation would therefore be a one-file change.

### 5.4.2 Correction of the paper's hash-function notation

The base paper is internally inconsistent in its use of hash functions. Section V.B declares

```
H₁ : {0,1}* → G₁        H₂ : {0,1}* → Z_p
```

but then uses `H₁(pk_i)` as an **exponent** (`APK = ∏ pk_i^{H₁(pk_i)}`), which requires `H₁` to map into `Z_q`. It also uses the same value as `H₂(F‖j)` in `TagGen` and as `H₃(F‖j)` in the verification equation and in the correctness proof of Theorem 5.

The implementation uses the assignment the equations actually require, with three distinctly named functions:

| Function | Codomain | Purpose |
|---|---|---|
| `H₁` | `Z_q` | `H₁(pk_i)`, binds an owner's key pair |
| `H₂` | `Z_q` | `H₂(sk_i‖a_i)`, the masked index exponent |
| `H₃` | `G₁` | `H₃(F‖j)`, hash-to-curve of the block index |

This is a correction of notation, not a modification of the scheme: every equation in the paper is reproduced exactly under this reading.

Two implementation details are worth recording. `H₁` and `H₂` reduce a **SHA-512** digest modulo `q`; using SHA-256 against a 255-bit `q` would introduce measurable modular bias, whereas a 512-bit digest reduces the bias below 2⁻²⁵⁵. `H₃` uses the RFC 9380 SSWU hash-to-curve construction, which is indifferentiable from a random oracle: the assumption under which Theorems 1–4 of the paper are stated. All three use domain-separation tags and length-prefixed inputs, so `H(a‖b)` is unambiguous.

### 5.4.3 Independence of the generators

`u` must not be a known power of `g`. If any party knew `log_g(u)`, the unforgeability reduction of Theorem 2 would collapse and that party could forge tags for arbitrary blocks.

The implementation derives `u` by hash-to-curve from a fixed public string, a construction known as *nothing-up-my-sleeve*. Because `u` is the output of a hash rather than a chosen value, no party (including the implementers) can know the discrete logarithm relating it to `g`.

### 5.4.4 The six algorithms

**SysGen.** Fixes the curve, the two generators and the hash functions.

**KeyGen.** Each owner independently draws `sk_i ← Z_q` and publishes `pk_i = g^{sk_i}`, together with a secret random mask `a_i`. There is no dealer, no shared secret and no interaction between owners. The group's aggregated public key is

```
APK = ∏ᵢ pk_i^{H₁(pk_i)}
```

Binding each public key with a hash of itself is what makes the scheme resistant to rogue-key attacks, at a cost of **one exponentiation per owner**, O(s). The baseline requires each owner to construct an authenticator against every other owner, costing O(s²).

**TagGen.** Each owner computes, for each block,

```
σ_ij = H₃(F‖j)^{H₂(sk_i‖a_i) + a_i} · (u^{m_j})^{sk_i·H₁(pk_i)}
```

and the shares are aggregated into one tag per block, `σ_j = ∏ᵢ σ_ij`. Only the aggregates `Rs = ∏ g^{a_i}` and `Hs = ∏ g^{H₂(sk_i‖a_i)}` are published; the individual masks `a_i` never are.

**Audit.** The TPA samples a challenge `Q = {(j, v_j)}` of `c` elements; the CSP replies with `DP = Σ m_j v_j` and `TP = ∏ σ_j^{v_j}`; the TPA verifies

```
e(TP, g) = e(∏ H₃(F‖j)^{v_j}, Hs·Rs) · e(u^{DP}, APK)
```

*Correctness under the Type-3 port.* Writing `A = Σᵢ (H₂(sk_i‖a_i) + a_i)` and `B = Σᵢ sk_i·H₁(pk_i)`, so that `σ_j = H₃(F‖j)^A · (u^{m_j})^B`:

```
TP = ∏ⱼ σ_j^{v_j} = (∏ⱼ H₃(F‖j)^{v_j})^A · u^{B·Σⱼ m_j v_j}
                  = (∏ⱼ H₃(F‖j)^{v_j})^A · u^{B·DP}

e(TP, g) = e(∏ⱼ H₃(F‖j)^{v_j}, g^A) · e(u^{DP}, g^B)
         = e(∏ⱼ H₃(F‖j)^{v_j}, Hs·Rs) · e(u^{DP}, APK)
```

since `Hs·Rs = g^A` and `APK = g^B`. ∎

**OwnerModify and OwnerTransfer.** Every ownership operation reduces to a constant-size **Ownership Dynamic Token** `ODT = (H, AUX, V)`, with which the CSP rewrites each tag in place:

```
σ′_j = σ_j · H₃(F‖j)^H · (u^{m_j})^{AUX} · V^{m_j}
```

Three properties of the token are deliberate:

1. **Constant size.** `H` and `AUX` are scalars and `V` is one group element, 160 bytes in total, independent of both `n` and `s`. The O(n) rewriting work falls on the CSP, which the system model describes as the party with "abundant storage and computational resources".
2. **Blinding.** Each owner splits its contribution as `aux_i = sk_i·H₁(pk_i) − x_i` with `v_i = u^{x_i}`, so `AUX` alone never reveals the group's aggregate signing exponent. The CSP recombines the parts only inside the exponent of `u`.
3. **Masks are never subtracted.** A joining owner contributes `H₂(sk_i‖a_i) + a_i`; a leaving owner cancels only `H₂(sk_i‖a_i)`, and `Rs` is left unchanged. The departing owner's mask therefore remains embedded in every tag permanently.

The third property is the security-critical one, and it is the reason the scheme resists the collusion attack. It is pinned by a dedicated test (§6.4) so that a future refactoring cannot silently remove it under the impression that it is an inconsistency.

---

## 5.5 Implementation of the System Entities

The four entities of the system model are implemented as classes whose boundaries encode the trust model.

| Class | Trust level | Holds | Never receives |
|---|---|---|---|
| `CloudStorageProvider` | semi-trusted | blocks, tags, tokens | secret keys, masks, un-blinded exponents |
| `ThirdPartyAuditor` | independent | challenges, proofs, `APK`/`Rs`/`Hs` | block contents, any secret |
| `OwnerGroup` (SG and BG) | owners | own `sk_i` and `a_i` | other owners' secrets |

`OwnerGroup` serves as both the Sale Group and the Buy Group, because "selling" and "buying" are roles within a single transaction rather than permanent properties: a research institute that buys a dataset today is the sale group when it resells tomorrow.

`CloudStorageProvider` deliberately exposes `corrupt_block()` and `delete_block()`. These are not defects but the modelled attack surface of a semi-trusted cloud, and they are exercised by the demonstrations in Chapter 6.

---

## 5.6 Sample Datasets

The base paper motivates itself with a concrete situation: organisations such as Kaggle and Zenodo hold large AI-related datasets and **sell** them to other organisations. Demonstrating that requires datasets that plausibly get sold: large, owned by a consortium rather than an individual, and carrying provenance that must survive the transfer. No public corpus of "datasets with transferable ownership records" exists, so five were generated.

| Dataset | Sale Group → Buy Group |
|---|---|
| `medical-imaging` | 3 hospitals → medical-AI research institute |
| `iot-telemetry` | city transport dept + 2 agencies → urban analytics firm |
| `financial-ledger` | regional bank → statutory audit firm |
| `genomics-variants` | 3 sequencing labs → pharmaceutical company |
| `ai-training-corpus` | data-labelling vendor → autonomous-systems AI lab |

Available at four size tiers (2 MB, 25 MB, 100 MB and 512 MB) sharded into 8 MB parts in the manner of a real data lake, so that a buyer can audit part of a purchase without retrieving the whole archive. 500 MB was generated for this project.

**All data is synthetic.** No real clinical, financial or personal record is used or reproduced; the generators emit statistically plausible but entirely fabricated records. This is what a research demonstration requires and it avoids every privacy concern that real data would raise. Generation is deterministic: the same seed reproduces a dataset byte for byte, so audits and benchmarks remain reproducible.

### 5.6.1 Mapping file blocks into Z_q

A BLS12-381 scalar holds approximately 255 bits, so a real file block cannot be embedded directly. Two modes are provided:

- **`hashed`** (default), `m_j = SHA-512(block) mod q`, for any block size. Soundness is preserved, because computing the hash still requires possessing the block: a cloud that has discarded the data cannot produce `m_j` and therefore cannot produce a valid `DP`.
- **`raw`**, `m_j = int(block) mod q` for blocks of at most 31 bytes. This is the mode the base paper uses (8-byte blocks) and is used by the benchmark suite for parity.

### 5.6.2 The significance of block size

Protocol cost is driven by the **number of blocks** `n`, not by file size:

```
tagging   = n · s group exponentiations
storage   = n · 96 bytes of tags
auditing  = c hash-to-curve operations + 3 pairings
```

At the default 256 KB block size, a 100 MB dataset is 400 blocks. Security is unaffected by this choice: the detection bound depends on the *fraction* of blocks corrupted, not on their size, so challenging a fixed number of blocks detects a given corruption rate equally well at any block size. Larger blocks in fact mean that an attacker who corrupts one block has damaged more bytes.

---

## 5.7 Summary

The implementation covers all six algorithms of the base paper, ported to a modern pairing curve with the correctness of the audit equation re-derived under that port. Two ambiguities in the paper's notation were identified and resolved. The cryptographic core is isolated from all I/O concerns, and the pairing backend is confined to a single replaceable module.

The next chapter reports the testing performed against this implementation, including the three attack demonstrations that test the paper's security claims directly.
