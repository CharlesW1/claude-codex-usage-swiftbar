# Claude Usage — SwiftBar plugin

Shows Claude Code's `/usage` session (5-hour) and weekly limits in the macOS menu bar.

Menu bar: `46% · 3h12m` — current 5-hour session usage and time until it resets,
colored green / orange / red as you approach the limit. Click for the weekly
number, reset times, and a refresh button.

## Install

1. Install SwiftBar: `brew install --cask swiftbar`
2. Launch SwiftBar and pick a plugin folder when prompted.
3. Point SwiftBar at a folder that contains **only** these two files —
   `claude-usage.300s.py` and `claude_usage.py`. Do **not** point SwiftBar at
   this git repo: SwiftBar tries to run every file it finds (recursively), so a
   repo full of docs/tests shows up as broken `[?]` plugins.

   A clean way to keep edits live is a dedicated folder with symlinks back here:
   ```bash
   mkdir -p ~/swiftbar-plugins
   ln -sf "$PWD/claude-usage.300s.py" ~/swiftbar-plugins/claude-usage.300s.py
   ln -sf "$PWD/claude_usage.py"      ~/swiftbar-plugins/claude_usage.py
   ```
   Then in SwiftBar → Preferences set the plugin folder to `~/swiftbar-plugins`.
4. In SwiftBar, choose **Refresh All**. On first run, macOS shows a Keychain
   prompt for `Claude Code-credentials` — click **Always Allow**.

The plugin refreshes every 5 minutes (the `.300s.` in the filename). A short
interval can trip the usage endpoint's rate limit (HTTP 429); 5 minutes is a
safe default. Rename to `.120s.` / `.60s.` etc. to change it.

## How it works

Each run reads your Claude Code OAuth token from the macOS Keychain
(`Claude Code-credentials`) and calls `GET https://api.anthropic.com/api/oauth/usage`
— the same data `/usage` shows.

**Auto-refresh:** OAuth access tokens expire every few hours. If the token is
expired (or within 5 minutes of expiry), the plugin uses the stored
`refreshToken` to mint a fresh one via Claude's OAuth endpoint and writes it back
to the Keychain — exactly like Claude Code does — so the widget stays current
even if you only ever use the Claude desktop app and never open the CLI. The
rotated token is also mirrored to `~/.cache/claude-usage/creds.json` (mode 600),
and whichever copy (Keychain vs. cache) has the later expiry wins, so the
plugin's refreshes and Claude Code's own refreshes stay in sync.

The last good reading is cached at `~/.cache/claude-usage/last.json` so a
transient rate-limit or network blip shows the previous numbers (marked "last
reading") instead of an error. Nothing is sent anywhere except Anthropic's API.

## Troubleshooting

- **`Claude ?`** — Keychain access was denied; click *Always Allow* on the prompt.
- **`auth`** — auto-refresh failed: the stored `refreshToken` itself is dead
  (revoked or you logged out). Sign in again in Claude Code or the desktop app,
  then Refresh.
- **`—`** — offline / couldn't reach `api.anthropic.com`.
- **`?` + "last reading"** — the endpoint rate-limited (HTTP 429) or a network
  blip; showing the cached value. Clears on the next successful poll. If it
  persists, increase the refresh interval (rename to a larger `.NNNs.`).

## Development

```bash
python3 -m unittest discover -s tests -v
```
