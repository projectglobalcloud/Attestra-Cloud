"""
Load the demonstration state and exercise the whole ownership-transfer flow.

The protocol keeps its keys in memory only, so a server restart leaves every
dataset "stale". Run this after starting the server to rebuild a complete,
consistent demo: five protected datasets, a populated ownership register,
marketplace listings, two completed sales with issued certificates, one
agreement left awaiting signature and one declined.

    cd web && ATTESTRA_PORT=5050 python3 app.py     # in one terminal
    cd web && python3 seed_scenario.py              # in another

It finishes by verifying a certificate as an outside party would: with no
session, checking the Ed25519 signature independently, and confirming that a
tampered payload is rejected.
"""
import json, sqlite3, time, urllib.error, urllib.parse, urllib.request, http.cookiejar

import os as _os
import pathlib as _pathlib

# Where the app is and where its database is. Both default to a local run and
# are overridable, so this script works against a deployment as well.
BASE = _os.environ.get("ATTESTRA_BASE", "http://127.0.0.1:5050").rstrip("/")
DB = _os.environ.get(
    "ATTESTRA_DB",
    str(_pathlib.Path(_os.environ.get("ATTESTRA_INSTANCE_DIR")
                      or (_pathlib.Path(__file__).resolve().parent / "instance"))
        / "attestra.db"))
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def req(path, data=None, method=None, form=False, anon=False):
    url = BASE + path
    op = urllib.request.build_opener() if anon else opener
    if form:
        r = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    else:
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
        if body: r.add_header("Content-Type", "application/json")
    try:
        with op.open(r) as resp:
            raw = resp.read().decode()
            try: return json.loads(raw)
            except Exception: return {"ok": True, "_len": len(raw), "_html": raw}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "body": e.read().decode()[:400]}

def upload_file(path, file_path, fields):
    """Multipart POST through the same signed-in opener the rest of this uses."""
    import mimetypes, os, uuid

    boundary = "----attestra" + uuid.uuid4().hex
    name = os.path.basename(file_path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    with open(file_path, "rb") as fh:
        parts.append(fh.read())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    r = urllib.request.Request(BASE + path, data=body, method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with opener.open(r) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "body": e.read().decode()[:300]}


DEMO_PASSWORD = "attestra2026"
DEMO_EMAILS = [
    "records@apollo.example", "data@bmtc.example", "custody@helixlab.example",
    "ops@labelworks.example", "custody@meridianbank.example",
    "acquisitions@medai.example", "data@urbananalytics.example",
    "ananya.rao@example.org", "research@autonomyai.example",
    "convenor@imagingcollective.in",
]


class Actor:
    """A demo party with its own login, so it signs its own instruments.

    The registry cannot sign for a party — that is the point of binding
    accounts to register entries — so every party that has to sign anything in
    this scenario is issued credentials and acts for itself.
    """

    def __init__(self, party_id, email, name):
        self.party_id, self.email, self.name = party_id, email, name
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        req(f"/api/parties/{party_id}/account",
            {"email": email, "name": name, "password": DEMO_PASSWORD})
        self.opener.open(BASE + "/signin")
        self.opener.open(urllib.request.Request(
            BASE + "/signin",
            data=urllib.parse.urlencode(
                {"email": email, "password": DEMO_PASSWORD}).encode()))

    def post(self, path, data=None):
        body = json.dumps(data if data is not None else {}).encode()
        r = urllib.request.Request(BASE + path, data=body, method="POST")
        r.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(r) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}",
                    "body": e.read().decode()[:300]}


def wait(job_id, limit=300):
    for _ in range(limit):
        time.sleep(1)
        j = req(f"/api/jobs/{job_id}").get("job", {})
        if j.get("status") in ("done", "error"): return j
    return {"status": "timeout"}

opener.open(BASE + "/signin")
req("/signin", {"email": "demo@attestra.test", "password": "examiner2026"}, form=True)
print("signed in\n")

# ---- clear -----------------------------------------------------------
# Accounts that people actually signed up with are left alone: this script
# resets the DEMONSTRATION state, not the register. Anything held by a party
# that has a login stays exactly where it is.
c = sqlite3.connect(DB)
# The demo logins from a previous run go first, so their parties are no longer
# "real accounts" and get rebuilt with everything else.
c.execute("DELETE FROM user WHERE email IN (%s)"
          % ",".join("?" * len(DEMO_EMAILS)), DEMO_EMAILS)
c.commit()
real = {r[0] for r in c.execute(
    "SELECT party_id FROM user WHERE party_id IS NOT NULL")}
ids = [r[0] for r in c.execute("SELECT id FROM dataset")
       if r[0] not in {d[0] for d in c.execute(
           "SELECT id FROM dataset WHERE owner_party_id IN (%s)"
           % (",".join(str(i) for i in real) or "-1"))}]
keep = ("(%s)" % (",".join(str(i) for i in real) or "-1"))
# The register goes first: a dataset named on an agreement or a deed is
# deliberately not deletable, so the instruments have to be cleared before the
# datasets they refer to.
# An instrument survives only if BOTH its parties survive. Keeping one whose
# counterparty is about to be deleted would leave it pointing at whatever row
# later lands on that id — a register that quietly renames a counterparty is
# worse than one that forgets the transaction.
c.execute(f"DELETE FROM certificate WHERE agreement_id IN "
          f"(SELECT id FROM transfer_agreement WHERE seller_party_id NOT IN {keep} "
          f"OR buyer_party_id NOT IN {keep})")
c.execute(f"DELETE FROM transfer_agreement WHERE seller_party_id NOT IN {keep} "
          f"OR buyer_party_id NOT IN {keep}")
c.execute(f"DELETE FROM listing WHERE seller_party_id NOT IN {keep}")
c.execute(f"DELETE FROM custody_event WHERE dataset_id NOT IN "
          f"(SELECT id FROM dataset WHERE owner_party_id IN {keep})")
c.execute(f"DELETE FROM party_member WHERE party_id NOT IN {keep}")
c.execute(f"DELETE FROM party WHERE id NOT IN {keep}")
c.commit(); c.close()
print(f"registry reset (kept {len(real)} party/parties with a login)")
for i in ids: req(f"/api/datasets/{i}", method="DELETE")
print(f"cleared {len(ids)} demo dataset(s)\n")

# ---- onboard ---------------------------------------------------------
PLAN = [
    ("medical-imaging",    ["apollo-hospital","manipal-hospital","narayana-hospital"], "ap-south-1",     20),
    ("iot-telemetry",      ["city-transport-dept","metro-agency","traffic-agency"],    "ap-southeast-1", 14),
    ("financial-ledger",   ["meridian-regional-bank"],                                 "ap-south-1",     24),
    ("genomics-variants",  ["helix-lab","genome-institute","biosample-lab"],           "eu-central-1",   16),
    ("ai-training-corpus", ["labelworks-vendor"],                                      "us-east-1",      24),
]
ds = {}
print("ONBOARDING")
for name, owners, region, blocks in PLAN:
    r = req("/api/datasets/onboard", {"name": name, "owners": owners,
                                      "region": region, "max_blocks": blocks,
                                      "block_size": 262144})
    if not r.get("ok"): print("  FAIL", name, r); continue
    j = wait(r["job_id"])
    ds[name] = r["dataset_id"]
    print(f"  {name:20} id={r['dataset_id']} {region:15} {j['status']}")
    a = req(f"/api/datasets/{r['dataset_id']}/audit", {"c": 10}).get("result", {})
    print(f"    audit passed={a.get('passed')} {a.get('duration_ms',0):.0f}ms")

# ---- register parties properly ---------------------------------------
print("\nOWNERSHIP REGISTER")
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
auto = {r["slug"]: r["id"] for r in c.execute("SELECT id, slug FROM party")}
c.close()

DETAILS = {
  "apollo-hospital": ("Apollo Hospital Bengaluru", "Apollo Hospitals Enterprise Limited",
                      "institution", "L85110TN1979PLC008035", "Tamil Nadu, India"),
  "city-transport-dept": ("City Transport Department", "Bengaluru Metropolitan Transport Corporation",
                          "institution", "BMTC/GOK/1997", "Karnataka, India"),
  "meridian-regional-bank": ("Meridian Regional Bank", "Meridian Regional Bank Limited",
                             "company", "U65999KA2004PLC034221", "Karnataka, India"),
  "helix-lab": ("Helix Lab", "Helix Genomics Private Limited",
                "startup", "U73100KA2019PTC128844", "Karnataka, India"),
  "labelworks-vendor": ("LabelWorks", "LabelWorks Data Services LLP",
                        "company", "AAF-8821", "Maharashtra, India"),
}
for slug, (disp, legal, ptype, reg, juris) in DETAILS.items():
    pid = auto.get(slug)
    if not pid: continue
    req(f"/api/parties/{pid}", {"display_name": disp, "legal_name": legal,
                                "party_type": ptype, "registration_id": reg,
                                "jurisdiction": juris})
    req(f"/api/parties/{pid}/verify", {"method": "Registration document review"})
    print(f"  amended + verified  {legal}")

BUYERS = [
  ("MedAI Research Institute", "MedAI Research Institute Private Limited", "company",
   "U72900KA2021PTC145902", "Karnataka, India"),
  ("Urban Analytics", "Urban Analytics Labs Private Limited", "startup",
   "U74999KA2022PTC161340", "Karnataka, India"),
  ("Dr. Ananya Rao", "Ananya Rao", "individual", "ABCPR4471K", "Karnataka, India"),
  ("Autonomy AI Lab", "Autonomy AI Research Pte. Ltd.", "company",
   "202145887K", "Singapore"),
]
buyers = {}
for disp, legal, ptype, reg, juris in BUYERS:
    r = req("/api/parties", {"display_name": disp, "legal_name": legal,
                             "party_type": ptype, "registration_id": reg,
                             "jurisdiction": juris,
                             "contact_email": "records@example.org"})
    buyers[disp] = r.get("party_id")
    print(f"  registered          {legal}  ({r.get('ref')})")

c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
parties = {r["slug"]: r["id"] for r in c.execute("SELECT id, slug FROM party")}
c.close()
# verify two buyers, leave one unverified on purpose
req(f"/api/parties/{buyers['MedAI Research Institute']}/verify",
    {"method": "Certificate of incorporation + PAN"})
req(f"/api/parties/{buyers['Autonomy AI Lab']}/verify",
    {"method": "ACRA business profile"})

# ---- a group of individuals, and a dataset of their own ---------------
# Not every holder is a company. A group is registered the way a shared
# repository is: whoever creates it owns it, collaborators join by email, and
# when the group holds data every member is a co-owner with a key of their own.
print("\nA GROUP OF INDIVIDUALS, HOLDING THEIR OWN UPLOADED FILE")
r = req("/api/parties", {
    "display_name": "Bengaluru Imaging Collective",
    "legal_name": "Bengaluru Imaging Collective (unincorporated association)",
    "party_type": "group",
    "jurisdiction": "Karnataka, India",
    "contact_email": "convenor@imagingcollective.in",
    "owner_name": "Vidya Prasad",
    "members": [
        {"email": "r.krishnan@imagingcollective.in", "name": "Ravi Krishnan"},
        {"email": "n.desai@imagingcollective.in", "name": "Nikita Desai"},
    ]})
collective = r.get("party_id")
roll = req(f"/api/parties/{collective}/members").get("members", [])
for m in roll:
    print(f"  {m['role_label']:13} {m['email']:38} owner id {m['slug']}")

# A real file, written here and uploaded through the console's own endpoint.
import random
upload_path = "/tmp/attestra-collective-scans.csv"
with open(upload_path, "w") as fh:
    fh.write("study_id,site,modality,slices,acquired_on\n")
    while fh.tell() < 1_600_000:
        fh.write(f"{random.randint(10**6, 10**7)},BLR-{random.randint(1, 9)},"
                 f"{random.choice(['CT', 'MR', 'CR'])},{random.randint(60, 420)},"
                 f"2026-0{random.randint(1, 9)}-{random.randint(10, 28)}\n")

up = upload_file("/api/datasets/upload", upload_path, {
    "name": "collective-imaging-2026",
    "custom_ref": "DS-COLLECTIVE-01",
    "region": "ap-south-1",
    "audit_every_min": "10",
    "holder_party_id": str(collective),
    "protect_now": "1",
    "max_blocks": "6",
    "block_size": "262144",
})
print(f"  uploaded      {up['ref']}  {up['bytes']:,} bytes  sha256 {up['sha256'][:16]}…")
j = wait(up["job_id"])
print(f"  protected     status={j['status']}  {j.get('result', {}).get('n_blocks')} blocks "
      f"sealed by the {len(roll)} members, verified every 10 minutes")
ds["collective-imaging-2026"] = up["dataset_id"]

# ---- credentials, so each party can act for itself ---------------------
print("\nCREDENTIALS ISSUED BY THE REGISTRY (password: " + DEMO_PASSWORD + ")")
ACCOUNTS = [
    (parties["apollo-hospital"],        "records@apollo.example",      "Apollo Hospital records office"),
    (parties["city-transport-dept"],    "data@bmtc.example",           "BMTC data office"),
    (parties["helix-lab"],              "custody@helixlab.example",    "Helix Lab custody desk"),
    (parties["labelworks-vendor"],      "ops@labelworks.example",      "LabelWorks operations"),
    (parties["meridian-regional-bank"], "custody@meridianbank.example", "Meridian custody desk"),
    (buyers["MedAI Research Institute"], "acquisitions@medai.example",  "MedAI acquisitions"),
    (buyers["Urban Analytics"],          "data@urbananalytics.example", "Urban Analytics data team"),
    (buyers["Dr. Ananya Rao"],           "ananya.rao@example.org",      "Dr. Ananya Rao"),
    (buyers["Autonomy AI Lab"],          "research@autonomyai.example", "Autonomy AI research"),
    (collective,                         "convenor@imagingcollective.in",
                                         "Bengaluru Imaging Collective convenor"),
]
actors = {}
for pid, email, contact in ACCOUNTS:
    actors[pid] = Actor(pid, email, contact)
    print(f"  {email:34} signs for party {pid}")

# ---- marketplace listings -------------------------------------------
print("\nMARKETPLACE LISTINGS")
LISTINGS = [
 (ds["medical-imaging"], parties["apollo-hospital"],
  "De-identified chest radiograph archive",
  "20 sealed blocks of de-identified chest radiography studies pooled by three "
  "hospitals for AI research. Patient identifiers removed at source; the archive "
  "has been under continuous integrity verification since it was placed under "
  "protection.", "Healthcare", 1850000,
  "Research and model training permitted. No redistribution of the raw archive. "
  "Derived model weights may be commercialised."),
 (ds["iot-telemetry"], parties["city-transport-dept"],
  "City transport telemetry — 14 sealed blocks",
  "Vehicle telemetry collected across the metropolitan bus fleet: position, "
  "occupancy and route adherence. Suitable for congestion modelling and transit "
  "demand forecasting.", "Mobility", 640000,
  "Non-exclusive. Attribution required in published work."),
 (ds["genomics-variants"], parties["helix-lab"],
  "Population variant call set",
  "Curated variant calls contributed by three laboratories under a shared "
  "consortium agreement. Consented for secondary research use.",
  "Life sciences", 2400000,
  "Secondary research use only. Re-identification attempts prohibited."),
 (ds["financial-ledger"], parties["meridian-regional-bank"],
  "Retail settlement ledger — 24 sealed blocks",
  "Anonymised retail settlement records covering a full financial year, sealed "
  "block by block and continuously verified. Prepared for statutory audit and "
  "fraud-model development.", "Financial services", 1450000,
  "Audit and model development permitted. No re-identification of counterparties."),
 (ds["ai-training-corpus"], parties["labelworks-vendor"],
  "Human-labelled instruction corpus",
  "24 blocks of human-written instruction/response pairs with reviewer "
  "adjudication. Delivered with label provenance intact.",
  "AI training data", 980000,
  "Perpetual training licence. Resale of the corpus itself not permitted."),
]
listing_ids = {}
for dsid, seller, title, summary, cat, price, terms in LISTINGS:
    r = req("/api/listings", {"dataset_id": dsid, "seller_party_id": seller,
                              "title": title, "summary": summary, "category": cat,
                              "price": price, "currency": "INR",
                              "licence_terms": terms})
    listing_ids[title] = r.get("listing_id")
    print(f"  listed  ₹{price:>10,}  {title}")

# ---- THE SCENARIO ----------------------------------------------------
print("\n" + "=" * 66)
print("SCENARIO: Apollo sells the radiograph archive to MedAI")
print("=" * 66)

r = req("/api/agreements", {
    "dataset_id": ds["medical-imaging"],
    "listing_id": listing_ids["De-identified chest radiograph archive"],
    "seller_party_id": parties["apollo-hospital"],
    "buyer_party_id": buyers["MedAI Research Institute"],
    "consideration": 1850000, "currency": "INR",
    "payment_reference": "HDFC/NEFT/2026090100418872",
    "terms": "Ownership of the sealed archive transfers in full on execution. "
             "The transferor retains no right to verify, re-sell or re-license "
             "the archive after completion. Research and model training "
             "permitted; redistribution of the raw archive is not.",
})
print(f"1. Agreement raised: {r.get('ref')}")
aid = r["agreement_id"]

seller_actor = actors[parties["apollo-hospital"]]
buyer_actor = actors[buyers["MedAI Research Institute"]]
print(f"2. Apollo signs, in its own account… "
      f"{seller_actor.post(f'/api/agreements/{aid}/sign-seller').get('status_label')}")
print("   (nothing has moved yet — ownership still with Apollo)")

print("3. MedAI countersigns, in its own account → transfer executes")
r = buyer_actor.post(f"/api/agreements/{aid}/sign-buyer")
j = wait(r["job_id"])
for line in j.get("log", []): print("     ", line)
cert_id = j.get("result", {}).get("certificate_id")
print(f"   status={j['status']}  certificate={cert_id}")

# second sale, different party shape: department -> startup
print("\nSCENARIO 2: City Transport (institution) sells telemetry to a startup")
r = req("/api/agreements", {
    "dataset_id": ds["iot-telemetry"],
    "listing_id": listing_ids["City transport telemetry — 14 sealed blocks"],
    "seller_party_id": parties["city-transport-dept"],
    "buyer_party_id": buyers["Urban Analytics"],
    "consideration": 640000, "currency": "INR",
    "payment_reference": "ICICI/RTGS/2026090100022145",
    "terms": "Non-exclusive transfer of custody for congestion modelling. "
             "Attribution required in published work.",
})
aid2 = r["agreement_id"]
actors[parties["city-transport-dept"]].post(f"/api/agreements/{aid2}/sign-seller")
j2 = wait(actors[buyers["Urban Analytics"]]
          .post(f"/api/agreements/{aid2}/sign-buyer")["job_id"])
cert2 = j2.get("result", {}).get("certificate_id")
print(f"   {r.get('ref')} → {j2['status']}, certificate {cert2}")

# an agreement left mid-flight, and one declined
print("\nOPEN + DECLINED INSTRUMENTS (so the console shows real states)")
r = req("/api/agreements", {
    "dataset_id": ds["genomics-variants"],
    "listing_id": listing_ids["Population variant call set"],
    "seller_party_id": parties["helix-lab"],
    "buyer_party_id": buyers["Dr. Ananya Rao"],
    "consideration": 2400000, "currency": "INR",
    "payment_reference": "AWAITING",
    "terms": "Secondary research use only.",
})
actors[parties["helix-lab"]].post(f"/api/agreements/{r['agreement_id']}/sign-seller")
print(f"   {r.get('ref')} left awaiting the transferee's signature")

r = req("/api/agreements", {
    "dataset_id": ds["ai-training-corpus"],
    "seller_party_id": parties["labelworks-vendor"],
    "buyer_party_id": buyers["Autonomy AI Lab"],
    "consideration": 980000, "currency": "INR",
    "payment_reference": "—",
    "terms": "Perpetual training licence.",
})
actors[parties["labelworks-vendor"]].post(
    f"/api/agreements/{r['agreement_id']}/sign-seller")
actors[buyers["Autonomy AI Lab"]].post(
    f"/api/agreements/{r['agreement_id']}/decline",
    {"reason": "Licence scope too narrow for intended fine-tuning use"})
print(f"   {r.get('ref')} declined by the transferee")

# ---- a resale by the new owner (chain of custody continues) ----------
print("\nRESALE — the new owner lists the asset it just acquired")
r = actors[buyers["MedAI Research Institute"]].post("/api/listings", {
    "dataset_id": ds["medical-imaging"],
    "seller_party_id": buyers["MedAI Research Institute"],
    "title": "Chest radiograph archive — research licence resale",
    "summary": "Acquired from Apollo Hospitals in September 2026 and offered on "
               "for onward research use. Full chain of custody is published with "
               "the listing, and the previous holder's credentials are on record "
               "as revoked.",
    "category": "Healthcare", "price": 2100000, "currency": "INR",
    "licence_terms": "Research use. Chain of custody published with the asset."})
print(f"   relisted at ₹2,100,000 by MedAI  (listing {r.get('listing_id')})")

# ---- schedules -------------------------------------------------------
for name, mins in [("medical-imaging", 5), ("financial-ledger", 15)]:
    req(f"/api/datasets/{ds[name]}/schedule", {"every_min": mins})
print("\nContinuous verification: medical 5 min, financial 15 min")

# ---- PUBLIC VERIFICATION (no session at all) -------------------------
print("\n" + "=" * 66)
print("PUBLIC VERIFICATION — no account, fresh client, no cookies")
print("=" * 66)
if cert_id:
    html = req(f"/verify?id={cert_id}", anon=True).get("_html", "")
    print(f"  GET /verify?id={cert_id}")
    print(f"    'This certificate is valid'  : {'This certificate is valid' in html}")
    print(f"    revocation finding shown     : {'can no longer prove possession' in html}")
    doc = req(f"/certificate/{cert_id}", anon=True).get("_html", "")
    print(f"  GET /certificate/{cert_id}  ({len(doc):,} bytes)")
    j = req(f"/certificate/{cert_id}.json", anon=True)
    print(f"  signed JSON: hash={j['payload_hash'][:20]}…  sig={j['signature'][:20]}…")

    # independent signature check, exactly as a third party would
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    body = json.dumps(j["payload"], sort_keys=True, separators=(",", ":")).encode()
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(j["issuer_public_key"]))
    try:
        pub.verify(base64.b64decode(j["signature"]), body)
        print("  INDEPENDENT Ed25519 CHECK: signature valid ✓")
    except Exception as e:
        print("  INDEPENDENT Ed25519 CHECK: FAILED", e)

    ev = j["payload"]["evidence"]
    print(f"  evidence: pre={ev['pre_transfer_audit']['passed']} "
          f"post={ev['post_transfer_audit']['passed']} "
          f"seller-revoked={ev['transferor_revocation_check']['passed'] is False}")

# tamper test
if cert_id:
    print("\n  TAMPER TEST — alter the price and re-check the signature")
    bad = json.loads(json.dumps(j["payload"]))
    bad["consideration"]["amount"] = 1.0
    body2 = json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()
    try:
        pub.verify(base64.b64decode(j["signature"]), body2)
        print("    signature still valid — THIS WOULD BE A BUG")
    except Exception:
        print("    signature rejected ✓  (a forged price cannot be passed off)")

print("\nDONE")
