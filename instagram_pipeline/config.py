"""Configuration loaded from environment variables (and an optional .env file).

Secrets are never hard-coded. Two kinds of token work:

* Instagram Login token (starts with "IG", no Facebook Page needed). Requests go
  to graph.instagram.com and IG_USER_ID is optional ("me" is used).
* Facebook Login token (starts with "EAA", needs a linked Facebook Page).
  Requests go to graph.facebook.com and IG_USER_ID is required.

    IG_ACCESS_TOKEN   long-lived access token
    IG_USER_ID        Instagram professional account id (not the @handle)

Optional:

    IG_APP_SECRET     if set, every request is signed with appsecret_proof
    IG_GRAPH_HOST     overrides the host picked from the token type
    IG_API_VERSION    defaults to v23.0
    IG_TIMEZONE       IANA timezone for the analysis, e.g. Asia/Kolkata
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "graph.facebook.com"
INSTAGRAM_HOST = "graph.instagram.com"
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

    @property
    def instagram_login(self) -> bool:
        return self.host == INSTAGRAM_HOST

    @classmethod
    def from_env(cls, *, require_user_id: bool = True) -> "Config":
        token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
        host = os.environ.get("IG_GRAPH_HOST") or host_for_token(token)
        user_id = os.environ.get("IG_USER_ID", "").strip()
        if not user_id and host == INSTAGRAM_HOST:
            user_id = "me"  # Instagram Login tokens belong to exactly one IG account
        missing = [n for n, v in (("IG_ACCESS_TOKEN", token), ("IG_USER_ID", user_id))
                   if not v and (n != "IG_USER_ID" or require_user_id)]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): " + ", ".join(missing)
                + ". See README.md for how to obtain them."
            )
        return cls(
            access_token=token,
            ig_user_id=user_id,
            app_secret=os.environ.get("IG_APP_SECRET") or None,
            host=host,
            api_version=os.environ.get("IG_API_VERSION") or DEFAULT_API_VERSION,
        )


def host_for_token(token: str) -> str:
    """Instagram Login tokens start with "IG" (e.g. IGAA...); Facebook ones with "EAA"."""
    return INSTAGRAM_HOST if token.startswith("IG") else DEFAULT_HOST
