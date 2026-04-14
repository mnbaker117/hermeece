# Changelog

All notable changes to Hermeece are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.4] — 2026-04-14

### Fixed

- **AthenaScout sends landed with `manual_inject_<id>` as the title.**
  `/api/v1/grabs/from-athenascout` was calling `inject_grab` without
  a `torrent_name`, so the default `f"manual_inject_{torrent_id}"`
  placeholder got written to `grabs.torrent_name` and leaked into
  the Snatch Budget widget, Recent Activity, the review queue, and
  (worst of all) the metadata enricher's fuzzy search — which
  returned garbage against the placeholder title. The `GrabItem`
  schema now accepts an optional `title` field; AthenaScout v1.1.4
  populates it from its own `books.title` row. Absent-title payloads
  from pre-v1.1.4 AthenaScout clients still work — they just keep
  the old placeholder behavior.

## [1.1.3] — 2026-04-14

Urgent hotfix for two v1.1.1 regressions plus a latent enricher
bug that's been silent since v1.0.

### Fixed

- **Dispatcher broken after any credential or settings save.**
  `_build_dispatcher` became async in v1.1.1 (so the filter's
  allow/ignore author sets can be loaded from the DB). The two
  rebuild call sites — `routers/credentials.py::_apply_credential`
  and `routers/settings.py::update_settings` — kept their
  synchronous `state.dispatcher = _build_dispatcher(settings)` form,
  which silently produces a bare coroutine object instead of a
  `DispatcherDeps`. Every attribute access afterward raises
  `AttributeError: 'coroutine' object has no attribute …`.
  User-visible symptoms: Send-to-Hermeece inject silently fails,
  `/api/v1/grabs/budget` returns 500, budget widget goes dark,
  and the IRC pipeline effectively stops processing announces as
  soon as the user touches any setting or credential. Fix: both
  sites now `await _build_dispatcher(settings, resolved_secrets)`,
  and both pull fresh secrets from the encrypted store via a new
  `_resolve_secrets()` helper rather than injecting only the one
  being updated.
- **Hardcover metadata enricher silently unauthenticated since
  Sprint 6.** `_build_default_sources` read `hardcover_api_key`
  via a fallback to `load_settings()` whenever the event loop is
  running (always true in the dispatcher build path). The Sprint 6
  encrypted-store migration blanked that field in `settings.json`,
  so Hardcover was being instantiated with `api_key=""` on every
  enrichment run and returning None for every lookup. Fix: the
  key is now plumbed through `_build_dispatcher` →
  `_build_metadata_enricher` → `MetadataEnricher.__init__` →
  `_build_default_sources` from `resolved_secrets`, same shape as
  qbit/mam credentials. Matches AthenaScout's v1.1.1 hotfix for
  the same storage-accessor-audit miss.
- **No-match sources were invisible in the log stream.** The
  enricher only emitted an INFO line on successful matches, so
  a book that hit Goodreads/Hardcover/IBDB with no result looked
  like those sources were never queried. Added a matching INFO
  line on the no-match path so the full provider chain is
  observable. (Timeouts and exceptions were already WARNING /
  ERROR; this only changes the silent-None case.)

## [1.1.2] — 2026-04-14

### Fixed

- **AthenaScout shared API key wasn't surfaced in the UI.** v1.1.1
  added the `athenascout_api_key` credential to the backend
  `SECRET_KEYS` but put the Generate button in `CredentialsPage.tsx`
  — which turns out to be orphaned dead code (never imported into
  `App.tsx`, no route, no nav entry). Credential editing actually
  happens inline inside `SettingsPage` via the `CredField` component
  filtered into mam / qbit / api buckets. Fix: extended `CredField`
  with a `canGenerate` prop (text-input + Generate button + copyable
  value pre-save) and surfaced the new key inside Settings → API
  Keys & Sink. The orphaned `CredentialsPage.tsx` is left as-is —
  harmless dead code, flagged for cleanup in v1.2.

## [1.1.1] — 2026-04-14

Post-release polish and a v1.0 latent bug fix. Bundles the three
patches that landed on `main` after the v1.1.0 tag plus a new
shared-API-key mechanism for AthenaScout's "Send to Hermeece"
integration.

### Added

- **AthenaScout shared API key.** New `athenascout_api_key` entry
  in the Credentials page. Generates a 64-char hex token (browser
  `crypto.getRandomValues`) that the user copies into AthenaScout's
  Settings. The auth middleware now accepts this token via the
  `X-API-Key` header as an alternative to the session cookie, so
  AthenaScout → Hermeece service-to-service calls don't need a
  login session. Constant-time compare to blunt timing oracles.
  Value is cached in `state.athenascout_api_key` and refreshed
  whenever the credential is set or deleted — no DB hit per request.

### Changed

- **Settings page column balance.** Grab Policy moved from the
  right column to the left (semantically pairs with Pipeline and
  Review — all "what gets grabbed / what gets approved" decisions).
  Snatch Budget moved from left to right (MAM-imposed infrastructure,
  pairs with MyAnonamouse and Download Client). Evens out column
  heights and tightens the mental grouping.

### Fixed

- **Author allow/ignore lists were never loaded into the filter at
  runtime.** Latent bug from v1.0: `_build_filter_config` hardcoded
  `allowed_authors=frozenset()` and `ignored_authors=frozenset()`,
  so every IRC announce was evaluated against empty sets regardless
  of what was in the DB. The symptom the user reported: James S A
  Corey was in the allowed list but an announce for his book went
  to tentative review. Fix: `_build_filter_config` now async, reads
  both sets from `load_normalized_sets()` at startup; every mutation
  site (authors router, auto-train, tentative-promotion cron, digest
  auto-promote) calls new `state.refresh_filter_authors()` so the
  live dispatcher sees changes immediately.
- **Epub metadata patch crashed on float `series_index`.** Python's
  XML writer raises `TypeError: argument of type 'float' is not
  iterable` in `_escape_attrib` when a non-string attribute value
  is passed. The enricher and the AthenaScout handoff both produce
  floats for `series_index`. Fix: `_set_meta` now coerces `content`
  to `str` up front and guards against `None`; handles both the
  "existing meta" update path and the "create new meta" path.
- **Log viewer missed overnight history.** Ring buffer was 5000
  records (~4–6 hours during active IRC periods) and the frontend
  requested 500 lines by default. Bumped to 20000 records / 2000
  lines so a user checking the log in the morning sees the full
  overnight activity window.
- **UI polish.** Dashboard navbar widened from `NARROW_WIDTH` to
  `WIDE_WIDTH` so it matches the content pages. Stat-tile grid
  tightened from `minmax(170px, 1fr)` to `minmax(150px, 1fr)` to
  reduce asymmetry when the tile count doesn't evenly divide the
  row width.

## [1.1.0] — 2026-04-14

Quality-of-life release. Thirteen items split across two sprints —
most are direct cross-ports from [AthenaScout](https://github.com/mnbaker117/AthenaScout)
v1.1.0's playbook plus Hermeece-specific integration + operator
tooling.

### Added

#### AthenaScout integration

- **Metadata handoff (plan item 1.2)** — `/from-athenascout` grab
  submissions now accept an optional `metadata` dict carrying
  AthenaScout's already-scanned book metadata (title, author,
  series, ISBN, cover URL, description, etc.). The blob is
  persisted on the grab row (new `grabs.source_metadata` column)
  and the pipeline's `_prepare_book` uses it to skip Hermeece's
  own enricher chain — saves ~6 outbound scraper requests per
  book and guarantees metadata consistency with AS.

#### Operator tooling

- **Database browser (plan item 4.3)** — new **🗄️ Database** page
  for inspecting Hermeece's SQLite without SSH-ing into the
  container. Left pane: whitelisted table list with row counts.
  Right pane: paginated row grid (50/page) with case-insensitive
  text-column search. **Read-only for v1.1**; cell editing, inserts,
  and deletes arrive in v1.2.
- **Build SHA display (plan item 4.2)** — Settings page footer now
  shows the git commit the running container was built from.
  Dockerfile bakes `GIT_SHA` via build-arg; `/api/version` exposes
  it; the Settings page reads it at mount time. Makes "which
  version am I actually running?" unambiguous after a pull.
- **Log viewer tabs (plan item 2.3)** — the Logs page gains two
  new tabs: **Application** (everything NOT under `hermeece.mam.irc`)
  and **IRC** (only `hermeece.mam.irc.*`). Drives a new `category`
  query param on `/api/v1/logs` that filters by logger-name prefix.
- **Log viewer filter input (plan item 2.2)** — case-insensitive
  substring filter narrows the visible log rows in real time.
  Matches against both logger name and message body.

#### Notifications

- **Per-event ntfy gating (plan item 4.5)** — three ntfy call
  sites in the pipeline were firing regardless of the
  `per_event_notifications` setting. Now all success-path
  notifications (`notify_pipeline_complete`, `notify_download_complete`)
  honor the setting uniformly; `notify_error` stays always-on
  because errors aren't in the daily digest and shouldn't be
  suppressed.
- **Weekly Calibre audit (plan item 5.1)** — new
  `weekly_calibre_audit` APScheduler job fires Sundays at 22:30
  local (one hour before the weekly digest). Shells out to
  `calibredb list --for-machine`, compares to
  `calibre_additions` over the 7-day window, and flags any book
  that entered Calibre outside Hermeece's knowledge (manual
  add, other tool, etc.). Silent when nothing's off.

#### Resilience

- **Global per-book enricher budget (plan item 6.1)** — on top of
  the existing per-source `asyncio.wait_for` timeouts, the
  enricher now enforces a wall-clock budget across an entire
  `enrich()` call (default 60s). Per-source timeouts are clamped
  to the remaining budget so a slow late-stage source can't
  single-handedly blow the cap. Source-log entries get
  `status: "budget_exceeded"` for anything skipped.

### Changed

- **Log noise reduction (plan item 2.1)** — new `_QuietAccessFilter`
  installed on `uvicorn.access` suppresses `/api/health` records
  (~2× per minute from the Docker healthcheck). Extensible list
  for future high-frequency paths.
- **Page Visibility API for polling (plan item 4.1)** — six pages
  (Dashboard, Review, Tentative, Migration, Logs, Mam) previously
  ran their `setInterval` pollers at full speed even when their
  tab was backgrounded. Extracted a reusable
  `useVisibleInterval()` hook that pauses the interval on
  `document.hidden` and fires a catch-up tick on visibility
  return. Saves idle network traffic + battery on parked tabs.
- **Per-page content widths (plan item 4.4)** — the hardcoded
  1400px main-content cap is now a per-page lookup: data-heavy
  pages get 1400px, form/config pages get 1120px, the navbar
  stays 1120px regardless. Mirrors AthenaScout's Sprint 7.3
  convention.
- **Amazon scraper hardening (plan item 1.3)** — backported from
  AthenaScout commit `423450b`:
  - Junk-listing pre-filter (regex for third-party seller
    titles, bracketed format suffixes, "By AUTHOR - Title" sham
    listings) runs at the search-result scoring step so garbage
    never reaches the detail-page fetch.
  - Audiobook detection in `_parse_detail_page` — checks RPI
    card text + `#productSubtitle` against keywords
    (`audible`, `audiobook`, `audio cd`, `listening length`).
    Audiobook hits return None so the enricher loop falls
    through to the next source.

### Fixed

- **MAM cookie rotation log leaked token prefix** — the INFO log
  on rotation included `{old[:8]}... → {new[:8]}...`. An 8-char
  prefix has enough entropy to correlate sessions across log
  aggregators, and `docker logs` readership is wider than
  "people authorized to see the MAM session." Dropped the
  snippets entirely; fact-of-rotation is the only diagnostic
  that matters. (Same lesson as AthenaScout v1.1.1 commit
  `23e01fd`.)

### Internals (no user-visible change)

- New `frontend/src/hooks/useVisibleInterval.ts` — reusable
  visibility-aware interval hook.
- New `app/routers/db_editor.py` — read-only SQLite browser.
- New `grabs.source_metadata` column (MIGRATIONS list's first
  entry).
- `EnrichmentConfig` gains `per_book_budget: float = 60.0`.
- `DigestContext` gains `calibre_library_path: str = ""` for the
  weekly audit.
- `app/storage/grabs.py` adds a thin `get_source_metadata(db,
  grab_id)` helper so the one consumer (pipeline._prepare_book)
  doesn't drag the column through the GrabRow dataclass.

---

## [1.0.0] — 2026-04-09

Initial public release. See release notes at
<https://github.com/mnbaker117/hermeece/releases/tag/v1.0.0>.

[1.1.0]: https://github.com/mnbaker117/hermeece/releases/tag/v1.1.0
[1.0.0]: https://github.com/mnbaker117/hermeece/releases/tag/v1.0.0
