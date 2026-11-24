"""
Start the Attestra platform on this computer.

    Windows :  START-WINDOWS.bat      (double-click, or run it from Command Prompt)
    macOS   :  bash START-MAC.command (or double-click it in Finder)

Both of those simply find Python and run this file. What it does:

  1. creates a private Python environment in .venv/ (first run only)
  2. installs the libraries the platform needs into it (first run only,
     needs an internet connection)
  3. generates the five sample consortium datasets (first run only)
  4. starts the web server on http://127.0.0.1:5050
  5. on the very first run, loads the demonstration scenario: the demo
     account, five protected datasets, parties, listings, completed sales
     and issued certificates
  6. opens the site in the default browser

Nothing is installed system-wide and nothing outside this folder is changed.
Press Ctrl+C in this window to stop the server.

Options:
    --port N       use another port (default 5050)
    --no-browser   do not open the browser
    --no-demo      start with an empty platform (first run only)
    --reseed       rebuild the demonstration scenario on this run
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
CODE_DIR = HERE / "code"
VENV_DIR = HERE / ".venv"
INSTANCE_DIR = WEB_DIR / "instance"
LOG_FILE = INSTANCE_DIR / "server.log"
SEED_MARK = INSTANCE_DIR / ".demo-loaded"
DEPS_MARK = VENV_DIR / ".attestra-deps"

IS_WINDOWS = os.name == "nt"
DEFAULT_PORT = 5050

DEMO_EMAIL = "demo@attestra.test"
DEMO_PASSWORD = "examiner2026"

# What the platform needs to run locally. The upper bounds keep a future major
# release from changing an API underneath the project; the lower bounds are the
# versions it was built and tested on. gunicorn (the deployment server) and
# matplotlib (only used to redraw the benchmark figures) are left out on
# purpose: neither is needed to run the platform here.
REQUIREMENTS = [
    "flask>=3.0,<4",
    "flask-sqlalchemy>=3.1,<4",
    "flask-login>=0.6,<0.7",
    "sqlalchemy>=2.0,<3",
    "cryptography>=42",
    "py_ecc>=7.0,<9",
    "pytest>=7.4",
]

MIN_PYTHON = (3, 10)


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------

def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"  > {msg}")


def fail(msg: str, hint: str | None = None) -> None:
    say()
    say(f"  ERROR: {msg}")
    if hint:
        say(f"  {hint}")
    say()
    sys.exit(1)


def tail(path: Path, lines: int = 25) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join("    " + line for line in text[-lines:])


# ----------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------

def child_env(port: int | None = None) -> dict:
    env = dict(os.environ)
    # UTF-8 everywhere: the console output and the files the project writes
    # contain characters a Windows code page cannot represent.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    # A leftover setting from someone's shell must not change how this runs.
    for name in ("DATABASE_URL", "ATTESTRA_INSTANCE_DIR", "ATTESTRA_DEV_LOGIN",
                 "ATTESTRA_ISSUER_KEY", "SECRET_KEY"):
        env.pop(name, None)
    if port is not None:
        env["ATTESTRA_PORT"] = str(port)
        env["ATTESTRA_BASE"] = f"http://127.0.0.1:{port}"
    return env


def venv_python() -> Path:
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run_quiet(cmd: list, log: Path, cwd: Path | None = None) -> int:
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n$ {' '.join(str(c) for c in cmd)}\n")
        fh.flush()
        return subprocess.call(cmd, cwd=str(cwd) if cwd else None,
                               stdout=fh, stderr=subprocess.STDOUT,
                               env=child_env())


def ensure_venv(setup_log: Path) -> Path:
    py = venv_python()
    if py.exists():
        ok = subprocess.call([str(py), "-c", "import sys"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL) == 0
        if ok:
            return py
        step("The private Python environment is damaged; rebuilding it")
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    step("Creating a private Python environment (first run only)")
    code = run_quiet([sys.executable, "-m", "venv", str(VENV_DIR)], setup_log)
    if code != 0 or not py.exists():
        fail("could not create the Python environment.",
             f"Details are in {setup_log}")
    return py


def ensure_packages(py: Path, setup_log: Path) -> None:
    wanted = hashlib.sha256("\n".join(REQUIREMENTS).encode()).hexdigest()
    if DEPS_MARK.exists() and DEPS_MARK.read_text().strip() == wanted:
        probe = subprocess.call(
            [str(py), "-c", "import flask, flask_sqlalchemy, flask_login, "
                            "cryptography, py_ecc"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe == 0:
            return

    step("Installing the required libraries (first run only, about a minute)")
    run_quiet([str(py), "-m", "pip", "install", "--upgrade", "--quiet",
               "--disable-pip-version-check", "pip"], setup_log)
    code = run_quiet([str(py), "-m", "pip", "install", "--quiet",
                      "--disable-pip-version-check", *REQUIREMENTS], setup_log)
    if code != 0:
        say(tail(setup_log, 15))
        fail("the libraries could not be installed.",
             "Check the internet connection and run the start command again.")
    DEPS_MARK.write_text(wanted)


def ensure_datasets(py: Path, setup_log: Path) -> None:
    manifests = [CODE_DIR / "datasets" / "data" / name / "manifest.json"
                 for name in ("ai-training-corpus", "financial-ledger",
                              "genomics-variants", "iot-telemetry",
                              "medical-imaging")]
    if all(m.exists() for m in manifests):
        return
    step("Generating the five sample datasets (first run only, ~125 MB)")
    code = run_quiet([str(py), "-m", "datasets.generate", "--dataset", "all",
                      "--size", "medium", "--quiet"], setup_log, cwd=CODE_DIR)
    if code != 0:
        say(tail(setup_log, 15))
        fail("the sample datasets could not be generated.",
             f"Details are in {setup_log}")


# ----------------------------------------------------------------------
# Server
# ----------------------------------------------------------------------

def http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return 0, ""


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def already_running(port: int) -> bool:
    status, body = http_get(f"http://127.0.0.1:{port}/", timeout=2)
    return status == 200 and "Attestra" in body


def pick_port(preferred: int) -> int:
    for port in [preferred] + list(range(5051, 5100)):
        if port_free(port):
            return port
    fail("no free port was found between 5050 and 5099.")
    return preferred


def start_server(py: Path, port: int) -> subprocess.Popen:
    log = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
    log.write(f"\n===== server start {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"on port {port} =====\n")
    log.flush()
    return subprocess.Popen([str(py), "app.py"], cwd=str(WEB_DIR),
                            stdout=log, stderr=subprocess.STDOUT,
                            env=child_env(port))


def wait_until_ready(proc: subprocess.Popen, port: int, limit: int = 180) -> None:
    deadline = time.time() + limit
    while time.time() < deadline:
        if proc.poll() is not None:
            say(tail(LOG_FILE))
            fail("the server stopped while starting.",
                 f"The full log is in {LOG_FILE}")
        status, _ = http_get(f"http://127.0.0.1:{port}/", timeout=2)
        if status == 200:
            return
        time.sleep(1)
    proc.terminate()
    fail("the server did not answer within three minutes.",
         f"The log is in {LOG_FILE}")


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ----------------------------------------------------------------------
# Demonstration scenario
# ----------------------------------------------------------------------

def demo_account_exists() -> bool:
    db = INSTANCE_DIR / "attestra.db"
    if not db.exists():
        return False
    import sqlite3
    try:
        con = sqlite3.connect(str(db))
        row = con.execute("SELECT 1 FROM user WHERE email = ?",
                          (DEMO_EMAIL,)).fetchone()
        con.close()
        return row is not None
    except sqlite3.Error:
        return False


def create_demo_account(port: int) -> bool:
    """Register the demo examiner. On a new platform the first account becomes
    the registry operator (administrator)."""
    form = urllib.parse.urlencode({
        "name": "Demo Examiner",
        "email": DEMO_EMAIL,
        "password": DEMO_PASSWORD,
        "organization": "Global Academy of Technology",
        "party_type": "institution",
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/signup", data=form), timeout=60).read()
    except Exception:
        pass
    return demo_account_exists()


def load_demo(py: Path, port: int) -> None:
    step("Loading the demonstration scenario (first run only, a few minutes)")
    if not demo_account_exists() and not create_demo_account(port):
        say("    The demo account could not be created; the platform is "
            "running, but empty.")
        return
    seed_log = INSTANCE_DIR / "demo-setup.log"
    with open(seed_log, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen([str(py), "seed_scenario.py"], cwd=str(WEB_DIR),
                                stdout=fh, stderr=subprocess.STDOUT,
                                env=child_env(port))
        started = time.time()
        while proc.poll() is None:
            time.sleep(5)
            mins, secs = divmod(int(time.time() - started), 60)
            print(f"\r    working... {mins}:{secs:02d}", end="", flush=True)
        print("\r" + " " * 30 + "\r", end="", flush=True)
    if proc.returncode == 0:
        SEED_MARK.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
        step("Demonstration scenario loaded")
    else:
        say("    The demonstration scenario did not finish; the platform is "
            "running and you can sign in, but some sample data may be missing.")
        say(f"    Details are in {seed_log}")


def restore_protection(port: int) -> None:
    """Re-seal the datasets after a restart.

    The protection keys are held only in the server's memory (by design: they
    are never written to disk), so after a restart every dataset reads as
    needing protection. The console's "Protect all" button on the Storage page
    rebuilds them; this presses it on the operator's behalf so the platform is
    ready the moment the browser opens. If the demo password was changed, it is
    skipped and the button is still there.
    """
    import http.cookiejar
    import json

    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        opener.open(base + "/signin", timeout=30).read()
        opener.open(urllib.request.Request(base + "/signin", data=urllib.parse.urlencode(
            {"email": DEMO_EMAIL, "password": DEMO_PASSWORD}).encode()), timeout=30).read()
        with opener.open(urllib.request.Request(
                base + "/api/datasets/protect-all", data=b"{}", method="POST",
                headers={"Content-Type": "application/json"}), timeout=60) as resp:
            started = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            started = json.loads(exc.read().decode())
        except Exception:
            return
    except Exception:
        return
    if not started.get("ok") or not started.get("job_id"):
        return  # nothing needed protecting, or the demo account was changed

    step(f"Restoring protection for {started.get('count', 0)} dataset(s) "
         f"(a few seconds)")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(1)
        try:
            with opener.open(f"{base}/api/jobs/{started['job_id']}", timeout=30) as resp:
                job = json.loads(resp.read().decode()).get("job", {})
        except Exception:
            continue
        if job.get("status") in ("done", "error"):
            if job.get("message"):
                say(f"    {job['message']}")
            return


# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Attestra platform.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-demo", action="store_true")
    parser.add_argument("--reseed", action="store_true")
    args = parser.parse_args()

    say()
    say("  Attestra: Cloud-Based Auditing System with Efficient Ownership")
    say("  Management for Group Data Transaction")
    say("  " + "-" * 64)

    if sys.version_info < MIN_PYTHON:
        fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required "
             f"(this is {sys.version.split()[0]}).",
             "Install it from https://www.python.org/downloads/ and run the "
             "start command again.")

    if not (WEB_DIR / "app.py").exists() or not CODE_DIR.exists():
        fail("this file must stay inside the project's code folder, next to "
             "the 'web' and 'code' folders.")

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    if already_running(args.port):
        url = f"http://127.0.0.1:{args.port}"
        step(f"The platform is already running at {url}; opening it")
        if not args.no_browser:
            webbrowser.open(url)
        return

    setup_log = INSTANCE_DIR / "setup.log"
    py = ensure_venv(setup_log)
    ensure_packages(py, setup_log)
    ensure_datasets(py, setup_log)

    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}"
    step(f"Starting the server on {url}")
    proc = start_server(py, port)
    try:
        wait_until_ready(proc, port)

        first_run = not SEED_MARK.exists()
        if args.reseed or (first_run and not args.no_demo):
            load_demo(py, port)
        elif first_run and args.no_demo:
            SEED_MARK.write_text("skipped")
        else:
            restore_protection(port)

        say()
        say("  " + "=" * 64)
        say(f"  Attestra is running at   {url}")
        say()
        if demo_account_exists():
            say(f"  Demo sign-in   email    : {DEMO_EMAIL}")
            say(f"                 password : {DEMO_PASSWORD}")
        else:
            say("  Create an account with Sign up; the first account becomes")
            say("  the administrator.")
        say()
        say("  Keep this window open while you use the site.")
        say("  To stop the server, press Ctrl+C here (or close this window).")
        say("  " + "=" * 64)
        say()

        if not args.no_browser:
            webbrowser.open(url)

        while proc.poll() is None:
            time.sleep(1)
        say(tail(LOG_FILE))
        fail("the server stopped unexpectedly.", f"The full log is in {LOG_FILE}")
    except KeyboardInterrupt:
        say()
        step("Stopping the server")
        stop_server(proc)
        step("Stopped. Run the start command again to restart it.")
    finally:
        stop_server(proc)


if __name__ == "__main__":
    main()
