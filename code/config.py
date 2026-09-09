"""
Central configuration.

Everything a reader might want to tune lives here rather than being scattered
through the code, so that the protocol modules stay pure protocol.

--------------------------------------------------------------------------
THE ONE PARAMETER THAT MATTERS: BLOCK SIZE
--------------------------------------------------------------------------
In any Provable Data Possession scheme the cost driver is the NUMBER OF BLOCKS
n, not the size of the file.  A file is split into n blocks and one tag is
computed per block, so:

    tagging cost   =  n * s  group exponentiations      (s = number of owners)
    storage cost   =  n * 96 bytes of tags
    audit cost     =  c hash-to-curve + 3 pairings      (c = challenged blocks)

Block size is therefore the knob that trades detection granularity against
tagging time:

    4 KB blocks   ->  a 100 MB dataset is 25,600 blocks   (fine granularity, slow)
    256 KB blocks ->  a 100 MB dataset is 400 blocks      (coarse, fast)

Crucially, the *security* guarantee is unaffected.  The detection probability

    P_detect = 1 - ((n - t)/n)^c

depends on the FRACTION of blocks corrupted (t/n), not on the absolute block
size.  Challenging 460 blocks detects a 1% corruption with 99% probability
whether those blocks are 4 KB or 256 KB.  Larger blocks simply mean an
attacker who corrupts one block has damaged more bytes -- which makes the
corruption easier to detect, not harder.

We therefore default to a large block size for the big datasets, and document
the choice rather than hiding it.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

CODE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_ROOT.parent

DATASET_DIR = CODE_ROOT / "datasets" / "data"
STORAGE_DIR = CODE_ROOT / "storage"          # simulated CSP disk
RESULTS_DIR = CODE_ROOT / "benchmarks" / "results"
REPORT_DIR = PROJECT_ROOT / "report"


# --------------------------------------------------------------------------
# Block handling
# --------------------------------------------------------------------------

# How a file block becomes an element of Z_q.
#
#   "hashed"  m_j = SHA-512(block) mod q.  Works for any block size.  Computing
#             the hash still requires possessing the block, so a cloud that has
#             deleted the data cannot answer a challenge -- soundness holds.
#             This is the default for real datasets.
#
#   "raw"     m_j = int(block) mod q, requires block size <= 31 bytes so the
#             value fits in Z_q without reduction.  This is what the paper uses
#             (8-byte blocks) and what the benchmarks use, for parity.
BLOCK_MODE = os.environ.get("CA_BLOCK_MODE", "hashed")

# Default block size for real datasets, in bytes.
DEFAULT_BLOCK_SIZE = int(os.environ.get("CA_BLOCK_SIZE", 256 * 1024))

# Block size used by the benchmark suite, matching the paper's setup.
BENCHMARK_BLOCK_SIZE = 8


# --------------------------------------------------------------------------
# Auditing
# --------------------------------------------------------------------------

# Number of blocks the TPA challenges per audit.
#
# Derived from the paper's own detection bound (section VII.B.3):
#     1 - ((n - t)/n)^c  >=  P_detect
# At n = 1000, t = 10 (1% corruption) and P_detect = 99%, this yields c = 460.
DEFAULT_CHALLENGE_SIZE = 460

# For small files we cannot challenge more blocks than exist; the challenge
# generator clamps automatically, but this keeps demos snappy.
DEMO_CHALLENGE_SIZE = 32


# --------------------------------------------------------------------------
# Benchmarks
# --------------------------------------------------------------------------

BENCH_OWNER_RANGE = list(range(1, 21))              # s = 1..20   (Fig. 6)
BENCH_BLOCK_RANGE = [100, 200, 400, 600, 800, 1000]  # n          (Fig. 5a)
BENCH_OWNER_SIZES = [1, 5, 10]                       # s          (Fig. 5a)
BENCH_CHALLENGE_RANGE = [50, 100, 200, 300, 460]     # c          (Fig. 5b,c)
BENCH_ADD_OWNERS = [10, 30, 80]                      # |U_in|  at s=20  (Fig. 7a)
BENCH_REVOKE_OWNERS = [10, 50, 80]                   # |U_out| at s=100 (Fig. 7b)


def ensure_dirs() -> None:
    """Create the directories the tooling writes into."""
    for path in (DATASET_DIR, STORAGE_DIR, RESULTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
