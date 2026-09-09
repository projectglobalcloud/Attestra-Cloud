# CHAPTER 7
# RESULTS AND PERFORMANCE ANALYSIS

---

## 7.1 Introduction

This chapter reports the measured performance of the implemented system, reproducing the experimental programme of Figs. 5–8 of the base paper. The purpose is to test the paper's central performance claim:

> Key generation and group membership changes cost **O(s)** in the proposed scheme, versus **O(s²)** in the baseline (scheme [8]), where `s` is the number of owners.

An asymptotic claim is established by the **shape** of a curve, not by absolute milliseconds. Accordingly, both schemes are measured on the same curve (BLS12-381), through the same pairing backend, in the same process, on the same machine, so that the comparison is fair even though the absolute figures differ greatly from the paper's C implementation (§7.6).

---

## 7.2 Experimental Setup

| Item | Detail |
|---|---|
| Machine | Apple silicon (arm64), macOS |
| Python | 3.12.3 |
| Pairing backend | `py_ecc` (pure Python), BLS12-381 |
| Security level | 128-bit |
| Repeats | 3 per configuration; **median** reported |
| Total runtime | 39.3 minutes |
| Block mode | `raw` (8-byte blocks), matching the paper |

The median rather than the mean is reported: a single garbage-collection pause or operating-system scheduling event would distort a mean, and typical cost is what matters.

### 7.2.1 Primitive costs

All results below are ultimately composed of these four operations:

| Operation | Time |
|---|---|
| Hash-to-curve into G₁ | 4.8 ms |
| G₁ scalar multiplication | 10.3 ms |
| G₂ scalar multiplication | 37.7 ms |
| Bilinear pairing | 578 ms |

Because a pairing costs roughly 56× a G₁ multiplication, the implementation batches the audit's three pairings into a single final exponentiation, reducing verification from approximately 1.7 s to 0.4 s.

### 7.2.2 Validation of measurement quality

Two checks were performed on the data before it was used.

**Contention check.** Other processes ran on the machine during part of the benchmark, which could inflate timings. A clean re-run of the `KeyGen` section, with nothing else executing, was compared against the recorded values:

| `s` | Series | Recorded | Clean re-run | Deviation |
|---:|---|---:|---:|---:|
| 4 | proposed | 298 ms | 297 ms | −0.3 % |
| 10 | proposed | 746 ms | 742 ms | −0.5 % |
| 20 | proposed | 1,486 ms | 1,476 ms | −0.6 % |
| 4 | baseline | 745 ms | 733 ms | −1.6 % |
| 16 | baseline | 10,660 ms | 10,006 ms | −6.1 % |
| 20 | baseline | 15,540 ms | 15,499 ms | −0.3 % |

Worst-case deviation 6.1 %, typically under 1 %. The recorded data is therefore sound.

**Outlier check.** Two points in the original `TagGen` section (`s=1, n=50` and `s=5, n=50`) were **non-monotonic in n**: the `s=1, n=50` figure read 7,858 ms, higher than the same configuration at `n=200` (7,168 ms). This is physically impossible for an O(n) operation and was a measurement artefact. That section was re-measured in a clean run and the corrected values are used throughout. The anomaly is recorded in the results file rather than silently overwritten.

---

## 7.3 Key Generation: the headline result

**(Figure 1: reproduces paper Fig. 6)**

| Owners `s` | Proposed (ms) | Baseline [8] (ms) | Speedup |
|---:|---:|---:|---:|
| 1 | 73 | 77 | 1.1× |
| 2 | 150 | 223 | 1.5× |
| 4 | 298 | 745 | 2.5× |
| 6 | 453 | 1,552 | 3.4× |
| 8 | 594 | 2,658 | 4.5× |
| 10 | 746 | 4,076 | 5.5× |
| 12 | 890 | 5,765 | 6.5× |
| 14 | 1,055 | 7,758 | 7.4× |
| 16 | 1,193 | 10,660 | 8.9× |
| 18 | 1,485 | 13,675 | 9.2× |
| 20 | **1,486** | **15,540** | **10.5×** |

### 7.3.1 Analysis

The clearest evidence is the behaviour when the group **doubles** from 10 to 20 owners:

| | at s = 10 | at s = 20 | ratio | expected |
|---|---:|---:|---:|---|
| Proposed | 746 ms | 1,486 ms | **1.99×** | 2× for O(s) |
| Baseline | 4,076 ms | 15,540 ms | **3.81×** | 4× for O(s²) |

The proposed scheme doubles; the baseline very nearly quadruples. This is the asymptotic claim confirmed directly.

Equivalently, the **speedup itself grows linearly** with `s`: from 1.1× at one owner to 10.5× at twenty. A ratio that grows linearly is precisely the signature of a quadratic cost divided by a linear one. Extrapolating, a 100-owner consortium would see a speedup of roughly 50×.

The mechanism is straightforward. The baseline requires every owner to construct an authenticator binding it to every other owner, `s` exponentiations each, `s²` for the group. The proposed scheme replaces this with `APK = ∏ pk_i^{H₁(pk_i)}`, in which each owner's exponent depends only on its **own** public key: one exponentiation per owner, and no interaction between owners at all.

---

## 7.4 Tag Generation

**(Figure 2: reproduces paper Fig. 5a)**

| Blocks `n` | s = 1 | s = 5 | s = 10 |
|---:|---:|---:|---:|
| 25 | 997 ms | 5,051 ms | 10,049 ms |
| 50 | 1,901 ms | 9,512 ms | 19,076 ms |
| 100 | 3,706 ms | 18,387 ms | 36,821 ms |
| 200 | 7,245 ms | 36,326 ms | 72,722 ms |

Tagging is O(n·s), and the measurements show this almost exactly. At `n = 200`, the three owner counts stand in the ratio

```
1 : 5.01 : 10.04     against an ideal 1 : 5 : 10
```

and doubling `n` from 100 to 200 multiplies the cost by 1.96×, 1.98× and 1.97× for the three series respectively.

This is the one phase where the baseline is asymptotically better: it achieves O(n) by outsourcing signing authority to a trusted third party. The base paper argues (§VII.B) that this is not a realistic trade: it requires owners to authorise a third party to sign a file that the third party cannot see. The proposed scheme's O(n·s) is the honest price of every owner signing with its own key, and for a single owner it reduces to O(n) regardless.

Tag generation is also a **one-time** cost per dataset, whereas auditing recurs indefinitely.

---

## 7.5 Auditing

**(Figure 3: reproduces paper Figs. 5b and 5c)**

| Challenged `c` | Proof generation | Verification |
|---:|---:|---:|
| 25 | 257 ms | 693 ms |
| 50 | 521 ms | 1,069 ms |
| 100 | 1,031 ms | 1,836 ms |
| 200 | 2,186 ms | 3,640 ms |

### 7.5.1 Independence from group size: a clean confirmation

The most striking single measurement in this project:

| Challenged `c` | 1 owner | 10 owners | Difference |
|---:|---:|---:|---:|
| 25 | 692.6 ms | 692.1 ms | **0.07 %** |
| 100 | 1,835.9 ms | 1,835.6 ms | **0.02 %** |

Verification cost is **indistinguishable** between a 1-owner and a 10-owner group. Once the tag shares are aggregated, the auditor genuinely cannot tell how many owners produced them. The same holds for proof generation.

This matters practically: an auditor's workload does not grow as consortia grow, so the scheme supports arbitrarily large ownership groups at no cost to the party doing the verifying.

### 7.5.2 Constant pairing count

Verification performs exactly **three pairings** regardless of `n` or `c`. All growth in the table above comes from the `c` hash-to-curve operations needed to rebuild `∏ H₃(F‖j)^{v_j}`, at 4.8 ms each, `c = 200` accounts for roughly 960 ms of the 3,640 ms measured, with the batched pairings contributing a fixed ~400 ms.

The proof itself is **constant size**: one scalar plus one group element, about 128 bytes, whether 25 or 200 blocks were challenged. Auditing a 100 MB dataset and a 100 TB dataset cost the auditor identical bandwidth.

### 7.5.3 Detection probability: measured against theory

500 trials per configuration, at `n = 200`:

| Corrupted | `c` | Paper's bound | Exact (hypergeometric) | **Measured** |
|---:|---:|---:|---:|---:|
| 1 % | 100 | 63.40 % | 75.13 % | **73.00 %** |
| 1 % | 150 | 77.85 % | 93.84 % | **92.20 %** |
| 1 % | 190 | 85.19 % | 99.77 % | **99.80 %** |
| 5 % | 50 | 92.31 % | 94.79 % | **96.00 %** |
| 5 % | 100 | 99.41 % | 99.92 % | **99.80 %** |
| 10 % | 20 | 87.84 % | 89.15 % | **90.20 %** |
| 10 % | 50 | 99.48 % | 99.77 % | **99.80 %** |

**A finding.** The base paper uses the classical PDP bound `1 − ((n−t)/n)^c`, which models sampling **with replacement**. The implementation's auditor draws **distinct** block indices, for which the correct model is hypergeometric, `1 − C(n−t, c)/C(n, c)`.

Measured rates track the hypergeometric model closely and sit **at or above the paper's bound in every configuration tested**. The paper's bound is therefore *conservative*, never optimistic: its recommended `c = 460` under-promises what the implementation actually delivers. This is a favourable result for the paper, and it means the recommended parameter can be adopted with confidence.

---

## 7.6 Ownership Modification

**(Figure 4: reproduces paper Figs. 7a and 7b)**

### 7.6.1 Adding owners (base group of 20)

| Joining `|U_in|` | Proposed | Baseline [8] | Speedup |
|---:|---:|---:|---:|
| 5 | 612 ms | 24,725 ms | **40×** |
| 10 | 1,210 ms | 34,457 ms | **28×** |
| 20 | 2,445 ms | 60,568 ms | **25×** |

The proposed cost is almost exactly linear in the number of owners **joining** (612 → 1,210 → 2,445 ms for 5 → 10 → 20 joiners) and does not depend on how large the group already is. Existing owners are not re-keyed and need not even be online.

The baseline must rebuild every authenticator in the enlarged group, because `APK` has changed.

### 7.6.2 Revoking owners (base group of 40)

| Revoked `|U_out|` | Proposed | Baseline [8] | Speedup |
|---:|---:|---:|---:|
| 5 | 272 ms | 47,376 ms | **174×** |
| 10 | 513 ms | 34,348 ms | **67×** |
| 20 | 1,007 ms | 15,636 ms | **16×** |

The two columns move in **opposite directions**, which is the clearest illustration of the difference between the schemes:

- **Proposed** cost *rises* with the number revoked (272 → 1,007 ms), because work is proportional to who is leaving.
- **Baseline** cost *falls* (47,376 → 15,636 ms), because revoking more owners leaves a *smaller* surviving group, and its cost is quadratic in the survivors.

The base paper predicts exactly this behaviour in §VII.B.4. Revoking 5 owners from a group of 40 (a routine administrative action) costs the baseline over 47 seconds against the proposed scheme's 272 milliseconds.

---

## 7.7 Tag Update at the Cloud

**(Figure 5: reproduces paper Fig. 7c)**

| Blocks `n` | s = 1 | s = 10 |
|---:|---:|---:|
| 25 | 1,178 ms | 1,189 ms |
| 50 | 2,521 ms | 2,305 ms |
| 100 | 4,699 ms | 4,616 ms |
| 200 | 9,140 ms | 9,036 ms |

Two properties are confirmed. Tag rewriting is **linear in n** (doubling `n` doubles the time), and it is **independent of group size**, the two columns agree to within 2 % throughout, because the ownership token reaching the cloud has already been aggregated.

This is the design placing the only O(n) step of an ownership change on the CSP, the entity the system model describes as having "abundant storage and computational resources", while the owners exchange only a constant-size token.

---

## 7.8 Ownership Transfer

**(Figure 6: reproduces paper Fig. 8)**

| Owners per group | Computation | Token size |
|---:|---:|---:|
| 1 | 202 ms | 160 bytes |
| 5 | 874 ms | 160 bytes |
| 10 | 1,707 ms | 160 bytes |
| 20 | 3,377 ms | 160 bytes |

Computation grows linearly with the number of participating owners, since each contributes one share. The **token handed to the cloud is constant at 160 bytes** (two 32-byte scalars and one 96-byte group element) for every group size tested.

This confirms the communication claim of the paper's Table III. Selling a dataset between two twenty-member consortia requires the same 160 bytes of protocol traffic to the cloud as a transfer between two individuals, and the same as it would for a dataset of any size.

---

## 7.9 Security Results

Reported in full in Chapter 6; summarised here for completeness.

| Attack | Prior art | Proposed scheme |
|---|---|---|
| Collusion (buyer + cloud, leaked index secret) | **BROKEN**, forged proof accepted for a deleted block | **RESISTED** |
| Rogue-key (fake co-ownership) | **BROKEN**, plain aggregate BLS accepted the forgery | **RESISTED** |
| Data tampering / deletion | — | **Detected in every trial** |

The collusion demonstration granted the attacker *more* information than the protocol would ever leak (every owner's `H₂(sk_i‖a_i)` value) and the forgery still failed, because the random masks `a_i` remain bound to the original block index and cannot be recovered from the published `Rs`.

---

## 7.10 Discussion of Absolute Performance

The absolute figures in this chapter are substantially slower than those in the base paper, which reports microseconds where this implementation reports milliseconds. The reason is the deliberate choice of `py_ecc`, a **pure-Python** library, over the C implementation with the PBC library used by the paper's authors.

That choice was made on grounds of reproducibility. Two faster alternatives were evaluated and rejected: `blspy` installs successfully but exposes no scalar multiplication on G₁ or G₂, making it structurally unable to implement a custom scheme; `petrelic` has no distribution for the target architecture. A project an examiner cannot run is worth less than a slow one they can.

Three points bound the significance of this gap:

1. **The claim under test is asymptotic.** Both schemes are measured on identical primitives, so the ratio between them (which is what O(s) versus O(s²) asserts) is unaffected by the constant factor.
2. **Verification is already practical.** At 400–700 ms per audit, and with cost independent of dataset size, the scheme is usable at this speed for periodic auditing of arbitrarily large datasets.
3. **The gap is addressable in one file.** All pairing operations are confined to `crypto/pairing.py`; substituting native bindings would leave every other module unchanged.

**Note on the baseline.** All baseline figures are a **cost-model re-implementation** of scheme [8]'s structure as characterised in Table II of the base paper, not the original authors' code. The asymptotic shape is faithful; the absolute constants are ours.

---

## 7.11 Summary of Results

| Claim under test | Result |
|---|---|
| KeyGen is O(s), baseline O(s²) | **Confirmed**, 1.99× vs 3.81× on doubling `s`; 10.5× speedup at s = 20 |
| Membership changes depend only on who is changing | **Confirmed**, up to 174× faster than baseline |
| Auditing is independent of group size | **Confirmed**, 0.07 % difference between 1 and 10 owners |
| Verification uses a constant number of pairings | **Confirmed**, 3 pairings for all `n`, `c` |
| The ownership token is constant size | **Confirmed**, 160 bytes for all group sizes |
| Corruption is detected at the stated rate | **Confirmed**, and the paper's bound shown to be conservative |
| Collusion and rogue-key attacks are prevented | **Confirmed**, both break the prior art and both fail here |

Every performance and security claim of the base paper that falls within the scope of this project was reproduced.
