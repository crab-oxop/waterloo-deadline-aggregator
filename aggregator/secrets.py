"""Credential storage via the OS keychain (Windows Credential Manager, DPAPI-encrypted).
Nothing secret is ever written to the repo, the DB, or logs."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import keyring

SERVICE = "waterloo-deadline-aggregator"


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def redact_url(url: str) -> str:
    """Feed URLs carry an auth token in the query string — strip it before display/logging."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>" if parts.query else "", ""))
