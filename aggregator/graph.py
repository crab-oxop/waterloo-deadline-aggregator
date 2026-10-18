"""Microsoft Graph sign-in + read-only GET helper (Outlook mail/calendar; Teams later).

Auth is MSAL's interactive browser flow for a public client (no client secret exists).
The token cache is a DPAPI-encrypted file in the data dir (paths.py) — encrypted to this Windows user,
never plaintext, never sent anywhere but Microsoft."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .paths import DATA_DIR
from .secrets import get_secret, set_secret

CONFIG_SECRET = "graph_config"  # {"client_id": ..., "tenant": ...}; not secret, kept beside the rest
CACHE_PATH = DATA_DIR / "msal_cache.bin"
# Read-only on purpose: the tool never sends, moves, flags, or marks anything read.
SCOPES = ["User.Read", "Mail.Read", "Calendars.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"
MAX_PAGES = 10


class GraphError(RuntimeError):
    pass


def config() -> dict | None:
    raw = get_secret(CONFIG_SECRET)
    return json.loads(raw) if raw else None


def _app():
    import msal
    from msal_extensions import PersistedTokenCache, build_encrypted_persistence

    cfg = config()
    if not cfg:
        raise GraphError("Outlook not connected — run: python -m aggregator setup-outlook")
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = PersistedTokenCache(build_encrypted_persistence(str(CACHE_PATH)))
    return msal.PublicClientApplication(
        cfg["client_id"], authority=f"https://login.microsoftonline.com/{cfg['tenant']}", token_cache=cache)


def login(client_id: str, tenant: str) -> str:
    """Interactive sign-in. Returns the signed-in account's username."""
    set_secret(CONFIG_SECRET, json.dumps({"client_id": client_id, "tenant": tenant}))
    result = _app().acquire_token_interactive(SCOPES, prompt="select_account")
    if "access_token" not in result:
        raise GraphError(_explain(result))
    return result.get("id_token_claims", {}).get("preferred_username", "(unknown account)")


def _explain(result: dict) -> str:
    desc = result.get("error_description", "") or result.get("error", "unknown error")
    codes = result.get("error_codes") or []
    if 65001 in codes or 90094 in codes or ("admin" in desc.lower() and "approv" in desc.lower()):
        return ("Waterloo IT has to approve this app before it can read Outlook "
                "(Microsoft requires admin consent for Mail.Read/Calendars.Read). " + desc.splitlines()[0])
    return desc.splitlines()[0] if desc else "sign-in failed"


def is_signed_in() -> bool:
    if not config() or not CACHE_PATH.exists():
        return False
    try:
        return bool(_app().get_accounts())
    except Exception:
        return False


def _token() -> str:
    app = _app()
    accounts = app.get_accounts()
    if not accounts:
        raise GraphError("Outlook sign-in expired — run: python -m aggregator setup-outlook")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise GraphError("Outlook sign-in expired — run: python -m aggregator setup-outlook")
    return result["access_token"]


def get_all(path: str, params: dict | None = None, headers: dict | None = None) -> list[dict]:
    """GET a Graph collection, following @odata.nextLink up to MAX_PAGES."""
    token = _token()
    url = f"{GRAPH}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                page = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.load(e).get("error", {}).get("message", "")
            except Exception:
                pass
            raise GraphError(f"Graph {path} returned {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise GraphError(f"Graph {path} unreachable: {getattr(e, 'reason', e)}") from None
        out.extend(page.get("value", []))
        url = page.get("@odata.nextLink")
        if not url:
            break
    return out
