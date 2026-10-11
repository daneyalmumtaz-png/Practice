"""One-time setup for running Manual Assistant on your own computer.

Asks for an administrator username and password, writes local.env and
users.yaml, and tells you what is missing. Run it once; after that use the start
script.

    python scripts/first_run.py
"""

from __future__ import annotations

import getpass
import secrets
import socket
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

LOCAL_ENV = PROJECT / "local.env"
USERS = PROJECT / "users.yaml"
OLLAMA = "http://127.0.0.1:11434"


def say(text: str = "") -> None:
    print(text, flush=True)


def rule(title: str) -> None:
    say()
    say(title)
    say("-" * len(title))


def ask_yes(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        say("Please answer y or n.")


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def ollama_running() -> bool:
    try:
        with urlopen(f"{OLLAMA}/api/tags", timeout=3) as response:
            return response.status == 200
    except (URLError, OSError, ValueError):
        return False


def ollama_models() -> set[str]:
    import json

    try:
        with urlopen(f"{OLLAMA}/api/tags", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return set()
    return {str(m.get("name", "")).split(":")[0] for m in data.get("models", [])}


def main() -> int:
    say("Manual Assistant: first run")
    say("===========================")
    say()
    say("This asks a few questions, then writes two files next to the app:")
    say(f"  {LOCAL_ENV.name}   settings for your computer")
    say(f"  {USERS.name}    who may sign in")
    say()
    say("Nothing is sent anywhere. Both files stay on this computer.")

    # ---------------------------------------------------------------- checks

    rule("1. Checking what is installed")

    if sys.version_info < (3, 12):
        say(f"  Python {sys.version_info.major}.{sys.version_info.minor} found, "
            "but 3.12 or newer is needed.")
        say("  Install it from https://www.python.org/downloads/ and run this again.")
        return 2
    say(f"  Python {sys.version_info.major}.{sys.version_info.minor}: ok")

    try:
        import bcrypt  # noqa: F401
        import fastapi  # noqa: F401
        import pymupdf  # noqa: F401
    except ImportError as exc:
        say(f"  The app's dependencies are not installed ({exc.name} is missing).")
        say("  Run this first, from the manual-assistant folder:")
        say("      pip install -r requirements.txt")
        return 2
    say("  App dependencies: ok")

    have_ollama = ollama_running()
    if have_ollama:
        models = ollama_models()
        say("  Ollama: running")
        for wanted, what in (("llama3.1", "the model that writes answers"),
                             ("bge-m3", "the model that understands Finnish")):
            if wanted in models:
                say(f"    {wanted}: ready")
            else:
                say(f"    {wanted}: NOT downloaded yet ({what})")
                say(f"      download it with:  ollama pull "
                    f"{'llama3.1:8b' if wanted == 'llama3.1' else 'bge-m3'}")
    else:
        say("  Ollama: not running.")
        say("    The app still works: it will find the right pages in your manuals,")
        say("    but it cannot write an answer until Ollama is installed and running.")
        say("    Get it from https://ollama.com/download")

    # ------------------------------------------------------------ the admin user

    rule("2. Your sign-in details")
    say("  You need an account to use the app. Make this one an administrator,")
    say("  so you can upload manuals.")
    say()

    if USERS.exists():
        say(f"  {USERS.name} already exists.")
        if not ask_yes("  Replace it with a new account?", default=False):
            say("  Keeping the existing accounts.")
            username = None
        else:
            username = ""
    else:
        username = ""

    if username is not None:
        from app.auth import hash_password

        while True:
            username = input("  Username (for example dmumtaz): ").strip()
            if username and username.isascii() and " " not in username:
                break
            say("  Use letters and numbers, no spaces.")

        while True:
            first = getpass.getpass("  Password (at least 10 characters): ")
            if len(first) < 10:
                say("  Too short. Use at least 10 characters.")
                continue
            if first != getpass.getpass("  Repeat the password: "):
                say("  The two did not match. Try again.")
                continue
            break

        USERS.write_text(
            "# Who may sign in. Roles: admin can upload manuals, user can only ask.\n"
            "# Add more people by copying the three lines below and generating a hash\n"
            "# with:  python -m app.auth hash\n"
            "users:\n"
            f"  - username: {username}\n"
            "    role: admin\n"
            f'    password_hash: "{hash_password(first)}"\n',
            encoding="utf-8",
        )
        say(f"  Written: {USERS.name}")

    # ------------------------------------------------------------------ settings

    rule("3. Settings for this computer")

    port = 8000
    if not port_is_free(port):
        say(f"  Port {port} is already in use, so the app will use 8099 instead.")
        port = 8099

    if LOCAL_ENV.exists():
        say(f"  {LOCAL_ENV.name} already exists.")
        if not ask_yes("  Write a new one? Your current settings would be replaced.",
                       default=False):
            say("  Keeping the existing settings.")
            say()
            say("Done. Start the app with:   python scripts/start.py")
            return 0

    LOCAL_ENV.write_text(
        "# Settings for running Manual Assistant on this computer.\n"
        "# Written by scripts/first_run.py. Safe to edit by hand.\n"
        "# Keep this file private: the secret below signs your sign-in cookie.\n"
        "\n"
        f"SESSION_SECRET={secrets.token_urlsafe(48)}\n"
        "\n"
        "# Where the app listens. 127.0.0.1 means this computer only, which is what\n"
        "# you want on a laptop. Nobody else on the office network can reach it.\n"
        "HOST=127.0.0.1\n"
        f"PORT={port}\n"
        "\n"
        "# Where manuals, the index and the database are kept.\n"
        f"DATA_DIR={PROJECT / 'data'}\n"
        "\n"
        "# The two models, served by Ollama on this computer.\n"
        f"LLM_BASE_URL={OLLAMA}/v1\n"
        f"EMBED_BASE_URL={OLLAMA}\n"
        "LLM_MODEL=llama3.1:8b\n"
        "EMBED_MODEL=bge-m3\n"
        "\n"
        "# 0 because this is plain http on your own machine. On a real server with\n"
        "# HTTPS this must be 1.\n"
        "COOKIE_SECURE=0\n",
        encoding="utf-8",
    )
    say(f"  Written: {LOCAL_ENV.name}")
    say(f"  The app will run at http://127.0.0.1:{port}")

    rule("Done")
    say("  Start the app with:")
    say("      python scripts/start.py")
    say()
    say("  On Windows, double-click:  windows\\Start Manual Assistant.bat")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped. Nothing was changed.")
        raise SystemExit(1)
