"""Start Manual Assistant on your own computer.

Reads local.env, written by scripts/first_run.py, then starts the app and opens
your browser at it.

    python scripts/start.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

LOCAL_ENV = PROJECT / "local.env"


def load_env(path: Path) -> dict[str, str]:
    """A plain KEY=VALUE file. No quoting rules, no substitution."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def port_is_free(host: str, port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex((host, port)) != 0


def open_browser_when_ready(url: str, host: str, port: int) -> None:
    for _ in range(120):            # up to a minute; start-up loads dictionaries
        time.sleep(0.5)
        if not port_is_free(host, port):
            webbrowser.open(url)
            return


def main() -> int:
    if not LOCAL_ENV.exists():
        print("local.env is missing, so this computer has not been set up yet.")
        print()
        print("Run this first:")
        print("    python scripts/first_run.py")
        print()
        print("On Windows, double-click:  windows\\Setup Manual Assistant.bat")
        return 2

    for key, value in load_env(LOCAL_ENV).items():
        os.environ.setdefault(key, value)

    if not os.environ.get("SESSION_SECRET"):
        print("local.env has no SESSION_SECRET. Run scripts/first_run.py again.")
        return 2

    users = PROJECT / "users.yaml"
    if not os.environ.get("USERS_YAML") and not users.exists():
        print("users.yaml is missing, so nobody could sign in.")
        print("Run scripts/first_run.py to create an account.")
        return 2

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    url = f"http://{host}:{port}"

    if not port_is_free(host, port):
        print(f"Something is already listening on {host}:{port}.")
        print("Either the app is already running, in which case open " + url + ",")
        print("or change PORT in local.env to another number such as 8099.")
        return 2

    print("Manual Assistant")
    print("================")
    print()
    print(f"  Open this in your browser : {url}")
    print(f"  Manuals and index kept in : {os.environ.get('DATA_DIR')}")
    print(f"  Answer model              : {os.environ.get('LLM_MODEL')}")
    print()
    print("  Your browser will open in a moment.")
    print("  Leave this window open while you use the app.")
    print("  To stop the app, close this window or press Ctrl+C.")
    print()

    threading.Thread(
        target=open_browser_when_ready, args=(url, host, port), daemon=True
    ).start()

    import uvicorn

    try:
        uvicorn.run("app.main:app", host=host, port=port)
    except KeyboardInterrupt:
        pass
    print()
    print("Stopped. Your manuals and index are kept; start it again any time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
