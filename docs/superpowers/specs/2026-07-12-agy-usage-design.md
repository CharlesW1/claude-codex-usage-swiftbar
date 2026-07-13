# agy usage tracking — design

**Date:** 2026-07-12
**Status:** Approved (Approach A); codex sol 5.6 review incorporated; pending implementation
**Component:** `claude_usage.py` (SwiftBar plugin)

## Goal

Add Antigravity (`agy`) as a third usage provider alongside Claude and Codex.
agy exposes **two independent quotas** — Gemini models and external models
(Claude/GPT accessed through Antigravity) — both of which must be surfaced.

## Approach (chosen)

**Approach A — agy as a third first-class provider.** Add agy symmetrically to
the existing Claude/Codex machinery: its own creds reader, fetcher, defensive
parser, cache, and `(usage, stale, note, help)` degradation tuple. The existing
Claude/Codex fetch paths are left untouched except for menu-bar tag strings.

The only genuinely new mechanics are (1) a 2×2 menu-bar tile grid and (2)
turning the single-choice "Show" toggle into per-provider checkboxes. We are
**not** refactoring Claude/Codex into a shared abstraction (rejected Approach B)
and **not** shipping a reduced-robustness version (rejected Approach C).

## Data source

Discovered by inspecting the `agy` binary and CLI logs:

- **Endpoint:** `POST https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`
  (Google Code Assist backend; the same call agy's internal `quotaRefreshLoop`
  in `quota_manager.go` makes on a timer).
- **Auth:** Bearer token from `~/.gemini/antigravity-cli/antigravity-oauth-token`,
  a JSON file shaped:
  ```json
  { "token": { "access_token": "...", "token_type": "...",
               "refresh_token": "...", "expiry": "..." },
    "auth_method": "consumer" }
  ```
- **Response:** `RetrieveUserQuotaResponse` carrying `BucketInfo` records. Each
  bucket has a `quotaId`, a `tokenType`, a `resetTime`, and a remaining value as
  either `remainingFraction` (0.0–1.0) or `remainingAmount`. A grouped variant
  (`QuotaSummaryGroup` → `QuotaSummaryBucket`) may also appear.

> **Unverified detail — must confirm during implementation.** The live endpoint
> could not be probed during design (the sandbox blocked firing agy's token at a
> Google endpoint). The exact JSON field casing (`remainingFraction` vs
> `remaining_fraction`, wrapper keys) and the precise `quotaId`/`tokenType`
> markers that distinguish the Gemini bucket from the external bucket are
> inferred from proto symbols, not a captured payload. The **first**
> implementation step is to capture one real response from within the plugin
> process (allowed — the plugin owns the call) and pin `parse_agy` to reality.
> `parse_agy` must be written defensively so an unexpected shape degrades to a
> note, never a crash.
>
> **Payload capture data-handling rule.** When capturing the reference response,
> save it only as a sanitized fixture under `tests/fixtures/`: never log or
> commit the bearer token, and strip account-identifying fields (emails, user
> ids, project ids, raw tokens) before writing. The fixture is for pinning field
> names/markers only.

## Token refresh — explicit non-goal for v1

Claude and Codex refresh their OAuth tokens when expired. agy's token is a
Google OAuth token whose refresh needs a client_id/secret we do not have and
must not guess. **v1 does not refresh agy's token.** Rule: use `access_token`
while valid; if the token is expired (or the API returns 401), render the
AgG/AgX tiles as `—` and show a dropdown note "agy token expired — open
Antigravity to refresh". This is the `auth` degradation kind for agy.

## Components

### 1. `AgyUsage` dataclass

```python
@dataclass
class AgyUsage:
    gemini_pct: Optional[float]        # % used; None = bucket absent/unknown
    gemini_resets_at: Optional[str]
    external_pct: Optional[float]
    external_resets_at: Optional[str]
    gemini_window_s: Optional[int] = None    # for timer-color scaling, if known
    external_window_s: Optional[int] = None
```

Mirrors `CodexUsage`'s present-or-absent modelling: a `None` pct means that
bucket had no limit or could not be identified, and it renders as `—` /
"no active limit" rather than `0%`.

### 2. Creds + fetch + parse

- `AGY_TOKEN_PATH = ~/.gemini/antigravity-cli/antigravity-oauth-token`
- `read_agy_creds() -> dict` — reads the token file; returns
  `{"access_token": ..., "expiry_ms": <int|None>}`. Raises
  `UsageError("no_token", ...)` when the file/token is missing or malformed
  (missing `token.access_token`). Never raises on a bad `expiry` — see policy.
- **Expiry unit policy** (`_parse_agy_expiry(raw) -> Optional[int]` returning
  epoch **milliseconds**):
  - RFC3339 / ISO-8601 string → parse via `_parse_iso`; naive datetimes are
    treated as UTC. Result → ms.
  - Numeric (int/float or all-digit string): disambiguate by magnitude —
    `>= 1e12` is already milliseconds; `1e9 <= v < 1e12` is seconds → `*1000`;
    anything else (too small / negative / non-finite) → `None`.
  - Any parse failure or out-of-range value → `None` (fall back to relying on a
    401 rather than a proactive expiry check). Never mark a token expired on an
    ambiguous value.
- `fetch_agy(token) -> dict` — `POST` with an empty `{}` body and
  `Authorization: Bearer <token>`, `Content-Type: application/json`. Same error
  mapping as `fetch_codex`: 401 → `auth`, other HTTP → `bad_response`,
  URLError/timeout → `offline`, non-JSON → `bad_response`. Timeout 5s.
- `classify_agy_bucket(bucket) -> "gemini" | "external" | None` — inspect
  `quotaId`/`tokenType` (case-insensitive) for known markers: Gemini markers
  contain `GEMINI`; external markers contain `CLAUDE`/`GPT`/`OPENAI`/`EXTERNAL`/
  `BYOK`. **No positional fallback** — a bucket matching no marker returns
  `None` (unclassified). Positional guessing is rejected: mislabeling a quota is
  worse than showing it as unknown. Pin the real markers from the captured
  fixture; if the fixture proves markers are stable, this classifier is exact.
- `parse_agy(data) -> AgyUsage` — locate the bucket list (handle both the flat
  `BucketInfo` list and the grouped `QuotaSummaryGroup`→`QuotaSummaryBucket`
  shape; if both are present, prefer the flat list and ignore the grouped
  duplicate). For each bucket, `classify_agy_bucket` it and derive:
  - **used-pct**: if `remainingFraction` r (0..1) present → `round((1 - r) * 100)`,
    clamped to `[0, 100]`. If only `remainingAmount` is present **without** a
    known denominator/limit, the pct is **unknown** (`None`) — do not invent a
    percentage. If a limit *is* present, `round((1 - amount/limit) * 100)`.
  - **resets_at**: `resetTime` → ISO (via `_epoch_to_iso` for epoch or passthrough
    for ISO string).
  - **Aggregation** when multiple buckets classify to the same category: keep the
    **most-depleted** one (highest used-pct; a `None`-pct bucket loses to any
    known pct). This yields one Gemini value and one external value.
  - Unclassified buckets are dropped.

  Fully defensive: non-dict / non-list / missing fields yield `None` for that
  category, never raise. If the payload is structurally unusable (no bucket list
  found at all, or a non-dict top level), raise `UsageError("bad_response", ...)`
  so the transient-cache path can show the last good reading. Note the
  distinction: a well-formed payload where a category is simply absent is **not**
  an error (that category renders "no active limit"); only an unparseable
  structure raises.

### 3. Cache

- `AGY_CACHE_PATH = ~/.cache/claude-usage/last_agy.json`
- `agy_cache_save(a)` / `agy_cache_load()` — round-trip all six fields. Both
  functions **never raise** (wrap I/O and JSON in try/except, mirroring
  `codex_cache_*`); on any error `save` is a no-op and `load` returns `None`.
- **Minimum cache validity**: `agy_cache_load` returns an `AgyUsage` only if the
  loaded object has at least one non-`None` pct (`gemini_pct` or `external_pct`).
  An all-unknown / empty (`{}`) cache is rejected (`None`), so the stale "last
  reading" path never shows a reading that contains no numbers.

### 4. Degradation — `_get_agy(now_ms)`

Returns `(AgyUsage|None, stale, note, help)`, identical contract to
`_get_claude`/`_get_codex`:

```python
def _get_agy(now_ms):
    try:
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
    except Exception:  # last-resort provider-scoped backstop
        return (None, False, _AGY_NOTES["bad_response"], _AGY_HELP["bad_response"])
```

**Exception-boundary contract** (makes "never crash" literal):
- `read_agy_creds`, `fetch_agy`, `parse_agy` convert **all** external-input
  failures (`OSError`, `ValueError`, `KeyError`, `TypeError`, `AttributeError`,
  HTTP/URL/timeout, numeric/overflow, bad dates) into `UsageError`.
- `agy_cache_save`/`agy_cache_load` never raise.
- The trailing `except Exception` in `_get_agy` is the backstop guaranteeing
  agy can never propagate an exception into `build_output`, so a surprise in the
  unverified payload can never blank the Claude/Codex sections. The two existing
  providers keep their current behavior; this backstop is agy-local.

Note/help maps:

```python
_AGY_NOTES = {
    "no_token": "not signed in — open Antigravity",
    "auth": "token expired — open Antigravity",
    "offline": "offline",
    "bad_response": "rate-limited or error",
}
_AGY_HELP = {
    "no_token": "agy fix: sign in to Antigravity, then Check now",
    "auth": "agy fix: open Antigravity to refresh its login, then Check now",
    "offline": "agy fix: check your connection, then Check now",
    "bad_response": "agy: temporary API error — retries at next check",
}
```

## Menu-bar tags and 2×2 grid

### Tag rename

`C` → `Cld`, `Cx` → `Cdx`. agy tiles are `AgG` (Gemini) and `AgX` (external).
All four tags are three characters, so columns align cleanly.

### Tile model

`BarRow` today carries only `label/value/value_color/timer/timer_color`
([claude_usage.py:276]) — it cannot express which provider a tile belongs to or
which grid cell it occupies. Introduce an explicit tile type rather than
inferring provider/column from the label string:

```python
@dataclass
class MenuTile:
    row: "BarRow"        # existing 5-field visual payload, reused as-is
    provider: str        # "claude" | "codex" | "agy"  (for filtering)
    logical_col: int     # 0 = Claude/Codex column, 1 = agy column
    logical_row: int     # 0 = top, 1 = bottom
```

`menubar_tiles(claude, codex, agy, now) -> List[MenuTile]` builds every tile a
present provider contributes, at fixed logical coordinates:

| Provider | Tile | logical_col | logical_row |
|----------|------|-------------|-------------|
| Claude   | `Cld` (5-hour session)          | 0 | 0 |
| Codex    | `Cdx` (tightest active window)  | 0 | 1 |
| agy      | `AgG` (Gemini)                  | 1 | 0 |
| agy      | `AgX` (external)                | 1 | 1 |

A missing/`None` provider contributes no tile. Each tile's `BarRow` is built by
the existing `_bar_row` helper, so severity/timer coloring is unchanged; agy
timer color uses `gemini_window_s`/`external_window_s` when known, else the
default window. An agy bucket with `None` pct still yields a tile showing `—`
(gray), matching how a missing Codex window renders.

### Filtering, then draw-layout

`filter_menubar_tiles(tiles, enabled) -> List[MenuTile]` keeps tiles whose
`provider` is in the enabled set (logical coordinates unchanged).

**Draw layout is derived from the *surviving* logical coordinates**, so empty
columns/rows collapse deterministically:
- **Draw columns** = the sorted distinct `logical_col` values still present,
  renumbered to 0..N-1. So an **agy-only** config (logical_col 1 only) draws in
  the leftmost physical column — no blank left gutter.
- **Draw row** within a physical column = tiles sorted by `logical_row`, packed
  top-down (a Codex-only column draws `Cdx` at the top row, not floating in
  row 1).
- Column widths are computed **per physical column** (label/value/timer aligned
  within each column independently); columns are laid left-to-right with a fixed
  inter-column gap. Vertical: each physical column packs its tiles from the top;
  columns need not have equal tile counts.

All eight enabled subsets resolve unambiguously. Notable cases:
- `{claude,codex}` → one column, `Cld`/`Cdx` — pixel-similar to today.
- `{agy}` → one column, `AgG`/`AgX`.
- `{claude,agy}` → two columns: left `Cld` (top), right `AgG`/`AgX`. Left column
  has one tile, right has two; no lower-left cell is drawn.
- `{codex,agy}` → two columns: left `Cdx` (top), right `AgG`/`AgX`.
- `{}` (empty) → no tiles; see empty-set placeholder below.

### Renderer change (Swift)

Extend each tile's CLI arg group from **5 fields to 6** by appending the
**physical column index**. The renderer:
1. Updates the arg-count guard from `(a.count-1) % 5 == 0` to `% 6 == 0`.
2. Groups tiles by the 6th field (physical column), preserving arg order for
   vertical packing.
3. Computes per-column max label/value/timer widths (independent alignment).
4. Lays columns left-to-right with a fixed gap; within a column, packs tiles
   top-down using the existing per-row baseline math.

Python emits tiles ordered by (physical column, draw row). Row order within a
column follows arg order.

Font scaling: today one visible row uses the larger `_MENUBAR_FONT_PT_SINGLE`
([claude_usage.py:592]). Generalize to **total surviving tile count**: exactly
one tile → large font; otherwise standard 9pt.

## Show toggle → per-provider checkboxes

### State model

Replace the single `display_mode` string with a **set of enabled providers**,
a subset of `{"claude", "codex", "agy"}`.

- Storage (`DISPLAY_MODE_PATH`): comma-separated sorted slugs, e.g.
  `agy,claude,codex`.
- `PROVIDERS = ("claude", "codex", "agy")` is the sole allowed set.
- **Normalization** (`enabled_load`): split on commas, strip whitespace on each
  token, lowercase, **intersect with `PROVIDERS`**, dedupe. Arbitrary/garbage
  slugs are dropped. This guarantees stored state can never hold an invalid
  provider regardless of how it got written.
- **Migration** of the old single-value file (values `both`/`claude`/`codex`):
  `both` → `{claude, codex, agy}` (agy defaults on when newly introduced),
  `claude` → `{claude}`, `codex` → `{codex}`. A file that is missing, empty, or
  normalizes to the empty set on first read → default `{claude, codex, agy}`.
  (Empty is only reachable by an explicit user toggle-off, handled below — not
  by a fresh/absent file.)
- `enabled_load() -> set[str]`, `enabled_save(set)` (writes sorted, normalized),
  `enabled_toggle(provider)`: if `provider not in PROVIDERS`, **no-op**;
  otherwise flip membership and persist. An explicit toggle **may** produce the
  empty set (all off) — that is allowed and handled by the placeholder.

### Filtering

`filter_menubar_tiles(tiles, enabled)` keeps tiles whose provider is enabled.
Empty set is allowed; the user can re-enable from the dropdown (which always
renders the toggles regardless of enabled state).

**Empty-set placeholder — wired into the output contract, not the renderer.**
Today `render_menubar_image([])` returns `None`, and `assemble_output` then
joins zero fallback rows into an **empty first line** — which would make the
SwiftBar item invisible and the dropdown hard to reach. Fix in
`assemble_output`: when there are no tiles (empty enabled set), the first line is
a fixed non-empty label `usage | color=#8e8e93` (SwiftBar renders it as gray
text; clicking still opens the dropdown). This is independent of Swift
availability. A test asserts the first output line is non-empty and the dropdown
follows when the enabled set is empty.

### Dropdown "Show" section

Three independent checkbox rows, each toggling one provider:

```
Show
--Claude ✓ | bash=... param2="toggle" param3="claude" ... refresh=true
--Codex  ✓ | bash=... param2="toggle" param3="codex"  ... refresh=true
--agy    ✓ | bash=... param2="toggle" param3="agy"    ... refresh=true
```

`✓` shown when enabled. A new CLI verb `toggle` calls `enabled_toggle(param3)`
then refreshes.

**Keep the `show` verb as a migration adapter for one release.** A menu action
rendered before this update (already sitting in a user's menu bar) can still fire
`show <mode>`. Map it forward: `show both` → enable all three; `show claude` →
`{claude}`; `show codex` → `{codex}`; then refresh. This prevents a stale menu
click from writing an unknown value. Tests target `toggle` for new behavior and
one test covers the `show` adapter.

## Dropdown provider sections

Add an agy section, gated on `"agy" in enabled`, matching the Claude/Codex block
style:

```
agy | color=#8e8e93
Gemini    <pct>  ·  resets in <countdown> | color=#8e8e93
External  <pct>  ·  resets in <countdown> | color=#8e8e93
```

Exact copy (so tests don't guess):
- A single bucket with `None` pct → its own line reads `Gemini  no active limit`
  / `External  no active limit` (per-bucket, singular).
- **Both** buckets `None` (agy present but no limits at all) → one line
  `no active limits | color=#8e8e93` (plural, whole-provider), matching the
  Codex convention.
- When agy is entirely unavailable (`None`), the section shows its note (e.g.
  `token expired — open Antigravity`) instead of bucket lines.
- The agy help line renders alongside the Claude/Codex help lines under the
  details line. The "…for details" footer gains an agy hint when agy is enabled.

## Wiring — exact signatures

`render_dropdown` and `assemble_output` already have long positional parameter
lists. To avoid swapped-argument bugs, **all new agy parameters are
keyword-only** (added after the existing params, each with a default so current
callers/tests keep working). Concretely:

```python
def render_dropdown(claude, codex, now, interval_s,
                    stale_claude=False, stale_codex=False,
                    claude_note=None, codex_note=None,
                    boost_remaining=None, cli=None,
                    display_mode="both",           # retained for back-compat; see note
                    detail_color=GRAY,
                    claude_help=None, codex_help=None,
                    *,                              # agy params keyword-only
                    agy=None, stale_agy=False, agy_note=None, agy_help=None,
                    enabled=None):                  # set[str]; None -> derive from display_mode
    ...

def assemble_output(rows, claude, codex, now, interval_s, stale_c, stale_x,
                    note_c, note_x, image_b64,
                    boost_remaining=None, cli=None, display_mode="both",
                    detail_color=GRAY, help_c=None, help_x=None,
                    *,                              # agy + enabled keyword-only
                    agy=None, stale_a=False, note_a=None, help_a=None,
                    enabled=None, tiles=None):
    ...
```

- The dropdown's per-provider sections are gated on `enabled` (the new set); the
  legacy `display_mode` string is retained only so existing tests that pass it
  still resolve (map `both`→all, `claude`/`codex`→singletons when `enabled` is
  `None`). New code always passes `enabled`.
- `build_output` computes `agy, stale_a, note_a, help_a = _get_agy(now_ms)` and
  `enabled = enabled_load()`, builds tiles via `menubar_tiles(...)`, filters via
  `filter_menubar_tiles(tiles, enabled)`, renders the image, and passes agy +
  `enabled` as keyword args to `assemble_output`.

## Error handling

Per-provider, unchanged in spirit: every external-input failure (missing file,
malformed JSON, bad token, HTTP error, timeout, unparseable payload) becomes a
provider-scoped `UsageError`; transient kinds (`offline`, `bad_response`) fall
back to the cached reading marked stale; `no_token`/`auth` surface a note + help
and render `—`. agy must **never** crash the plugin or degrade the Claude/Codex
sections.

## Testing

Mirror the existing test structure; all new logic is pure and unit-testable.

- **`tests/test_agy.py`** (new):
  - `classify_agy_bucket`: gemini marker, each external marker, unclassified
    (no marker) → `None` (assert **no** positional guessing).
  - `parse_agy`: both buckets present; one category absent (renders later as
    "no active limit", not error); malformed (non-dict/non-list/missing fields)
    → category `None`, no raise; `remainingFraction`→used-pct conversion +
    clamp; `remainingAmount` without denominator → `None` pct; multiple buckets
    same category → most-depleted wins; grouped-vs-flat payload (prefer flat);
    structurally unusable payload → `bad_response`.
  - `read_agy_creds`: present; missing file; malformed JSON; missing
    access_token → `no_token`. Shape only — never assert token values.
  - `_parse_agy_expiry`: RFC3339 string; naive datetime as UTC; epoch seconds
    (10-digit) → ms; epoch ms (13-digit) passthrough; too-small/negative/
    non-numeric → `None`.
  - `fetch_agy`: request method (POST), empty-`{}` body, Authorization/
    Content-Type headers; 401 → `auth`; other HTTP → `bad_response`; timeout/
    URLError → `offline`; non-JSON → `bad_response` (mock `urlopen`, no network).
  - `_get_agy`: happy path; expired token → `auth` note with **no** network
    call; `auth`/`no_token` never use stale cache; transient (`offline`/
    `bad_response`) → cache fallback marked stale; a raised non-`UsageError`
    (e.g. surprise `KeyError` in a monkeypatched `parse_agy`) is caught by the
    backstop and returns a provider-scoped note, not a crash.
- **`tests/test_menubar.py`** (extend): `menubar_tiles` logical coordinates for
  each provider; `filter_menubar_tiles` by enabled set incl. empty; draw-layout
  collapse for `{agy}`, `{claude,agy}`, `{codex,agy}` (leftmost physical column,
  top-packed rows); single-tile large font; agy timer color uses agy window;
  agy `None` pct → `—` tile.
- **`tests/test_menubar_render.py`** (new or extend): renderer arg serialization
  emits 6-field groups with the physical column index; grouping/guard accepts
  `% 6`; text fallback (`_bar_row_text`) across multi-tile layouts.
- **`tests/test_render_combined.py`** (extend): agy section present when
  enabled/absent when not; per-provider checkbox rows with `✓` reflecting the
  enabled set; agy note + help placement under the details line; per-bucket
  "no active limit" vs whole-provider "no active limits"; details footer agy
  hint.
- **`tests/test_display_mode.py`** (new): old-value migration
  (`both`/`claude`/`codex` → set); fresh/empty file → all three; whitespace/
  duplicate/garbage slugs normalized away; `enabled_toggle` flips + persists;
  `enabled_toggle("garbage")` no-op; toggling to empty set persists empty;
  `show` adapter maps old values forward.
- **`tests/test_cache.py`** (extend): agy cache round-trip; missing-key old
  cache tolerance; **all-unknown / `{}` cache rejected** (returns `None`);
  save/load never raise on I/O error (monkeypatch to force `OSError`).
- **`tests/test_main.py`** (extend): agy signed-out note in combined output;
  empty enabled set → non-empty first line (`usage`) + dropdown present;
  `toggle` verb flips membership; all-three combined happy path renders 2×2.

## Out of scope (v1)

- Refreshing agy's OAuth token.
- Reading the IDE's separate token store (`~/.gemini/antigravity/`); the CLI
  token is sufficient and is what the user's agy usage runs against.
- Any `agy` CLI subcommand integration (none exposes quota).
- Boost/next-check/Swift-renderer-availability behavior — unchanged.

## Risks

1. **Unverified payload shape** (biggest). Field casing and bucket markers are
   inferred. Mitigation: capture a real response first; defensive `parse_agy`.
2. **Token expiry with no refresh.** If agy's token expires often, tiles sit at
   `—` until the user opens Antigravity. Acceptable for v1; revisit refresh if
   it proves annoying.
3. **Renderer grid regressions.** The Claude/Codex-only look must stay
   pixel-similar to today (left column only). Covered by keeping column 0 =
   Claude/Codex and the single-tile font rule.
