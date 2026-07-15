from __future__ import annotations

import json
import math
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
BETA_HEADER = "oauth-2025-04-20"

CODEX_AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
# Claude Code stores its OAuth blob in the macOS Keychain on some installs and
# in this plaintext file on others (current CLI versions favor the file) —
# read_credentials() tries the Keychain first, then falls back here.
CLAUDE_CODE_CREDS_PATH = os.path.expanduser("~/.claude/.credentials.json")
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CACHE_PATH = os.path.expanduser("~/.cache/claude-usage/last.json")
CODEX_CACHE_PATH = os.path.expanduser("~/.cache/claude-usage/last_codex.json")
AGY_TOKEN_PATH = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
AGY_CACHE_PATH = os.path.expanduser("~/.cache/claude-usage/last_agy.json")
PERCENT_MODE_PATH = os.path.expanduser("~/.cache/claude-usage/percent_mode")
# Read the short-lived provider-specific setting once as a migration fallback.
LEGACY_AGY_PERCENT_MODE_PATH = os.path.expanduser("~/.cache/claude-usage/agy_percent_mode")
CREDS_PATH = os.path.expanduser("~/.cache/claude-usage/creds.json")
DISPLAY_MODE_PATH = os.path.expanduser("~/.cache/claude-usage/display_mode")
BOOST_UNTIL_PATH = os.path.expanduser("~/.cache/claude-usage/boost_until")
BOOST_LOCK_PATH = os.path.expanduser("~/.cache/claude-usage/boost.lock")
BOOST_INTERVAL_S = 60

# Claude Code's public OAuth client; refresh proactively this far before expiry.
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
EXPIRY_SKEW_MS = 5 * 60 * 1000

WHITE = "#ffffff"
GREEN = "#34c759"
ORANGE = "#ff9500"
RED = "#ff3b30"
GRAY = "#8e8e93"
AGY_PROD_HOST = "https://cloudcode-pa.googleapis.com"
AGY_DAILY_HOST = "https://daily-cloudcode-pa.googleapis.com"

PROVIDERS = ("claude", "codex", "agy")
DISPLAY_MODES = ("both", "claude", "codex")
PERCENT_MODES = ("used", "remaining")


def normalize_display_mode(mode: Optional[str]) -> str:
    return mode if mode in DISPLAY_MODES else "both"



def enabled_load() -> set[str]:
    try:
        with open(DISPLAY_MODE_PATH) as f:
            raw = f.read().strip()
            if not raw:
                return set(PROVIDERS)
            if raw == "none":
                return set()
            if raw == "both":
                return set(PROVIDERS)

            # Normalization
            slugs = [s.strip().lower() for s in raw.split(",")]
            res = {s for s in slugs if s in PROVIDERS}

            # If after migration/normalization it's empty but wasn't "none", default it
            if not res and raw not in ("none", ""):
                return set(PROVIDERS)
            return res
    except OSError:
        return set(PROVIDERS)

def enabled_save(enabled: set[str]) -> None:
    try:
        os.makedirs(os.path.dirname(DISPLAY_MODE_PATH), exist_ok=True)
        with open(DISPLAY_MODE_PATH, "w") as f:
            if not enabled:
                f.write("none")
            else:
                f.write(",".join(sorted(enabled)))
    except OSError:
        pass

def enabled_toggle(provider: str) -> None:
    if provider not in PROVIDERS:
        return
    current = enabled_load()
    if provider in current:
        current.remove(provider)
    else:
        current.add(provider)
    enabled_save(current)

def percent_mode_load() -> str:
    for path in (PERCENT_MODE_PATH, LEGACY_AGY_PERCENT_MODE_PATH):
        try:
            with open(path) as f:
                mode = f.read().strip().lower()
            if mode in PERCENT_MODES:
                return mode
        except OSError:
            pass
    return "used"

def percent_mode_save(mode: str) -> None:
    if mode not in PERCENT_MODES:
        return
    try:
        os.makedirs(os.path.dirname(PERCENT_MODE_PATH), exist_ok=True)
        with open(PERCENT_MODE_PATH, "w") as f:
            f.write(mode)
    except OSError:
        pass

def _display_pct(pct: Optional[float], mode: str,
                 stored_as_remaining: bool = False) -> Optional[float]:
    if pct is None or ((mode == "remaining") == stored_as_remaining):
        return pct
    return max(0, min(100, 100 - pct))

def display_mode_load() -> str:
    try:
        with open(DISPLAY_MODE_PATH) as f:
            return normalize_display_mode(f.read().strip())
    except OSError:
        return "both"


def display_mode_save(mode: str) -> None:
    mode = normalize_display_mode(mode)
    try:
        os.makedirs(os.path.dirname(DISPLAY_MODE_PATH), exist_ok=True)
        with open(DISPLAY_MODE_PATH, "w") as f:
            f.write(mode)
    except OSError:
        pass


def severity_color(pct: float) -> str:
    if pct >= 90:
        return RED
    if pct >= 75:
        return ORANGE
    if pct >= 50:
        return GREEN
    return WHITE  # best: plenty of headroom


def format_duration(seconds: float, compact: bool) -> str:
    total_minutes = int(seconds // 60)
    if total_minutes <= 0:
        return "now"
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    sep = "" if compact else " "
    return f"{hours}h{sep}{minutes}m"


def format_countdown(seconds: float) -> str:
    """Time remaining as its two largest non-zero units, e.g. '2d 1h',
    '1h 35m', '35m 8s', or '8s'."""
    s = int(max(0, seconds))
    days, r = divmod(s, 86400)
    hours, r = divmod(r, 3600)
    minutes, secs = divmod(r, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


@dataclass
class Usage:
    session_pct: float
    session_resets_at: Optional[str]
    weekly_pct: float
    weekly_resets_at: Optional[str]
    # Fable is a weekly, model-scoped limit inside the Claude subscription (no
    # 5-hour window of its own). None when the account has no Fable allotment.
    fable_pct: Optional[float] = None
    fable_resets_at: Optional[str] = None


@dataclass
class AgyUsage:
    # Antigravity exposes the same two windows for each model family. Unlike
    # Claude/Codex, its native UI reports percent remaining, so preserve that
    # provider-specific convention here.
    gemini_weekly_pct: Optional[float]
    gemini_weekly_resets_at: Optional[str]
    gemini_5h_pct: Optional[float]
    gemini_5h_resets_at: Optional[str]
    external_weekly_pct: Optional[float]
    external_weekly_resets_at: Optional[str]
    external_5h_pct: Optional[float]
    external_5h_resets_at: Optional[str]


@dataclass
class CodexUsage:
    # Codex's window model has changed shape before (mid-2026 it dropped the
    # 5-hour tier), so a window is present-or-absent: pct is None when that
    # tier has no limit, and *_window_s carries the payload's own duration so
    # labels track reality instead of hardcoding "5-hour"/"Weekly".
    primary_pct: Optional[float]     # % used; None = window absent
    primary_resets_at: Optional[str]
    secondary_pct: Optional[float]
    secondary_resets_at: Optional[str]
    primary_window_s: Optional[int] = None    # limit_window_seconds
    secondary_window_s: Optional[int] = None


class UsageError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


def _epoch_to_iso(epoch) -> Optional[str]:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _parse_codex_window(w) -> Tuple[Optional[float], Optional[str], Optional[int]]:
    """(pct, resets_at_iso, window_seconds) for one rate-limit window.
    Codex sends null (or omits) a window when that tier has no limit — e.g.
    mid-2026 the 5-hour tier disappeared — so non-dict means absent, all None."""
    if not isinstance(w, dict):
        return None, None, None
    try:
        pct = float(w.get("used_percent") or 0.0)
    except (TypeError, ValueError):
        pct = 0.0
    win = w.get("limit_window_seconds")
    win = int(win) if isinstance(win, (int, float)) and win > 0 else None
    return pct, _epoch_to_iso(w.get("reset_at")), win


def parse_codex(data: dict) -> CodexUsage:
    """Parse chatgpt.com/backend-api/wham/usage. Codex reports used_percent,
    matching Claude's 'utilization' — no remaining->used conversion needed."""
    try:
        rl = data["rate_limit"]
        p_pct, p_resets, p_win = _parse_codex_window(rl.get("primary_window"))
        s_pct, s_resets, s_win = _parse_codex_window(rl.get("secondary_window"))
        return CodexUsage(
            primary_pct=p_pct, primary_resets_at=p_resets,
            secondary_pct=s_pct, secondary_resets_at=s_resets,
            primary_window_s=p_win, secondary_window_s=s_win,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UsageError("bad_response", f"unexpected codex payload: {exc}")


def window_label(window_s: Optional[int], default: str) -> str:
    """Human label for a rate-limit window duration. Falls back to the legacy
    default when the payload (or an old cache) doesn't report the duration."""
    if not window_s:
        return default
    if window_s >= 6 * 86400:
        return "Weekly"
    if window_s >= 47 * 3600:
        return f"{round(window_s / 86400)}-day"
    if window_s >= 20 * 3600:
        return "Daily"
    return f"{max(1, round(window_s / 3600))}-hour"


def _parse_fable(data: dict) -> Tuple[Optional[float], Optional[str]]:
    """Pull the Fable weekly-scoped limit out of the `limits` array, if present.
    Fable surfaces only there — as a `weekly_scoped` entry whose
    scope.model.display_name is "Fable" — not as a top-level bucket.

    Fully defensive: Fable is a supplementary line, so any malformed shape
    (non-list limits, non-dict entry/scope/model, non-numeric percent) yields no
    Fable rather than raising and breaking the core 5-hour/weekly Claude parse."""
    limits = data.get("limits")
    if not isinstance(limits, list):
        return None, None
    for lim in limits:
        if not isinstance(lim, dict):
            continue
        scope = lim.get("scope")
        model = scope.get("model") if isinstance(scope, dict) else None
        if not isinstance(model, dict) or model.get("display_name") != "Fable":
            continue
        pct = lim.get("percent")
        try:
            pct = float(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct = None
        resets = lim.get("resets_at")
        return pct, resets if isinstance(resets, str) else None
    return None, None


def parse_usage(data: dict) -> Usage:
    try:
        fh = data["five_hour"]
        sd = data["seven_day"]
        fable_pct, fable_resets_at = _parse_fable(data)
        return Usage(
            session_pct=float(fh.get("utilization") or 0.0),
            session_resets_at=fh.get("resets_at"),  # None when no active window
            weekly_pct=float(sd.get("utilization") or 0.0),
            weekly_resets_at=sd.get("resets_at"),
            fable_pct=fable_pct,
            fable_resets_at=fable_resets_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageError("bad_response", f"unexpected payload: {exc}")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def time_until(resets_at: str, now: datetime) -> float:
    return (_parse_iso(resets_at) - now).total_seconds()


def _pct(p: float) -> str:
    return f"{int(round(p))}%"


def _has_reset(resets_at: Optional[str]) -> bool:
    # The endpoint sends null (or a stale cache may hold the string "None")
    # when there is no active window.
    return bool(resets_at) and resets_at != "None"


# Timer color reflects time left until reset: the sooner it resets the better,
# so a near-imminent reset is the "best" tier (white), a long wait is red.
# Tiers are fractions of the window so a weekly limit isn't judged on the
# 5-hour clock (it would sit red all week). The fractions are the historical
# 5h absolutes (15m/1h/3h of 18000s), so 5h windows behave exactly as before.
TIMER_DEFAULT_WINDOW_S = 18000
TIMER_BEST_MAX_FRAC = 900 / 18000    # <= 5% of window left  -> white (best)
TIMER_SOON_MAX_FRAC = 3600 / 18000   # <= 20% left           -> green
TIMER_MID_MAX_FRAC = 10800 / 18000   # <= 60% left           -> orange; else red


def timer_color(seconds_left: float, window_s: Optional[int] = None) -> str:
    window = window_s or TIMER_DEFAULT_WINDOW_S
    if seconds_left <= window * TIMER_BEST_MAX_FRAC:
        return WHITE
    if seconds_left <= window * TIMER_SOON_MAX_FRAC:
        return GREEN
    if seconds_left <= window * TIMER_MID_MAX_FRAC:
        return ORANGE
    return RED


@dataclass
class BarRow:
    label: str           # "C" / "Cx"
    value: str           # "44%" / "—"
    value_color: str     # by usage severity
    timer: str           # "2h0m" or "" when no active window
    timer_color: str     # by time-left


def _bar_row(tag: str, pct: float, resets_at: Optional[str], now: datetime,
             window_s: Optional[int] = None,
             severity_pct: Optional[float] = None) -> "BarRow":
    severity = pct if severity_pct is None else severity_pct
    if _has_reset(resets_at):
        secs = time_until(resets_at, now)
        timer = (format_countdown(secs) if window_s and window_s >= 86400
                 else format_duration(secs, compact=True))
        return BarRow(tag, _pct(pct), severity_color(severity),
                      timer,
                      timer_color(secs, window_s))
    return BarRow(tag, _pct(pct), severity_color(severity), "", GRAY)


@dataclass
class MenuTile:
    row: "BarRow"
    provider: str
    logical_col: int
    logical_row: int




def menubar_tiles(claude: Optional["Usage"], codex: Optional["CodexUsage"],
                  agy: Optional["AgyUsage"], now: datetime,
                  percent_mode: str = "used") -> List[MenuTile]:
    tiles = []
    if claude is not None:
        r = _bar_row("Cld", _display_pct(claude.session_pct, percent_mode),
                     claude.session_resets_at, now,
                     severity_pct=claude.session_pct)
    else:
        r = BarRow("Cld", "—", GRAY, "", GRAY)
    tiles.append(MenuTile(r, "claude", 0, 0))

    if codex is not None:
        if codex.primary_pct is not None:
            x = _bar_row("Cdx", _display_pct(codex.primary_pct, percent_mode),
                         codex.primary_resets_at, now, codex.primary_window_s,
                         codex.primary_pct)
        elif codex.secondary_pct is not None:
            x = _bar_row("Cdx", _display_pct(codex.secondary_pct, percent_mode),
                         codex.secondary_resets_at, now, codex.secondary_window_s,
                         codex.secondary_pct)
        else:
            x = BarRow("Cdx", "—", GRAY, "", GRAY)
    else:
        x = BarRow("Cdx", "—", GRAY, "", GRAY)
    tiles.append(MenuTile(x, "codex", 0, 1))

    if agy is not None:
        gemini = [(_display_pct(agy.gemini_5h_pct, percent_mode, True),
                   agy.gemini_5h_resets_at, 18000),
                  (_display_pct(agy.gemini_weekly_pct, percent_mode, True),
                   agy.gemini_weekly_resets_at, 604800)]
        gemini = [w for w in gemini if w[0] is not None]
        gemini_primary = gemini[0] if gemini else None
        if gemini_primary:
            pct, resets, window_s = gemini_primary
            remaining = (agy.gemini_5h_pct if agy.gemini_5h_pct is not None
                         else agy.gemini_weekly_pct)
            r_gem = _bar_row("AgG", pct, resets, now, window_s,
                             100 - remaining)
        else:
            r_gem = BarRow("AgG", "—", GRAY, "", GRAY)

        external = [(_display_pct(agy.external_5h_pct, percent_mode, True),
                     agy.external_5h_resets_at, 18000),
                    (_display_pct(agy.external_weekly_pct, percent_mode, True),
                     agy.external_weekly_resets_at, 604800)]
        external = [w for w in external if w[0] is not None]
        external_primary = external[0] if external else None
        if external_primary:
            pct, resets, window_s = external_primary
            remaining = (agy.external_5h_pct if agy.external_5h_pct is not None
                         else agy.external_weekly_pct)
            r_ext = _bar_row("AgX", pct, resets, now, window_s,
                             100 - remaining)
        else:
            r_ext = BarRow("AgX", "—", GRAY, "", GRAY)
    else:
        r_gem = BarRow("AgG", "—", GRAY, "", GRAY)
        r_ext = BarRow("AgX", "—", GRAY, "", GRAY)
    tiles.append(MenuTile(r_gem, "agy", 1, 0))
    tiles.append(MenuTile(r_ext, "agy", 1, 1))
    return tiles

def filter_menubar_tiles(tiles: List[MenuTile], enabled: set[str]) -> List[MenuTile]:
    return [t for t in tiles if t.provider in enabled]


def _bar_row_text(r: "BarRow") -> str:
    """Plain-text form of a row for the no-Swift fallback menu bar."""
    return f"{r.label} {r.value}" + (f" · {r.timer}" if r.timer else "")


def next_check_label(now: datetime, interval_s: int) -> str:
    """Full local clock time of the next scheduled refresh, with seconds and
    AM/PM (e.g. '↻ 1:33:24 PM')."""
    nxt = (now + timedelta(seconds=interval_s)).astimezone()
    return "↻ " + nxt.strftime("%-I:%M:%S %p")


def _window_line(label: str, pct: float, resets_at: Optional[str],
                 now: datetime, color: str = GRAY) -> str:
    # Match the passive provider-label rows so detail lines do not become
    # highlightable menu actions.
    body = f"{label}  {_pct(pct)}"
    if _has_reset(resets_at):
        body += "  ·  resets in " + format_countdown(time_until(resets_at, now))
    return f"{body} | color={color}"


_STALE_NOTE = "last reading (rate-limited or offline) | color=" + GRAY


def render_dropdown(
    claude: Optional["Usage"], codex: Optional["CodexUsage"],
    now: datetime, interval_s: int,
    stale_claude: bool = False, stale_codex: bool = False,
    claude_note: Optional[str] = None, codex_note: Optional[str] = None,
    boost_remaining: Optional[int] = None, cli: Optional[Tuple[str, str]] = None,
    display_mode: str = "both", detail_color: str = GRAY,
    claude_help: Optional[str] = None, codex_help: Optional[str] = None,
    *,
    agy: Optional["AgyUsage"] = None, stale_agy: bool = False, agy_note: Optional[str] = None, agy_help: Optional[str] = None,
    enabled: Optional[set] = None, percent_mode: str = "used"
) -> str:
    if enabled is None:
        display_mode = normalize_display_mode(display_mode)
        if display_mode == "both": enabled = set(PROVIDERS)
        elif display_mode == "claude": enabled = {"claude"}
        elif display_mode == "codex": enabled = {"codex"}
        else: enabled = set()

    lines = ["---"]

    if "claude" in enabled:
        lines.append("Claude | color=" + GRAY)
        if claude is not None:
            lines.append(_window_line("5-hour", _display_pct(claude.session_pct, percent_mode),
                                      claude.session_resets_at, now, detail_color))
            lines.append(_window_line("Weekly", _display_pct(claude.weekly_pct, percent_mode),
                                      claude.weekly_resets_at, now, detail_color))
            if claude.fable_pct is not None:
                lines.append(_window_line("Fable", _display_pct(claude.fable_pct, percent_mode),
                                          claude.fable_resets_at, now, detail_color))
            if stale_claude:
                lines.append(_STALE_NOTE)
        else:
            lines.append((claude_note or "unavailable") + " | color=" + GRAY)

    if "codex" in enabled:
        lines.append("Codex | color=" + GRAY)
        if codex is not None:
            windows = [
                (window_label(codex.primary_window_s, "5-hour"),
                 _display_pct(codex.primary_pct, percent_mode), codex.primary_resets_at),
                (window_label(codex.secondary_window_s, "Weekly"),
                 _display_pct(codex.secondary_pct, percent_mode), codex.secondary_resets_at),
            ]
            present = [w for w in windows if w[1] is not None]
            for lbl, pct, resets in present:
                lines.append(_window_line(lbl, pct, resets, now, detail_color))
            if not present:
                lines.append("no active limits | color=" + GRAY)
            if stale_codex:
                lines.append(_STALE_NOTE)
        else:
            lines.append((codex_note or "unavailable") + " | color=" + GRAY)

    if "agy" in enabled:
        lines.append("Antigravity | color=" + GRAY)
        if agy is not None:
            windows = [
                ("Gemini 5-hour", _display_pct(agy.gemini_5h_pct, percent_mode, True), agy.gemini_5h_resets_at),
                ("Gemini weekly", _display_pct(agy.gemini_weekly_pct, percent_mode, True), agy.gemini_weekly_resets_at),
                ("External 5-hour", _display_pct(agy.external_5h_pct, percent_mode, True), agy.external_5h_resets_at),
                ("External weekly", _display_pct(agy.external_weekly_pct, percent_mode, True), agy.external_weekly_resets_at),
            ]
            present = [w for w in windows if w[1] is not None]
            if not present:
                lines.append("no active limits | color=" + GRAY)
            else:
                for label, pct, resets in present:
                    lines.append(_window_line(label, pct, resets, now, detail_color))
            if stale_agy:
                lines.append(_STALE_NOTE)
        else:
            lines.append((agy_note or "unavailable") + " | color=" + GRAY)

    lines.append("---")
    lines.append("Display | color=" + GRAY)
    lines.append("Show")
    for mode in ("claude", "codex", "agy"):
        lbl = {"claude": "Claude", "codex": "Codex", "agy": "Antigravity"}[mode]
        label = f"--{lbl}" + (" ✓" if mode in enabled else "")
        if cli is None:
            lines.append(label)
        else:
            py, mod = cli
            lines.append(f'{label} | bash="{py}" param1="{mod}" '
                         f'param2="toggle" param3="{mode}" terminal=false refresh=true')
    lines.append(f"Percentages: {percent_mode.title()}")
    for mode in PERCENT_MODES:
        label = f"--{mode.title()}" + (" ✓" if mode == percent_mode else "")
        if cli is None:
            lines.append(label)
        else:
            py, mod = cli
            lines.append(f'{label} | bash="{py}" param1="{mod}" '
                         f'param2="percent" param3="{mode}" terminal=false refresh=true')
    lines.append("---")
    if boost_remaining:
        ends = (now + timedelta(seconds=boost_remaining)).astimezone().strftime("%-I:%M:%S %p")
        lines.append(f"{next_check_label(now, interval_s)} · next check "
                     f"(boosted to 1m, until {ends}) | color={GRAY}")
    else:
        lines.append(f"{next_check_label(now, interval_s)} · next check "
                     f"(every {interval_s // 60}m) | color={GRAY}")
    lines.append("Check now | refresh=true")
    if cli is not None:
        py, mod = cli
        if boost_remaining:
            lines.append(f'Stop 1-minute boost | bash="{py}" param1="{mod}" '
                         f'param2="stop" terminal=false refresh=true')
        else:
            lines.append(f'Check every 1 min for 30 min | bash="{py}" param1="{mod}" '
                         f'param2="boost" terminal=false refresh=true')

    d_map = []
    if "claude" in enabled: d_map.append("Claude /usage")
    if "codex" in enabled: d_map.append("Codex /status")
    if "agy" in enabled: d_map.append("Antigravity")
    if d_map:
        details = " · ".join(d_map) + " for details"
    else:
        details = "no providers enabled"
    lines.append(details + " | color=" + GRAY)

    for help_text in (claude_help, codex_help, agy_help):
        if help_text:
            lines.append(help_text + " | color=" + GRAY)
    return "\n".join(lines)


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


RENDERER_BIN = os.path.expanduser("~/.cache/claude-usage/menubar_render")
RENDERER_SRC = os.path.expanduser("~/.cache/claude-usage/menubar_render.swift")

# Sizing knobs. SwiftBar shows the image at its POINT size (pixels ÷ scale via
# the PNG's encoded resolution), so _MENUBAR_FONT_PT is the on-screen point size
# per line — set it to match neighboring stacked menu-bar items (~9pt each).
# _MENUBAR_SCALE only adds Retina sharpness; it does not change displayed size.
_MENUBAR_FONT_PT = 9.0
# One visible provider = one row, so there's vertical room to fill the bar with
# a larger, more legible glyph instead of a lone tiny 9pt line.
_MENUBAR_FONT_PT_SINGLE = 15.0
_MENUBAR_PAD_X = 2.0
_MENUBAR_PAD_Y = 1.0
_MENUBAR_LINE_GAP = 0.0
_MENUBAR_SCALE = 3  # render @3x, then tag the PNG so it displays at 1x points

# Kept in a string (not a sibling file) so SwiftBar's plugin folder stays clean —
# a stray .swift there would be run as a broken plugin.
MENUBAR_SWIFT_SRC = r'''
import AppKit

func color(_ hex: String) -> NSColor {
    var s = hex
    if s.hasPrefix("#") { s.removeFirst() }
    guard s.count == 6, let v = Int(s, radix: 16) else { return .labelColor }
    return NSColor(srgbRed: CGFloat((v >> 16) & 0xff)/255.0,
                   green: CGFloat((v >> 8) & 0xff)/255.0,
                   blue: CGFloat(v & 0xff)/255.0, alpha: 1.0)
}

// Per row: label, value, valueColor, timer, timerColor.
let a = CommandLine.arguments
guard a.count >= 7 && (a.count - 1) % 6 == 0 else { exit(1) }
let scale: CGFloat = CGFloat(Double(ProcessInfo.processInfo.environment["MB_SCALE"] ?? "3") ?? 3)
let fontPt = CGFloat(Double(ProcessInfo.processInfo.environment["MB_FONT"] ?? "9") ?? 9)
let padX = CGFloat(Double(ProcessInfo.processInfo.environment["MB_PADX"] ?? "2") ?? 2)
let padY = CGFloat(Double(ProcessInfo.processInfo.environment["MB_PADY"] ?? "1") ?? 1)
let gap  = CGFloat(Double(ProcessInfo.processInfo.environment["MB_GAP"] ?? "0") ?? 0)

let font = NSFont.systemFont(ofSize: fontPt * scale, weight: .medium)
func run(_ s: String, _ hex: String) -> NSAttributedString {
    NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: color(hex)])
}

struct Row { let label, value, timer: NSAttributedString; let hasTimer: Bool; let col: Int }
let sepGray = "#8e8e93"
var rows: [Row] = []
let rowCount = (a.count - 1) / 6
for i in 0..<rowCount {
    let b = 1 + i * 6
    let hasTimer = !a[b+3].isEmpty
    let col = Int(a[b+5]) ?? 0
    rows.append(Row(label: run(a[b], "#ffffff"), value: run(a[b+1], a[b+2]),
                    timer: run(a[b+3], a[b+4]), hasTimer: hasTimer, col: col))
}
let sep = run("·", sepGray)
let spc = fontPt * scale * 0.30
let sepW = sep.size().width

let cols = Array(Set(rows.map { $0.col })).sorted()
var colWidths: [Int: (label: CGFloat, value: CGFloat, timer: CGFloat, anyTimer: Bool)] = [:]
var maxRowsInCol = 0
var rowsByCol: [Int: [Row]] = [:]

for c in cols {
    let crows = rows.filter { $0.col == c }
    maxRowsInCol = max(maxRowsInCol, crows.count)
    rowsByCol[c] = crows
    colWidths[c] = (
        label: crows.map { $0.label.size().width }.max() ?? 0,
        value: crows.map { $0.value.size().width }.max() ?? 0,
        timer: crows.map { $0.hasTimer ? $0.timer.size().width : 0 }.max() ?? 0,
        anyTimer: crows.contains { $0.hasTimer }
    )
}

let lh = ceil(rows.map { max($0.label.size().height, $0.value.size().height) }.max() ?? 0)

var currentX = padX * scale
var colStarts: [Int: CGFloat] = [:]
let colGap = padX * scale * 2

for c in cols {
    colStarts[c] = currentX
    let w = colWidths[c]!
    let cw = (w.anyTimer ? (w.label + spc + w.value + spc + sepW + spc + w.timer) : (w.label + spc + w.value))
    currentX += cw + colGap
}

let pxW = currentX - colGap + padX * scale
let pxH = lh * CGFloat(maxRowsInCol) + gap * scale * CGFloat(max(0, maxRowsInCol - 1)) + padY * scale * 2

guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(pxW),
        pixelsHigh: Int(pxH), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
        isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
else { exit(1) }
rep.size = NSSize(width: pxW, height: pxH)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

for c in cols {
    let crows = rowsByCol[c]!
    let cx = colStarts[c]!
    let w = colWidths[c]!

    let labelX = cx
    let valueX = labelX + w.label + spc
    let sepX = valueX + w.value + spc
    let timerX = sepX + sepW + spc

    for (i, row) in crows.enumerated() {
        let y = padY * scale + CGFloat(maxRowsInCol - 1 - i) * (lh + gap * scale)
        row.label.draw(at: NSPoint(x: labelX, y: y))
        row.value.draw(at: NSPoint(x: valueX, y: y))
        if row.hasTimer {
            sep.draw(at: NSPoint(x: sepX, y: y))
            row.timer.draw(at: NSPoint(x: timerX, y: y))
        }
    }
}
NSGraphicsContext.restoreGraphicsState()
rep.size = NSSize(width: pxW / scale, height: pxH / scale)
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
print(png.base64EncodedString())
'''


def ensure_renderer() -> Optional[str]:
    """Write the embedded Swift source to the cache dir and compile it once
    (recompile when the source changes). Returns the binary path, or None if
    Swift/compilation is unavailable."""
    try:
        os.makedirs(os.path.dirname(RENDERER_BIN), exist_ok=True)
        current = None
        if os.path.exists(RENDERER_SRC):
            with open(RENDERER_SRC) as f:
                current = f.read()
        if current != MENUBAR_SWIFT_SRC:
            with open(RENDERER_SRC, "w") as f:
                f.write(MENUBAR_SWIFT_SRC)
        fresh = os.path.exists(RENDERER_BIN) and \
            os.path.getmtime(RENDERER_BIN) >= os.path.getmtime(RENDERER_SRC)
        if not fresh:
            r = subprocess.run(["swiftc", "-O", RENDERER_SRC, "-o", RENDERER_BIN],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return None
        return RENDERER_BIN
    except (OSError, subprocess.SubprocessError):
        return None



def render_menubar_image(tiles: List["MenuTile"]) -> Optional[str]:
    binp = ensure_renderer()
    if not binp or not tiles:
        return None

    cols = sorted(list(set(t.logical_col for t in tiles)))
    col_map = {c: i for i, c in enumerate(cols)}
    draw_tiles = sorted(tiles, key=lambda t: (col_map[t.logical_col], t.logical_row))

    args = []
    for t in draw_tiles:
        args += [t.row.label, t.row.value, t.row.value_color, t.row.timer, t.row.timer_color, str(col_map[t.logical_col])]

    font_pt = _MENUBAR_FONT_PT_SINGLE if len(draw_tiles) == 1 else _MENUBAR_FONT_PT
    env = dict(os.environ,
               MB_SCALE=str(_MENUBAR_SCALE), MB_FONT=str(font_pt),
               MB_PADX=str(_MENUBAR_PAD_X), MB_PADY=str(_MENUBAR_PAD_Y),
               MB_GAP=str(_MENUBAR_LINE_GAP))
    try:
        r = subprocess.run([binp] + args, env=env,
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def read_codex_creds() -> dict:
    """Read the ChatGPT OAuth token Codex stores in ~/.codex/auth.json."""
    try:
        with open(CODEX_AUTH_PATH) as f:
            d = json.load(f)
    except (OSError, ValueError) as exc:
        raise UsageError("no_token", f"codex auth: {exc}")
    tok = d.get("tokens")
    if isinstance(tok, str):
        try:
            tok = json.loads(tok)
        except ValueError:
            tok = {}
    tok = tok or {}
    access = tok.get("access_token")
    if not access:
        raise UsageError("no_token", "no codex access token; run `codex login`")
    return {"access_token": access, "account_id": tok.get("account_id")}


def fetch_codex(token: str, account_id: Optional[str]) -> dict:
    req = urllib.request.Request(
        CODEX_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id or "",
            "Accept": "application/json",
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
                "fable_pct": u.fable_pct,
                "fable_resets_at": u.fable_resets_at,
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
            fable_pct=d.get("fable_pct"),
            fable_resets_at=d.get("fable_resets_at"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def codex_cache_save(c: CodexUsage) -> None:
    try:
        os.makedirs(os.path.dirname(CODEX_CACHE_PATH), exist_ok=True)
        with open(CODEX_CACHE_PATH, "w") as f:
            json.dump({
                "primary_pct": c.primary_pct,
                "primary_resets_at": c.primary_resets_at,
                "secondary_pct": c.secondary_pct,
                "secondary_resets_at": c.secondary_resets_at,
                "primary_window_s": c.primary_window_s,
                "secondary_window_s": c.secondary_window_s,
            }, f)
    except OSError:
        pass


def codex_cache_load() -> Optional[CodexUsage]:
    try:
        with open(CODEX_CACHE_PATH) as f:
            d = json.load(f)
        return CodexUsage(
            primary_pct=d["primary_pct"],
            primary_resets_at=d["primary_resets_at"],
            secondary_pct=d["secondary_pct"],
            secondary_resets_at=d["secondary_resets_at"],
            primary_window_s=d.get("primary_window_s"),
            secondary_window_s=d.get("secondary_window_s"),
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


def _read_claude_code_file_creds() -> Optional[dict]:
    try:
        with open(CLAUDE_CODE_CREDS_PATH) as f:
            return json.load(f)["claudeAiOauth"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_credentials() -> dict:
    """Return the freshest credentials: try the Keychain, fall back to Claude
    Code's own credentials file, then merge with our refresh cache (whichever
    has the later expiry wins, so Claude Code's refreshes and ours stay in sync)."""
    oauth = None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            oauth = json.loads(out.stdout)["claudeAiOauth"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        oauth = None

    if oauth is None:
        oauth = _read_claude_code_file_creds()

    if oauth is None:
        raise UsageError("no_token",
                         "no credentials in Keychain or ~/.claude/.credentials.json")

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

_CLAUDE_NOTES = {
    "no_token": "not signed in — open Claude Code",
    "auth": "auth expired — open Claude Code",
    "offline": "offline",
    "bad_response": "rate-limited or error",
}
_CODEX_NOTES = {
    "no_token": "signed out — run `codex login`",
    "auth": "auth expired — run codex",
    "offline": "offline",
    "bad_response": "rate-limited or error",
}

# What the user can DO about each failure, shown at the bottom of the dropdown.
# no_token also covers a missing keychain item (Claude Code moved/re-saved its
# login), so the remedy is the same either way: re-sign-in via Claude Code.
_CLAUDE_HELP = {
    "no_token": "Claude fix: open Claude Code & sign in (/login); if no prompt, restart SwiftBar, then Check now",
    "auth": "Claude fix: open Claude Code & sign in (/login), then Check now",
    "offline": "Claude fix: check your connection, then Check now",
    "bad_response": "Claude: temporary API error — retries at next check",
}
_CODEX_HELP = {
    "no_token": "Codex fix: run `codex login` in a terminal, then Check now",
    "auth": "Codex fix: run `codex login` in a terminal, then Check now",
    "offline": "Codex fix: check your connection, then Check now",
    "bad_response": "Codex: temporary API error — retries at next check",
}


def _get_claude(now_ms: int):
    """Return (Usage|None, stale, note, help). Refreshes proactively/reactively
    and falls back to the cached reading on a transient error."""
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
        return usage, False, None, None
    except UsageError as err:
        if err.kind in _TRANSIENT_KINDS:
            cached = cache_load()
            if cached is not None:
                return cached, True, None, None
        return (None, False, _CLAUDE_NOTES.get(err.kind, "unavailable"),
                _CLAUDE_HELP.get(err.kind))




def read_agy_creds() -> dict:
    try:
        with open(AGY_TOKEN_PATH) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise UsageError("no_token", "token file is not a dictionary")
    except (OSError, ValueError) as exc:
        raise UsageError("no_token", f"agy auth: {exc}")
    tok = d.get("token")
    if not isinstance(tok, dict):
        tok = {}
    access = tok.get("access_token")
    if not isinstance(access, str) or not access:
        raise UsageError("no_token", "no agy access token")
    return {"access_token": access, "expiry_ms": _parse_agy_expiry(tok.get("expiry"))}



def _parse_agy_expiry(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            dt = _parse_iso(raw)
            if dt.tzinfo is None:  # treat a naive timestamp as UTC
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            pass  # not ISO — fall through to numeric epoch handling
    try:
        v = float(raw)
        if not math.isfinite(v) or not v > 0:
            return None
        if v >= 1e12:
            return int(v)
        elif v >= 1e9:
            return int(v * 1000)
        return None
    except (ValueError, TypeError, OverflowError, OSError):
        return None

def classify_agy_bucket(bucket: dict, group: Optional[dict] = None) -> Optional[str]:
    # retrieveUserQuotaSummary identifies families through human-readable group
    # names; bucket ids are deliberately treated only as a defensive fallback.
    fields = ("displayName", "description", "bucketId", "modelId",
              "quotaId", "tokenType")
    marker = " ".join(str(obj.get(k, "")) for obj in (group or {}, bucket)
                      for k in fields).lower()
    if "gemini" in marker:
        return "gemini"
    if any(k in marker for k in ("claude", "gpt", "openai", "external", "byok")):
        return "external"
    return None

def _normalize_agy_reset(raw) -> Optional[str]:
    if isinstance(raw, (int, float)):
        return _epoch_to_iso(raw)
    if not isinstance(raw, str):
        return None
    try:
        _parse_iso(raw)
        return raw
    except (TypeError, ValueError):
        return None

def parse_agy(data: dict) -> AgyUsage:
    if not isinstance(data, dict):
        raise UsageError("bad_response", "agy payload not a dict")

    if "buckets" not in data and "groups" not in data:
        raise UsageError("bad_response", "no buckets")

    grouped_buckets = []
    buckets = data.get("buckets", [])
    groups = data.get("groups", [])
    
    if isinstance(buckets, list) and len(buckets) > 0:
        grouped_buckets.extend((bucket, None) for bucket in buckets)
    elif isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("buckets", []), list):
                continue
            grouped_buckets.extend((bucket, group) for bucket in group["buckets"])
    else:
        raise UsageError("bad_response", "agy payload missing usable buckets or groups")

    valid_buckets_parsed = 0
    total_raw_buckets = len(grouped_buckets)
    
    values = {("gemini", "weekly"): (None, None),
              ("gemini", "5h"): (None, None),
              ("external", "weekly"): (None, None),
              ("external", "5h"): (None, None)}
    for b, group in grouped_buckets:
        if not isinstance(b, dict):
            continue
        cat = classify_agy_bucket(b, group)
        if not cat:
            continue

        pct = None
        if "remainingFraction" in b:
            try:
                frac = float(b["remainingFraction"])
                if math.isfinite(frac):
                    pct = max(0, min(100, round(frac * 100)))
            except (TypeError, ValueError, OverflowError):
                pass
        elif "remainingAmount" in b and "limit" in b:
            try:
                amt = float(b["remainingAmount"])
                limit = float(b["limit"])
                if math.isfinite(amt) and math.isfinite(limit) and limit > 0:
                    pct = max(0, min(100, round((amt / limit) * 100)))
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                pass

        if pct is None:
            continue

        window_raw = str(b.get("window", "")).lower().replace(" ", "")
        if window_raw in ("18000s", "5h", "5-hour", "fivehour"):
            window = "5h"
        elif window_raw in ("604800s", "168h", "7d", "weekly", "week"):
            window = "weekly"
        else:
            continue
            
        valid_buckets_parsed += 1
        resets = _normalize_agy_reset(b.get("resetTime"))
        old_pct, _ = values[(cat, window)]
        # If the service ever returns duplicate buckets for one window, the
        # lowest remaining value is the limiting one.
        if pct is not None and (old_pct is None or pct < old_pct):
            values[(cat, window)] = (pct, resets)

    if total_raw_buckets > 0 and valid_buckets_parsed == 0:
        raise UsageError("bad_response", "no recognizable buckets in payload")

    return AgyUsage(
        *values[("gemini", "weekly")], *values[("gemini", "5h")],
        *values[("external", "weekly")], *values[("external", "5h")],
    )

def fetch_agy(token: str, host: str = AGY_PROD_HOST) -> dict:
    req = urllib.request.Request(
        f"{host}/v1internal:retrieveUserQuotaSummary",
        data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "antigravity/1.1.1"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise UsageError("auth", "HTTP 401")
        if exc.code in (403, 404) and host == AGY_PROD_HOST:
            return fetch_agy(token, AGY_DAILY_HOST)
        raise UsageError("bad_response", f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UsageError("offline", str(exc))
    except ValueError as exc:
        raise UsageError("bad_response", f"non-JSON: {exc}")

def agy_cache_save(a: AgyUsage) -> None:
    try:
        os.makedirs(os.path.dirname(AGY_CACHE_PATH), exist_ok=True)
        with open(AGY_CACHE_PATH, "w") as f:
            json.dump({
                field: getattr(a, field) for field in AgyUsage.__dataclass_fields__
            }, f)
    except OSError:
        pass

def agy_cache_load() -> Optional[AgyUsage]:
    try:
        with open(AGY_CACHE_PATH) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        fields = tuple(AgyUsage.__dataclass_fields__)
        pct_fields = tuple(field for field in fields if field.endswith("_pct"))
        
        for field in pct_fields:
            if field in d and d[field] is not None:
                try:
                    val = float(d[field])
                    if not math.isfinite(val) or val < 0 or val > 100:
                        d[field] = None
                    else:
                        d[field] = val
                except (TypeError, ValueError):
                    d[field] = None
                    
        if all(d.get(field) is None for field in pct_fields):
            return None
            
        for field in fields:
            if not field.endswith("_pct") and d.get(field) is not None:
                if not isinstance(d[field], str) or _normalize_agy_reset(d[field]) is None:
                    d[field] = None
                    
        return AgyUsage(*(d.get(field) for field in fields))
    except Exception:
        return None

_AGY_NOTES = {
    "no_token": "not signed in — open Antigravity",
    "auth": "token expired — open Antigravity",
    "offline": "offline",
    "bad_response": "rate-limited or error",
}
_AGY_HELP = {
    "no_token": "Antigravity fix: sign in to Antigravity, then Check now",
    "auth": "Antigravity fix: open Antigravity to refresh its login, then Check now",
    "offline": "Antigravity fix: check your connection, then Check now",
    "bad_response": "Antigravity: temporary API error — retries at next check",
}

def _parse_agy_ls_processes(ps_output: str) -> List[Tuple[str, str]]:
    """(pid, csrf_token) for every Antigravity IDE language server. Only the
    IDE server carries --csrf_token (the CLI server has none), so requiring it
    selects the right processes; an IDE restart can leave more than one, so we
    return all candidates and let the caller probe each."""
    found: List[Tuple[str, str]] = []
    for line in ps_output.splitlines():
        if "language_server" not in line or "--csrf_token" not in line:
            continue
        if "antigravity" not in line:  # skip other Codeium/Windsurf servers
            continue
        m = re.search(r"--csrf_token[=\s]+(\S+)", line)
        if not m:
            continue
        pid = line.strip().split(None, 1)[0]
        if pid.isdigit():
            found.append((pid, m.group(1)))
    return found


def _parse_lsof_listeners(lsof_output: str) -> List[Tuple[str, str]]:
    """(host, port) loopback listeners in first-seen order; host is
    "127.0.0.1" or "::1"."""
    listeners: List[Tuple[str, str]] = []
    for m in re.finditer(r"(127\.0\.0\.1|\[?::1\]?):(\d+)\b", lsof_output):
        host = "::1" if ":" in m.group(1) else "127.0.0.1"
        item = (host, m.group(2))
        if item not in listeners:
            listeners.append(item)
    return listeners


def _find_agy_local_server() -> Optional[Tuple[str, List[Tuple[str, str]]]]:
    """(csrf_token, [(host, port), ...]) for a running Antigravity language
    server, or None. Tries each candidate process until one has listeners.
    Best-effort: any failure returns None so callers fall back."""
    try:
        ps = subprocess.run(["ps", "-axww", "-o", "pid=", "-o", "command="],
                            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    for pid, csrf in _parse_agy_ls_processes(ps.stdout):
        try:
            ls = subprocess.run(["lsof", "-nP", "-a", "-p", pid,
                                 "-iTCP", "-sTCP:LISTEN"],
                                capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            continue
        listeners = _parse_lsof_listeners(ls.stdout)
        if listeners:
            return csrf, listeners[:4]  # cap probes to bound latency
    return None


def _agy_local_request(host: str, port: str, csrf: str) -> Optional[dict]:
    """POST the quota-summary RPC to one loopback listener. Returns the
    unwrapped payload dict, or None on any failure."""
    netloc = "[%s]:%s" % (host, port) if ":" in host else "%s:%s" % (host, port)
    url = ("https://%s/exa.language_server_pb.LanguageServerService"
           "/RetrieveUserQuotaSummary" % netloc)
    try:
        req = urllib.request.Request(
            url, data=b"{}", method="POST",
            headers={"Content-Type": "application/json",
                     "x-codeium-csrf-token": csrf})
        # Self-signed cert on loopback; verifying it is neither possible nor
        # meaningful for a 127.0.0.1 / ::1 connection to our own machine.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=2, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    # The local RPC wraps the payload as {"response": {...}}; the cloud
    # endpoint returns the same groups/buckets object flat. Unwrap so parse_agy
    # sees the shape it already handles.
    if "groups" not in body and "buckets" not in body:
        inner = body.get("response")
        return inner if isinstance(inner, dict) else None
    return body


def _fetch_agy_local() -> Optional[dict]:
    """Query the running Antigravity language server for quota. Returns an
    unwrapped payload dict on success, or None if the server isn't running or
    doesn't answer — the server holds a live, auto-refreshed token, so this
    works even when the on-disk token has expired."""
    found = _find_agy_local_server()
    if not found:
        return None
    csrf, listeners = found
    for host, port in listeners:
        data = _agy_local_request(host, port, csrf)
        if data is not None:
            return data
    return None


def _get_agy(now_ms: int):
    try:
        # Prefer the running Antigravity language server: it holds a live,
        # auto-refreshed token, so it keeps working when the on-disk token has
        # expired (Antigravity refreshes in memory but rewrites the token file
        # only occasionally).
        try:
            local = _fetch_agy_local()
        except Exception:
            local = None  # never let a local-probe error skip cloud/cache
        if local is not None:
            try:
                a = parse_agy(local)
            except UsageError:
                a = None  # malformed local reply — fall through to the cloud
            if a is not None:
                agy_cache_save(a)
                return a, False, None, None
        # Fall back to the on-disk token against the cloud quota endpoint.
        creds = read_agy_creds()
        if creds["expiry_ms"] and now_ms >= creds["expiry_ms"]:
            raise UsageError("auth", "agy token expired")
        data = fetch_agy(creds["access_token"])
        a = parse_agy(data)
        agy_cache_save(a)
        return a, False, None, None
    except UsageError as err:
        if err.kind in _TRANSIENT_KINDS:
            cached = agy_cache_load()
            if cached is not None:
                return cached, True, None, None
        return (None, False, _AGY_NOTES.get(err.kind, "unavailable"),
                _AGY_HELP.get(err.kind))
    except Exception:
        return (None, False, _AGY_NOTES["bad_response"], _AGY_HELP["bad_response"])


def _get_codex(now_ms: int):
    """Return (CodexUsage|None, stale, note, help), cache fallback on transient."""
    try:
        creds = read_codex_creds()
        data = fetch_codex(creds["access_token"], creds["account_id"])
        cx = parse_codex(data)
        codex_cache_save(cx)
        return cx, False, None, None
    except UsageError as err:
        if err.kind in _TRANSIENT_KINDS:
            cached = codex_cache_load()
            if cached is not None:
                return cached, True, None, None
        return (None, False, _CODEX_NOTES.get(err.kind, "unavailable"),
                _CODEX_HELP.get(err.kind))


def _detect_interval() -> int:
    argv0 = sys.argv[0] if sys.argv else ""
    m = re.search(r"\.(\d+)s\.", os.path.basename(argv0))
    return int(m.group(1)) if m else 300


def assemble_output(rows, claude, codex, now, interval_s, stale_c, stale_x,
                    note_c, note_x, image_b64,
                    boost_remaining=None, cli=None, display_mode="both",
                    detail_color=GRAY, help_c=None, help_x=None,
                    *,
                    agy=None, stale_a=False, note_a=None, help_a=None,
                    enabled=None, tiles=None, percent_mode="used") -> str:
    if enabled is not None and not enabled:
        first = "usage | color=" + GRAY
    elif not rows:
        first = "usage | color=" + GRAY
    elif image_b64:
        first = f"| image={image_b64}"
    else:
        first = " · ".join(_bar_row_text(r) for r in rows)
    dropdown = render_dropdown(claude, codex, now, interval_s, stale_c, stale_x,
                               note_c, note_x, boost_remaining, cli, display_mode,
                               detail_color, help_c, help_x,
                               agy=agy, stale_agy=stale_a, agy_note=note_a,
                               agy_help=help_a, enabled=enabled,
                               percent_mode=percent_mode)
    return first + "\n" + dropdown


# ---- temporary "boost" (1-minute refresh for 30 minutes) --------------------

def _boost_read_until() -> float:
    try:
        with open(BOOST_UNTIL_PATH) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return 0.0


def boost_remaining(now_epoch: float) -> Optional[int]:
    rem = _boost_read_until() - now_epoch
    return int(rem) if rem > 0 else None


def _boost_lock_alive() -> bool:
    try:
        with open(BOOST_LOCK_PATH) as f:
            os.kill(int(f.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def _swiftbar_refresh() -> None:
    try:
        subprocess.run(["open", "-g", "swiftbar://refreshallplugins"],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def boost_start(minutes: int = 30) -> None:
    os.makedirs(os.path.dirname(BOOST_UNTIL_PATH), exist_ok=True)
    with open(BOOST_UNTIL_PATH, "w") as f:
        f.write(str(time.time() + minutes * 60))
    _swiftbar_refresh()
    if not _boost_lock_alive():
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "_boostloop"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def boost_stop() -> None:
    try:
        os.remove(BOOST_UNTIL_PATH)
    except OSError:
        pass
    _swiftbar_refresh()


def boost_loop() -> None:
    with open(BOOST_LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    try:
        while time.time() < _boost_read_until():
            time.sleep(BOOST_INTERVAL_S)
            _swiftbar_refresh()
    finally:
        try:
            os.remove(BOOST_LOCK_PATH)
        except OSError:
            pass


def build_output(now: datetime, interval_s: Optional[int] = None) -> str:
    base = interval_s if interval_s is not None else _detect_interval()
    now_ms = int(now.timestamp() * 1000)
    boost_rem = boost_remaining(now.timestamp())
    effective = BOOST_INTERVAL_S if boost_rem else base
    claude, stale_c, note_c, help_c = _get_claude(now_ms)
    codex, stale_x, note_x, help_x = _get_codex(now_ms)
    agy, stale_a, note_a, help_a = _get_agy(now_ms)

    enabled = enabled_load()
    percent_mode = percent_mode_load()
    display_mode = display_mode_load()

    tiles = menubar_tiles(claude, codex, agy, now, percent_mode)
    filtered = filter_menubar_tiles(tiles, enabled)
    rows = [t.row for t in filtered]
    image_b64 = render_menubar_image(filtered)

    cli = (sys.executable, os.path.abspath(__file__))
    return assemble_output(rows, claude, codex, now, effective, stale_c, stale_x,
                           note_c, note_x, image_b64, boost_rem, cli, display_mode,
                           GRAY, help_c, help_x,
                           agy=agy, stale_a=stale_a, note_a=note_a, help_a=help_a,
                           enabled=enabled, tiles=filtered,
                           percent_mode=percent_mode)


def main() -> None:
    print(build_output(datetime.now(timezone.utc)))


if __name__ == "__main__":
    _cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if _cmd == "boost":
        boost_start()
    elif _cmd == "stop":
        boost_stop()
    elif _cmd == "show":
        val = sys.argv[2] if len(sys.argv) > 2 else "both"
        if val == "both":
            enabled_save(set(PROVIDERS))
        elif val in ("claude", "codex"):
            enabled_save({val})
        _swiftbar_refresh()
    elif _cmd == "toggle":
        enabled_toggle(sys.argv[2] if len(sys.argv) > 2 else "")
        _swiftbar_refresh()
    elif _cmd in ("percent", "agy-percent"):
        percent_mode_save(sys.argv[2] if len(sys.argv) > 2 else "used")
        _swiftbar_refresh()
    elif _cmd == "_boostloop":
        boost_loop()
    else:
        main()
