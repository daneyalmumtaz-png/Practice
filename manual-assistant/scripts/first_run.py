"""One-time setup for running Manual Assistant on your own computer.

Asks for an administrator username and password, writes local.env and
users.yaml, and tells you what is missing. Run it once; after that use the start
script.

    python scripts/first_run.py

It can also run without asking anything, which is what an automated install or a
coding assistant needs. The password comes from --password, or the
MA_ADMIN_PASSWORD environment variable, or is generated and printed:

    python scripts/first_run.py --non-interactive --username dmumtaz
    python scripts/first_run.py --non-interactive --username dmumtaz --force
"""

from __future__ import annotations

import argparse
import getpass
import os
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


def dependencies_ready() -> str:
    """Empty string when the app can run, otherwise what is missing."""
    if sys.version_info < (3, 12):
        return (
            f"Python {sys.version_info.major}.{sys.version_info.minor} found, "
            "but 3.12 or newer is needed"
        )
    try:
        import bcrypt  # noqa: F401
        import fastapi  # noqa: F401
        import pymupdf  # noqa: F401
    except ImportError as exc:
        return f"the app's dependencies are not installed ({exc.name} is missing)"
    return ""


def write_users(path: Path, username: str, password: str) -> None:
    from app.auth import hash_password

    path.write_text(
        "# Who may sign in. Roles: admin can upload manuals, user can only ask.\n"
        "# Add more people by copying the three lines below and generating a hash\n"
        "# with:  python -m app.auth hash\n"
        "users:\n"
        f"  - username: {username}\n"
        "    role: admin\n"
        f'    password_hash: "{hash_password(password)}"\n',
        encoding="utf-8",
    )


def write_local_env(path: Path, port: int) -> None:
    path.write_text(
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


def report_ollama(indent: str = "  ") -> None:
    if not ollama_running():
        say(f"{indent}Ollama: not running.")
        say(f"{indent}  The app still works: it will find the right pages in your")
        say(f"{indent}  manuals, but it cannot write an answer until Ollama is")
        say(f"{indent}  installed and running. Get it from https://ollama.com/download")
        return
    models = ollama_models()
    say(f"{indent}Ollama: running")
    for prefix, full, what in (
        ("llama3.1", "llama3.1:8b", "the model that writes answers"),
        ("bge-m3", "bge-m3", "the model that understands Finnish"),
    ):
        if prefix in models:
            say(f"{indent}  {prefix}: ready")
        else:
            say(f"{indent}  {prefix}: NOT downloaded yet ({what})")
            say(f"{indent}    download it with:  ollama pull {full}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python scripts/first_run.py",
        description="Set up Manual Assistant on this computer.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="ask nothing: take the username and password from the options or the "
        "environment, or generate a password and print it",
    )
    parser.add_argument("--username", help="the administrator account to create")
    parser.add_argument(
        "--password",
        help="the account's password. Prefer the MA_ADMIN_PASSWORD environment "
        "variable: a password given here is visible in your shell history",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing users.yaml or local.env instead of keeping it",
    )
    return parser.parse_args(argv)


# ------------------------------------------------------------------ unattended


def run_unattended(args: argparse.Namespace) -> int:
    """Set up without asking anything. Used by automated installs."""
    say("Manual Assistant: setting up without prompts")
    say("===========================================")
    say()

    missing = dependencies_ready()
    if missing:
        say(f"  Cannot continue: {missing}.")
        say("  Run:  pip install -r requirements.txt")
        return 2
    say("  Python and dependencies: ok")

    username = (args.username or "admin").strip()
    if not username or " " in username or not username.isascii():
        say(f"  {username!r} is not a usable username: letters and numbers, no spaces.")
        return 2

    password = args.password or os.environ.get("MA_ADMIN_PASSWORD") or ""
    generated = not password
    if generated:
        password = secrets.token_urlsafe(12)
    if len(password) < 10:
        say("  The password must be at least 10 characters.")
        return 2

    kept_account = USERS.exists() and not args.force
    if kept_account:
        say(f"  {USERS.name} already exists and was kept. Pass --force to replace it.")
    else:
        write_users(USERS, username, password)
        say(f"  Written: {USERS.name}")

    port = 8000 if port_is_free(8000) else 8099
    if LOCAL_ENV.exists() and not args.force:
        say(f"  {LOCAL_ENV.name} already exists and was kept. Pass --force to replace it.")
        port = int(
            next(
                (
                    line.split("=", 1)[1].strip()
                    for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines()
                    if line.strip().startswith("PORT=")
                ),
                str(port),
            )
        )
    else:
        write_local_env(LOCAL_ENV, port)
        say(f"  Written: {LOCAL_ENV.name}")

    report_ollama()

    rule("Done")
    if kept_account:
        say("  Account  : the accounts already in users.yaml were kept")
    else:
        say(f"  Username : {username}")
        if generated:
            say(f"  Password : {password}")
            say("             Generated. Write it down now: only a one-way hash of")
            say("             it is stored, so it cannot be read back.")
        else:
            say("  Password : the one you supplied")
    say(f"  Address  : http://127.0.0.1:{port}")
    say()
    say("  Start the app with:  python scripts/start.py")
    return 0


# ------------------------------------------------------------------ interactive


def run_interactive() -> int:
    say("Manual Assistant: first run")
    say("===========================")
    say()
    say("This asks a few questions, then writes two files next to the app:")
    say(f"  {LOCAL_ENV.name}   settings for your computer")
    say(f"  {USERS.name}    who may sign in")
    say()
    say("Nothing is sent anywhere. Both files stay on this computer.")

    rule("1. Checking what is installed")

    missing = dependencies_ready()
    if missing:
        say(f"  {missing[0].upper()}{missing[1:]}.")
        if "Python" in missing:
            say("  Install it from https://www.python.org/downloads/ and run this again.")
        else:
            say("  Run this first, from the manual-assistant folder:")
            say("      pip install -r requirements.txt")
        return 2
    say(f"  Python {sys.version_info.major}.{sys.version_info.minor}: ok")
    say("  App dependencies: ok")
    report_ollama()

    rule("2. Your sign-in details")
    say("  You need an account to use the app. Make this one an administrator,")
    say("  so you can upload manuals.")
    say()

    create_account = True
    if USERS.exists():
        say(f"  {USERS.name} already exists.")
        create_account = ask_yes("  Replace it with a new account?", default=False)
        if not create_account:
            say("  Keeping the existing accounts.")

    if create_account:
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

        write_users(USERS, username, first)
        say(f"  Written: {USERS.name}")

    rule("3. Settings for this computer")

    port = 8000
    if not port_is_free(port):
        say(f"  Port {port} is already in use, so the app will use 8099 instead.")
        port = 8099

    if LOCAL_ENV.exists():
        say(f"  {LOCAL_ENV.name} already exists.")
        if not ask_yes(
            "  Write a new one? Your current settings would be replaced.", default=False
        ):
            say("  Keeping the existing settings.")
            say()
            say("Done. Start the app with:   python scripts/start.py")
            return 0

    write_local_env(LOCAL_ENV, port)
    say(f"  Written: {LOCAL_ENV.name}")
    say(f"  The app will run at http://127.0.0.1:{port}")

    rule("Done")
    say("  Start the app with:")
    say("      python scripts/start.py")
    say()
    say("  On Windows, double-click:  windows\\Start Manual Assistant.bat")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.non_interactive:
        return run_unattended(args)
    return run_interactive()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped. Nothing was changed.")
        raise SystemExit(1)
