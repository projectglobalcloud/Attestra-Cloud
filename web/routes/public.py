"""Public marketing site (cloud-vendor style) + file serving."""

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from settings import CODE_DIR, DOCS_DIR, REPORT_DIR

bp = Blueprint("public", __name__)

# ---------------------------------------------------------------------------
# Catalog data used across the marketing site (single source of truth)
# ---------------------------------------------------------------------------

PRODUCTS = {
    "integrity-verification": {
        "category": "Integrity",
        "name": "Integrity Verification",
        "tagline": "Continuous proof-of-possession for data held in third-party storage",
        "summary": "Challenge your storage provider to prove — from the stored bytes "
                   "themselves — that every block is exactly as deposited. Without "
                   "downloading anything.",
        "offer": "Unlimited verifications on protected datasets",
        "benefits": [
            ("Fixed-size proofs", "Roughly 128 bytes per verification, whether the dataset is 100 MB or 100 TB. Network cost never grows with data."),
            ("Sub-second checks", "Typical verification completes in under a second on commodity hardware, with a constant number of core operations."),
            ("Tunable confidence", "Challenge more blocks for higher statistical coverage — 99%+ detection of a 1% corruption at recommended settings."),
            ("Deterministic detection", "Any challenged block that was altered or lost fails verification. No heuristics, no sampling of file hashes."),
        ],
        "specs": [
            ("Proof size", "~128 bytes, constant"),
            ("Verification latency", "< 1 s typical (this deployment)"),
            ("Detection at c=460, 1% corruption", "≥ 99%"),
            ("Data downloaded per check", "0 bytes"),
            ("Auditor trust required", "None — publicly verifiable parameters"),
        ],
        "related": ["ownership-transfer", "security-validation", "consortium-management"],
    },
    "ownership-transfer": {
        "category": "Custody",
        "name": "Ownership Transfer",
        "tagline": "Sell or hand over datasets with cryptographic finality",
        "summary": "A transfer is a short signed exchange between organizations. The "
                   "storage layer re-seals every block in place — the buyer's "
                   "verifications pass, the seller's stop, permanently.",
        "offer": "Unlimited transfers between ownership groups",
        "benefits": [
            ("Zero data movement", "No re-upload, no re-encryption window, no double-custody period. The dataset never leaves storage."),
            ("Provable revocation", "The previous owner's credentials demonstrably stop verifying — run them yourself and watch them fail."),
            ("Fixed-size handover", "The update applied to storage is the same size for a 2-organization deal or a 200-organization consortium."),
            ("Chained resales", "Today's buyer is tomorrow's seller. Custody chains stay verifiable end to end."),
        ],
        "specs": [
            ("Data moved per transfer", "0 bytes"),
            ("Storage update size", "Fixed, independent of group size"),
            ("Typical completion", "Seconds at demonstration sizes"),
            ("Seller access after transfer", "Provably revoked"),
            ("Custody history", "Fully auditable"),
        ],
        "related": ["integrity-verification", "consortium-management", "security-validation"],
    },
    "consortium-management": {
        "category": "Custody",
        "name": "Consortium Management",
        "tagline": "Shared ownership without shared secrets",
        "summary": "Hospitals, agencies, and labs co-own data as equals. Every member "
                   "signs with its own key; no member can act alone; membership "
                   "changes never disturb the rest of the group.",
        "offer": "Unlimited member organizations per dataset",
        "benefits": [
            ("No trusted dealer", "Each organization generates its own keys locally. Nothing is pooled, escrowed, or shared."),
            ("Frictionless joining", "Admit new members without re-keying existing ones — they don't even need to be online."),
            ("Permanent revocation", "Removed members immediately and irreversibly lose the ability to act for the group."),
            ("Work scales with change", "A 100-member consortium admitting 2 members does 2 members' worth of work — not 100."),
        ],
        "specs": [
            ("Group size limit", "None in the protocol"),
            ("Re-key on membership change", "Never"),
            ("Members online for a change", "Only those joining or leaving"),
            ("Single-member unilateral action", "Impossible by construction"),
            ("Typical membership operation", "Milliseconds"),
        ],
        "related": ["ownership-transfer", "integrity-verification", "storage-catalog"],
    },
    "security-validation": {
        "category": "Governance",
        "name": "Security Validation Suite",
        "tagline": "We attack our own platform, live, while you watch",
        "summary": "Adversarial scenarios — post-sale collusion, key substitution, "
                   "storage tampering — executed against a live deployment, phase by "
                   "phase, with verdicts for legacy designs and for Attestra.",
        "offer": "All validations runnable on demand",
        "benefits": [
            ("Real attacks, not slides", "Each validation runs the genuine adversarial procedure against the production protocol in an isolated environment."),
            ("Side-by-side verdicts", "The same attack is executed against the legacy designs it defeats — so you see what 'protected' actually means."),
            ("No collateral damage", "Every run confirms honest operations continue to pass while the attack fails."),
            ("Statistical coverage", "Tamper detection includes hundreds of sampling trials measured against the theoretical model."),
        ],
        "specs": [
            ("Validation scenarios", "3 (collusion, key substitution, tampering)"),
            ("Execution environment", "Isolated, per run"),
            ("Typical runtime", "1–4 minutes per validation"),
            ("Legacy-design outcome", "Compromised in every scenario"),
            ("Attestra outcome", "Protected in every scenario"),
        ],
        "related": ["integrity-verification", "ownership-transfer", "analytics"],
    },
    "storage-catalog": {
        "category": "Integrity",
        "name": "Protected Storage Catalog",
        "tagline": "Five consortium-scale datasets, ready to protect",
        "summary": "Healthcare imaging, financial ledgers, genomics, civic telemetry "
                   "and AI corpora — synthetic, deterministic, and structured like "
                   "real data lakes with multi-shard layouts.",
        "offer": "All 5 datasets included free",
        "benefits": [
            ("Real-world shapes", "Multi-owner scenarios modeled on actual data-sale situations, from hospital consortia to labeling vendors."),
            ("Fully synthetic", "No real personal, clinical or financial records anywhere — safe for demos, training and evaluation."),
            ("Deterministic", "Identical seeds reproduce identical datasets byte for byte, keeping every measurement reproducible."),
            ("Shard-aware", "8 MB sharding like a production data lake, so partial audits work the way they would at scale."),
        ],
        "specs": [
            ("Datasets", "5 scenarios"),
            ("Size tiers", "2 MB / 25 MB / 100 MB / 512 MB"),
            ("Records", "1M+ across the catalog"),
            ("Real data", "None — 100% synthetic"),
            ("Reproducibility", "Seeded, byte-exact"),
        ],
        "related": ["integrity-verification", "consortium-management", "analytics"],
    },
    "analytics": {
        "category": "Insights",
        "name": "Custody Analytics",
        "tagline": "Live measurement of every operation, charted from real data",
        "summary": "Verification latency, group-setup scaling, membership-change and "
                   "transfer costs — every chart in the console is rendered from live "
                   "measurements of this deployment, never from a datasheet.",
        "offer": "Included with every account",
        "benefits": [
            ("Deployment-local truth", "Numbers come from your hardware and your operations — the honest baseline for capacity planning."),
            ("Scaling made visible", "Watch linear-vs-quadratic behavior directly: the platform's headline advantage, plotted from measurements."),
            ("Operational history", "Every verification's latency and outcome, charted as your audit trail grows."),
            ("No stale screenshots", "Charts are generated at view time from the measurement store."),
        ],
        "specs": [
            ("Chart source", "Live measurement JSON"),
            ("Deployment ops charted", "All verifications"),
            ("Benchmark dimensions", "6 operation families"),
            ("Export", "Underlying JSON available"),
            ("Refresh", "Every page view"),
        ],
        "related": ["integrity-verification", "security-validation", "storage-catalog"],
    },
}

FREE_TIER = [
    {"name": "Integrity Verification", "offer": "Unlimited verifications", "cat": "Integrity",
     "desc": "Continuous proof-of-possession checks against protected datasets, with fixed-size proofs.", "slug": "integrity-verification"},
    {"name": "Protected Storage", "offer": "5 datasets · 512 MB tiers", "cat": "Integrity",
     "desc": "Place any catalog dataset under protection with your chosen consortium and block size.", "slug": "storage-catalog"},
    {"name": "Ownership Transfer", "offer": "Unlimited transfers", "cat": "Custody",
     "desc": "Hand datasets between organizations with cryptographic finality and zero data movement.", "slug": "ownership-transfer"},
    {"name": "Consortium Management", "offer": "Unlimited members", "cat": "Custody",
     "desc": "Admit and remove member organizations without re-keying the group.", "slug": "consortium-management"},
    {"name": "Security Validations", "offer": "All 3 scenarios", "cat": "Governance",
     "desc": "Run collusion, key-substitution and tampering scenarios against a live deployment.", "slug": "security-validation"},
    {"name": "Custody Analytics", "offer": "Always free", "cat": "Insights",
     "desc": "Live charts of verification latency, scaling behavior and operation history.", "slug": "analytics"},
    {"name": "Fault Injection", "offer": "Always free", "cat": "Governance",
     "desc": "Alter storage blocks on purpose and watch routine verification catch it.", "slug": "security-validation"},
    {"name": "Custody Event Log", "offer": "Always free", "cat": "Insights",
     "desc": "A complete, timestamped trail of every protection, verification, membership and transfer event.", "slug": "analytics"},
]

FAQ = [
    ("Is this a real working platform or a mock-up?",
     "Everything on this site drives a real cryptographic protocol. Every verification, "
     "transfer and security validation you run in the console executes genuine "
     "protocol operations — nothing is simulated or pre-recorded."),
    ("Do I need a credit card to start?",
     "No. Create an account with an email address and you get the full platform: all "
     "five catalog datasets, unlimited verifications, transfers and validations."),
    ("What data can I protect?",
     "The catalog ships five consortium-scale synthetic datasets modeled on real "
     "data-sale scenarios. They contain no real personal, clinical or financial "
     "records, so you can demonstrate and evaluate freely."),
    ("How is this different from checksums or object-storage ETags?",
     "A checksum verifies data you already downloaded. Attestra makes the storage "
     "provider prove possession of data you never download — and extends that proof "
     "across ownership changes, which no checksum can do."),
    ("What happens to the seller after a dataset is sold?",
     "Their verification credentials provably stop working. You can demonstrate this "
     "in the console: run a verification as the previous owner and watch it fail."),
    ("Where do the performance numbers come from?",
     "From this deployment. The Analytics section charts live measurements — "
     "verification latency, group-setup scaling, membership and transfer costs — "
     "recorded on the machine you're using."),
]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@bp.get("/")
def landing():
    return render_template("public/landing.html", active_page="home",
                           products=PRODUCTS, faq=FAQ[:4])


@bp.get("/free")
def free():
    return render_template("public/free.html", active_page="free",
                           offers=FREE_TIER, faq=FAQ)


@bp.get("/products")
def products():
    cats = ["All", "Integrity", "Custody", "Governance", "Insights"]
    return render_template("public/products.html", active_page="products",
                           products=PRODUCTS, categories=cats)


@bp.get("/products/<slug>")
def product_detail(slug: str):
    product = PRODUCTS.get(slug)
    if product is None:
        abort(404)
    related = [(s, PRODUCTS[s]) for s in product["related"] if s in PRODUCTS]
    return render_template("public/product_detail.html", active_page="products",
                           slug=slug, p=product, related=related)


@bp.get("/pricing")
def pricing():
    return render_template("public/pricing.html", active_page="pricing", faq=FAQ)


@bp.get("/solutions")
def solutions():
    return render_template("public/solutions.html", active_page="solutions")


@bp.get("/trust")
def trust():
    return render_template("public/trust.html", active_page="trust")


@bp.get("/company")
def company():
    return render_template("public/company.html", active_page="company")


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            flash(f"Thank you, {name} — our team will reach out within one business day.",
                  "info")
        return redirect(url_for("public.contact"))
    return render_template("public/contact.html", active_page="contact")


# Legacy path from the previous design.
@bp.get("/platform")
def platform():
    return redirect(url_for("public.products"))


# ---------------------------------------------------------------------------
# File serving (deep links; not part of site navigation)
# ---------------------------------------------------------------------------

@bp.get("/figures/<path:filename>")
def benchmark_figure(filename: str):
    return send_from_directory(CODE_DIR / "benchmarks" / "results" / "figures", filename)


@bp.get("/docs/<path:filename>")
def docs_file(filename: str):
    return send_from_directory(DOCS_DIR, filename)


@bp.get("/report/<path:filename>")
def report_file(filename: str):
    return send_from_directory(REPORT_DIR, filename)
