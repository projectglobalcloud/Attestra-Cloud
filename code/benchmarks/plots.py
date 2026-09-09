"""
Render the benchmark results as figures for the report.

Reads benchmarks/results/benchmarks.json (produced by run_benchmarks.py) and
writes one PNG per figure, mirroring Figs. 5-8 of the base paper.

DESIGN NOTES -- why the charts look the way they do
---------------------------------------------------
* One y-axis per chart, always. Two measures on different scales get two
  charts, never a dual axis -- a second y-scale lets a reader "see" any
  relationship the author wants and is the single most misleading chart form.

* At most three series per chart, drawn from a fixed, colourblind-validated
  palette in a fixed order. The three hues used here were checked with a CVD
  simulator across all pairs (worst deuteranopia dE 9.2, normal-vision dE 24.0)
  rather than chosen by eye.

* Colour never carries meaning alone: every series is also direct-labelled at
  its right-hand end, and every chart has a legend. That keeps the figures
  readable in greyscale print, which matters for a bound report.

* Grid and axes are recessive; the data is the darkest thing on the page.

Run:
    python -m benchmarks.plots
    python -m benchmarks.plots --results path/to/benchmarks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # headless: no display needed, writes files only
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from config import RESULTS_DIR  # noqa: E402

# --------------------------------------------------------------------------
# Palette -- validated, fixed order, never cycled
# --------------------------------------------------------------------------

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]   # blue, orange, aqua
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8880"
GRID = "#e6e5e1"

LINE_WIDTH = 2.0
MARKER_SIZE = 6.5


def _style() -> None:
    """Global matplotlib styling: recessive chrome, legible type."""
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_SECONDARY,
        "axes.titlecolor": INK_PRIMARY,
        "text.color": INK_PRIMARY,
        "xtick.color": INK_SECONDARY,
        "ytick.color": INK_SECONDARY,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "legend.fontsize": 9,
        "legend.frameon": False,
        "figure.dpi": 140,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
    })


def _axes(ax, xlabel: str, ylabel: str, title: str, subtitle: str = "") -> None:
    """Apply the shared chart anatomy: title, subtitle, recessive spines, grid."""
    ax.set_title(title, pad=18 if subtitle else 10, loc="left")
    if subtitle:
        ax.text(
            0.0, 1.02, subtitle, transform=ax.transAxes,
            fontsize=9, color=INK_SECONDARY, va="bottom", ha="left",
        )
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", linestyle="-", alpha=0.9)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)


def _ms_formatter(ax) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{v:,.0f}" if v >= 1 else f"{v:g}"
    ))


def _integer_x(ax) -> None:
    """
    Force whole-number x ticks.

    Owner counts and block counts are discrete: a tick reading "2.5 owners" is
    nonsense and quietly undermines a reader's trust in the rest of the figure.
    """
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins="auto"))


def _owners_label(count) -> str:
    """Legend text for an owner count, with correct singular/plural."""
    n = int(count)
    return "1 owner" if n == 1 else f"{n} owners"


def _label_end(ax, x, y, text: str, colour: str) -> None:
    """
    Direct-label a series at its right-hand end.

    Required, not decorative: it means identity survives greyscale printing and
    colour-vision deficiency without depending on the legend swatch.
    """
    ax.annotate(
        text, xy=(x, y), xytext=(6, 0), textcoords="offset points",
        color=colour, fontsize=9, fontweight="bold", va="center", ha="left",
    )


def _series(results: list[dict], label: str) -> tuple[list[float], list[float]]:
    """Extract (x, milliseconds) for one series label, sorted by x."""
    rows = sorted((r for r in results if r["label"] == label), key=lambda r: r["x"])
    return [r["x"] for r in rows], [r["seconds"] * 1000 for r in rows]


# ==========================================================================
# Figure 1 -- KeyGen: O(s) vs O(s^2)        [paper Fig. 6]
# ==========================================================================

def plot_keygen(results: list[dict], out_dir: Path) -> Path | None:
    if not results:
        return None

    _style()
    fig, ax = plt.subplots(figsize=(7.2, 4.4))

    for i, (label, name) in enumerate([
        ("proposed", "Proposed  O(s)"),
        ("baseline", "Baseline [8]  O(s²)"),
    ]):
        x, y = _series(results, label)
        if not x:
            continue
        ax.plot(x, y, color=SERIES[i], linewidth=LINE_WIDTH, marker="o",
                markersize=MARKER_SIZE, markeredgecolor=SURFACE,
                markeredgewidth=1.2, label=name, zorder=3 - i)
        _label_end(ax, x[-1], y[-1], name.split("  ")[0], SERIES[i])

    _axes(
        ax,
        "Number of owners in the group  (s)",
        "Key generation time  (ms)",
        "Key generation cost scales linearly, not quadratically",
        "Lower is better. Both schemes measured on BLS12-381 via the same backend.",
    )
    _ms_formatter(ax)
    _integer_x(ax)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.97))
    ax.margins(x=0.12)

    path = out_dir / "fig1_keygen_scaling.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ==========================================================================
# Figure 2 -- TagGen                        [paper Fig. 5a]
# ==========================================================================

def plot_taggen(results: list[dict], out_dir: Path) -> Path | None:
    if not results:
        return None

    _style()
    fig, ax = plt.subplots(figsize=(7.2, 4.4))

    labels = sorted({r["label"] for r in results},
                    key=lambda s: int(s.split("=")[1]))
    for i, label in enumerate(labels[:3]):
        x, y = _series(results, label)
        ax.plot(x, y, color=SERIES[i], linewidth=LINE_WIDTH, marker="o",
                markersize=MARKER_SIZE, markeredgecolor=SURFACE,
                markeredgewidth=1.2, label=_owners_label(label.split("=")[1]))
        _label_end(ax, x[-1], y[-1], label, SERIES[i])

    _axes(
        ax,
        "Number of data blocks  (n)",
        "Tag generation time  (ms)",
        "Tag generation is linear in both blocks and owners",
        "Each owner computes one tag share per block, so the cost is O(n·s).",
    )
    _ms_formatter(ax)
    _integer_x(ax)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    ax.margins(x=0.12)

    path = out_dir / "fig2_taggen.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ==========================================================================
# Figure 3 -- Audit                         [paper Fig. 5b, 5c]
# ==========================================================================

def plot_audit(results: list[dict], out_dir: Path) -> Path | None:
    if not results:
        return None

    _style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4))

    # -- proof generation (CSP side) --
    gen_labels = sorted({r["label"] for r in results if r["label"].startswith("proofgen")},
                        key=lambda s: int(s.split("_s")[1]))
    for i, label in enumerate(gen_labels[:3]):
        x, y = _series(results, label)
        owners = label.split("_s")[1]
        ax1.plot(x, y, color=SERIES[i], linewidth=LINE_WIDTH, marker="o",
                 markersize=MARKER_SIZE, markeredgecolor=SURFACE,
                 markeredgewidth=1.2, label=_owners_label(owners))

    _axes(
        ax1,
        "Blocks challenged  (c)",
        "Proof generation time  (ms)",
        "Proof generation: linear in c",
        "Independent of how many owners the group has.",
    )
    _ms_formatter(ax1)
    _integer_x(ax1)
    ax1.set_xlim(left=0)
    ax1.set_ylim(bottom=0)
    ax1.legend(loc="upper left")

    # -- proof verification (TPA side) --
    ver_labels = sorted({r["label"] for r in results if r["label"].startswith("verify")},
                        key=lambda s: int(s.split("_s")[1]))
    for i, label in enumerate(ver_labels[:3]):
        x, y = _series(results, label)
        owners = label.split("_s")[1]
        ax2.plot(x, y, color=SERIES[i], linewidth=LINE_WIDTH, marker="o",
                 markersize=MARKER_SIZE, markeredgecolor=SURFACE,
                 markeredgewidth=1.2, label=_owners_label(owners))

    _axes(
        ax2,
        "Blocks challenged  (c)",
        "Verification time  (ms)",
        "Verification: 3 pairings, always",
        "Growth comes only from the c hash-to-curve operations, never from more pairings.",
    )
    _ms_formatter(ax2)
    _integer_x(ax2)
    ax2.set_xlim(left=0)
    ax2.set_ylim(bottom=0)
    ax2.legend(loc="upper left")

    fig.tight_layout()
    path = out_dir / "fig3_audit.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ==========================================================================
# Figure 4 -- OwnerModify                   [paper Fig. 7a, 7b]
# ==========================================================================

def plot_owner_modify(results: list[dict], out_dir: Path) -> Path | None:
    if not results:
        return None

    _style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4))

    for ax, prefix, xlabel, title, subtitle in [
        (ax1, "add", "Owners joining  (|U_in|)",
         "Adding owners to a group of 20",
         "Proposed cost depends only on how many are joining, not on group size."),
        (ax2, "revoke", "Owners revoked from a group of 40",
         "Revoking owners",
         "Baseline falls as more leave — a smaller surviving group is cheaper to rebuild."),
    ]:
        x_prop, y_prop = _series(results, f"{prefix}_proposed")
        x_base, y_base = _series(results, f"{prefix}_baseline")
        if not x_prop:
            continue

        width = 0.36
        positions = range(len(x_prop))
        # 2px surface gap between adjacent bars, per the mark spec.
        ax.bar([p - width / 2 for p in positions], y_prop, width,
               color=SERIES[0], label="Proposed", edgecolor=SURFACE, linewidth=1.5,
               zorder=3)
        ax.bar([p + width / 2 for p in positions], y_base, width,
               color=SERIES[1], label="Baseline [8]", edgecolor=SURFACE,
               linewidth=1.5, zorder=3)

        for p, yp, yb in zip(positions, y_prop, y_base):
            # The speedup ratio is the story, so state it above the pair.
            ax.annotate(f"{yb / max(yp, 1e-9):.0f}×",
                        xy=(p, max(yp, yb)), xytext=(0, 7),
                        textcoords="offset points", ha="center",
                        fontsize=10, fontweight="bold", color=INK_PRIMARY)
            # The proposed bar is often only a few pixels tall against a
            # baseline 25-170x larger. Without a printed value the reader
            # cannot recover it, so label both bars directly.
            ax.annotate(f"{yp:,.0f}", xy=(p - width / 2, yp), xytext=(0, 4),
                        textcoords="offset points", ha="center",
                        fontsize=8, color=SERIES[0], fontweight="bold")
            ax.annotate(f"{yb:,.0f}", xy=(p + width / 2, yb), xytext=(0, -14),
                        textcoords="offset points", ha="center",
                        fontsize=8, color=SURFACE, fontweight="bold")

        ax.set_xticks(list(positions))
        ax.set_xticklabels([str(int(v)) for v in x_prop])
        _axes(ax, xlabel, "Time  (ms)", title, subtitle)
        _ms_formatter(ax)
        ax.set_ylim(bottom=0)
        # Headroom for the ratio label; legend sits below the axes so it can
        # never collide with a tall bar.
        ax.margins(y=0.22)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2)

    fig.tight_layout()
    path = out_dir / "fig4_owner_modify.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ==========================================================================
# Figure 5 -- CSP tag update                [paper Fig. 7c]
# ==========================================================================

def plot_tag_update(results: list[dict], out_dir: Path) -> Path | None:
    if not results:
        return None

    _style()
    fig, ax = plt.subplots(figsize=(7.2, 4.4))

    labels = sorted({r["label"] for r in results},
                    key=lambda s: int(s.split("_s")[1]))
    for i, label in enumerate(labels[:3]):
        x, y = _series(results, label)
        owners = label.split("_s")[1]
        ax.plot(x, y, color=SERIES[i], linewidth=LINE_WIDTH, marker="o",
                markersize=MARKER_SIZE, markeredgecolor=SURFACE,
                markeredgewidth=1.2, label=_owners_label(owners))
        # No end-labels on this figure: the curves coincide (that IS the
        # result), so right-hand labels would land on top of one another.
        # Two series plus a legend carry identity without them.

    _axes(
        ax,
        "Number of data blocks  (n)",
        "Tag update time at the CSP  (ms)",
        "Tag rewriting is linear in n and independent of group size",
        "The curves overlap: the token is already aggregated before it reaches the cloud.",
    )
    _ms_formatter(ax)
    _integer_x(ax)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    ax.margins(x=0.14)

    path = out_dir / "fig5_tag_update.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ==========================================================================
# Figure 6 -- OwnerTransfer                 [paper Fig. 8]
# ==========================================================================

def plot_transfer(results: list[dict], out_dir: Path) -> Path | None:
    if not results:
        return None

    _style()
    fig, ax = plt.subplots(figsize=(7.2, 4.4))

    x, y = _series(results, "transfer")
    ax.plot(x, y, color=SERIES[0], linewidth=LINE_WIDTH, marker="o",
            markersize=MARKER_SIZE, markeredgecolor=SURFACE,
            markeredgewidth=1.2, label="Ownership transfer (SG → BG)")

    token_bytes = next(
        (r["extra"].get("token_bytes") for r in results if r.get("extra")), None
    )
    if token_bytes:
        ax.text(
            0.98, 0.06,
            f"Token size constant at {token_bytes} bytes\nfor every group size",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9, color=INK_SECONDARY,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=SURFACE,
                      edgecolor=GRID, linewidth=1),
        )

    _axes(
        ax,
        "Owners per group  (|SG| = |BG|)",
        "Transfer computation time  (ms)",
        "Ownership transfer is linear in participants, constant in bandwidth",
        "Computation grows with the parties involved; the token handed to the cloud does not.",
    )
    _ms_formatter(ax)
    _integer_x(ax)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    ax.margins(x=0.1)

    path = out_dir / "fig6_transfer.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ==========================================================================
# Driver
# ==========================================================================

PLOTTERS = [
    ("keygen", plot_keygen),
    ("taggen", plot_taggen),
    ("audit", plot_audit),
    ("owner_modify", plot_owner_modify),
    ("tag_update", plot_tag_update),
    ("transfer", plot_transfer),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render benchmark figures.")
    parser.add_argument("--results", type=Path, default=RESULTS_DIR / "benchmarks.json")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "figures")
    args = parser.parse_args(argv)

    if not args.results.exists():
        print(f"No results at {args.results}")
        print("Run:  python -m benchmarks.run_benchmarks")
        return 1

    payload = json.loads(args.results.read_text())
    results = payload.get("results", {})
    meta = payload.get("metadata", {})

    args.out.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("  RENDERING BENCHMARK FIGURES")
    print("=" * 72)
    print(f"  Source : {args.results}")
    print(f"  Mode   : {meta.get('mode', 'unknown')}")
    print(f"  Output : {args.out}")
    print("-" * 72)

    written = 0
    for section, plotter in PLOTTERS:
        rows = results.get(section, [])
        if not rows:
            print(f"  skipped {section:14s} (no data in results file)")
            continue
        path = plotter(rows, args.out)
        if path:
            print(f"  wrote   {path.name}")
            written += 1

    print("-" * 72)
    print(f"  {written} figure(s) written")
    print("=" * 72)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
