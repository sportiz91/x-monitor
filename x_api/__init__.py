"""Reverse-engineered read-only client for x.com's private GraphQL API.

Auth is cookie-based (auth_token + ct0, ct0 doubling as the x-csrf-token) plus the
public web Bearer. Everything is replayed with curl_cffi impersonating Chrome so the
TLS/HTTP2 fingerprint matches a browser — validated: a bare curl_cffi replay from a
residential IP returns HTTP 200 *without* the x-client-transaction-id header, which
x.com sends but does not enforce on reads.
"""
from .auth import Auth
from .client import XClient
from .models import Tweet, XUser

__all__ = ["Auth", "XClient", "Tweet", "XUser"]
