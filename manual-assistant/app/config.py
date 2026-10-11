"""Configuration: config.yaml for wording and retrieval, environment for deployment.

Nothing product-specific belongs in this module. If a string is shown to a user,
it comes from config.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Retrieval:
    chunk_chars: int = 1200
    chunk_overlap_chars: int = 150
    ui_results: int = 8
    token_budget: int = 3000
    rrf_k: int = 60
    section_path_weight: int = 2
    boilerplate_page_share: float = 0.2


@dataclass(frozen=True)
class Settings:
    """Everything the app needs to run, resolved once at import of get_settings()."""

    # Wording, from config.yaml.
    app_name: str
    disclaimer: str
    greeting: str
    products: tuple[str, ...]
    example_questions: tuple[str, ...]
    system_prompt: str
    retrieval: Retrieval

    # Paths.
    data_dir: Path
    manuals_dir: Path
    vectors_dir: Path
    pagecache_dir: Path
    db_path: Path
    static_dir: Path

    # Model endpoints.
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    embed_model: str
    embed_base_url: str
    llm_timeout_s: float
    embed_timeout_s: float
    llm_temperature: float
    llm_max_tokens: int

    # Serving and limits.
    host: str
    port: int
    session_secret: str
    users_file: Path
    users_yaml_inline: str
    max_upload_bytes: int
    max_json_bytes: int
    rate_limit_per_minute: int
    retention_days: int
    page_render_dpi: int
    cookie_secure: bool
    session_max_age_s: int

    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def ollama_embed_url(self) -> str:
        return self.embed_base_url.rstrip("/") + "/api/embed"


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or _env_path("CONFIG_PATH", PROJECT_DIR / "config.yaml")
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    app_cfg = raw.get("app") or {}
    ret_cfg = raw.get("retrieval") or {}
    defaults = Retrieval()
    retrieval = Retrieval(
        chunk_chars=int(ret_cfg.get("chunk_chars", defaults.chunk_chars)),
        chunk_overlap_chars=int(
            ret_cfg.get("chunk_overlap_chars", defaults.chunk_overlap_chars)
        ),
        ui_results=int(ret_cfg.get("ui_results", defaults.ui_results)),
        token_budget=int(ret_cfg.get("token_budget", defaults.token_budget)),
        rrf_k=int(ret_cfg.get("rrf_k", defaults.rrf_k)),
        section_path_weight=int(
            ret_cfg.get("section_path_weight", defaults.section_path_weight)
        ),
        boilerplate_page_share=float(
            ret_cfg.get("boilerplate_page_share", defaults.boilerplate_page_share)
        ),
    )

    data_dir = _env_path("DATA_DIR", PROJECT_DIR / "data")
    llm_base_url = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    # Embeddings are an Ollama-native API, not an OpenAI one, so they get their own
    # base URL. It defaults to the LLM host with the /v1 suffix removed.
    default_embed_base = llm_base_url.rstrip("/")
    if default_embed_base.endswith("/v1"):
        default_embed_base = default_embed_base[: -len("/v1")]

    return Settings(
        app_name=str(app_cfg.get("name", "Manual Assistant")),
        disclaimer=str(app_cfg.get("disclaimer", "")).strip(),
        greeting=str(app_cfg.get("greeting", "")).strip(),
        products=tuple(str(p) for p in (raw.get("products") or [])),
        example_questions=tuple(str(q) for q in (raw.get("example_questions") or [])),
        system_prompt=str(raw.get("system_prompt", "")).strip(),
        retrieval=retrieval,
        data_dir=data_dir,
        manuals_dir=data_dir / "manuals",
        vectors_dir=data_dir / "vectors",
        pagecache_dir=data_dir / "pagecache",
        db_path=_env_path("DB_PATH", data_dir / "app.db"),
        static_dir=_env_path("STATIC_DIR", PROJECT_DIR / "static"),
        llm_base_url=llm_base_url,
        llm_api_key=os.environ.get("LLM_API_KEY", "not-needed-for-ollama"),
        llm_model=os.environ.get("LLM_MODEL", "llama3.1:8b"),
        embed_model=os.environ.get("EMBED_MODEL", "bge-m3"),
        embed_base_url=os.environ.get("EMBED_BASE_URL", default_embed_base),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", 120.0),
        embed_timeout_s=_env_float("EMBED_TIMEOUT_S", 60.0),
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.1),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 700),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8000),
        session_secret=os.environ.get("SESSION_SECRET", ""),
        users_file=_env_path("USERS_FILE", PROJECT_DIR / "users.yaml"),
        users_yaml_inline=os.environ.get("USERS_YAML", ""),
        max_upload_bytes=_env_int("MAX_UPLOAD_MB", 200) * 1024 * 1024,
        max_json_bytes=_env_int("MAX_JSON_BYTES", 16 * 1024),
        rate_limit_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 20),
        retention_days=_env_int("RETENTION_DAYS", 90),
        page_render_dpi=_env_int("PAGE_RENDER_DPI", 110),
        cookie_secure=os.environ.get("COOKIE_SECURE", "0").lower()
        in ("1", "true", "yes"),
        session_max_age_s=_env_int("SESSION_MAX_AGE_S", 12 * 3600),
        raw=raw,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = load_settings()
    for d in (s.data_dir, s.manuals_dir, s.vectors_dir, s.pagecache_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


def reset_settings_cache() -> None:
    """Used by tests that point the app at a temporary data directory."""
    get_settings.cache_clear()
