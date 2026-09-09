"""
Configuration for the Attestra web console.

Defaults are chosen so `python3 app.py` works with zero setup: SQLite database
in web/instance/, a generated dev secret key, and the protocol code imported
from the sibling code/ directory.
"""

import os
import secrets
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
PROJECT_DIR = WEB_DIR.parent
CODE_DIR = PROJECT_DIR / "code"
DOCS_DIR = PROJECT_DIR / "Docs"
REPORT_DIR = PROJECT_DIR / "report"
# Everything that must survive a restart — the database, the uploaded files,
# the registry's issuing key — lives here. It is overridable so a deployment
# can point it at a mounted volume; locally it stays where it always was.
INSTANCE_DIR = Path(os.environ.get("ATTESTRA_INSTANCE_DIR") or (WEB_DIR / "instance"))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
# Uploaded datasets are kept on this machine, beside the database — nothing is
# sent to a third-party object store. One directory per dataset.
UPLOAD_DIR = INSTANCE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = int(os.environ.get("ATTESTRA_MAX_UPLOAD_MB", "512")) * 1024 * 1024

# Access Keys are hidden from the console for now: they are a power-user
# feature that adds noise to a demonstration. The machinery behind them is
# untouched and still live — key authentication, the API endpoints and the
# credentials themselves all work — so putting the section back is this one
# switch: ATTESTRA_ACCESS_KEYS=1.
SHOW_ACCESS_KEYS = os.environ.get("ATTESTRA_ACCESS_KEYS", "0") == "1"

# Billing is hidden from the console for the same reason: the engine is real
# and still computes this account's usage against the price sheet, but a
# metered bill is noise in a demonstration of custody and ownership. Bring the
# section back with ATTESTRA_BILLING=1.
SHOW_BILLING = os.environ.get("ATTESTRA_BILLING", "0") == "1"

_SECRET_FILE = INSTANCE_DIR / "secret_key"


def _dev_secret() -> str:
    """Persist a random dev secret so sessions survive restarts."""
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip()
    key = secrets.token_hex(32)
    _SECRET_FILE.write_text(key)
    return key


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or _dev_secret()
    # Report §3.2 names MySQL; SQLite is the zero-setup default. Point
    # DATABASE_URL at MySQL (mysql+pymysql://...) to switch.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{INSTANCE_DIR / 'attestra.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES
