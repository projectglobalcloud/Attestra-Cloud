# Deploying Attestra to Fly.io

Everything in this folder is the deployment. Nothing in the application had to
change to make it work: the console, the protocol, the register and the
marketplace all run exactly as they do on your machine.

**This has been tested, not just written.** The image in this folder was built
locally, run in a container with a mounted volume, signed up, given a dataset,
protected it, verified it, then restarted — and the account, the dataset, the
file and the process history all survived. See "What was tested" at the end.

---

## 0. What it costs — read this first

**Fly.io is not free.** They ended the free allowance for new accounts, so a
card is required and you are billed for what runs.

For this app, always-on, expect roughly:

| Item | Size | Approx. per month |
|---|---|---|
| One machine, `shared-cpu-1x`, 1 GB RAM | always on | **≈ $5.70** |
| One volume | 3 GB | **≈ $0.45** |
| Outbound bandwidth | a demo's worth | usually $0 |
| | | **≈ $6 / month** |

You can halve the machine cost by dropping to 512 MB (`memory = "512mb"` in
`fly.toml`), but pairing arithmetic in pure Python is memory-hungry; 1 GB is
the comfortable choice for a demo somebody is watching. Confirm today's prices
at <https://fly.io/docs/about/pricing/> before you commit — mine is an
estimate, not a quote.

**If you want it genuinely free instead:** Render's free tier will run this,
with one real trade-off — it sleeps after 15 minutes of inactivity and takes
about a minute to wake, and while it sleeps the scheduled verifications do not
run. The app recovers by itself (protection state is rebuilt automatically),
but a judge clicking your link cold would wait. Tell me and I'll prepare that
instead; it is the same Dockerfile.

---

## 1. Install the Fly CLI (once)

```bash
curl -L https://fly.io/install.sh | sh
```

Then add it to your shell, as the installer tells you (usually):

```bash
export FLYCTL_INSTALL="$HOME/.fly"
export PATH="$FLYCTL_INSTALL/bin:$PATH"
```

Check it:

```bash
fly version
```

## 2. Sign up with your new project email

```bash
fly auth signup
```

A browser opens. Use **the new email you created for this project** and add the
card there. If you already made the account in the browser, use `fly auth login`
instead.

Confirm which account you are on before deploying anything:

```bash
fly auth whoami
```

## 3. Create the app (from the project root — not from this folder)

```bash
cd "/Users/adithyan/Desktop/project global cloud"
fly apps create attestra-registry
```

Pick any free name. Then open `fly.io/fly.toml` and set the first line to the
name you chose:

```toml
app = "attestra-registry"
```

The region is set to `bom` (Mumbai). Change `primary_region` if your audience
is elsewhere — `fly platform regions` lists them.

## 4. Create the volume — this is where your data lives

```bash
fly volumes create attestra_data --region bom --size 3 --app attestra-registry
```

3 GB holds the database, every uploaded dataset and the registry's signing key.
Grow it later with `fly volumes extend`.

> **Why a volume matters here:** the database, the uploaded files, the issuing
> key and the session secret all live on it. Without one, every deploy would
> start the platform empty.

## 5. Set the session secret

```bash
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" --app attestra-registry
```

Without this the app generates one on the volume, which also works — but
setting it explicitly means sessions survive even if you ever recreate the
volume.

## 6. Deploy

Still from the project root:

```bash
fly deploy --config fly.io/fly.toml --dockerfile fly.io/Dockerfile --app attestra-registry
```

The first build takes a few minutes (the consortium catalog is 125 MB and
travels with the image). When it finishes:

```bash
fly open --app attestra-registry
```

Your site is at `https://attestra-registry.fly.dev`.

## 7. Create your registry administrator account

Open the site, click **Create account**, and sign up. **The first account on a
fresh deployment automatically becomes the registry administrator** — the one
that can approve transfers and verify identities. Use the project email.

Everyone after that is an ordinary member acting for their own party, which is
what you want.

## 8. (Optional) Load the demonstration state

If you want the ten demo parties, the five catalog datasets, the listings and
the two completed sales — the state you have been showing:

```bash
fly ssh console --app attestra-registry
cd /app/web
ATTESTRA_BASE="http://127.0.0.1:8080" python3 seed_scenario.py
exit
```

It signs in as `demo@attestra.test`, so create that account first (or edit the
credentials at the top of `seed_scenario.py`). **Skip this entirely** if you
want a clean platform for real clients — the app works perfectly empty, as the
test above proved.

---

## Day-to-day

| What you want | Command |
|---|---|
| Push a change | `fly deploy --config fly.io/fly.toml --dockerfile fly.io/Dockerfile` |
| Watch the logs | `fly logs` |
| Open a shell on the server | `fly ssh console` |
| See what is running | `fly status` |
| Restart it | `fly apps restart attestra-registry` |
| Roll back | `fly releases` then `fly deploy --image <older image>` |
| Back up the database | `fly ssh sftp get /data/instance/attestra.db ./backup.db` |
| Your own domain | `fly certs add attestra.com` then add the DNS records it prints |

### Two things not to change

1. **One machine, one worker.** `fly.toml` runs a single machine and
   `gunicorn.conf.py` runs a single worker with threads. The protection keys,
   the live protocol state and the verification scheduler all live in one
   process's memory by design — keys are never written to disk. A second worker
   or a second machine would be a second, empty universe: a dataset protected
   in one would read as "needs re-protection" in the other.

2. **`auto_stop_machines = false`.** A stopped machine loses the in-memory
   keys. The app now rebuilds them automatically the moment anybody uses a
   dataset, so nothing breaks — but while it is stopped the scheduled
   verifications do not run, and your Monitor page goes quiet.

### Where your data is

```
/data/instance/attestra.db      the register, agreements, deeds, process history
/data/instance/uploads/<id>/    the actual dataset files clients upload
/data/instance/issuer_ed25519   the registry's signing key — every deed is signed with it
/data/instance/secret_key       session signing
```

`/data` is the volume. It survives deploys and restarts. **Back up
`issuer_ed25519`** — lose it and previously issued deeds can no longer be
verified against the key printed on them.

### Hidden sections

Access Keys and Billing are hidden. To bring either back:

```bash
fly secrets set ATTESTRA_ACCESS_KEYS=1 --app attestra-registry   # or ATTESTRA_BILLING=1
```

---

## What was tested before this was written

Locally, with Docker, against this exact Dockerfile and config:

- the image builds (491 MB) and the container boots under gunicorn;
- the public site, marketplace and verification pages serve;
- a brand-new account signs up on an empty volume and becomes the administrator;
- a dataset uploads, is protected with real BLS12-381 tags, and verifies;
- **the container was restarted** — the account, the dataset, its file and the
  process history all survived, and a verification passed afterwards because
  the protection state rebuilt itself automatically;
- the same run was repeated with a clean virtual environment to confirm
  `requirements.txt` is complete (it was missing `cryptography`, which every
  deed signature depends on — now fixed).

## Files here

| File | What it is |
|---|---|
| `Dockerfile` | The image: Python 3.12, the protocol, the console, the catalog |
| `fly.toml` | The Fly app: one machine, the volume mount, health check |
| `gunicorn.conf.py` | One worker, eight threads — and why that is required |
| `entrypoint.sh` | Prepares the volume, then starts gunicorn |
| `README.md` | This file |

The build also uses `.dockerignore` at the project root, which keeps the
report, the docs and your local database out of the image.
