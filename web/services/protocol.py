"""
Bridge between the web console and the protocol implementation in code/.

One ProtocolHub per server process holds the live objects: system parameters,
one CSP, one TPA, and a Space per onboarded dataset (owner group + group
state). Secret keys and masks exist only inside these objects, in memory —
persisting them would contradict the trust model, so a server restart simply
requires onboarding a dataset again (seconds of work on demo sizes).

Everything below calls the same entity layer the CLI demo and tests use.
The crypto core is not touched.
"""

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from settings import CODE_DIR

sys.path.insert(0, str(CODE_DIR))

from crypto.scheme import GroupState  # noqa: E402
from datasets.loader import (  # noqa: E402
    chunk_file,
    list_datasets,
    load_manifest,
)
from entities.csp import CloudStorageProvider  # noqa: E402
from entities.group import OwnerGroup  # noqa: E402
from entities.tpa import ThirdPartyAuditor  # noqa: E402
from crypto.scheme import sys_gen  # noqa: E402

# Cost of one tag share on the reference machine (used only for the console's
# estimated progress bar; measured in PROJECT_PLAN §3.2).
TAG_SHARE_MS = 55.0
ODT_BYTES = 160  # 2 x 32-byte scalars + one 96-byte G1 element

# Suggested consortium rosters per catalog dataset (from the scenario designs
# in datasets/generate.py); users may override in the onboarding form.
SUGGESTED_OWNERS = {
    "medical-imaging": ["apollo-hospital", "manipal-hospital", "narayana-hospital"],
    "iot-telemetry": ["city-transport-dept", "metro-agency", "traffic-agency"],
    "financial-ledger": ["meridian-regional-bank"],
    "genomics-variants": ["helix-lab", "genome-institute", "biosample-lab"],
    "ai-training-corpus": ["labelworks-vendor"],
}
SUGGESTED_BUYERS = {
    "medical-imaging": ("medai-research-institute", ["medai-research-institute"]),
    "iot-telemetry": ("urban-analytics-firm", ["urban-analytics-firm"]),
    "financial-ledger": ("statutory-audit-firm", ["statutory-audit-firm"]),
    "genomics-variants": ("pharma-buyer", ["pharma-r-and-d", "pharma-clinical"]),
    "ai-training-corpus": ("autonomy-ai-lab", ["autonomy-ai-lab"]),
}


class _NullJob:
    """Stand-in for a background job when a staged run is executed headlessly.

    The evidence a deed carries comes from the same staged audit the console
    watches; when nobody is watching, the emitted lines simply go nowhere.
    """

    def __init__(self):
        self.message = ""
        self.progress = 0.0
        self.result: dict = {}
        self.log: list[str] = []

    def emit(self, line: str) -> None:
        self.log.append(line)


@dataclass
class Space:
    """Live protocol state for one onboarded dataset."""

    dataset_id: int
    file_id: str
    group: OwnerGroup
    state: GroupState
    previous_state: GroupState | None = None   # seller's stale params after a sale
    previous_group_name: str = ""
    corrupted: dict[int, int] = field(default_factory=dict)  # index -> original
    # Half-executed sale: the Sale Group has cancelled its exponents and handed
    # over ODT_SG, but no Buy Group has accepted yet. Held only between the two
    # halves of one transfer.
    pending: dict | None = None


class ProtocolHub:
    _instance: "ProtocolHub | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self.param = sys_gen()
        self.csp = CloudStorageProvider(self.param)
        self.tpa = ThirdPartyAuditor(self.param)
        self.spaces: dict[int, Space] = {}

    @classmethod
    def instance(cls) -> "ProtocolHub":
        with cls._lock:
            if cls._instance is None:
                cls._instance = ProtocolHub()
            return cls._instance

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def catalog(self) -> list[dict]:
        out = []
        for name in list_datasets():
            m = load_manifest(name)
            out.append(
                {
                    "name": name,
                    "title": m.get("title", name),
                    "description": m.get("description", ""),
                    "tags": m.get("tags", []),
                    "record_count": m.get("record_count", 0),
                    "total_bytes": m.get("total_bytes", 0),
                    "shard_count": m.get("shard_count", 0),
                    "suggested_owners": SUGGESTED_OWNERS.get(name, ["owner-1"]),
                }
            )
        return out

    def shard_path(self, name: str, shard_index: int = 0) -> Path:
        m = load_manifest(name)
        shard = m["shards"][shard_index]
        from datasets.loader import DATASET_DIR

        return DATASET_DIR / name / shard["path"]

    # ------------------------------------------------------------------
    # Onboarding (TagGen + upload) — runs inside a job thread
    # ------------------------------------------------------------------

    def onboard(self, job, dataset_id: int, name: str, owners: list[str],
                max_blocks: int, block_size: int) -> dict:
        """Protect a dataset from the bundled consortium catalog."""
        path = self.shard_path(name)
        return self.onboard_file(job, dataset_id, path, f"{name}/{path.name}#{dataset_id}",
                                 owners, max_blocks, block_size,
                                 group_name=f"consortium-{name}")

    def onboard_file(self, job, dataset_id: int, path: Path, file_id: str,
                     owners: list[str], max_blocks: int, block_size: int,
                     group_name: str = "") -> dict:
        """Protect any file on this machine — a catalog shard or an upload.

        Identical work in both cases: cut the file into blocks, generate a key
        pair per owner, compute one aggregated tag per block, hand blocks and
        tags to the storage provider and publish the verification parameters.
        """
        job = job or _NullJob()
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"the stored file is missing from disk: {path}")

        group_name = group_name or f"consortium-{path.stem}"

        job.message = "Chunking the file into Z_q blocks"
        job.emit(f"Loading {path.name} ({path.stat().st_size:,} bytes), "
                 f"block size {block_size // 1024} KB, first {max_blocks} blocks")
        chunks = chunk_file(path, file_id=file_id, block_size=block_size,
                            max_blocks=max_blocks)
        n = len(chunks.blocks)

        est_s = n * len(owners) * TAG_SHARE_MS / 1000.0
        job.result["estimate_s"] = round(est_s, 1)
        job.message = f"TagGen: {len(owners)} owner(s) signing {n} blocks"
        job.emit(f"KeyGen for {len(owners)} owner(s) — each draws sk_i and mask a_i")
        group = OwnerGroup.create(self.param, group_name, owners)

        job.emit(f"TagGen: computing {n * len(owners)} tag shares "
                 f"(~{est_s:.0f}s estimated on this machine)")
        t0 = time.perf_counter()
        tags, state = group.tag_file(chunks.file_abstract, chunks.blocks)
        tag_ms = (time.perf_counter() - t0) * 1000

        job.message = "Uploading blocks and tags to the CSP"
        self.csp.store(file_id, chunks.file_abstract, chunks.blocks, tags)
        job.emit(f"Stored {n} blocks + {n} aggregated tags at the CSP "
                 f"({tag_ms:,.0f} ms of signing)")
        job.emit("Published verification parameters APK, Rs, Hs to the TPA")

        self.spaces[dataset_id] = Space(
            dataset_id=dataset_id, file_id=file_id, group=group, state=state
        )
        return {
            "n_blocks": n,
            "block_size": block_size,
            "total_bytes": chunks.total_bytes,
            "tag_ms": tag_ms,
            "group_name": group_name,
            "file_id": file_id,
        }

    def ensure_live(self, dataset, job=None) -> bool:
        """Rebuild a dataset's protection state if this process has lost it.

        Keys live in memory and never on disk, so a restart empties them while
        the row still says "active". Everything needed to seal the file again
        is recorded, though — the bytes, the owner ids, the block size — so
        rather than telling the operator their asset is unusable, the platform
        re-protects it from what it already has and carries on.

        Returns True when it actually had to rebuild.
        """
        if dataset.id in self.spaces:
            return False

        owners = list(dataset.owners) or [dataset.group_name or "owner-1"]
        block_size = dataset.block_size or 262144
        max_blocks = dataset.n_blocks or 24
        # A rebuild is seconds of pairing work; refuse a pathological one
        # rather than hang a request on it.
        if max_blocks * len(owners) > 600:
            raise ValueError(
                f"{dataset.name} is too large to restore automatically — "
                f"protect it again from Storage")

        if dataset.source == "upload":
            path = Path(dataset.storage_path or "")
            if not path.exists():
                raise ValueError(f"the stored file for {dataset.name} is missing "
                                 "from this server, so its protection cannot be "
                                 "restored")
            file_id = dataset.file_id if dataset.file_id and "#" in dataset.file_id \
                else f"upload/{dataset.name}#{dataset.id}"
        else:
            path = self.shard_path(dataset.name)
            file_id = f"{dataset.name}/{path.name}#{dataset.id}"

        self.onboard_file(job, dataset.id, path, file_id, owners,
                          max_blocks, block_size,
                          group_name=dataset.group_name)
        return True

    # ------------------------------------------------------------------
    # Auditing
    # ------------------------------------------------------------------

    def audit(self, dataset_id: int, c: int, use_stale: bool = False) -> dict:
        space = self._space(dataset_id)
        state = space.previous_state if use_stale else space.state
        if state is None:
            raise ValueError("no stale parameters exist — no transfer has happened")
        c = max(1, min(c, state.n_blocks))
        record = self.tpa.audit(self.csp, file_id=space.file_id, state=state, c=c)
        exact = self.tpa.detection_probability_exact(state.n_blocks, c)
        return {
            "passed": record.passed,
            "challenged": record.challenged_blocks,
            "total_blocks": record.total_blocks,
            "detection_pct": record.detection_probability * 100,
            "detection_exact_pct": exact * 100,
            "duration_ms": record.duration_ms,
            "stale": use_stale,
        }

    def audit_staged(self, job, dataset_id: int, c: int,
                     use_stale: bool = False) -> dict:
        """The same audit, run one protocol step at a time so it can be watched.

        `audit()` above calls TPA.audit(), which does challenge → proof →
        verify internally and returns a single elapsed time. Here the three
        steps are driven separately against the very same objects, so each one
        can be timed and reported while it happens. Nothing is simulated and
        nothing is added: the calls, in order, are exactly the ones TPA.audit()
        makes, and the totals agree with it.
        """
        job = job or _NullJob()
        space = self._space(dataset_id)
        state = space.previous_state if use_stale else space.state
        if state is None:
            raise ValueError("no stale parameters exist — no transfer has happened")

        stored = self.csp.get(space.file_id)
        n = stored.n_blocks
        c = max(1, min(int(c), n))

        stages: list[dict] = [
            {"key": "challenge", "label": "Issue random challenge", "state": "pending"},
            {"key": "proof", "label": "Provider computes proof", "state": "pending"},
            {"key": "verify", "label": "Auditor checks the pairing equation", "state": "pending"},
            {"key": "record", "label": "Record the result", "state": "pending"},
        ]
        job.result["stages"] = stages
        job.result["dataset_id"] = dataset_id
        job.result["total_blocks"] = n

        def begin(i: int, message: str) -> float:
            stages[i]["state"] = "running"
            job.message = message
            return time.perf_counter()

        def finish(i: int, t0: float, detail: str) -> float:
            ms = (time.perf_counter() - t0) * 1000
            stages[i].update(state="done", ms=round(ms, 1), detail=detail)
            job.emit(f"{stages[i]['label']}: {detail} ({ms:.1f} ms)")
            job.progress = (i + 1) / len(stages)
            return ms

        total_t0 = time.perf_counter()

        # 1. The auditor picks the blocks. Doing this at audit time, at random,
        #    is what stops the provider keeping only the blocks it expects to
        #    be asked about.
        t0 = begin(0, f"Sampling {c} of {n} blocks at random")
        challenge = self.tpa.issue_challenge(n, c)
        indices = sorted(j for j, _ in challenge.pairs)
        preview = ", ".join(str(j) for j in indices[:12])
        challenge_ms = finish(
            0, t0, f"{challenge.size} block indices drawn without replacement "
                   f"[{preview}{'…' if len(indices) > 12 else ''}]")

        # 2. The provider answers. It cannot do this without holding the data.
        t0 = begin(1, "Waiting for the storage provider's proof")
        proof = self.csp.generate_proof(space.file_id, challenge)
        # One Z_q scalar (32 B) + one G1 element (96 B), the same accounting
        # ODT_BYTES uses above. Constant, whatever c was.
        proof_bytes = 32 + 96
        proof_ms = finish(
            1, t0, f"constant-size proof returned: DP {proof.dp.bit_length()} bits "
                   f"+ TP one G1 element ({proof_bytes} bytes total)")

        # 3. Three pairings, whatever the size of the dataset.
        t0 = begin(2, "Verifying the proof against the published parameters")
        passed = self.tpa.verify(state, challenge, proof)
        verify_ms = finish(
            2, t0, "pairing equation holds" if passed
                   else "pairing equation does NOT hold — possession not proved")

        duration_ms = (time.perf_counter() - total_t0) * 1000
        exact = self.tpa.detection_probability_exact(n, c)

        from crypto.pairing import g1_to_bytes, g2_to_bytes  # noqa: E402

        t0 = begin(3, "Writing the result to the verification log")
        result = {
            # The BLS12-381 material this round was actually built from: the
            # group's aggregated public key, and the aggregated tag the CSP
            # returned. Printed on a deed, these are what a later reader
            # checks the transfer against.
            "bls": {
                "curve": "BLS12-381",
                "group_public_key": g2_to_bytes(state.apk).hex(),
                "proof_tp": g1_to_bytes(proof.tp).hex(),
                "proof_dp": f"{proof.dp:064x}",
            },
            "passed": passed,
            "challenged": challenge.size,
            "total_blocks": n,
            "detection_pct": self.tpa.detection_probability(n, c) * 100,
            "detection_exact_pct": exact * 100,
            "duration_ms": duration_ms,
            "stale": use_stale,
            "tag_version": stored.tag_version,
            "proof_bytes": proof_bytes,
            "timings": {
                "challenge_ms": round(challenge_ms, 1),
                "proof_ms": round(proof_ms, 1),
                "verify_ms": round(verify_ms, 1),
            },
        }
        job.result.update(result)
        return result

    # ------------------------------------------------------------------
    # Tampering controls (the CSP's modelled attack surface)
    # ------------------------------------------------------------------

    def corrupt(self, dataset_id: int, index: int) -> dict:
        space = self._space(dataset_id)
        if index in space.corrupted:
            raise ValueError(f"block {index} is already corrupted")
        original = self.csp.corrupt_block(space.file_id, index)
        space.corrupted[index] = original
        return {"index": index, "corrupted_count": len(space.corrupted)}

    def restore(self, dataset_id: int) -> dict:
        space = self._space(dataset_id)
        for index, value in space.corrupted.items():
            self.csp.restore_block(space.file_id, index, value)
        n = len(space.corrupted)
        space.corrupted.clear()
        return {"restored": n}

    # ------------------------------------------------------------------
    # Ownership operations
    # ------------------------------------------------------------------

    def add_owner(self, dataset_id: int, owner_ids: list[str]) -> dict:
        space = self._space(dataset_id)
        t0 = time.perf_counter()
        token, new_state, _ = space.group.add_members(space.state, owner_ids)
        self.csp.apply_ownership_token(space.file_id, token)
        ms = (time.perf_counter() - t0) * 1000
        space.state = new_state
        return {"owners": space.group.member_ids, "duration_ms": ms,
                "token_bytes": ODT_BYTES}

    def revoke_owner(self, dataset_id: int, owner_ids: list[str]) -> dict:
        space = self._space(dataset_id)
        t0 = time.perf_counter()
        token, new_state = space.group.revoke_members(space.state, owner_ids)
        self.csp.apply_ownership_token(space.file_id, token)
        ms = (time.perf_counter() - t0) * 1000
        space.state = new_state
        return {"owners": space.group.member_ids, "duration_ms": ms,
                "token_bytes": ODT_BYTES}

    def transfer(self, dataset_id: int, buyer_group: str,
                 buyer_owner_ids: list[str]) -> dict:
        """Full sale in one call: SG cancels, BG accepts, CSP rewrites tags.

        Kept for the direct console action and the demo scripts. A sale made
        under a signed agreement goes through the two halves below instead, so
        that nothing moves until the buyer has actually countersigned.
        """
        self.transfer_initiate(dataset_id)
        return self.transfer_finalize(dataset_id, buyer_group, buyer_owner_ids)

    # -- the sale, as two signed halves ---------------------------------

    def transfer_initiate(self, dataset_id: int) -> dict:
        """Step 1, run for the Sale Group once the SELLER has signed.

        SG cancels its own index and data exponents and produces ODT_SG. The
        dataset is now mid-sale: the seller has given up its authority but no
        buyer holds it yet, so the token is parked until the buyer signs.
        """
        space = self._space(dataset_id)
        if space.pending is not None:
            raise ValueError("a transfer is already in progress for this dataset")
        t0 = time.perf_counter()
        sg_token, intermediate = space.group.initiate_transfer(space.state)
        ms = (time.perf_counter() - t0) * 1000
        space.pending = {
            "sg_token": sg_token,
            "intermediate": intermediate,
            "seller_group": space.group.group_id,
            "seller_state": space.state,
            "seller_owners": list(space.group.member_ids),
        }
        return {"seller": space.group.group_id, "duration_ms": ms,
                "token_bytes": ODT_BYTES}

    def transfer_finalize(self, dataset_id: int, buyer_group: str,
                          buyer_owner_ids: list[str]) -> dict:
        """Step 2, run for the Buy Group once the BUYER has countersigned."""
        space = self._space(dataset_id)
        if space.pending is None:
            raise ValueError("no transfer is awaiting completion for this dataset")
        pending = space.pending
        t0 = time.perf_counter()
        bg = OwnerGroup.create(self.param, buyer_group, buyer_owner_ids)
        bg_token, new_state = bg.accept_transfer(
            pending["intermediate"], pending["sg_token"])
        self.csp.apply_ownership_token(space.file_id, bg_token)
        ms = (time.perf_counter() - t0) * 1000

        space.previous_state = pending["seller_state"]   # now-worthless params
        space.previous_group_name = pending["seller_group"]
        space.group = bg
        space.state = new_state
        space.pending = None
        return {
            "seller": pending["seller_group"],
            "seller_owners": pending["seller_owners"],
            "buyer": buyer_group,
            "owners": bg.member_ids,
            "duration_ms": ms,
            "token_bytes": ODT_BYTES,
        }

    def transfer_abort(self, dataset_id: int) -> dict:
        """Undo an initiated-but-unaccepted sale (buyer declined, or timeout).

        The Sale Group re-admits itself, which restores exactly the authority
        it cancelled in step 1 — the same operation AddOwner performs.
        """
        space = self._space(dataset_id)
        if space.pending is None:
            raise ValueError("no transfer is awaiting completion for this dataset")
        pending = space.pending
        bg = OwnerGroup.create(self.param, pending["seller_group"],
                               pending["seller_owners"])
        bg_token, new_state = bg.accept_transfer(
            pending["intermediate"], pending["sg_token"])
        self.csp.apply_ownership_token(space.file_id, bg_token)
        space.group = bg
        space.state = new_state
        space.pending = None
        return {"restored_to": pending["seller_group"]}

    def bls_state(self, dataset_id: int, previous: bool = False) -> dict:
        """The group's published BLS12-381 verification parameters.

        APK, Rs and Hs are the aggregate of every current owner's key material.
        They change when ownership changes — which is precisely why printing
        them before and after a sale evidences that the sale happened.
        """
        from crypto.pairing import g2_to_bytes

        space = self._space(dataset_id)
        state = space.previous_state if previous else space.state
        if state is None:
            return {}
        return {
            "curve": "BLS12-381",
            "group_public_key": g2_to_bytes(state.apk).hex(),
            "mask_aggregate": g2_to_bytes(state.rs).hex(),
            "key_aggregate": g2_to_bytes(state.hs).hex(),
            "owner_ids": list(state.owner_ids),
            "n_blocks": state.n_blocks,
        }

    # -- asset identity -------------------------------------------------

    def fingerprint(self, dataset_id: int) -> dict:
        """A content-derived identity for the asset, for printing on a deed.

        Names can be reused and paths can be rewritten; this is derived from
        the stored blocks and the file abstract the tags were built over, so a
        certificate names the asset in a way that cannot be quietly swapped.
        """
        import hashlib

        space = self._space(dataset_id)
        stored = self.csp.get(space.file_id)
        h = hashlib.sha256()
        for block in stored.blocks:
            h.update(int(block).to_bytes(32, "big", signed=False))
        return {
            "content_digest": "sha256:" + h.hexdigest(),
            "file_abstract": "sha256:" + hashlib.sha256(
                space.state.file_abstract).hexdigest(),
            "block_count": space.state.n_blocks,
            "resource_id": space.file_id,
        }

    # ------------------------------------------------------------------
    # Attack demonstrations — stream the real scripts' output
    # ------------------------------------------------------------------

    def run_attack(self, job, name: str) -> dict:
        assert name in {"collusion", "rogue_key", "tampering"}
        job.message = f"Running attacks/{name}.py against the live implementation"
        proc = subprocess.Popen(
            [sys.executable, "-m", f"attacks.{name}"],
            cwd=str(CODE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            job.emit(line.rstrip("\n"))
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"attack script exited with status {code}")
        return {"exit_code": code}

    # ------------------------------------------------------------------

    def space_info(self, dataset_id: int) -> dict | None:
        space = self.spaces.get(dataset_id)
        if space is None:
            return None
        return {
            "owners": space.group.member_ids,
            "group_name": space.group.group_id,
            "n_blocks": space.state.n_blocks,
            "corrupted": sorted(space.corrupted),
            "has_stale": space.previous_state is not None,
            "previous_group": space.previous_group_name,
            "transfer_pending": space.pending is not None,
        }

    def _space(self, dataset_id: int) -> Space:
        space = self.spaces.get(dataset_id)
        if space is None:
            raise KeyError(
                "protocol state for this dataset is not in memory "
                "(the server restarted) — onboard it again"
            )
        return space
