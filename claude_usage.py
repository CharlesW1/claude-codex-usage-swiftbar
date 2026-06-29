from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
BETA_HEADER = "oauth-2025-04-20"
CACHE_PATH = os.path.expanduser("~/.cache/claude-usage/last.json")
CREDS_PATH = os.path.expanduser("~/.cache/claude-usage/creds.json")

# Claude Code's public OAuth client; refresh proactively this far before expiry.
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
EXPIRY_SKEW_MS = 5 * 60 * 1000

GREEN = "#34c759"
ORANGE = "#ff9500"
RED = "#ff3b30"


def severity_color(pct: float) -> str:
    if pct > 90:
        return RED
    if pct >= 70:
        return ORANGE
    return GREEN


def format_duration(seconds: float, compact: bool) -> str:
    total_minutes = int(seconds // 60)
    if total_minutes <= 0:
        return "now"
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    sep = "" if compact else " "
    return f"{hours}h{sep}{minutes}m"


@dataclass
class Usage:
    session_pct: float
    session_resets_at: str
    weekly_pct: float
    weekly_resets_at: str


class UsageError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


def parse_usage(data: dict) -> Usage:
    try:
        fh = data["five_hour"]
        sd = data["seven_day"]
        return Usage(
            session_pct=float(fh["utilization"]),
            session_resets_at=str(fh["resets_at"]),
            weekly_pct=float(sd["utilization"]),
            weekly_resets_at=str(sd["resets_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageError("bad_response", f"unexpected payload: {exc}")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def time_until(resets_at: str, now: datetime) -> float:
    return (_parse_iso(resets_at) - now).total_seconds()


def format_reset_day(resets_at: str, now: datetime) -> str:
    dt = _parse_iso(resets_at)
    return dt.strftime("%a %-d %b")


def _pct(p: float) -> str:
    return f"{int(round(p))}%"


def render_usage(u: Usage, now: datetime, stale: bool = False) -> str:
    sess_color = severity_color(u.session_pct)
    week_color = severity_color(u.weekly_pct)
    sess_left = format_duration(time_until(u.session_resets_at, now), compact=True)
    sess_left_long = format_duration(time_until(u.session_resets_at, now), compact=False)
    week_day = format_reset_day(u.weekly_resets_at, now)

    bar = f"{_pct(u.session_pct)} · {sess_left} | sfimage=gauge.medium color={sess_color}"
    lines = [
        bar,
        "---",
        f"Session (5h)  {_pct(u.session_pct)}  ·  resets in {sess_left_long} | color={sess_color}",
        f"Weekly  {_pct(u.weekly_pct)}  ·  resets {week_day} | color={week_color}",
        "---",
    ]
    if stale:
        lines.append("Showing last reading (rate-limited or offline) | color=gray")
    lines += [
        "Refresh | refresh=true",
        "Run /usage in Claude Code for full details | color=gray",
    ]
    return "\n".join(lines)


_ERROR_BARS = {
    "no_token": ("􀇿 Claude ? | sfimage=exclamationmark.triangle color=#ff9500",
                 "Keychain access denied — click Always Allow on the prompt."),
    "auth": ("􀇿 auth | sfimage=exclamationmark.triangle color=#ff9500",
             "Token expired. Open Claude Code to refresh."),
    "offline": ("— | sfimage=gauge.medium color=gray",
                "Offline — could not reach api.anthropic.com."),
}


def render_error(err: UsageError) -> str:
    if err.kind in _ERROR_BARS:
        bar, note = _ERROR_BARS[err.kind]
    else:  # bad_response / unknown
        bar = "? | sfimage=gauge.medium color=gray"
        note = f"Unexpected response: {err.detail}"
    return "\n".join([bar, "---", f"{note} | color=gray", "Refresh | refresh=true"])


def read_token() -> str:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UsageError("no_token", str(exc))
    if out.returncode != 0 or not out.stdout.strip():
        raise UsageError("no_token", out.stderr.strip() or "keychain item not found")
    try:
        data = json.loads(out.stdout)
        return data["claudeAiOauth"]["accessToken"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UsageError("no_token", f"bad credential payload: {exc}")


def fetch_usage(token: str) -> dict:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": "claude-usage-swiftbar/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise UsageError("auth", "HTTP 401")
        raise UsageError("bad_response", f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UsageError("offline", str(exc))
    except ValueError as exc:
        raise UsageError("bad_response", f"non-JSON: {exc}")


def cache_save(u: Usage) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump({
                "session_pct": u.session_pct,
                "session_resets_at": u.session_resets_at,
                "weekly_pct": u.weekly_pct,
                "weekly_resets_at": u.weekly_resets_at,
            }, f)
    except OSError:
        pass


def cache_load() -> Optional[Usage]:
    try:
        with open(CACHE_PATH) as f:
            d = json.load(f)
        return Usage(
            session_pct=d["session_pct"],
            session_resets_at=d["session_resets_at"],
            weekly_pct=d["weekly_pct"],
            weekly_resets_at=d["weekly_resets_at"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def creds_file_save(oauth: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CREDS_PATH), exist_ok=True)
        with open(CREDS_PATH, "w") as f:
            json.dump({"claudeAiOauth": oauth}, f)
        os.chmod(CREDS_PATH, 0o600)
    except OSError:
        pass


def creds_file_load() -> Optional[dict]:
    try:
        with open(CREDS_PATH) as f:
            return json.load(f)["claudeAiOauth"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _keychain_account() -> str:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
        capture_output=True, text=True, timeout=10,
    )
    m = re.search(r'"acct"<blob>="([^"]*)"', out.stdout)
    return m.group(1) if m else os.environ.get("USER", "")


def read_credentials() -> dict:
    """Return the freshest credentials, merging the Keychain blob with our own
    cache file (whichever has the later expiry wins, so Claude Code's refreshes
    and ours stay in sync)."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UsageError("no_token", str(exc))
    if out.returncode != 0 or not out.stdout.strip():
        raise UsageError("no_token", out.stderr.strip() or "keychain item not found")
    try:
        oauth = json.loads(out.stdout)["claudeAiOauth"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UsageError("no_token", f"bad credential payload: {exc}")

    cached = creds_file_load()
    if cached and (cached.get("expiresAt") or 0) > (oauth.get("expiresAt") or 0):
        oauth = cached

    if not oauth.get("accessToken"):
        raise UsageError("no_token", "no access token in credentials")
    return {
        "access_token": oauth.get("accessToken"),
        "refresh_token": oauth.get("refreshToken"),
        "expires_at": oauth.get("expiresAt"),
        "account": _keychain_account(),
        "oauth": oauth,
    }


def token_refresh(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token via Claude's OAuth
    endpoint. Returns {access_token, refresh_token|None, expires_in}."""
    if not refresh_token:
        raise UsageError("auth", "no refresh token; re-login in Claude Code")
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OAUTH_CLIENT_ID,
    }).encode("utf-8")
    req = urllib.request.Request(
        OAUTH_TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "claude-usage-swiftbar/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            j = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 400 invalid_grant means the refresh token itself is dead.
        raise UsageError("auth", f"refresh failed (HTTP {exc.code}); re-login in Claude Code")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UsageError("offline", str(exc))
    except ValueError as exc:
        raise UsageError("auth", f"refresh returned non-JSON: {exc}")
    if not j.get("access_token"):
        raise UsageError("auth", "refresh response had no access_token")
    return {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token"),
        "expires_in": j.get("expires_in", 28800),
    }


def keychain_write(account: str, oauth: dict) -> None:
    """Best-effort write-back of the rotated token to the Keychain, like Claude
    Code does. Failures are non-fatal: the cache file already holds the tokens."""
    blob = json.dumps({"claudeAiOauth": oauth})
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-a", account, "-s", KEYCHAIN_SERVICE, "-w", blob],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def current_token(now_ms: int, force: bool = False) -> str:
    """Return a valid access token, refreshing (and persisting) if it is expired,
    within the proactive skew window, or when forced."""
    creds = read_credentials()
    expires_at = creds["expires_at"]
    if not force and expires_at and now_ms < expires_at - EXPIRY_SKEW_MS:
        return creds["access_token"]

    refreshed = token_refresh(creds["refresh_token"])
    oauth = dict(creds["oauth"])
    oauth["accessToken"] = refreshed["access_token"]
    oauth["refreshToken"] = refreshed["refresh_token"] or creds["refresh_token"]
    oauth["expiresAt"] = now_ms + refreshed["expires_in"] * 1000
    creds_file_save(oauth)
    keychain_write(creds["account"], oauth)
    return oauth["accessToken"]


# Transient failures fall back to the last good reading; auth/no_token do not,
# because those need the user to act and the cached number would mask that.
_TRANSIENT_KINDS = ("offline", "bad_response")


def build_output(now: datetime) -> str:
    now_ms = int(now.timestamp() * 1000)
    try:
        token = current_token(now_ms)
        try:
            data = fetch_usage(token)
        except UsageError as exc:
            if exc.kind == "auth":  # reactive: token rejected, force one refresh
                token = current_token(now_ms, force=True)
                data = fetch_usage(token)
            else:
                raise
        usage = parse_usage(data)
        cache_save(usage)
        return render_usage(usage, now)
    except UsageError as err:
        if err.kind in _TRANSIENT_KINDS:
            cached = cache_load()
            if cached is not None:
                return render_usage(cached, now, stale=True)
        return render_error(err)


def main() -> None:
    print(build_output(datetime.now(timezone.utc)))


if __name__ == "__main__":
    main()
