# Hermeece

**Hermes for the meece.** A single-purpose courier for [MyAnonamouse](https://www.myanonamouse.net/) that watches the IRC announce channel, filters new ebook and audiobook releases against a personal author allow/ignore list, grabs the matching torrents into qBittorrent, ingests completed downloads into a Calibre library after metadata review, and tells you about it on ntfy.

Sibling project to [AthenaScout](https://github.com/mnbaker117/AthenaScout). AthenaScout finds the books you're missing; Hermeece goes and gets them.

## Status

🚧 **Pre-alpha — Phase 1 in progress.** This README will grow as features land.

## Why

Replaces a multi-app workflow (Autobrr → qBittorrent → manual copy → manual Calibre add → manual metadata) with a single service that owns the whole pipeline end-to-end and exposes it through one web UI.

Hermeece is **deliberately MAM-only**. No tracker abstractions, no plugin system. Every line of code knows it's talking to MyAnonamouse, and that's by design — it lets the codebase stay small and the user experience stay opinionated.

## What it does (planned)

- Connects to `irc.myanonamouse.net` and parses MouseBot announces in real time
- Filters announces against a configurable author allowlist + ignore list
- Tracks an "unknown authors" weekly review queue
- Manages your `mam_id` session cookie: validates, warns before expiry, retries failed grabs after a fresh cookie is uploaded
- Respects MAM's snatch budget by tracking torrent seedtime via qBittorrent's API and queueing grabs locally when the budget is full
- Watches qBittorrent for completed downloads (whether Hermeece grabbed them or you did manually)
- Stages completed books for metadata review, fetches candidates via Calibre's `fetch-ebook-metadata`
- Adds approved books to your Calibre library via `calibredb`
- Treats Calibre as the source of truth for the author allowlist via a weekly audit
- Sends a daily ntfy digest of newly added books
- Provides a React + Vite web UI for review queues, author management, snatch budget, cookie status, and a dashboard across all four apps in the pipeline

## What it does NOT do

- Support trackers other than MAM
- Replace your Calibre or Calibre-Web-Automated install (it writes to the same library; CWA stays read-only relative to Hermeece's pipeline)
- Manage your VPN or torrent client networking
- Pretend to be a general-purpose torrent manager

## Architecture

See `previous-stuff/` for the original shell-script workflow this project replaces. The Python rewrite lives under `app/` and is structured as one FastAPI service with background workers, a SQLite database for workflow state, and a React frontend.

Detailed architecture and phased build plan: TBD (will be added as Phase 1 lands).

## Development

Requires Python 3.12+.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## License

TBD
