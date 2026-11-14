"""Authentication, kept behind one interface.

Everything the rest of the app uses is in `AuthBackend`, `User` and the three
FastAPI dependencies at the bottom. The prototype backend reads usernames,
bcrypt hashes and roles from users.yaml or the USERS_YAML environment variable.

To move to company single sign-on, write a second backend with the same two
methods, add a route that completes the OpenID Connect code flow (Microsoft
Entra ID, for example) and call `issue_session` with the user it returns. No
route and no template needs to change, because they only ever see `User`.

Command line:
    python -m app.auth hash                 prompt for a password, print the hash
    python -m app.auth check <user> <pass>  verify against the configured users
"""

from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import bcrypt
import yaml
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings, get_settings
from .logsetup import get_logger

log = get_logger("auth")

SESSION_COOKIE = "manual_assistant_session"
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_ADMIN)


@dataclass(frozen=True)
class User:
    username: str
    role: str = ROLE_USER

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@runtime_checkable
class AuthBackend(Protocol):
    """What a replacement sign-on backend has to provide."""

    def authenticate(self, username: str, password: str) -> User | None: ...

    def get_user(self, username: str) -> User | None: ...


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


class LocalUsersBackend:
    """Users from a YAML file or the USERS_YAML environment variable.

    Expected shape:

        users:
          - username: admin
            role: admin
            password_hash: "$2b$12$..."
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._users: dict[str, tuple[User, str]] = {}
        self._source = "none"
        self._load()

    def _load(self) -> None:
        raw: object = None
        if self.settings.users_yaml_inline.strip():
            raw = yaml.safe_load(self.settings.users_yaml_inline)
            self._source = "USERS_YAML"
        elif Path(self.settings.users_file).is_file():
            raw = yaml.safe_load(Path(self.settings.users_file).read_text("utf-8"))
            self._source = str(self.settings.users_file)
        if not isinstance(raw, dict):
            log.warning("no_users_configured", extra={"looked_at": self._source})
            return

        for entry in raw.get("users") or []:
            if not isinstance(entry, dict):
                continue
            username = str(entry.get("username") or "").strip()
            password_hash = str(entry.get("password_hash") or "").strip()
            role = str(entry.get("role") or ROLE_USER).strip().lower()
            if not username or not password_hash:
                continue
            if role not in ROLES:
                log.warning(
                    "unknown_role_ignored", extra={"username": username, "role": role}
                )
                role = ROLE_USER
            self._users[username.lower()] = (User(username, role), password_hash)
        log.info(
            "users_loaded", extra={"count": len(self._users), "source": self._source}
        )

    def authenticate(self, username: str, password: str) -> User | None:
        found = self._users.get((username or "").strip().lower())
        if found is None:
            # Spend the same time as a real check so a missing user and a wrong
            # password are not distinguishable by how long the reply takes.
            verify_password(password, "$2b$12$" + "x" * 53)
            return None
        user, password_hash = found
        if verify_password(password, password_hash):
            return user
        return None

    def get_user(self, username: str) -> User | None:
        found = self._users.get((username or "").strip().lower())
        return found[0] if found else None

    @property
    def count(self) -> int:
        return len(self._users)

    @property
    def source(self) -> str:
        return self._source


_backend: AuthBackend | None = None


def get_backend(settings: Settings | None = None) -> AuthBackend:
    global _backend
    if _backend is None:
        _backend = LocalUsersBackend(settings or get_settings())
    return _backend


def set_backend(backend: AuthBackend | None) -> None:
    """Swap the backend. Used by tests, and by whatever replaces it later."""
    global _backend
    _backend = backend


# --------------------------------------------------------------------- sessions


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError(
            "SESSION_SECRET is not set. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )
    return URLSafeTimedSerializer(settings.session_secret, salt="manual-assistant")


def issue_session(response: Response, user: User, settings: Settings) -> None:
    token = _serializer(settings).dumps({"u": user.username, "r": user.role})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def read_session(request: Request, settings: Settings) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = _serializer(settings).loads(
            token, max_age=settings.session_max_age_s
        )
    except SignatureExpired:
        return None
    except BadSignature:
        log.warning("bad_session_signature")
        return None
    except RuntimeError:
        return None
    username = str(data.get("u") or "")
    role = str(data.get("r") or ROLE_USER)
    if not username:
        return None
    # The role is re-read from the backend, so revoking admin takes effect at
    # once rather than when the cookie expires.
    known = get_backend(settings).get_user(username)
    if known is None:
        return None
    return User(known.username, known.role if known.role in ROLES else role)


# ----------------------------------------------------------------- dependencies


def optional_user(request: Request) -> User | None:
    return read_session(request, get_settings())


def require_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="sign in required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin role required"
        )
    return user


# ------------------------------------------------------------------ command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash", help="prompt for a password and print a bcrypt hash")
    check = sub.add_parser("check", help="verify a password against the configured users")
    check.add_argument("username")
    check.add_argument("password", nargs="?")
    args = parser.parse_args(argv)

    if args.command == "hash":
        first = getpass.getpass("password: ")
        if first != getpass.getpass("repeat: "):
            print("passwords do not match", file=sys.stderr)
            return 2
        if len(first) < 10:
            print("use at least 10 characters", file=sys.stderr)
            return 2
        print(hash_password(first))
        return 0

    password = args.password or getpass.getpass("password: ")
    backend = get_backend()
    user = backend.authenticate(args.username, password)
    if user is None:
        print("no", file=sys.stderr)
        return 1
    print(f"yes, role {user.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
