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
`.30s.` / `.5m.` etc. to change the interval.

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
