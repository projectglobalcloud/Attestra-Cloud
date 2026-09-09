# Viva Preparation: Likely Questions and Answers

Cloud-Based Auditing System with Efficient Ownership Management for Group Data Transactions
(Base paper: Wu, You, Huang — IEEE TIFS vol. 20, 2025)

Keep answers short in the viva. Each entry below is the one-breath answer; the *(more)* pointer says where the full detail lives if pressed.

---

## The problem

**Q. What problem does this project solve?**
Cloud users cannot verify their data is still intact without downloading it, and when a dataset is *sold*, the buyer must genuinely gain — and the seller genuinely lose — the ability to verify it. Provable Data Possession (PDP) solves the first; our DT-PDP scheme solves both. *(more: README "What problem this solves")*

**Q. What was wrong with existing schemes?**
Two things. (1) They treat an owner group as one fixed unit, so key generation and every membership change cost O(s²) in the number of owners. (2) They hand the index secret key to the buyer in the clear, so a buyer colluding with the cloud can forge proofs for data that was never sold. *(more: PROJECT_PLAN §1)*

**Q. And the fix?**
(1) Bind each owner's key with a hash of its own public key — `APK = ∏ pk_i^{H₁(pk_i)}` — giving O(s) and killing rogue-key attacks. (2) Add a secret per-owner random mask `a_i` to the tag exponent and publish only the aggregate `Rs`; even a leaked index secret is then useless. Both are demonstrated by running code, not asserted.

---

## The scheme

**Q. Walk me through an audit.**
The TPA sends a random challenge `Q = {(j, v_j)}`. The cloud returns two values: `DP = Σ m_j·v_j` (a scalar) and `TP = ∏ σ_j^{v_j}` (one group element). The TPA checks one pairing equation: `e(TP, g) = e(∏H₃(F‖j)^{v_j}, Hs·Rs) · e(u^{DP}, APK)`. Three pairings, ~128 bytes of proof, whatever the file size.

**Q. How does ownership transfer work without re-uploading the file?**
Sellers cancel their exponents and buyers add theirs, aggregated into one constant-size (160-byte) token ODT = (H, AUX, V). The cloud rewrites every tag in place: `σ'_j = σ_j · H₃(F‖j)^H · (u^{m_j})^{AUX} · V^{m_j}`. After that the buyer's parameters verify and the seller's fail — ownership changed cryptographically, not in a database row.

**Q. Why does the departing owner's mask stay in the tags? Isn't that a bug?**
It is the entire security argument. A revoked/selling owner cancels only their `H₂` term; the mask `a_i` stays welded into every tag and `Rs` is never decreased. That residue is what makes a leaked index secret useless to a colluding buyer. A dedicated test (`test_masks_are_retained_on_revoke`) pins this so no cleanup can remove it.

**Q. What stops the cloud learning the owners' keys from the token?**
Blinding: each owner contributes `aux_i = sk_i·H₁(pk_i) − x_i` together with `v_i = u^{x_i}`. The cloud can only recombine them inside the exponent of `u`; it never sees the unblinded aggregate.

---

## Implementation decisions

**Q. Which language/library, and why?**
Python 3.12 with `py_ecc` (BLS12-381). Three libraries were evaluated on the target machine: `blspy` exposes no scalar multiplication (signature API only), `petrelic` has no arm64 macOS build; `py_ecc` is pure Python, installs anywhere, and exposes raw group arithmetic. *(more: Ch 5 §5.2.1)*

**Q. The paper uses a symmetric pairing. Yours doesn't. Why, and is that sound?**
Type-1 (symmetric) curves are obsolete and unavailable in maintained libraries. We ported to Type-3 BLS12-381 with one placement rule: published verification material in G₂, tags and their bases in G₁. The audit equation was re-derived algebraically under the port before coding, and tests cover it. *(more: Ch 5 §5.4.1)*

**Q. Your timings are milliseconds; the paper's are microseconds. Explain.**
The authors used C with the PBC library; we use pure Python. The claim under test is *asymptotic* — O(s) vs O(s²) — and both schemes are measured on identical primitives, so the shape comparison is fair. The backend is confined to one file (`crypto/pairing.py`) and is swappable for native bindings. *(more: Ch 7 §7.10)*

**Q. How do real file blocks fit into a 255-bit scalar?**
Default mode hashes each block: `m_j = SHA-512(block) mod q`. Soundness is preserved because producing the hash still requires possessing the block. The paper's 8-byte raw mode is kept for benchmark parity. *(more: Ch 5 §5.6.1)*

**Q. Did you find anything the paper got wrong?**
Two notation inconsistencies (H₁'s codomain, H₂ vs H₃ for the same value) — fixed and documented. And one original finding: the paper's detection bound assumes sampling *with* replacement, but a real auditor samples *without*; the exact model is hypergeometric. Measured detection meets or beats the paper's bound everywhere, so their bound is conservative — a favourable result. *(more: Ch 7 §7.5.3)*

---

## Results

**Q. Your headline number?**
KeyGen at 20 owners: proposed 1.49 s vs baseline 15.5 s — 10.5× — and the speedup grows linearly with s, which is exactly the signature of quadratic-over-linear. Doubling the group 10→20 doubles our cost (1.99×) but nearly quadruples the baseline (3.81×).

**Q. Most striking single measurement?**
Verification is *indistinguishable* between a 1-owner and a 10-owner group — 692.6 ms vs 692.1 ms (0.07%). Once tags aggregate, the auditor cannot tell group size. Auditor workload never grows with the consortium.

**Q. What do the attack demos show?**
Each attack runs twice: it *succeeds* against the prior-art scheme and *fails* against ours. Collusion (buyer+cloud, leaked index secret): prior art broken, ours resists even when the attacker is handed every H₂ value. Rogue-key: plain aggregate BLS broken, hashed-APK resists. Tampering: every challenged corruption detected. *(more: Ch 6 §6.4)*

**Q. Is the baseline comparison fair?**
The baseline is a cost-model re-implementation of scheme [8] per Table II of the paper (the original code is not public), measured on the same curve, backend, process and machine. The asymptotic shape is faithful; we state this everywhere its numbers appear.

---

## Traps to answer honestly

- **"Is any data real?"** No — all five datasets are synthetic and deterministic by seed. No privacy issue exists.
- **"Did you copy the authors' code?"** No — implemented independently from the paper's equations; their repo is cited as related work.
- **"Why no encryption?"** PDP audits *integrity*, not confidentiality; blocks may be encrypted before tagging without changing the scheme. Out of scope by design.
- **"Single point of failure?"** The TPA is stateless — anyone holding APK/Rs/Hs can audit; the design has no trusted dealer at any point (each owner draws its own key; there is no shared secret).

---

## One-line project summary (say this if asked "so what did you build?")

A faithful, working implementation of a 2025 IEEE TIFS cloud-auditing scheme — all six algorithms on BLS12-381 — with 41 passing correctness tests, three attack demonstrations that break the prior art and fail against ours, and measured benchmarks confirming every performance claim of the paper, including the O(s) vs O(s²) headline.
