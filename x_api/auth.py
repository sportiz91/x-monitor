"""Authentication + HTTP session for x.com's private GraphQL API.

Cookie-based: `auth_token` (the session bearer) + `ct0` (whose value doubles as the
`x-csrf-token` header, a double-submit token). The `authorization: Bearer` is the
public web-client token hardcoded in x.com's JS bundle (stable for years). Requests
are replayed with curl_cffi impersonating Chrome so the TLS/HTTP2 fingerprint matches.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from curl_cffi import requests

# Public web bearer, hardcoded in x.com's main.js. Not a secret; every browser sends it.
BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs="
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _load_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


@dataclass
class Auth:
    auth_token: str
    ct0: str
    tx_id: str = ""          # x-client-transaction-id, only needed by hardened ops (search)
    user_agent: str = DEFAULT_UA
    _session: requests.Session = field(default=None, repr=False)

    @classmethod
    def from_env(cls, path: str | None = None) -> "Auth":
        # Prefer the process environment (systemd EnvironmentFile on the VPS); fall
        # back to a local .env (dev on the laptop).
        path = path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
        env = _load_env(path) if os.path.exists(path) else {}
        at = os.environ.get("X_AUTH_TOKEN") or env.get("X_AUTH_TOKEN", "")
        ct0 = os.environ.get("X_CT0") or env.get("X_CT0", "")
        tx = os.environ.get("X_TX_ID") or env.get("X_TX_ID", "")
        if not at or not ct0:
            raise RuntimeError("X_AUTH_TOKEN/X_CT0 missing (env or .env)")
        return cls(auth_token=at, ct0=ct0, tx_id=tx)

    @property
    def cookie(self) -> str:
        return f"auth_token={self.auth_token}; ct0={self.ct0}"

    def headers(self, lang: str = "en", *, tx: bool = False) -> dict:
        h = {
            "authorization": f"Bearer {BEARER}",
            "x-csrf-token": self.ct0,          # double-submit: must equal the ct0 cookie
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": lang,
            "cookie": self.cookie,
            "user-agent": self.user_agent,
            "content-type": "application/json",
            "accept": "*/*",
            "referer": "https://x.com/",
            "origin": "https://x.com",
        }
        # A few hardened endpoints (search) 404 without a *valid* x-client-transaction-id.
        # x.com sends it on every call but only enforces it on some; supply it when asked.
        if tx and self.tx_id:
            h["x-client-transaction-id"] = self.tx_id
        return h

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            # Pin a concrete impersonate profile (not the floating "chrome" alias) so the
            # fingerprint is stable. X_PROXY (e.g. a chisel tunnel to a residential IP for
            # the fase-2 VPS monitor) is set at construction, never mutated mid-life.
            proxy = os.environ.get("X_PROXY")
            proxies = {"http": proxy, "https": proxy} if proxy else None
            self._session = requests.Session(impersonate="chrome124", proxies=proxies)
        return self._session
