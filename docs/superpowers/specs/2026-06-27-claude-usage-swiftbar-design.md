# Claude Usage SwiftBar Plugin — Design

**Date:** 2026-06-27
**Status:** Approved (pending spec review)

## Goal

A lightweight macOS menu bar item that displays Claude Code subscription usage —
the same session and weekly limit data shown by Claude Code's `/usage` screen —
rendered via SwiftBar, similar to how the Stats app shows system metrics.

## Data source

Verified working: Claude Code authenticates with an OAuth token stored in the
macOS Keychain (generic password, service `Claude Code-credentials`). That token
can call:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken>
anthropic-beta: oauth-2025-04-20
```

Response (relevant fields):

```json
{
  "five_hour": { "utilization": 46.0, "resets_at": "2026-06-28T10:30:00+00:00" },
  "seven_day": { "utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00" }
}
```

`five_hour` = the rolling 5-hour session window. `seven_day` = the weekly window.
`utilization` is a percentage (0–100). This matches what `/usage` displays.

## Architecture

A single stateless executable script:

```
claude-usage.60s.py
```

- The `.60s.` segment tells SwiftBar to run the script every 60 seconds and render
  its stdout in the menu bar.
- No daemon, no persisted state. Each run: read token → fetch → print → exit.
- Python 3 (already present on the system; good JSON/HTTP handling via stdlib
  `urllib` — no third-party Python deps).

### Per-run flow

1. Read the Keychain item:
   `security find-generic-password -s "Claude Code-credentials" -w`,
   JSON-parse, extract `claudeAiOauth.accessToken`.
   Re-reading each run keeps the token fresh as Claude Code rotates it.
2. `GET` the usage endpoint with a 5-second timeout.
3. Parse `five_hour.utilization` + `resets_at`, `seven_day.utilization` + `resets_at`.
4. Print SwiftBar-formatted output.

## Display

### Menu bar (compact)

`<gauge-icon> <session%> · <time-left-in-5h-window>` — e.g. `􀙦 46% · 3h12m`.

Color by session severity (SwiftBar `color=` param):
- green: < 70%
- yellow: 70–90%
- red: > 90%

Icon: an SF Symbol gauge (e.g. `gauge.medium`) via SwiftBar's `sfimage=`.

### Dropdown

```
Session (5h)   46%   · resets in 3h 12m
Weekly         23%   · resets Sat 4 Jul
---
Refresh                       (SwiftBar refresh=true)
Open /usage in Claude Code    (note / how-to line)
```

## Error handling

Every failure path prints exactly one menu-bar line and a dropdown note — the
script never crashes or emits a stack trace into the bar.

| Condition | Menu bar | Dropdown note |
|---|---|---|
| No token / Keychain access denied | `􀇿 Claude ?` | "Keychain access denied — click Always Allow on the prompt." |
| HTTP 401 (token expired, Claude Code not running) | `􀇿 auth` | "Token expired. Open Claude Code to refresh." |
| Network error / timeout | `􀙦 —` | "Offline — could not reach api.anthropic.com." |
| Unexpected response shape | `􀙦 ?` | Short error detail. |

**Explicit tradeoff:** we do NOT implement OAuth refresh ourselves. If the token
is expired and Claude Code isn't running to refresh it, we show the `auth` state
and ask the user to open Claude Code. This keeps the plugin lightweight and avoids
embedding refresh-token logic.

## Install (covered by README)

1. `brew install --cask swiftbar`
2. Launch SwiftBar, choose a plugin folder.
3. Copy `claude-usage.60s.py` into that folder, `chmod +x` it.
4. On first run, approve the macOS Keychain prompt with **Always Allow**.

## Out of scope (YAGNI)

- Historical graphs / trends.
- Token counts or dollar cost from local `~/.claude/projects/*.jsonl` logs.
- Desktop notifications on threshold crossing.
- User-configurable thresholds (hardcode 70 / 90; trivial to edit later).
- OAuth token refresh.

## Testing

- Unit-testable pure functions: response-JSON → display model; severity from
  percent; "time left" formatting from `resets_at`. Run with fixture JSON
  (success, 401, malformed, missing fields).
- Manual: place in SwiftBar plugin folder, confirm bar renders and matches the
  numbers shown by `/usage` in Claude Code.
