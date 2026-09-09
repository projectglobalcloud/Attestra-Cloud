# Deploying Attestra to Render

Same application, same Docker image as the Fly.io setup — The blueprint is `render.yaml` at the repository root (Render only looks
there); it points at `../fly.io/Dockerfile`, so there is one image to maintain, not two.

---

## The two things to decide first

### 1. Free will not work for this app

Render's free plan has **no persistent disk**. The filesystem resets on every
restart and deploy, and free services also sleep after 15 minutes idle. For
this platform that means, on every restart:

- every account gone,
- every uploaded dataset gone,
- every issued deed gone,
- the registry's own signing key regenerated — so deeds issued before it can no
  longer be verified against the key printed on them.

That is not a demo you want a judge clicking into. So:

| | | |
|---|---|---|
| Web service, **Starter** plan | 512 MB RAM, always on | **$7.00/mo** |
| Persistent disk | 3 GB | **$0.75/mo** |
| | | **≈ $7.75/month** |

(Fly.io works out at about $6/month for a 1 GB machine. Render is a little
more, and gives you 512 MB rather than 1 GB at that price — enough for this
app, but with less headroom for the pairing arithmetic.)

### 2. How the code reaches Render

Render deploys from a **Git repository** or from a **pre-built Docker image in a
registry**. It cannot take an upload from your laptop the way `fly deploy` can.

- **Route A — private GitHub repo (easiest, all in the browser).** Push this
  project to a private repo, then in Render: New → Blueprint → pick the repo.
  Render builds the Dockerfile itself. Every later change is `git push`.
- **Route B — no GitHub.** Build the image on your machine and push it to Docker
  Hub, then create the service from that image. No repository anywhere, but you
  rebuild and push by hand for each change.

You said earlier you did not want to be forced through GitHub. For Render,
Route A is genuinely the smoother one — but Route B is written out below.

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
| Back up the database | Shell tab: `cp /data/instance/attestra.db /tmp/` then download |
| Load the demo state | Shell tab: `cd /app/web && ATTESTRA_BASE=http://127.0.0.1:$PORT python3 seed_scenario.py` |

### Never change these two

1. **One instance.** `numInstances: 1`. Scaling out breaks the in-memory model
   described at the top of `render.yaml`.
2. **Keep the disk.** `/data` holds the database, the uploads and
   `issuer_ed25519`. **Back that key up** — lose it and previously issued deeds
   can no longer be verified against the key printed on them.

### If Render ever sleeps or restarts the service

Nothing is lost while the disk is attached. The protection keys are held in
memory only, so they go — but the app now rebuilds them automatically the first
time anybody touches a dataset, and the Storage page has a **Protect all**
button to seal everything again in one press.
