# Attestra — web console

A company-style web interface over the protocol implementation in `../code/`.
Public marketing site → sign-up/sign-in → portal-style console where every
action (onboarding, audits, tampering, membership changes, ownership
transfers, attack demonstrations) drives the **real cryptography** through the
same entity layer the CLI demo and tests use. The crypto core is untouched.

## Run

```bash
# prerequisites: ../code/requirements.txt installed (py_ecc etc.)
pip install -r requirements.txt
python3 app.py
# open http://127.0.0.1:5000
```

Sign up with any email/password (stored locally, password hashed). SQLite is
the zero-setup default database; to use MySQL as named in report §3.2:

```bash
pip install pymysql
export DATABASE_URL="mysql+pymysql://user:pass@localhost/attestra"
python3 app.py
```

## What's where

```
web/
├── app.py                 Flask app factory + entry point
├── settings.py            paths, secret key, database URL
├── models.py              User / Dataset / AuditRow / EventRow (accounts + history)
├── services/
│   ├── protocol.py        ProtocolHub — live param/CSP/TPA/groups; calls entities/
│   └── jobs.py            background jobs for slow protocol ops (browser polls)
├── routes/
│   ├── public.py          landing page, document/figure downloads
│   ├── auth.py            sign-up / sign-in / sign-out (Flask-Login)
│   ├── console.py         server-rendered console pages
│   └── api.py             JSON actions: onboard, audit, corrupt, owners, transfer, attacks
├── templates/             Jinja2 (public site, auth, console)
└── static/                css / js / photography
```

## Design decisions worth knowing

- **Secret keys are never persisted.** Keys and masks live only in the
  ProtocolHub in memory; writing them to disk would contradict the trust model
  the scheme enforces. After a server restart an onboarded dataset shows as
  *stale* — onboard it again (seconds at demo sizes). The database keeps only
  accounts and history.
- **Long operations are jobs.** TagGen and the attack scripts take seconds to
  minutes on pure-Python pairings, so they run in worker threads; the browser
  polls `/api/jobs/<id>` and renders a live log (the attack pages stream the
  actual `attacks/*.py` output).
- **Demo sizes are capped** (4–200 blocks per onboarding) so every console
  action stays interactive. The protocol itself has no such limit — the
  benchmarks chapter covers scaling.
- `GET /dev/login` exists for screenshots/demos and is **disabled unless** the
  server is started with `ATTESTRA_DEV_LOGIN=1`. Never set that on a shared
  deployment.

## Stack

Python 3.12 · Flask · Flask-SQLAlchemy · Flask-Login · vanilla JS — matching
the languages/tools named in the report (§3.2: Python, Flask, JavaScript,
MySQL-compatible storage).
