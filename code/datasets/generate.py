"""
Sample dataset generator.

WHY THIS EXISTS
---------------
The base paper motivates itself with a very specific real-world situation
(section I): organisations like Kaggle and Zenodo hold large AI-related
datasets, and they *sell* those datasets to other organisations.  The buyer
must end up genuinely owning the data -- able to audit its integrity -- and the
seller must stop being able to.  That is what "ownership transfer" means here.

To demonstrate that, we need datasets that plausibly get sold: large, owned by
a consortium rather than one person, and carrying provenance that has to
survive the transfer.  There is no public corpus of "datasets with transferable
ownership records", so we generate them.

Everything produced here is SYNTHETIC.  No real patient, customer, or personal
data is used or reproduced -- the generators emit statistically plausible but
entirely fabricated records, which is exactly what a research demonstration
needs and avoids every privacy concern that real data would raise.

FIVE SCENARIOS
--------------
Each mirrors a real transaction where cloud auditing with ownership transfer
is the natural fit:

  medical-imaging   Hospital consortium  ->  medical AI research institute
  iot-telemetry     City transport dept  ->  urban analytics company
  financial-ledger  Regional bank        ->  external audit firm
  genomics-variants Sequencing lab       ->  pharmaceutical company
  ai-training-corpus Data labelling vendor -> AI model developer

Usage
-----
    python -m datasets.generate --dataset all --size medium
    python -m datasets.generate --dataset medical-imaging --size large
    python -m datasets.generate --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATASET_DIR  # noqa: E402

# --------------------------------------------------------------------------
# Size tiers
#
# Chosen so that a reader can start small and scale up.  See config.py for why
# file size does not drive protocol cost -- block COUNT does.
# --------------------------------------------------------------------------

SIZE_TIERS = {
    "small": 2 * 1024 * 1024,        # 2 MB    - quick correctness runs
    "medium": 25 * 1024 * 1024,      # 25 MB   - the default demo size
    "large": 100 * 1024 * 1024,      # 100 MB  - realistic transaction size
    "xlarge": 512 * 1024 * 1024,     # 512 MB  - stress / scalability runs
}

SHARD_SIZE = 8 * 1024 * 1024  # split output into 8 MB shards, like real data lakes


# --------------------------------------------------------------------------
# Scenario description
# --------------------------------------------------------------------------

@dataclass
class Scenario:
    """One sellable dataset: who owns it, who buys it, and how records look."""

    name: str
    title: str
    description: str
    sale_group: list[str]            # the consortium that currently owns it
    buy_group: list[str]             # the intended purchaser
    file_extension: str
    header: str | None
    record_writer: Callable[[random.Random, int], str]
    licence: str
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Record generators -- one per scenario
#
# Each takes a seeded RNG and a record index, and returns one line of output.
# Keeping them tiny and pure makes the whole generator deterministic: the same
# --seed always produces byte-identical datasets, so benchmark results and
# audit demonstrations are reproducible.
# --------------------------------------------------------------------------

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)

_MODALITIES = ["CT", "MRI", "X-RAY", "ULTRASOUND", "PET", "MAMMOGRAPHY"]
_BODY_PARTS = ["CHEST", "ABDOMEN", "BRAIN", "SPINE", "PELVIS", "KNEE", "SHOULDER"]
_FINDINGS = ["normal", "nodule", "fracture", "lesion", "effusion", "atrophy", "inflammation"]


def _medical_record(rng: random.Random, i: int) -> str:
    study_time = _EPOCH + timedelta(minutes=rng.randint(0, 500_000))
    return ",".join([
        f"STU-{i:09d}",
        f"PAT-{rng.randint(10**7, 10**8 - 1)}",          # synthetic pseudonym
        rng.choice(_MODALITIES),
        rng.choice(_BODY_PARTS),
        study_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        str(rng.randint(1, 512)),                          # slice count
        f"{rng.uniform(0.4, 5.0):.3f}",                    # slice thickness mm
        rng.choice(_FINDINGS),
        f"{rng.uniform(0.50, 0.999):.4f}",                 # annotation confidence
        f"SITE-{rng.randint(1, 40):03d}",
        hashlib.sha256(f"img{i}".encode()).hexdigest(),    # pixel-data digest
    ]) + "\n"


_SENSOR_TYPES = ["air_quality", "traffic_flow", "noise", "temperature", "humidity", "parking"]
_WARDS = ["RAJARAJESHWARI", "JAYANAGAR", "WHITEFIELD", "HEBBAL", "KORAMANGALA", "YELAHANKA"]


def _iot_record(rng: random.Random, i: int) -> str:
    ts = _EPOCH + timedelta(seconds=i * 7 + rng.randint(0, 6))
    return ",".join([
        f"SNS-{rng.randint(1, 5000):05d}",
        rng.choice(_SENSOR_TYPES),
        rng.choice(_WARDS),
        ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        f"{rng.uniform(-10, 400):.4f}",                    # reading
        f"{rng.uniform(12.90, 13.10):.6f}",                # latitude
        f"{rng.uniform(77.45, 77.75):.6f}",                # longitude
        f"{rng.uniform(0.80, 1.0):.3f}",                   # quality score
        str(rng.randint(0, 3)),                            # calibration flag
    ]) + "\n"


_TXN_TYPES = ["NEFT", "RTGS", "UPI", "IMPS", "CARD", "ACH", "CHEQUE"]
_CURRENCIES = ["INR", "USD", "EUR", "GBP", "SGD"]
_CHANNELS = ["mobile", "web", "branch", "atm", "pos"]


def _financial_record(rng: random.Random, i: int) -> str:
    ts = _EPOCH + timedelta(seconds=rng.randint(0, 31_000_000))
    return ",".join([
        f"TXN-{i:012d}",
        f"ACC-{rng.randint(10**9, 10**10 - 1)}",           # synthetic account
        f"ACC-{rng.randint(10**9, 10**10 - 1)}",
        rng.choice(_TXN_TYPES),
        rng.choice(_CURRENCIES),
        f"{rng.lognormvariate(7.0, 1.5):.2f}",             # amount, heavy-tailed
        ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        rng.choice(_CHANNELS),
        f"BR-{rng.randint(1, 900):04d}",
        f"{rng.uniform(0, 1):.5f}",                        # fraud score
        "1" if rng.random() < 0.003 else "0",              # flagged
    ]) + "\n"


_CHROMOSOMES = [str(c) for c in range(1, 23)] + ["X", "Y", "MT"]
_BASES = ["A", "C", "G", "T"]
_CONSEQUENCES = ["missense", "synonymous", "frameshift", "stop_gained",
                 "splice_region", "intron", "UTR3", "UTR5"]
_SIGNIFICANCE = ["benign", "likely_benign", "uncertain", "likely_pathogenic", "pathogenic"]


def _genomics_record(rng: random.Random, i: int) -> str:
    ref = rng.choice(_BASES)
    alt = rng.choice([b for b in _BASES if b != ref])
    return ",".join([
        f"VAR-{i:010d}",
        f"SMP-{rng.randint(1, 20000):06d}",                # synthetic sample
        rng.choice(_CHROMOSOMES),
        str(rng.randint(1, 248_000_000)),                  # position
        ref,
        alt,
        f"{rng.uniform(0, 0.5):.6f}",                      # allele frequency
        f"{rng.uniform(1, 500):.1f}",                      # read depth
        f"{rng.uniform(10, 99):.1f}",                      # quality
        rng.choice(_CONSEQUENCES),
        rng.choice(_SIGNIFICANCE),
        f"GENE{rng.randint(1, 20000):05d}",
    ]) + "\n"


_LABELS = ["pedestrian", "car", "bus", "truck", "bicycle", "motorcycle",
           "traffic_sign", "traffic_light", "animal", "obstacle"]
_SPLITS = ["train", "val", "test"]
_WEATHER = ["clear", "rain", "fog", "night", "overcast", "snow"]


def _ai_corpus_record(rng: random.Random, i: int) -> str:
    """JSON Lines -- typical of an ML training corpus manifest."""
    n_boxes = rng.randint(1, 6)
    boxes = [
        {
            "label": rng.choice(_LABELS),
            "x": round(rng.uniform(0, 1), 4),
            "y": round(rng.uniform(0, 1), 4),
            "w": round(rng.uniform(0.02, 0.4), 4),
            "h": round(rng.uniform(0.02, 0.4), 4),
            "occluded": rng.random() < 0.2,
        }
        for _ in range(n_boxes)
    ]
    record = {
        "sample_id": f"IMG-{i:09d}",
        "split": rng.choice(_SPLITS),
        "width": rng.choice([1280, 1920, 2048]),
        "height": rng.choice([720, 1080, 1536]),
        "weather": rng.choice(_WEATHER),
        "annotations": boxes,
        "annotator_agreement": round(rng.uniform(0.7, 1.0), 4),
        "sha256": hashlib.sha256(f"ai{i}".encode()).hexdigest(),
    }
    return json.dumps(record, separators=(",", ":")) + "\n"


# --------------------------------------------------------------------------
# The five scenarios
# --------------------------------------------------------------------------

SCENARIOS: dict[str, Scenario] = {
    "medical-imaging": Scenario(
        name="medical-imaging",
        title="Multi-Hospital Radiology Study Index",
        description=(
            "De-identified radiology study metadata pooled by a consortium of "
            "hospitals. Sold to a medical-AI research institute for model "
            "training. Every hospital in the consortium is a co-owner, so no "
            "single hospital may transfer the dataset alone -- exactly the "
            "multi-owner situation the base paper addresses."
        ),
        sale_group=[
            "apollo-hospital-consortium",
            "manipal-health-enterprises",
            "narayana-health-network",
        ],
        buy_group=["medical-ai-research-institute", "clinical-ml-lab"],
        file_extension="csv",
        header="study_id,patient_pseudonym,modality,body_part,study_datetime,"
               "slice_count,slice_thickness_mm,finding,confidence,site_id,pixel_digest\n",
        record_writer=_medical_record,
        licence="CC-BY-NC-4.0 (synthetic research data)",
        tags=["healthcare", "imaging", "multi-owner", "high-sensitivity"],
    ),
    "iot-telemetry": Scenario(
        name="iot-telemetry",
        title="Smart City Sensor Telemetry Archive",
        description=(
            "Continuous telemetry from municipal sensors. The transport "
            "department and two civic agencies jointly own the archive and "
            "sell a historical slice to an urban analytics company."
        ),
        sale_group=[
            "city-transport-department",
            "municipal-environment-agency",
            "civic-data-authority",
        ],
        buy_group=["urban-analytics-pvt-ltd"],
        file_extension="csv",
        header="sensor_id,sensor_type,ward,timestamp,reading,latitude,longitude,"
               "quality_score,calibration_flag\n",
        record_writer=_iot_record,
        licence="ODbL-1.0 (synthetic research data)",
        tags=["iot", "time-series", "smart-city"],
    ),
    "financial-ledger": Scenario(
        name="financial-ledger",
        title="Retail Banking Transaction Ledger",
        description=(
            "A quarter of retail transaction records. Ownership passes from "
            "the bank to an external audit firm for the duration of a "
            "statutory audit -- a case where the seller must PROVABLY lose the "
            "ability to alter the data, and the auditor must gain the ability "
            "to verify it."
        ),
        sale_group=["regional-commercial-bank", "bank-data-custodian-unit"],
        buy_group=["statutory-audit-firm", "regulatory-compliance-office"],
        file_extension="csv",
        header="txn_id,from_account,to_account,txn_type,currency,amount,"
               "timestamp,channel,branch_id,fraud_score,flagged\n",
        record_writer=_financial_record,
        licence="Proprietary (synthetic research data)",
        tags=["finance", "audit", "regulatory", "integrity-critical"],
    ),
    "genomics-variants": Scenario(
        name="genomics-variants",
        title="Population Genomics Variant Call Archive",
        description=(
            "Variant calls from a population sequencing programme. Three "
            "sequencing labs co-own the archive; a pharmaceutical company "
            "purchases full ownership for drug-target research."
        ),
        sale_group=[
            "national-genome-lab",
            "university-sequencing-centre",
            "bioinformatics-core-facility",
        ],
        buy_group=["pharma-research-division"],
        file_extension="csv",
        header="variant_id,sample_id,chromosome,position,ref_allele,alt_allele,"
               "allele_frequency,read_depth,quality,consequence,clinical_significance,gene\n",
        record_writer=_genomics_record,
        licence="CC-BY-4.0 (synthetic research data)",
        tags=["genomics", "research", "multi-owner"],
    ),
    "ai-training-corpus": Scenario(
        name="ai-training-corpus",
        title="Autonomous Driving Annotation Corpus",
        description=(
            "Bounding-box annotations for an autonomous-driving image corpus. "
            "This is the archetypal case from the base paper's introduction: a "
            "data-labelling vendor selling an AI training set, where the buyer "
            "needs continuing proof that the cloud still holds the data intact."
        ),
        sale_group=["data-labelling-vendor", "annotation-quality-partner"],
        buy_group=["autonomous-systems-ai-lab", "adas-model-team"],
        file_extension="jsonl",
        header=None,
        record_writer=_ai_corpus_record,
        licence="CC-BY-SA-4.0 (synthetic research data)",
        tags=["machine-learning", "annotations", "kaggle-style"],
    ),
}


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def _shard_paths(base: Path, extension: str) -> Iterator[Path]:
    index = 0
    while True:
        yield base / f"part-{index:05d}.{extension}"
        index += 1


def generate_dataset(
    scenario: Scenario,
    target_bytes: int,
    out_root: Path,
    seed: int = 20262027,
    quiet: bool = False,
) -> dict:
    """
    Write one dataset to disk and return its manifest.

    Output is sharded into 8 MB parts, the way real data lakes store large
    tables.  Sharding matters for this project: each shard is an independently
    auditable file with its own tags, so a buyer can audit part of a purchase
    without pulling the whole archive.
    """
    dataset_dir = out_root / scenario.name
    data_dir = dataset_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Remove stale shards so regenerating at a smaller size cannot leave
    # orphaned files behind that would corrupt the manifest's byte count.
    for stale in data_dir.glob(f"part-*.{scenario.file_extension}"):
        stale.unlink()

    rng = random.Random(seed)
    shards: list[dict] = []
    total_written = 0
    record_index = 0

    paths = _shard_paths(data_dir, scenario.file_extension)

    while total_written < target_bytes:
        shard_path = next(paths)
        remaining = target_bytes - total_written
        shard_target = min(SHARD_SIZE, remaining)

        digest = hashlib.sha256()
        written = 0
        buffer: list[str] = []

        with shard_path.open("w", encoding="utf-8", newline="") as handle:
            if scenario.header:
                handle.write(scenario.header)
                digest.update(scenario.header.encode())
                written += len(scenario.header)

            while written < shard_target:
                line = scenario.record_writer(rng, record_index)
                record_index += 1
                buffer.append(line)
                written += len(line)

                # Flush in batches: one write() per record would dominate
                # runtime when generating hundreds of megabytes.
                if len(buffer) >= 4096:
                    chunk = "".join(buffer)
                    handle.write(chunk)
                    digest.update(chunk.encode())
                    buffer.clear()

            if buffer:
                chunk = "".join(buffer)
                handle.write(chunk)
                digest.update(chunk.encode())

        actual = shard_path.stat().st_size
        total_written += actual
        shards.append({
            "path": f"data/{shard_path.name}",
            "bytes": actual,
            "sha256": digest.hexdigest(),
        })

        if not quiet:
            print(f"    {shard_path.name}  {actual / 1024 / 1024:7.2f} MB")

    manifest = {
        "name": scenario.name,
        "title": scenario.title,
        "description": scenario.description,
        "licence": scenario.licence,
        "tags": scenario.tags,
        "synthetic": True,
        "generator_seed": seed,
        "record_count": record_index,
        "total_bytes": total_written,
        "shard_count": len(shards),
        "shards": shards,
        "ownership": {
            # The dataset ships owned by the sale group. The demo transfers it
            # to the buy group; nothing here is baked into the crypto -- these
            # are just the identities the scenario scripts use.
            "current_owners": scenario.sale_group,
            "intended_buyers": scenario.buy_group,
            "transfer_history": [],
        },
    }

    (dataset_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    _write_readme(dataset_dir, scenario, manifest)
    return manifest


def _write_readme(dataset_dir: Path, scenario: Scenario, manifest: dict) -> None:
    """A human-readable card for the dataset, so a judge can see what it is."""
    mb = manifest["total_bytes"] / 1024 / 1024
    readme = f"""# {scenario.title}

**Dataset id:** `{scenario.name}`
**Size:** {mb:.2f} MB across {manifest['shard_count']} shard(s), {manifest['record_count']:,} records
**Licence:** {scenario.licence}
**Synthetic:** yes — no real personal, clinical, or financial data is present.

## What this is

{scenario.description}

## Ownership scenario

| Role | Parties |
|---|---|
| Sale Group (current owners) | {', '.join(f'`{o}`' for o in scenario.sale_group)} |
| Buy Group (purchaser) | {', '.join(f'`{o}`' for o in scenario.buy_group)} |

Ownership is **shared** across every member of the sale group. Under the
scheme implemented in `code/crypto/`, that means the file tags carry a
contribution from each owner, and no single owner can transfer the dataset
or forge a proof on their own.

## Regenerating

```bash
python -m datasets.generate --dataset {scenario.name} --size medium
```

Generation is deterministic — the same `--seed` reproduces this dataset
byte for byte, so audits and benchmarks stay reproducible.
"""
    (dataset_dir / "README.md").write_text(readme)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate large synthetic datasets with ownership scenarios.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset", default="all",
        help="scenario name, or 'all' (default: all)",
    )
    parser.add_argument(
        "--size", default="medium", choices=sorted(SIZE_TIERS),
        help="target size per dataset (default: medium)",
    )
    parser.add_argument("--seed", type=int, default=20262027)
    parser.add_argument("--out", type=Path, default=DATASET_DIR)
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        print("Available dataset scenarios:\n")
        for scenario in SCENARIOS.values():
            print(f"  {scenario.name:20s} {scenario.title}")
            print(f"  {'':20s} {len(scenario.sale_group)} owner(s) -> "
                  f"{len(scenario.buy_group)} buyer(s)\n")
        print("Size tiers: " + ", ".join(
            f"{k} ({v // 1024 // 1024} MB)" for k, v in SIZE_TIERS.items()))
        return 0

    if args.dataset == "all":
        chosen = list(SCENARIOS.values())
    elif args.dataset in SCENARIOS:
        chosen = [SCENARIOS[args.dataset]]
    else:
        parser.error(
            f"unknown dataset '{args.dataset}'. "
            f"Choose from: {', '.join(SCENARIOS)}, or 'all'."
        )
        return 2

    target = SIZE_TIERS[args.size]
    args.out.mkdir(parents=True, exist_ok=True)

    total = 0
    for scenario in chosen:
        if not args.quiet:
            print(f"\n[{scenario.name}]  {scenario.title}")
        manifest = generate_dataset(
            scenario, target, args.out, seed=args.seed, quiet=args.quiet
        )
        total += manifest["total_bytes"]
        if not args.quiet:
            print(f"    -> {manifest['record_count']:,} records, "
                  f"{manifest['total_bytes'] / 1024 / 1024:.2f} MB")

    print(f"\nGenerated {len(chosen)} dataset(s), {total / 1024 / 1024:.2f} MB total")
    print(f"Location: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
