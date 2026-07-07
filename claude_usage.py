from __future__ import annotations

import json
import os
import re
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
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CACHE_PATH = os.path.expanduser("~/.cache/claude-usage/last.json")
CODEX_CACHE_PATH = os.path.expanduser("~/.cache/claude-usage/last_codex.json")
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
DISPLAY_MODES = ("both", "claude", "codex")


def normalize_display_mode(mode: Optional[str]) -> str:
    return mode if mode in DISPLAY_MODES else "both"


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


@dataclass
class CodexUsage:
    primary_pct: float               # 5-hour window, % used
    primary_resets_at: Optional[str]
    secondary_pct: float             # 7-day window, % used
    secondary_resets_at: Optional[str]


class UsageError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}")


def _epoch_to_iso(epoch) -> Optional[str]:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def parse_codex(data: dict) -> CodexUsage:
    """Parse chatgpt.com/backend-api/wham/usage. Codex reports used_percent,
    matching Claude's 'utilization' — no remaining->used conversion needed."""
    try:
        rl = data["rate_limit"]
        pw = rl["primary_window"]
        sw = rl["secondary_window"]
        return CodexUsage(
            primary_pct=float(pw.get("used_percent") or 0.0),
            primary_resets_at=_epoch_to_iso(pw.get("reset_at")),
            secondary_pct=float(sw.get("used_percent") or 0.0),
            secondary_resets_at=_epoch_to_iso(sw.get("reset_at")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageError("bad_response", f"unexpected codex payload: {exc}")


def parse_usage(data: dict) -> Usage:
    try:
        fh = data["five_hour"]
        sd = data["seven_day"]
        return Usage(
            session_pct=float(fh.get("utilization") or 0.0),
            session_resets_at=fh.get("resets_at"),  # None when no active window
            weekly_pct=float(sd.get("utilization") or 0.0),
            weekly_resets_at=sd.get("resets_at"),
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
TIMER_BEST_MAX_S = 900      # <= 15m left -> white (best)
TIMER_SOON_MAX_S = 3600     # <= 1h left  -> green
TIMER_MID_MAX_S = 10800     # <= 3h left  -> orange; longer -> red


def timer_color(seconds_left: float) -> str:
    if seconds_left <= TIMER_BEST_MAX_S:
        return WHITE
    if seconds_left <= TIMER_SOON_MAX_S:
        return GREEN
    if seconds_left <= TIMER_MID_MAX_S:
        return ORANGE
    return RED


@dataclass
class BarRow:
    label: str           # "C" / "Cx"
    value: str           # "44%" / "—"
    value_color: str     # by usage severity
    timer: str           # "2h0m" or "" when no active window
    timer_color: str     # by time-left


def _bar_row(tag: str, pct: float, resets_at: Optional[str], now: datetime) -> "BarRow":
    if _has_reset(resets_at):
        secs = time_until(resets_at, now)
        return BarRow(tag, _pct(pct), severity_color(pct),
                      format_duration(secs, compact=True), timer_color(secs))
    return BarRow(tag, _pct(pct), severity_color(pct), "", GRAY)


def menubar_rows(
    claude: Optional["Usage"], codex: Optional["CodexUsage"], now: datetime
) -> List["BarRow"]:
    """Two stacked rows: Claude 5h session over Codex 5h, each carrying an
    independently-colored value and reset timer."""
    c = _bar_row("C", claude.session_pct, claude.session_resets_at, now) \
        if claude is not None else BarRow("C", "—", GRAY, "", GRAY)
    x = _bar_row("Cx", codex.primary_pct, codex.primary_resets_at, now) \
        if codex is not None else BarRow("Cx", "—", GRAY, "", GRAY)
    return [c, x]


def filter_menubar_rows(rows: List["BarRow"], display_mode: str) -> List["BarRow"]:
    mode = normalize_display_mode(display_mode)
    if mode == "claude":
        return [r for r in rows if r.label == "C"]
    if mode == "codex":
        return [r for r in rows if r.label == "Cx"]
    return rows


def _bar_row_text(r: "BarRow") -> str:
    """Plain-text form of a row for the no-Swift fallback menu bar."""
    return f"{r.label} {r.value}" + (f" · {r.timer}" if r.timer else "")


def next_check_label(now: datetime, interval_s: int) -> str:
    """Full local clock time of the next scheduled refresh, with seconds and
    AM/PM (e.g. '↻ 1:33:24 PM')."""
    nxt = (now + timedelta(seconds=interval_s)).astimezone()
    return "↻ " + nxt.strftime("%-I:%M:%S %p")


def _window_line(label: str, pct: float, resets_at: Optional[str],
                 now: datetime) -> str:
    # No status color in the dropdown — it's hard to read on the translucent
    # menu; the color coding lives only in the menu-bar image. Default text color.
    body = f"{label}  {_pct(pct)}"
    if _has_reset(resets_at):
        body += "  ·  resets in " + format_countdown(time_until(resets_at, now))
    return body


_STALE_NOTE = "last reading (rate-limited or offline) | color=" + GRAY


def render_dropdown(
    claude: Optional["Usage"], codex: Optional["CodexUsage"],
    now: datetime, interval_s: int,
    stale_claude: bool = False, stale_codex: bool = False,
    claude_note: Optional[str] = None, codex_note: Optional[str] = None,
    boost_remaining: Optional[int] = None, cli: Optional[Tuple[str, str]] = None,
    display_mode: str = "both",
) -> str:
    display_mode = normalize_display_mode(display_mode)
    lines = ["---"]

    if display_mode in ("both", "claude"):
        lines.append("Claude | color=" + GRAY)
        if claude is not None:
            lines.append(_window_line("5-hour", claude.session_pct,
                                      claude.session_resets_at, now))
            lines.append(_window_line("Weekly", claude.weekly_pct,
                                      claude.weekly_resets_at, now))
            if stale_claude:
                lines.append(_STALE_NOTE)
        else:
            lines.append((claude_note or "unavailable") + " | color=" + GRAY)

    if display_mode in ("both", "codex"):
        lines.append("Codex | color=" + GRAY)
        if codex is not None:
            lines.append(_window_line("5-hour", codex.primary_pct,
                                      codex.primary_resets_at, now))
            lines.append(_window_line("Weekly", codex.secondary_pct,
                                      codex.secondary_resets_at, now))
            if stale_codex:
                lines.append(_STALE_NOTE)
        else:
            lines.append((codex_note or "unavailable") + " | color=" + GRAY)

    lines.append("---")
    lines.append("Display | color=" + GRAY)
    labels = {"claude": "Claude", "codex": "Codex", "both": "Both"}
    for mode in ("claude", "codex", "both"):
        label = f"Show: {labels[mode]}" + (" ✓" if mode == display_mode else "")
        if cli is None:
            lines.append(label)
        else:
            py, mod = cli
            lines.append(f'{label} | bash="{py}" param1="{mod}" '
                         f'param2="show" param3="{mode}" terminal=false refresh=true')
    lines.append("---")
    if boost_remaining:
        ends = (now + timedelta(seconds=boost_remaining)).astimezone().strftime("%-I:%M:%S %p")
        lines.append(f"{next_check_label(now, interval_s)} · next check "
                     f"(boosted to 1m, until {ends}) | color={GRAY}")
    else:
        lines.append(f"{next_check_label(now, interval_s)} · next check "
                     f"(every {interval_s // 60}m) | color={GRAY}")
    lines.append("Refresh now | refresh=true")
    if cli is not None:
        py, mod = cli
        if boost_remaining:
            lines.append(f'Stop 1-minute boost | bash="{py}" param1="{mod}" '
                         f'param2="stop" terminal=false refresh=true')
        else:
            lines.append(f'Refresh every 1 min for 30 min | bash="{py}" param1="{mod}" '
                         f'param2="boost" terminal=false refresh=true')
    details = {
        "claude": "Claude /usage for details",
        "codex": "Codex /status for details",
        "both": "Claude /usage · Codex /status for details",
    }[display_mode]
    lines.append(details + " | color=" + GRAY)
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
guard a.count >= 6 && (a.count - 1) % 5 == 0 else { exit(1) }
let scale: CGFloat = CGFloat(Double(ProcessInfo.processInfo.environment["MB_SCALE"] ?? "3") ?? 3)
let fontPt = CGFloat(Double(ProcessInfo.processInfo.environment["MB_FONT"] ?? "9") ?? 9)
let padX = CGFloat(Double(ProcessInfo.processInfo.environment["MB_PADX"] ?? "2") ?? 2)
let padY = CGFloat(Double(ProcessInfo.processInfo.environment["MB_PADY"] ?? "1") ?? 1)
let gap  = CGFloat(Double(ProcessInfo.processInfo.environment["MB_GAP"] ?? "0") ?? 0)

let font = NSFont.systemFont(ofSize: fontPt * scale, weight: .medium)
func run(_ s: String, _ hex: String) -> NSAttributedString {
    NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: color(hex)])
}

struct Row { let label, value, timer: NSAttributedString; let hasTimer: Bool }
let sepGray = "#8e8e93"
var rows: [Row] = []
let rowCount = (a.count - 1) / 5
for i in 0..<rowCount {
    let b = 1 + i * 5
    let hasTimer = !a[b+3].isEmpty
    rows.append(Row(label: run(a[b], "#ffffff"), value: run(a[b+1], a[b+2]),
                    timer: run(a[b+3], a[b+4]), hasTimer: hasTimer))
}
let sep = run("·", sepGray)
let spc = fontPt * scale * 0.30
let sepW = sep.size().width
let maxLabelW = rows.map { $0.label.size().width }.max() ?? 0
let maxValueW = rows.map { $0.value.size().width }.max() ?? 0
let maxTimerW = rows.map { $0.hasTimer ? $0.timer.size().width : 0 }.max() ?? 0
let anyTimer = rows.contains { $0.hasTimer }
let lh = ceil(rows.map { max($0.label.size().height, $0.value.size().height) }.max() ?? 0)

let labelX = padX * scale
let valueX = labelX + maxLabelW + spc
let sepX = valueX + maxValueW + spc
let timerX = sepX + sepW + spc
let pxW = (anyTimer ? timerX + maxTimerW : valueX + maxValueW) + padX * scale
let pxH = lh * CGFloat(rows.count) + gap * scale * CGFloat(max(0, rows.count - 1)) + padY * scale * 2

guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(pxW),
        pixelsHigh: Int(pxH), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
        isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
else { exit(1) }
rep.size = NSSize(width: pxW, height: pxH)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
for (i, row) in rows.enumerated() {
    // origin bottom-left: draw row 0 at the top, later rows underneath.
    let y = padY * scale + CGFloat(rows.count - 1 - i) * (lh + gap * scale)
    row.label.draw(at: NSPoint(x: labelX, y: y))
    row.value.draw(at: NSPoint(x: valueX, y: y))
    if row.hasTimer {
        sep.draw(at: NSPoint(x: sepX, y: y))
        row.timer.draw(at: NSPoint(x: timerX, y: y))
    }
}
NSGraphicsContext.restoreGraphicsState()
// Tag point size = pixels ÷ scale so it displays at ~bar height, Retina-crisp.
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


def render_menubar_image(rows: List["BarRow"]) -> Optional[str]:
    """Render visible rows (aligned, independently-colored value + timer)
    to a base64 PNG, or None if Swift is unavailable."""
    binp = ensure_renderer()
    if not binp or not rows:
        return None
    args = []
    for r in rows:
        args += [r.label, r.value, r.value_color, r.timer, r.timer_color]
    env = dict(os.environ,
               MB_SCALE=str(_MENUBAR_SCALE), MB_FONT=str(_MENUBAR_FONT_PT),
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


def codex_cache_save(c: CodexUsage) -> None:
    try:
        os.makedirs(os.path.dirname(CODEX_CACHE_PATH), exist_ok=True)
        with open(CODEX_CACHE_PATH, "w") as f:
            json.dump({
                "primary_pct": c.primary_pct,
                "primary_resets_at": c.primary_resets_at,
                "secondary_pct": c.secondary_pct,
                "secondary_resets_at": c.secondary_resets_at,
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

_CLAUDE_NOTES = {
    "no_token": "Keychain locked — allow access",
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


def _get_claude(now_ms: int):
    """Return (Usage|None, stale, note). Refreshes proactively/reactively and
    falls back to the cached reading on a transient error."""
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
        return usage, False, None
    except UsageError as err:
        if err.kind in _TRANSIENT_KINDS:
            cached = cache_load()
            if cached is not None:
                return cached, True, None
        return None, False, _CLAUDE_NOTES.get(err.kind, "unavailable")


def _get_codex(now_ms: int):
    """Return (CodexUsage|None, stale, note), with cache fallback on transient."""
    try:
        creds = read_codex_creds()
        data = fetch_codex(creds["access_token"], creds["account_id"])
        cx = parse_codex(data)
        codex_cache_save(cx)
        return cx, False, None
    except UsageError as err:
        if err.kind in _TRANSIENT_KINDS:
            cached = codex_cache_load()
            if cached is not None:
                return cached, True, None
        return None, False, _CODEX_NOTES.get(err.kind, "unavailable")


def _detect_interval() -> int:
    argv0 = sys.argv[0] if sys.argv else ""
    m = re.search(r"\.(\d+)s\.", os.path.basename(argv0))
    return int(m.group(1)) if m else 300


def assemble_output(rows, claude, codex, now, interval_s, stale_c, stale_x,
                    note_c, note_x, image_b64,
                    boost_remaining=None, cli=None, display_mode="both") -> str:
    """Pure: build the SwiftBar output (menu-bar line + dropdown). The next-check
    time lives only in the dropdown; the menu bar shows just the image."""
    if image_b64:
        first = f"| image={image_b64}"
    else:  # graceful fallback when Swift rendering is unavailable
        first = " · ".join(_bar_row_text(r) for r in rows)
    dropdown = render_dropdown(claude, codex, now, interval_s, stale_c, stale_x,
                               note_c, note_x, boost_remaining, cli, display_mode)
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
    claude, stale_c, note_c = _get_claude(now_ms)
    codex, stale_x, note_x = _get_codex(now_ms)
    display_mode = display_mode_load()
    rows = filter_menubar_rows(menubar_rows(claude, codex, now), display_mode)
    image_b64 = render_menubar_image(rows)
    cli = (sys.executable, os.path.abspath(__file__))
    return assemble_output(rows, claude, codex, now, effective, stale_c, stale_x,
                           note_c, note_x, image_b64, boost_rem, cli, display_mode)


def main() -> None:
    print(build_output(datetime.now(timezone.utc)))


if __name__ == "__main__":
    _cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if _cmd == "boost":
        boost_start()
    elif _cmd == "stop":
        boost_stop()
    elif _cmd == "show":
        display_mode_save(sys.argv[2] if len(sys.argv) > 2 else "both")
        _swiftbar_refresh()
    elif _cmd == "_boostloop":
        boost_loop()
    else:
        main()
