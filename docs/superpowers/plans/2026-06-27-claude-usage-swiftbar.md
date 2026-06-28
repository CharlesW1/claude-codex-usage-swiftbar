# Claude Usage SwiftBar Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A SwiftBar plugin that shows Claude Code's `/usage` session + weekly limits in the macOS menu bar.

**Architecture:** All logic lives in an importable, unit-tested module `claude_usage.py` (pure formatting/parsing functions plus thin I/O functions). The SwiftBar entry point `claude-usage.60s.py` is a tiny shim that imports the module and prints `main()`'s output. Stateless: each run reads the Keychain OAuth token, calls `GET /api/oauth/usage`, and prints SwiftBar-formatted text.

**Tech Stack:** Python 3.9 (stdlib only — `urllib`, `subprocess`, `datetime`, `json`, `dataclasses`); `unittest` for tests; SwiftBar as the menu bar host; macOS `security` CLI for Keychain.

## Global Constraints

- Python 3.9-compatible; **stdlib only**, no third-party runtime or test dependencies.
- Usage endpoint: `GET https://api.anthropic.com/api/oauth/usage` with headers `Authorization: Bearer <token>` and `anthropic-beta: oauth-2025-04-20`; 5-second timeout.
- Keychain item: generic password, service name exactly `Claude Code-credentials`; token at JSON path `claudeAiOauth.accessToken`.
- Severity color thresholds (session %): `< 70` green `#34c759`; `70–90` orange `#ff9500`; `> 90` red `#ff3b30`.
- Menu bar text format: `<session%> · <compact-time-left>` e.g. `46% · 3h12m`, with `sfimage=gauge.medium`.
- Never crash into the menu bar: all failures render exactly one bar line + a dropdown note.
- Run all tests with `python3 -m unittest discover -s tests -v`.

---

### Task 1: Module scaffold + pure helpers (`severity_color`, `format_duration`)

**Files:**
- Create: `claude_usage.py`
- Test: `tests/test_helpers.py`
- Create: `tests/__init__.py` (empty)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `severity_color(pct: float) -> str` — returns `"#34c759"` / `"#ff9500"` / `"#ff3b30"`.
  - `format_duration(seconds: float, compact: bool) -> str` — `compact=True` → `"3h12m"`, `"12m"`, `"now"`; `compact=False` → `"3h 12m"`, `"12m"`, `"now"`. Negative/zero seconds → `"now"`. Rounds down to whole minutes.
  - Module constants `GREEN="#34c759"`, `ORANGE="#ff9500"`, `RED="#ff3b30"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_helpers.py
import unittest
from claude_usage import severity_color, format_duration, GREEN, ORANGE, RED

class TestSeverityColor(unittest.TestCase):
    def test_below_70_is_green(self):
        self.assertEqual(severity_color(0), GREEN)
        self.assertEqual(severity_color(69.9), GREEN)
    def test_70_to_90_is_orange(self):
        self.assertEqual(severity_color(70), ORANGE)
        self.assertEqual(severity_color(90), ORANGE)
    def test_above_90_is_red(self):
        self.assertEqual(severity_color(90.1), RED)
        self.assertEqual(severity_color(100), RED)

class TestFormatDuration(unittest.TestCase):
    def test_hours_and_minutes_compact(self):
        self.assertEqual(format_duration(3*3600 + 12*60, compact=True), "3h12m")
    def test_hours_and_minutes_spaced(self):
        self.assertEqual(format_duration(3*3600 + 12*60, compact=False), "3h 12m")
    def test_minutes_only(self):
        self.assertEqual(format_duration(12*60 + 59, compact=True), "12m")
    def test_zero_or_negative_is_now(self):
        self.assertEqual(format_duration(0, compact=True), "now")
        self.assertEqual(format_duration(-5, compact=False), "now")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_helpers -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError: cannot import name 'severity_color'`.

- [ ] **Step 3: Write minimal implementation**

```python
# claude_usage.py
from __future__ import annotations

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_helpers -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add claude_usage.py tests/test_helpers.py tests/__init__.py
git commit -m "feat: add severity_color and format_duration helpers"
```

---

### Task 2: Parse endpoint JSON + time math (`Usage`, `UsageError`, `parse_usage`, `time_until`, `format_reset_day`)

**Files:**
- Modify: `claude_usage.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces:
  - `@dataclass Usage` with fields `session_pct: float`, `session_resets_at: str`, `weekly_pct: float`, `weekly_resets_at: str`.
  - `UsageError(Exception)` with attributes `kind: str` (one of `"no_token"`, `"auth"`, `"offline"`, `"bad_response"`) and `detail: str`.
  - `parse_usage(data: dict) -> Usage` — reads `data["five_hour"]["utilization"]` / `["resets_at"]` and `data["seven_day"]["utilization"]` / `["resets_at"]`; raises `UsageError("bad_response", ...)` if any key is missing or not the expected type.
  - `time_until(resets_at: str, now: datetime) -> float` — seconds from `now` to the parsed ISO timestamp; accepts trailing `Z` or `+00:00`.
  - `format_reset_day(resets_at: str, now: datetime) -> str` — e.g. `"Sat 4 Jul"` (`"%a %-d %b"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parse.py
import unittest
from datetime import datetime, timezone
from claude_usage import parse_usage, time_until, format_reset_day, Usage, UsageError

GOOD = {
    "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00.673002+00:00"},
    "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"},
}

class TestParseUsage(unittest.TestCase):
    def test_parses_good_payload(self):
        u = parse_usage(GOOD)
        self.assertEqual(u.session_pct, 46.0)
        self.assertEqual(u.weekly_pct, 23.0)
        self.assertEqual(u.session_resets_at, "2026-06-28T10:30:00.673002+00:00")
    def test_missing_key_raises_bad_response(self):
        with self.assertRaises(UsageError) as ctx:
            parse_usage({"five_hour": {"utilization": 1.0}})  # no resets_at / seven_day
        self.assertEqual(ctx.exception.kind, "bad_response")

class TestTimeMath(unittest.TestCase):
    def test_time_until_seconds(self):
        now = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
        secs = time_until("2026-06-28T10:30:00+00:00", now)
        self.assertEqual(int(secs), 3*3600 + 12*60)
    def test_time_until_accepts_z_suffix(self):
        now = datetime(2026, 6, 28, 10, 0, 0, tzinfo=timezone.utc)
        secs = time_until("2026-06-28T10:30:00Z", now)
        self.assertEqual(int(secs), 30*60)
    def test_format_reset_day(self):
        now = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(format_reset_day("2026-07-04T12:00:00+00:00", now), "Sat 4 Jul")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parse -v`
Expected: FAIL — `ImportError: cannot import name 'parse_usage'`.

- [ ] **Step 3: Write minimal implementation**

Append to `claude_usage.py`:

```python
import json
from dataclasses import dataclass
from datetime import datetime, timezone


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parse -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add claude_usage.py tests/test_parse.py
git commit -m "feat: add Usage model, parse_usage, and reset-time math"
```

---

### Task 3: Render success output (`render_usage`)

**Files:**
- Modify: `claude_usage.py`
- Test: `tests/test_render_usage.py`

**Interfaces:**
- Consumes: `Usage`, `severity_color`, `format_duration`, `time_until`, `format_reset_day`.
- Produces:
  - `render_usage(u: Usage, now: datetime) -> str` — full SwiftBar output. Line 1 is the menu bar text + params; then a `---`; then session and weekly dropdown lines; then `---`; then `Refresh` and a `/usage` hint line.

Expected output shape (for the `GOOD` fixture at `now = 2026-06-28T07:18:00Z`):

```
46% · 3h12m | sfimage=gauge.medium color=#34c759
---
Session (5h)  46%  ·  resets in 3h 12m | color=#34c759
Weekly  23%  ·  resets Sat 4 Jul | color=#34c759
---
Refresh | refresh=true
Run /usage in Claude Code for full details | color=gray
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_usage.py
import unittest
from datetime import datetime, timezone
from claude_usage import render_usage, Usage

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
U = Usage(
    session_pct=46.0, session_resets_at="2026-06-28T10:30:00+00:00",
    weekly_pct=23.0, weekly_resets_at="2026-07-04T12:00:00+00:00",
)

class TestRenderUsage(unittest.TestCase):
    def test_menu_bar_line(self):
        first = render_usage(U, NOW).splitlines()[0]
        self.assertEqual(first, "46% · 3h12m | sfimage=gauge.medium color=#34c759")
    def test_has_session_and_weekly_lines(self):
        out = render_usage(U, NOW)
        self.assertIn("Session (5h)  46%  ·  resets in 3h 12m | color=#34c759", out)
        self.assertIn("Weekly  23%  ·  resets Sat 4 Jul | color=#34c759", out)
    def test_has_refresh_and_hint(self):
        out = render_usage(U, NOW)
        self.assertIn("Refresh | refresh=true", out)
        self.assertIn("Run /usage in Claude Code for full details", out)
    def test_high_session_is_red_in_bar(self):
        u = Usage(95.0, U.session_resets_at, 23.0, U.weekly_resets_at)
        self.assertIn("color=#ff3b30", render_usage(u, NOW).splitlines()[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_render_usage -v`
Expected: FAIL — `ImportError: cannot import name 'render_usage'`.

- [ ] **Step 3: Write minimal implementation**

Append to `claude_usage.py`:

```python
def _pct(p: float) -> str:
    return f"{int(round(p))}%"


def render_usage(u: Usage, now: datetime) -> str:
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
        "Refresh | refresh=true",
        "Run /usage in Claude Code for full details | color=gray",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_render_usage -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add claude_usage.py tests/test_render_usage.py
git commit -m "feat: render SwiftBar success output"
```

---

### Task 4: Render error states (`render_error`)

**Files:**
- Modify: `claude_usage.py`
- Test: `tests/test_render_error.py`

**Interfaces:**
- Consumes: `UsageError`.
- Produces:
  - `render_error(err: UsageError) -> str` — maps `err.kind` to a one-line bar + dropdown note. Mapping:
    - `"no_token"` → bar `􀇿 Claude ? | sfimage=exclamationmark.triangle color=#ff9500`; note `Keychain access denied — click Always Allow on the prompt.`
    - `"auth"` → bar `􀇿 auth | sfimage=exclamationmark.triangle color=#ff9500`; note `Token expired. Open Claude Code to refresh.`
    - `"offline"` → bar `— | sfimage=gauge.medium color=gray`; note `Offline — could not reach api.anthropic.com.`
    - `"bad_response"` → bar `? | sfimage=gauge.medium color=gray`; note `Unexpected response: <detail>`
    - The dropdown always also includes `Refresh | refresh=true`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_error.py
import unittest
from claude_usage import render_error, UsageError

class TestRenderError(unittest.TestCase):
    def test_no_token(self):
        out = render_error(UsageError("no_token"))
        self.assertTrue(out.splitlines()[0].startswith("􀇿 Claude ?"))
        self.assertIn("Keychain access denied", out)
        self.assertIn("Refresh | refresh=true", out)
    def test_auth(self):
        out = render_error(UsageError("auth"))
        self.assertIn("auth", out.splitlines()[0])
        self.assertIn("Open Claude Code to refresh", out)
    def test_offline(self):
        out = render_error(UsageError("offline"))
        self.assertIn("could not reach api.anthropic.com", out)
    def test_bad_response_includes_detail(self):
        out = render_error(UsageError("bad_response", "missing five_hour"))
        self.assertIn("missing five_hour", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_render_error -v`
Expected: FAIL — `ImportError: cannot import name 'render_error'`.

- [ ] **Step 3: Write minimal implementation**

Append to `claude_usage.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_render_error -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add claude_usage.py tests/test_render_error.py
git commit -m "feat: render SwiftBar error states"
```

---

### Task 5: I/O layer + orchestration (`read_token`, `fetch_usage`, `main`)

**Files:**
- Modify: `claude_usage.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: all prior functions.
- Produces:
  - `read_token() -> str` — runs `security find-generic-password -s "Claude Code-credentials" -w`, JSON-parses stdout, returns `claudeAiOauth.accessToken`. On non-zero exit, missing item, or parse failure raises `UsageError("no_token", ...)`.
  - `fetch_usage(token: str) -> dict` — GETs `USAGE_URL` with required headers, 5s timeout. Returns parsed JSON dict. Raises `UsageError("auth")` on HTTP 401; `UsageError("offline")` on `URLError`/timeout; `UsageError("bad_response")` on other HTTP errors or non-JSON.
  - `build_output(now: datetime) -> str` — orchestrates `read_token → fetch_usage → parse_usage → render_usage`; catches `UsageError` and returns `render_error(...)`. (Pure-ish: takes `now`, calls I/O internally — tested via monkeypatching the I/O functions.)
  - `main() -> None` — `print(build_output(datetime.now(timezone.utc)))`.
  - Module constants `USAGE_URL`, `KEYCHAIN_SERVICE`, `BETA_HEADER = "oauth-2025-04-20"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py
import unittest
from datetime import datetime, timezone
import claude_usage
from claude_usage import build_output, UsageError, Usage

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)

class TestBuildOutput(unittest.TestCase):
    def setUp(self):
        self._orig_token = claude_usage.read_token
        self._orig_fetch = claude_usage.fetch_usage
    def tearDown(self):
        claude_usage.read_token = self._orig_token
        claude_usage.fetch_usage = self._orig_fetch

    def test_success_path(self):
        claude_usage.read_token = lambda: "tok"
        claude_usage.fetch_usage = lambda t: {
            "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00+00:00"},
            "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"},
        }
        out = build_output(NOW)
        self.assertEqual(out.splitlines()[0], "46% · 3h12m | sfimage=gauge.medium color=#34c759")

    def test_no_token_renders_error(self):
        def boom():
            raise UsageError("no_token")
        claude_usage.read_token = boom
        claude_usage.fetch_usage = lambda t: {}
        out = build_output(NOW)
        self.assertIn("Keychain access denied", out)

    def test_auth_error_renders_error(self):
        claude_usage.read_token = lambda: "tok"
        def boom(t):
            raise UsageError("auth")
        claude_usage.fetch_usage = boom
        out = build_output(NOW)
        self.assertIn("Open Claude Code to refresh", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_main -v`
Expected: FAIL — `AttributeError`/`ImportError` for `build_output`.

- [ ] **Step 3: Write minimal implementation**

Append to `claude_usage.py`:

```python
import subprocess
import urllib.request
import urllib.error

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
BETA_HEADER = "oauth-2025-04-20"


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


def build_output(now: datetime) -> str:
    try:
        token = read_token()
        data = fetch_usage(token)
        usage = parse_usage(data)
        return render_usage(usage, now)
    except UsageError as err:
        return render_error(err)


def main() -> None:
    print(build_output(datetime.now(timezone.utc)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_main -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite + smoke-run against the live endpoint**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (all tasks' tests, ~23 total).

Run: `python3 claude_usage.py`
Expected: a SwiftBar block whose first line looks like `NN% · Xh Ym ...` matching your current `/usage` numbers (approve the Keychain prompt if it appears).

- [ ] **Step 6: Commit**

```bash
git add claude_usage.py tests/test_main.py
git commit -m "feat: add Keychain token read, usage fetch, and orchestration"
```

---

### Task 6: SwiftBar entry shim + README

**Files:**
- Create: `claude-usage.60s.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `claude_usage.main`.
- Produces: an executable SwiftBar plugin that imports the sibling module and prints output. Because the plugin folder may differ from the repo, the shim adds its own directory to `sys.path` so it can import `claude_usage` when both files are copied together.

- [ ] **Step 1: Write the shim**

```python
#!/usr/bin/env python3
# <xbar.title>Claude Usage</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.desc>Claude Code /usage session + weekly limits in the menu bar.</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_usage import main  # noqa: E402

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x claude-usage.60s.py
```

- [ ] **Step 3: Verify the shim runs**

Run: `./claude-usage.60s.py`
Expected: identical output to `python3 claude_usage.py` (first line `NN% · ...` or an error state line).

- [ ] **Step 4: Write the README**

```markdown
# Claude Usage — SwiftBar plugin

Shows Claude Code's `/usage` session (5-hour) and weekly limits in the macOS menu bar.

Menu bar: `46% · 3h12m` — current 5-hour session usage and time until it resets,
colored green / orange / red as you approach the limit. Click for the weekly
number, reset times, and a refresh button.

## Install

1. Install SwiftBar: `brew install --cask swiftbar`
2. Launch SwiftBar and pick a plugin folder when prompted.
3. Copy **both** `claude-usage.60s.py` and `claude_usage.py` into that folder:
   ```bash
   cp claude-usage.60s.py claude_usage.py "$HOME/Library/Application Support/SwiftBar/Plugins/"
   chmod +x "$HOME/Library/Application Support/SwiftBar/Plugins/claude-usage.60s.py"
   ```
   (Adjust the path to whichever plugin folder you chose.)
4. In SwiftBar, choose **Refresh All**. On first run, macOS shows a Keychain
   prompt for `Claude Code-credentials` — click **Always Allow**.

The plugin refreshes every 60 seconds (the `.60s.` in the filename). Rename to
`.30s.`/`.5m.` etc. to change the interval.

## How it works

Each run reads your Claude Code OAuth token from the macOS Keychain and calls
`GET https://api.anthropic.com/api/oauth/usage` — the same data `/usage` shows.
Nothing is stored or sent anywhere else.

## Troubleshooting

- **`Claude ?`** — Keychain access was denied; click *Always Allow* on the prompt.
- **`auth`** — the token expired and Claude Code isn't running to refresh it.
  Open Claude Code once, then Refresh.
- **`—`** — offline / couldn't reach `api.anthropic.com`.

## Development

```bash
python3 -m unittest discover -s tests -v
```
```

- [ ] **Step 5: Commit**

```bash
git add claude-usage.60s.py README.md
git commit -m "feat: add SwiftBar entry shim and README"
```

---

## Notes for the implementer

- `claude_usage.py` is built up additively across Tasks 1–5; imports (`json`, `subprocess`, `datetime`, etc.) may be added in whichever task first needs them — consolidate at the top of the file as you go; don't duplicate import lines.
- Keep the module import-safe: no network or Keychain calls at import time (only inside functions / `__main__`).
- The live smoke test (Task 5 Step 5, Task 6 Step 3) is the only step that touches the real Keychain/network and may show a one-time macOS prompt.
