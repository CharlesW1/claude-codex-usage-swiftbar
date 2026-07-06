# Claude + Codex Usage — SwiftBar plugin

Shows **Claude** and **Codex** usage side-by-side in the macOS menu bar, as two
stacked colored lines:

```
C 11% · 3h12m
Cx 50% · 1h40m
```

- **Top line (`C`)** — Claude Code's 5-hour session usage (the `/usage` number)
  and the time until that window resets.
- **Bottom line (`Cx`)** — Codex's 5-hour usage (`/status`) and its reset.
- The `C`/`Cx` labels are white. The **usage %** and **reset timer** are colored
  independently, with **white as the "best" tier**:
  - usage %: white `<50` · green `50–75` · orange `75–90` · red `≥90`
  - timer (time left): white `≤15m` · green `≤1h` · orange `≤3h` · red `>3h`
  Columns are aligned across rows. (In the dropdown, the white tier renders as
  the menu's default text color so it stays readable in light mode.)

Both providers report **% used**, so they're directly comparable (Codex's API
returns `used_percent`; the "% remaining" you may see elsewhere is just a display
choice). Click for per-window detail, weekly limits, reset times, and controls.

### Menu (dropdown) controls

- **`↻ H:MM · next check`** — clock time of the next scheduled refresh.
- **Refresh now** — refresh immediately.
- **Refresh every 1 min for 30 min** — temporarily polls every minute for 30
  minutes (handy when you're close to a limit and want a live view), then
  reverts to the normal interval. While active it shows **Stop 1-minute boost**.
  (Implemented by a small background loop that pings
  `swiftbar://refreshallplugins` each minute; SwiftBar's own interval is fixed by
  the filename.)

## Install

1. Install SwiftBar: `brew install --cask swiftbar`
2. Launch SwiftBar and pick a plugin folder when prompted.
3. Point SwiftBar at a folder that contains **only** the plugin files. Do **not**
   point it at this git repo — SwiftBar runs every file it finds (recursively),
   so docs/tests show up as broken `[?]` plugins. A clean way to keep edits live
   is a dedicated folder with symlinks back here:
   ```bash
   mkdir -p ~/swiftbar-plugins
   ln -sf "$PWD/claude-usage.300s.py"  ~/swiftbar-plugins/claude-usage.300s.py
   ln -sf "$PWD/claude_usage.py"       ~/swiftbar-plugins/claude_usage.py
   ```
   (Only these two files — the Swift renderer is embedded in `claude_usage.py`,
   so nothing else belongs in the plugin folder.)
   Then in SwiftBar → Preferences set the plugin folder to `~/swiftbar-plugins`.
4. In SwiftBar, choose **Refresh All**. On first run macOS shows a Keychain
   prompt for `Claude Code-credentials` — click **Always Allow**. The first run
   also compiles the Swift renderer (~1–2 s); later runs reuse the cached binary.

Refreshes every 5 minutes (the `.300s.` in the filename). A short interval can
trip the usage endpoints' rate limits; 5 minutes is a safe default. Rename to
`.120s.` / `.60s.` etc. to change it.

**Requirements:** macOS with `swiftc` (Xcode command line tools) for the stacked
image; Claude Code and/or Codex signed in. If Swift is unavailable the plugin
falls back to a plain-text menu bar (`C 11% · Cx 50%  ↻ 3:24`).

## How it works

Each run fetches both providers independently; one being signed out or offline
never blocks the other.

**Claude** — reads the OAuth token from the Keychain (`Claude Code-credentials`)
and calls `GET https://api.anthropic.com/api/oauth/usage`. If the access token is
expired (or within 5 min of expiry), the plugin uses the stored `refreshToken` to
mint a fresh one via Claude's OAuth endpoint and writes it back to the Keychain —
exactly like Claude Code does — so it stays fresh even if you only use the Claude
desktop app. The rotated token is mirrored to `~/.cache/claude-usage/creds.json`
(mode 600); whichever of Keychain/cache has the later expiry wins.

**Codex** — reads the ChatGPT OAuth token from `~/.codex/auth.json` and calls
`GET https://chatgpt.com/backend-api/wham/usage` (`primary_window` = 5-hour,
`secondary_window` = weekly).

The last good reading for each provider is cached under `~/.cache/claude-usage/`
so a transient rate-limit or network blip shows the previous numbers (marked
"last reading") instead of an error. Nothing is sent anywhere except Anthropic's
and OpenAI's own APIs.

## Menu bar rendering

The Swift renderer source is embedded in `claude_usage.py` (`MENUBAR_SWIFT_SRC`).
On first run it's written to `~/.cache/claude-usage/menubar_render.swift` and
compiled to a cached binary (recompiled only when the source changes). It draws
the two colored lines to a Retina PNG via AppKit and prints base64, which the
plugin emits with SwiftBar's `image=` parameter. SwiftBar scales the image to the
menu-bar height. Tune the look via the `_MENUBAR_*` constants near the top of the
renderer section (`_MENUBAR_FONT_PT`, `_MENUBAR_PAD_X/Y`, `_MENUBAR_SCALE`).

## Troubleshooting (dropdown notes)

- **`C —` / "Keychain locked"** — Keychain access denied; click *Always Allow*.
- **`C —` / "auth expired — open Claude Code"** — the stored `refreshToken` is
  dead (revoked / logged out). Sign in again in Claude Code or the desktop app.
- **`Cx —` / "signed out — run `codex login`"** — no Codex token; run `codex login`.
- **"last reading"** — a rate-limit/network blip; showing the cached value.
  Clears on the next successful poll.

## Development

```bash
python3 -m unittest discover -s tests -v
```
