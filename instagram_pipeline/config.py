"""Configuration loaded from environment variables (and an optional .env file).

Secrets are never hard-coded. The access token and IG user id come from:

    IG_ACCESS_TOKEN   long-lived user (or system user) access token
    IG_USER_ID        Instagram Business/Creator account id (not the @handle)

Optional:

    IG_APP_SECRET     if set, every request is signed with appsecret_proof
    IG_GRAPH_HOST     defaults to graph.facebook.com (use graph.instagram.com
                      for tokens issued via "Instagram API with Instagram Login")
    IG_API_VERSION    defaults to v23.0
    IG_TIMEZONE       IANA timezone for the analysis, e.g. Asia/Kolkata
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "graph.facebook.com"
DEFAULT_API_VERSION = "v23.0"


class ConfigError(RuntimeError):
    pass


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader. Existing environment variables always win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    access_token: str
    ig_user_id: str
    app_secret: str | None = None
    host: str = DEFAULT_HOST
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
        user_id = os.environ.get("IG_USER_ID", "").strip()
        missing = [n for n, v in (("IG_ACCESS_TOKEN", token), ("IG_USER_ID", user_id)) if not v]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): " + ", ".join(missing)
                + ". See README.md for how to obtain them."
            )
        return cls(
            access_token=token,
            ig_user_id=user_id,
            app_secret=os.environ.get("IG_APP_SECRET") or None,
            host=os.environ.get("IG_GRAPH_HOST") or DEFAULT_HOST,
            api_version=os.environ.get("IG_API_VERSION") or DEFAULT_API_VERSION,
        )
