# Deploying Hermeece

This document covers the first-time deployment + smoke test. Read
it once before you build, then keep it as a reference for future
production checks.

## Prerequisites

- A Linux host with Docker (Unraid, a Pi, a server — anything)
- Network reachability from that host to:
  - `irc.myanonamouse.net` on TCP/6697 (TLS)
  - Your qBittorrent WebUI (typically a LAN address)
  - `www.myanonamouse.net` for `.torrent` downloads
- A NickServ-registered bot nick on MAM IRC (the same one you use
  with Autobrr is fine)
- A valid `mam_id` session cookie from MAM → Preferences → Security
- qBittorrent WebUI credentials and a category configured
  (`mam-complete` by default)

## First-time deploy

The intent is to start with EVERYTHING disabled, verify the container
boots cleanly, then turn on each subsystem one at a time. This way
we never debug two failures at once.

### 1. Get the code on your Docker host

```sh
# On the Docker host (e.g. Unraid)
cd /mnt/user/appdata
git clone <your-private-repo-url> hermeece
cd hermeece
```

### 2. Create your real `docker-compose.yml`

```sh
cp docker-compose.example.yml docker-compose.yml
```

`docker-compose.yml` is `.gitignore`d so your real credentials
never get committed. Edit `docker-compose.yml` and fill in:

- `MAM_IRC_NICK`, `MAM_IRC_ACCOUNT`, `MAM_IRC_PASSWORD`
- `MAM_SESSION_ID`
- `QBIT_URL`, `QBIT_USERNAME`, `QBIT_PASSWORD`

Leave `VERBOSE_LOGGING=true` for the smoke test — turn it off later
once everything is humming.

### 3. Build the image

```sh
docker compose build
```

The first build takes ~2 minutes (downloading the python:3.12-slim
base + installing wheels). Subsequent builds are layer-cached and
much faster.

### 4. Boot it

```sh
docker compose up -d
docker compose logs -f hermeece
```

Watch for these lines in order:

```
Hermeece starting
Database initialized
Dispatcher initialized
Budget watcher started (interval=60s, qbit_category=mam-complete)
IRC listener started (server=irc.myanonamouse.net, channel=#announce, nick=...)
```

If you DON'T see "IRC listener started", one of three things is
wrong:
- IRC creds aren't in `docker-compose.yml` (the listener
  auto-disables when credentials are missing)
- `mam_irc_enabled: false` in your settings.json (delete the file
  to re-seed from env vars, or edit it directly)
- Hermeece is still booting — give it 5 seconds

If you see SASL errors, check your IRC password — Hermeece masks
it in logs but the underlying error from MAM IRC will be visible.

### 5. Verify health

```sh
curl http://10.0.10.20:8788/api/health
```

Expected:
```json
{"status":"ok","service":"hermeece","dispatcher_ready":true}
```

`dispatcher_ready: false` means the lifespan didn't finish — go
read the logs.

## Smoke test

The goal: prove the full pipeline works end-to-end against real
MAM and real qBit using exactly **one** snatch (or two if you also
do the cookie rotation test).

### Test A — Inject a known torrent

Pick a small free-leech ebook from your MAM Recent Activity page.
Note the torrent ID (the number at the end of the URL,
`https://www.myanonamouse.net/t/<NUMBER>`).

```sh
curl -X POST http://10.0.10.20:8788/api/v1/grabs/inject \
    -H 'Content-Type: application/json' \
    -d '{"torrent_id": "1233592"}'
```

Expected response:
```json
{
  "ok": true,
  "action": "submit",
  "reason": "ok",
  "announce_id": 1,
  "grab_id": 1,
  "qbit_hash": "<40-char hex>",
  "error": null
}
```

Now verify in qBittorrent's WebUI: the torrent should appear in
the `mam-complete` category and start downloading. If it doesn't,
the `qbit_hash` from the response tells you what to look for.

**This counts as one snatch against your MAM budget.**

### Test B — Verify the audit and ledger

```sh
docker compose exec hermeece sqlite3 /app/data/hermeece.db \
    "SELECT id, state, qbit_hash, submitted_at FROM grabs"
```

You should see one row with `state=submitted` and a
`submitted_at` timestamp.

```sh
docker compose exec hermeece sqlite3 /app/data/hermeece.db \
    "SELECT * FROM snatch_ledger"
```

One row, `released_at=NULL`, `seeding_seconds=0`. The budget
watcher will start updating it on its next 60s tick.

After waiting ~60 seconds:

```sh
docker compose logs hermeece | grep "budget watcher tick"
```

You should see lines like:
```
budget watcher tick: qbit_seen=1 released_seedtime=0 released_removed=0 pops=0/0
```

This confirms the watcher is polling qBit and seeing your snatched
torrent.

### Test C — Verify IRC announces are flowing

```sh
docker compose logs hermeece | grep "PRIVMSG"
```

With `VERBOSE_LOGGING=true` you should see incoming announces every
few minutes. They'll all be filtered out (because the allow list
is empty in Phase 1 — author management UI lands in Phase 3), but
the audit log is still being populated:

```sh
docker compose exec hermeece sqlite3 /app/data/hermeece.db \
    "SELECT torrent_name, decision, decision_reason FROM announces ORDER BY id DESC LIMIT 10"
```

You should see real recent MAM releases here, all with
`decision=skip` and `decision_reason=author_not_allowlisted`. This
confirms the IRC → parser → filter pipeline is working end-to-end
against real MAM IRC traffic.

### Test D — Cookie rotation (optional, costs 2 snatches total)

This validates the full cookie expiration + recovery path that
caused us so much grief in Autobrr.

1. **Confirm cookie A works** — you already did this in Test A
2. **Generate cookie B on MAM** (Preferences → Security → Generate)
   This rotates cookie A out server-side. **Do not paste B into
   Hermeece yet.**
3. **Inject another grab** with cookie A (now stale):

   ```sh
   curl -X POST http://10.0.10.20:8788/api/v1/grabs/inject \
       -H 'Content-Type: application/json' \
       -d '{"torrent_id": "<NEW_TORRENT_ID>"}'
   ```

   Expected response:
   ```json
   {
     "ok": false,
     "action": "submit",
     "reason": "fetch_failed:cookie_expired",
     ...
     "error": "..."
   }
   ```

   **This grab does NOT count against your snatch budget** because
   the .torrent fetch failed before qBit ever saw it.

4. **Update Hermeece's cookie** — edit `data/settings.json`,
   replace the `mam_session_id` value, then:

   ```sh
   docker compose restart hermeece
   ```

5. **Re-inject the same torrent ID** (or a different one — your
   choice):

   ```sh
   curl -X POST http://10.0.10.20:8788/api/v1/grabs/inject \
       -H 'Content-Type: application/json' \
       -d '{"torrent_id": "<NEW_TORRENT_ID>"}'
   ```

   Expected: `ok=true`, the torrent shows up in qBit. **This is
   snatch #2.**

If all four tests pass, Hermeece is production-ready for Phase 1.
The next pieces — author allow list management, post-download
ingest into Calibre, the React UI — land in Phases 2 and 3.

## Stopping and updating

```sh
# Stop
docker compose down

# Update from git
git pull
docker compose build
docker compose up -d
```

The data volume persists across rebuilds — your settings.json,
your DB, and your auth secret all survive.

## Troubleshooting

**Hermeece keeps restarting** — `docker compose logs hermeece`
will show the error. Common causes: bad MAM cookie format,
unreachable qBit URL, malformed `MAM_IRC_PASSWORD` (escape `$`
and `!` characters in YAML).

**IRC listener never connects** — check `MAM_IRC_NICK`,
`MAM_IRC_ACCOUNT`, `MAM_IRC_PASSWORD` are all populated. Hermeece
auto-disables the listener if any of the three are empty. Test
your creds first by logging into MAM IRC manually.

**Inject endpoint returns 503** — `dispatcher not initialized`.
The lifespan startup hit an error. Read the logs.

**Inject succeeds but qBit never sees the torrent** — the
`add_torrent` call returned success but qBit didn't actually
ingest it. Check qBit's own logs and the WebUI. Verify the
category exists.

**Snatched torrent is in qBit but not in `snatch_ledger`** — the
budget watcher tick failed. Check `docker compose logs hermeece
| grep budget`.
