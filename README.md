# Hermeece

**Hermes for the meece.** A self-hosted MAM ([MyAnonamouse](https://www.myanonamouse.net/)) courier and Calibre ingest pipeline.

Watches MAM's IRC announce channel for new ebook and audiobook releases, filters them against your personal author lists, downloads them through your torrent client, enriches metadata from 7 sources, queues them for your manual review with cover images, and delivers approved books to Calibre/CWA.

Sibling project to [AthenaScout](https://github.com/mnbaker117/AthenaScout). AthenaScout finds the books you're missing; Hermeece goes and gets them.

![Dashboard](https://img.shields.io/badge/UI-React_18-blue) ![Python](https://img.shields.io/badge/Python-3.12+-green) ![Tests](https://img.shields.io/badge/Tests-625_passing-brightgreen) ![License](https://img.shields.io/badge/License-Private-lightgrey)

## Features

### Pipeline
- **IRC announce monitoring** — connects to `irc.myanonamouse.net`, parses every announce in real time
- **Author-based filtering** — allow list, ignore list, tentative (unknown author) capture for review
- **VIP / freeleech / wedge / ratio policy engine** — configurable economic guards before every grab
- **Snatch budget management** — tracks the MAM active-snatches cap, queues when full, FIFO-rotates to a delayed folder when the queue overflows
- **Excluded uploaders** — prevents downloading your own uploads (MAM duplicate detection)
- **Monthly folder organization** — auto-creates `[YYYY-MM]` subfolders in your download directory

### Post-Download
- **Mandatory manual review queue** — every downloaded book is enriched + staged for your approval before Calibre delivery
- **Metadata enrichment from 7 sources** — MAM (primary, zero extra cost), Goodreads, Amazon, Hardcover, Kobo, IBDB, Google Books
- **Cover images** — MAM poster (primary) + Goodreads/enricher cover (alternative) side by side
- **Metadata editing** — inline title/author/series/ISBN/publisher editing before approval
- **Auto-add timeout** — undecided books get imported with basic metadata after a configurable grace period

### Review Flows
- **Tentative torrent review** — announces from unknown authors are captured with MAM cover images for your decision (approve = download + train author; reject = weekly review bucket)
- **Weekly ignored review** — ignored-author torrents grouped by author with expandable book dropdowns and covers
- **3-tier author taxonomy** — allowed → tentative review → ignored, with weekly auto-promotion

### Notifications
- **Daily digests** — accepted books, tentative captures, ignored summary via ntfy
- **Weekly digest** — author moves, Calibre additions, stale tentative-review auto-promotions
- **Per-event notifications** — optional ntfy pings on every grab and completion
- **Granular toggles** — enable/disable each notification type individually

### Web UI
- **13 pages** — Dashboard, Book Review, New Authors, Weekly Ignored, Author Lists, Filters, Delayed Torrents, Migration Wizard, MAM Status, Logs, Settings
- **AthenaScout-style design** — dark/dim/light themes, collapsible settings sections, masonry layout
- **Authentication** — single-admin with bcrypt passwords, signed session cookies, first-run setup wizard
- **Encrypted credential storage** — Fernet-encrypted secrets in a separate auth database
- **Error boundary** — crashed pages show a recovery button instead of a blank screen

### Download Clients
- **qBittorrent** (fully tested)
- **Transmission** (RPC with session-id auto-refresh)
- **Deluge** (JSON-RPC with Label plugin auto-detection)
- **rTorrent** (XML-RPC via reverse proxy)

### Integrations
- **CWA sink** — atomic write to Calibre-Web-Automated's ingest folder
- **Calibre sink** — direct `calibredb add`
- **AthenaScout** — `POST /api/v1/grabs/from-athenascout` accepts batched torrent URLs
- **Migration wizard** — relocate existing downloads to monthly folders with dry-run mode

## Quick Start

### Docker (recommended)

```bash
docker pull ghcr.io/mnbaker117/hermeece:latest
```

Copy and customize the compose file:

```bash
curl -O https://raw.githubusercontent.com/mnbaker117/hermeece/main/docker-compose.example.yml
mv docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml — adjust volume mount paths for your system
docker compose up -d
```

Open `http://your-server:8788` in a browser.

**First-boot setup:**
1. Create your admin account (setup wizard appears automatically)
2. Go to **Settings** and configure:
   - **MAM**: IRC nick, account, password, and session cookie (from MAM → Preferences → Security)
   - **Download Client**: qBittorrent/Transmission/Deluge/rTorrent URL + credentials
   - **Notifications** (optional): ntfy URL and topic
   - **API Keys** (optional): Hardcover API key for richer metadata
3. The IRC listener connects and starts processing announces immediately

All credentials are entered through the web UI and stored encrypted — never as environment variables.

### Unraid

In the Unraid Docker tab, click **Add Container** and set:
- **Repository:** `ghcr.io/mnbaker117/hermeece:latest`
- **Port:** 8788 → 8788

Add volume mappings for: App Data (`/app/data`), Downloads (`/downloads`), CWA Ingest (`/cwa-ingest`), Calibre Library (`/calibre`), Review Staging (`/review-staging`), Staging (`/staging`).

See [DEPLOY.md](DEPLOY.md) for detailed Unraid setup instructions and first-boot walkthrough.

### Build from source

```bash
git clone https://github.com/mnbaker117/hermeece.git
cd hermeece
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cd frontend && npm install && npm run build && cd ..
pytest
uvicorn app.main:app --reload --port 8788
```

## Architecture

```
IRC announce → Filter gate → Policy engine → Rate limiter
    ↓ (allowed)                                  ↓
Tentative capture ← (unknown author)      Fetch .torrent → qBit submit
    ↓                                            ↓
Review queue ← (download complete)     Download watcher → Pipeline
    ↓                                            ↓
Metadata enrichment (MAM + 7 scrapers)    Stage for review
    ↓                                            ↓
Manual approval ─────────────────────→ CWA/Calibre sink
```

- **Backend:** FastAPI + SQLite (WAL mode) + aiosqlite
- **Frontend:** Vite + React 18 + TypeScript
- **Background jobs:** supervised asyncio tasks + APScheduler
- **Auth:** bcrypt + itsdangerous signed cookies + Fernet-encrypted secrets

## Configuration

All configuration is managed through the web UI after first boot. Settings are persisted in `settings.json`; credentials are stored Fernet-encrypted in `hermeece_auth.db`.

No sensitive values should be set as environment variables in production.

## What Hermeece Does NOT Do

- Support trackers other than MyAnonamouse
- Replace your Calibre or CWA installation
- Manage your VPN or torrent client networking
- Pretend to be a general-purpose torrent manager

## License

MIT
