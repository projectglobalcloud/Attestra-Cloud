# Deploying Attestra to Render

Same application, same Docker image as the Fly.io setup — The blueprint is `render.yaml` at the repository root (Render only looks
there); it points at `../fly.io/Dockerfile`, so there is one image to maintain, not two.

---

## How this deployment keeps its data

The free plan has no persistent disk: the container's filesystem is rebuilt on
every restart and every wake from sleep. Three things would otherwise be lost,
and two of them are now handled.

**The database — handled.** Litestream replicates `attestra.db` to
S3-compatible storage (this deployment uses Supabase Storage) as it changes,
and restores it on boot. Accounts, datasets, parties, agreements, offers,
deeds, custody history and the process feed all survive. Verified by building
the image, signing up, protecting a dataset, destroying the container outright,
and starting a fresh one: the account signed in, the dataset was there, and a
verification passed against the restored state.

**The issuing key — handled.** `ATTESTRA_ISSUER_KEY` holds the registry's
Ed25519 key. It must be set once and never changed: every deed carries the
matching public key, and a verifier checks against it, so a new key silently
invalidates every certificate already issued.

**Uploaded files — NOT handled yet.** The bytes of a file a client uploads live
on the container's filesystem, so they go on a restart. The database row
survives but the dataset cannot be re-protected without them. The bundled
catalog is inside the image, so those five datasets are always available.
Moving uploads to the same bucket is the remaining piece of work.

Two consequences of the free plan worth stating plainly: the service sleeps
after 15 minutes idle, so the first visitor after a quiet spell waits about a
minute; and the verification scheduler does not run while it is asleep.

Moving to a paid plan removes all of this — set `plan: starter`, restore the
`disk:` block in `render.yaml`, and the S3 variables become optional.

---

## Before you start

You need an S3-compatible bucket. Supabase Storage works and needs no payment
card; Cloudflare R2 and AWS S3 also work but ask for one. From the provider,
collect five values: endpoint, region, bucket name, access key id, secret.

---

## Route A — from a private GitHub repository

1. Create an empty **private** repository on GitHub.
2. From the project root:

   ```bash
   cd "/Users/adithyan/Desktop/project global cloud"
   git init
   git add code web fly.io render .dockerignore
   git commit -m "Attestra — protocol, console and deployment"
   git branch -M main
   git remote add origin git@github.com:<you>/attestra.git
   git push -u origin main
   ```

   Note what is **not** committed: `web/instance/` (your local database and
   uploads) is excluded by `.dockerignore` and should stay out of the repo.
3. In Render: **New → Blueprint**, choose the repository. It reads
   `render.yaml` — confirm the plan is **Starter** and the disk is there.
4. **Apply**. The first build takes a few minutes (the 125 MB catalog is in the
   image).
5. When it is live, open the URL and **create your account first** — the first
   account on a fresh deployment becomes the registry administrator.

## Route B — from a Docker image, no GitHub

1. Create a Docker Hub account and log in locally:

   ```bash
   docker login
   ```

2. Build and push (from the project root):

   ```bash
   cd "/Users/adithyan/Desktop/project global cloud"
   docker build --platform linux/amd64 -f fly.io/Dockerfile -t <dockerhub-user>/attestra:latest .
   docker push <dockerhub-user>/attestra:latest
   ```

   `--platform linux/amd64` matters: your Mac is ARM, Render runs x86.

3. In Render: **New → Web Service → Deploy an existing image**, give it
   `<dockerhub-user>/attestra:latest`, then set by hand what `render.yaml`
   would have set:

   - Region **Singapore**, plan **Starter**, instances **1**
   - Disk: name `attestra-data`, mount path `/data`, size **3 GB**
   - Environment variables:
     `ATTESTRA_INSTANCE_DIR=/data/instance`, `ATTESTRA_MAX_UPLOAD_MB=512`,
     `ATTESTRA_ACCESS_KEYS=0`, `ATTESTRA_BILLING=0`,
     `SECRET_KEY=` (paste the output of
     `python3 -c 'import secrets; print(secrets.token_hex(32))'`)
   - Health check path: `/`

4. To update later: rebuild, push, then **Manual Deploy → Deploy latest
   reference** in Render.

---

## After it is live

| What you want | Where |
|---|---|
| Your own domain | Settings → Custom Domains, then the DNS records it prints |
| Logs | The Logs tab (or `render logs` with their CLI) |
| A shell on the server | The Shell tab |
| Back up the database | It is already replicated to your bucket; download it from there |
| Load the demo state | Shell tab: `cd /app/web && ATTESTRA_BASE=http://127.0.0.1:$PORT python3 seed_scenario.py` |

### Never change these two

1. **One instance.** The free plan gives exactly one, which is what this app
   requires: scaling out breaks the in-memory model described at the top of
   `render.yaml`, and Litestream is a single-writer replicator — two writers
   would corrupt the replica.
2. **Never change `ATTESTRA_ISSUER_KEY`.** Every deed carries the matching
   public key and a verifier checks against it, so replacing this value
   silently invalidates every certificate already issued. Keep a copy of it
   somewhere safe.

### If Render sleeps or restarts the service

The database comes back from the replica, so accounts, datasets, agreements and
deeds are all still there. Two things do change:

* Datasets read **stale**, because the protection keys live in memory only. The
  app rebuilds them automatically the first time anybody touches a dataset, and
  the Storage page has a **Protect all** button to seal everything in one press.
* A dataset a client *uploaded* cannot be rebuilt — its bytes went with the
  filesystem. Catalog datasets are in the image and are always fine.
