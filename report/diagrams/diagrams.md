# Chapter 4: System Design Diagrams

All diagrams are written in **Mermaid**, and every one is already rendered as a high-resolution PNG in `images/` (one file per figure, named after its figure number), ready to place in the bound report. The individual Mermaid sources are in `src/`.

To re-render after editing a diagram:

```bash
npx -y @mermaid-js/mermaid-cli -i src/<name>.mmd -o images/<name>.png -b white -s 3
```

(or paste a code block into <https://mermaid.live> and export manually.)

Keeping the diagrams as text (rather than only as images) means they stay editable and stay in step with the code as it changes.

---

## Figure 4.1.1: System Architecture

The four entities of the paper's system model, and what actually crosses each boundary. Note that no secret material ever reaches the CSP or the TPA.

```mermaid
flowchart TB
    subgraph SG["Sale Group (SG): s data owners"]
        SG1["Owner 1<br/>sk₁, a₁"]
        SG2["Owner 2<br/>sk₂, a₂"]
        SG3["Owner s<br/>sk_s, a_s"]
    end

    subgraph BG["Buy Group (BG): s′ data owners"]
        BG1["Owner 1′<br/>sk₁′, a₁′"]
        BG2["Owner s′<br/>sk_s′, a_s′"]
    end

    CSP["<b>Cloud Storage Provider</b><br/>semi-trusted<br/><br/>stores blocks m_j<br/>stores tags σ_j<br/>generates proofs<br/>applies ODTs"]
    TPA["<b>Third Party Auditor</b><br/>independent<br/><br/>issues challenges Q<br/>verifies proofs<br/>issues audit reports"]

    SG1 & SG2 & SG3 -->|"tag shares σ_ij"| AGG(["aggregate<br/>σ_j = ∏ σ_ij"])
    AGG -->|"upload {m_j, σ_j}"| CSP
    AGG -.->|"publish APK, Rs, Hs"| TPA

    TPA -->|"challenge Q = {(j, v_j)}"| CSP
    CSP -->|"proof P = (DP, TP)"| TPA

    SG1 & SG2 & SG3 -->|"ODT_SG"| BG1
    BG1 & BG2 -->|"ODT_BG = (H, AUX, V)"| CSP
    BG1 -.->|"publish updated APK, Rs, Hs"| TPA

    classDef cloud fill:#e8f0fb,stroke:#2a78d6,stroke-width:2px
    classDef audit fill:#fdeee7,stroke:#eb6834,stroke-width:2px
    classDef owner fill:#e7f6f0,stroke:#1baf7a,stroke-width:1px
    class CSP cloud
    class TPA audit
    class SG1,SG2,SG3,BG1,BG2 owner
```

**Key point for the viva:** the only things the TPA ever sees are `APK`, `Rs`, `Hs`, the challenge it chose itself, and a constant-size proof. It cannot reconstruct any block, which is why this is *privacy-preserving* public auditing.

---

## Figure 4.2.1: Data Flow Diagram (Level 0 / Context)

```mermaid
flowchart LR
    DO(["Data Owners<br/>(SG / BG)"])
    SYS["<b>0.0</b><br/>Cloud Auditing System<br/>with Ownership Transfer"]
    CLOUD(["Cloud Storage<br/>Provider"])
    AUD(["Third Party<br/>Auditor"])

    DO -->|"data blocks, key material,<br/>ownership requests"| SYS
    SYS -->|"tags, ownership tokens,<br/>transfer confirmations"| DO

    SYS -->|"blocks + tags, ODTs"| CLOUD
    CLOUD -->|"storage proofs"| SYS

    SYS -->|"verification parameters,<br/>proofs"| AUD
    AUD -->|"challenges, audit verdicts"| SYS
```

---

## Figure 4.2.2: Data Flow Diagram (Level 1)

```mermaid
flowchart TB
    DO(["Data Owners"])
    CLOUD[("D1: Block &<br/>Tag Store")]
    PARAMS[("D2: Verification<br/>Parameters")]
    LOG[("D3: Audit Log")]
    AUD(["TPA"])

    P1["<b>1.0</b><br/>Key Generation<br/>sk_i, pk_i, APK"]
    P2["<b>2.0</b><br/>Tag Generation<br/>σ_ij → σ_j"]
    P3["<b>3.0</b><br/>Integrity Auditing<br/>challenge / proof / verify"]
    P4["<b>4.0</b><br/>Ownership Modification<br/>Add / Revoke"]
    P5["<b>5.0</b><br/>Ownership Transfer<br/>SG → BG"]
    P6["<b>6.0</b><br/>Report Generation"]

    DO --> P1
    P1 -->|"APK"| PARAMS
    P1 -->|"key pairs"| P2

    DO -->|"file blocks"| P2
    P2 -->|"{m_j, σ_j}"| CLOUD
    P2 -->|"Rs, Hs"| PARAMS

    AUD --> P3
    CLOUD -->|"blocks + tags"| P3
    PARAMS -->|"APK, Rs, Hs"| P3
    P3 -->|"verdict"| LOG

    DO -->|"add / revoke request"| P4
    P4 -->|"ODT"| CLOUD
    P4 -->|"updated Rs, Hs, APK"| PARAMS

    DO -->|"sale agreement"| P5
    P5 -->|"ODT_SG → ODT_BG"| CLOUD
    P5 -->|"updated parameters"| PARAMS

    LOG --> P6
    P6 -->|"audit report"| AUD
```

### Level 2: expansion of process 5.0 (Ownership Transfer)

```mermaid
flowchart LR
    SG(["Sale Group"])
    BG(["Buy Group"])
    CSP(["CSP"])

    P51["<b>5.1</b><br/>SG cancels its exponents<br/>h_i = −H₂(sk_i‖a_i)<br/>aux_i = −sk_i·H₁(pk_i) − x_i"]
    P52["<b>5.2</b><br/>Aggregate ODT_SG<br/>update Hs (Rs untouched)"]
    P53["<b>5.3</b><br/>BG adds exponents<br/>+ fresh masks a_i′"]
    P54["<b>5.4</b><br/>Fold ODT_SG into ODT_BG<br/>update Rs, Hs, APK"]
    P55["<b>5.5</b><br/>CSP rewrites every tag<br/>σ′_j = σ_j·H₃(F‖j)^H·(u^{m_j})^{AUX}·V^{m_j}"]

    SG --> P51 --> P52 -->|"ODT_SG"| P53
    BG --> P53 --> P54 -->|"ODT_BG = (H, AUX, V)"| P55
    P55 --> CSP
```

> **Why Rs is not updated in 5.2**: the sale group's random masks `a_i` stay embedded in every tag permanently. This is the mechanism that defeats the collusion attack: even a buyer who learns the index secret cannot forge tags, because the seller's masks remain and cannot be removed.

---

## Figure 4.3.1: Class Diagram

Mirrors the actual module structure in `code/`.

```mermaid
classDiagram
    class SystemParams {
        +int q
        +G2Point g
        +G1Point u
    }

    class OwnerKey {
        +int sk
        +G2Point pk
        +int a
        +str owner_id
        +h1() int
        +h2() int
        +apk_share() G2Point
        +h_share() G2Point
        +r_share() G2Point
    }

    class GroupState {
        +G2Point apk
        +G2Point rs
        +G2Point hs
        +bytes file_abstract
        +int n_blocks
        +list owner_ids
    }

    class Challenge {
        +tuple pairs
        +size() int
    }

    class Proof {
        +int dp
        +G1Point tp
    }

    class OwnershipToken {
        +int h
        +int aux
        +G1Point v
        +combine(other) OwnershipToken
    }

    class OwnerGroup {
        +str group_id
        +list~DataOwner~ members
        +create(param, id, ids)$ OwnerGroup
        +tag_file(F, blocks)
        +add_members(state, ids)
        +revoke_members(state, ids)
        +initiate_transfer(state)
        +accept_transfer(state, token)
    }

    class DataOwner {
        +str owner_id
        +OwnerKey key
        +str display_name
    }

    class CloudStorageProvider {
        +store(id, F, blocks, tags)
        +generate_proof(id, challenge) Proof
        +apply_ownership_token(id, token)
        +corrupt_block(id, index)
    }

    class ThirdPartyAuditor {
        +list~AuditRecord~ records
        +issue_challenge(n, c) Challenge
        +verify(state, chal, proof) bool
        +audit(csp, id, state, c) AuditRecord
        +detection_probability(n, c, rate)$ float
        +report() str
    }

    class StoredFile {
        +str file_id
        +bytes file_abstract
        +list blocks
        +list tags
        +int tag_version
    }

    class AuditRecord {
        +str file_id
        +bool passed
        +int challenged_blocks
        +float detection_probability
        +float duration_ms
    }

    OwnerGroup "1" *-- "many" DataOwner
    DataOwner "1" *-- "1" OwnerKey
    OwnerGroup ..> GroupState : produces
    OwnerGroup ..> OwnershipToken : produces
    CloudStorageProvider "1" *-- "many" StoredFile
    CloudStorageProvider ..> Proof : generates
    CloudStorageProvider ..> OwnershipToken : consumes
    ThirdPartyAuditor "1" *-- "many" AuditRecord
    ThirdPartyAuditor ..> Challenge : issues
    ThirdPartyAuditor ..> GroupState : verifies against
    SystemParams <.. OwnerGroup : uses
    SystemParams <.. CloudStorageProvider : uses
    SystemParams <.. ThirdPartyAuditor : uses
```

---

## Figure 4.3.2: Use Case Diagram

```mermaid
flowchart LR
    OWNER(("Data Owner<br/>SG / BG"))
    CSPA(("Cloud Storage<br/>Provider"))
    TPAA(("Third Party<br/>Auditor"))
    ATT(("Attacker"))

    UC1(["Generate key pair"])
    UC2(["Upload & tag dataset"])
    UC3(["Add group member"])
    UC4(["Revoke group member"])
    UC5(["Initiate ownership transfer"])
    UC6(["Accept ownership transfer"])
    UC7(["Store blocks & tags"])
    UC8(["Generate storage proof"])
    UC9(["Apply ownership token"])
    UC10(["Issue random challenge"])
    UC11(["Verify proof"])
    UC12(["Generate audit report"])
    UC13(["Tamper with stored data"])
    UC14(["Attempt rogue-key forgery"])
    UC15(["Attempt collusion forgery"])

    OWNER --- UC1 & UC2 & UC3 & UC4 & UC5 & UC6
    CSPA --- UC7 & UC8 & UC9
    TPAA --- UC10 & UC11 & UC12
    ATT --- UC13 & UC14 & UC15

    UC11 -.->|"detects"| UC13
    UC11 -.->|"rejects"| UC14
    UC11 -.->|"rejects"| UC15
```

---

## Figure 4.3.3(a): Sequence Diagram: Integrity Auditing

```mermaid
sequenceDiagram
    autonumber
    participant O as Data Owners (SG)
    participant C as CSP
    participant T as TPA

    Note over O: TagGen, each owner computes σ_ij from its own secrets
    O->>O: σ_ij = H₃(F‖j)^{H₂(sk_i‖a_i)+a_i} · (u^{m_j})^{sk_i·H₁(pk_i)}
    O->>O: σ_j = ∏ᵢ σ_ij
    O->>C: upload {m_j, σ_j} for j = 1..n
    O-->>T: publish APK, Rs, Hs

    Note over T,C: Audit, repeated on a schedule, data never moves
    T->>T: sample Q = {(j, v_j)}, c elements
    T->>C: challenge Q
    C->>C: DP = Σ m_j·v_j
    C->>C: TP = ∏ σ_j^{v_j}
    C->>T: proof P = (DP, TP)  ← constant size
    T->>T: e(TP, g) ?= e(∏H₃(F‖j)^{v_j}, Hs·Rs) · e(u^{DP}, APK)

    alt equation balances
        T-->>O: PASS, data intact
    else equation fails
        T-->>O: FAIL, do not certify
    end
```

## Figure 4.3.3(b): Sequence Diagram: Ownership Transfer

```mermaid
sequenceDiagram
    autonumber
    participant S as Sale Group
    participant B as Buy Group
    participant C as CSP
    participant T as TPA

    Note over S,B: sale agreed off-protocol
    S->>S: h_i = −H₂(sk_i‖a_i)
    S->>S: aux_i = −sk_i·H₁(pk_i) − x_i,  v_i = u^{x_i}
    S->>S: Hs ← Hs·g^{H_SG}   (Rs deliberately unchanged)
    S->>B: ODT_SG = (H_SG, AUX_SG, V_SG)

    B->>B: draw fresh masks a_i′
    B->>B: h_i = H₂(sk_i′‖a_i′) + a_i′
    B->>B: aux_i = sk_i′·H₁(pk_i′) − x_i′
    B->>B: fold ODT_SG into ODT_BG
    B->>B: Rs ← Rs·∏g^{a_i′},  Hs ← Hs·∏g^{H₂}
    B->>C: ODT_BG = (H, AUX, V)   ← constant size

    C->>C: σ′_j = σ_j·H₃(F‖j)^H·(u^{m_j})^{AUX}·V^{m_j}  for all j
    B-->>T: publish updated APK, Rs, Hs

    T->>C: challenge
    C->>T: proof
    T-->>B: PASS, buyer now owns and can audit
    T-->>S: FAIL against old parameters, seller has lost ownership
```

---

## Figure 4.3.3(c): Activity Diagram: Complete Dataset Lifecycle

```mermaid
flowchart TD
    START([Start]) --> A1["SysGen, publish g, u"]
    A1 --> A2["KeyGen, each owner draws sk_i, a_i"]
    A2 --> A3["Compute APK = ∏ pk_i^{H₁(pk_i)}"]
    A3 --> A4["Split dataset into n blocks, map to Z_q"]
    A4 --> A5["TagGen, each owner computes σ_ij"]
    A5 --> A6["Aggregate σ_j, publish Rs and Hs"]
    A6 --> A7["Upload {m_j, σ_j} to CSP"]

    A7 --> AUDIT{"Audit round"}
    AUDIT --> B1["TPA samples Q = {(j, v_j)}"]
    B1 --> B2["CSP computes DP and TP"]
    B2 --> B3{"Pairing equation<br/>balances?"}
    B3 -->|Yes| B4["Record PASS"]
    B3 -->|No| B5["Record FAIL, data corrupted<br/>or parameters stale"]

    B4 --> EVENT{"Ownership event?"}
    B5 --> EVENT

    EVENT -->|"member joins"| C1["AddOwner, ODT from joiners only"]
    EVENT -->|"member leaves"| C2["RevokeOwner, cancel H₂, KEEP mask"]
    EVENT -->|"dataset sold"| C3["OwnerTransfer, SG then BG"]
    EVENT -->|none| AUDIT

    C1 --> D1["CSP rewrites all tags with ODT"]
    C2 --> D1
    C3 --> D1
    D1 --> D2["Publish updated APK, Rs, Hs"]
    D2 --> D3{"Was it a sale?"}
    D3 -->|Yes| D4["Seller's old parameters<br/>no longer verify"]
    D3 -->|No| AUDIT
    D4 --> AUDIT

    AUDIT -->|"retention period ends"| END([End])
```

---

## Notes for the report

- Figures 4.1.1, 4.2.1, 4.2.2 replace the corresponding placeholders in the drafted report.
- Figure 4.3.3 in the drafted report is listed as "Activity and Sequence Diagram"; it is split here into three sub-figures (a), (b), (c) because one diagram cannot legibly carry both the audit protocol and the transfer protocol.
- Every diagram was drawn from the implemented code in `code/`, not from the paper alone, so the class names and method signatures match what an examiner will find if they open the source.
