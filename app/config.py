"""
Configuration loading and persistence.

Two layers of config, mirroring the AthenaScout pattern:

  1. **Environment variables** (read once at import time): things the
     deployment owner sets via `docker run -e ...`. These seed
     `settings.json` on first run only — after that, settings.json is
     the source of truth and env vars are ignored.
  2. **Saved settings** (`settings.json` under DATA_DIR): runtime-mutable
     state edited via the Settings UI. `load_settings()` always merges
     the on-disk file over `DEFAULT_SETTINGS`, so every key listed in
     DEFAULT_SETTINGS is guaranteed to be present in the returned dict.

INVARIANT for adding a new setting:
  1. Add it to DEFAULT_SETTINGS first with its canonical default value.
  2. Any inline `.get("key", FALLBACK)` calls scattered across the code
     MUST use the same FALLBACK as the entry here. Mismatched defaults
     silently diverge for users whose settings.json predates the key.
"""
import json
import logging
import os
from pathlib import Path

from app.runtime import IS_DOCKER, get_data_dir

_log = logging.getLogger("hermeece.config")


# ─── Environment variables (first-run seeds) ─────────────────

# Web server bind. NOT 8787 — that's AthenaScout.
ENV_WEBUI_HOST = os.getenv("WEBUI_HOST", "0.0.0.0")
ENV_WEBUI_PORT = int(os.getenv("WEBUI_PORT", "8788"))

# Verbose logging toggle (DEBUG level vs INFO).
ENV_VERBOSE_LOGGING = os.getenv("VERBOSE_LOGGING", "").lower() in ("true", "1", "yes")

# MAM session cookie — first-run seed only. After settings.json exists,
# the UI is the only way to update it.
ENV_MAM_SESSION_ID = os.getenv("MAM_SESSION_ID", "")

# MAM IRC bot credentials — first-run seeds. The lifespan reads the
# saved settings to decide whether to start the IRC listener at all,
# so all three of these must be populated for the listener to come up.
ENV_MAM_IRC_NICK = os.getenv("MAM_IRC_NICK", "")
ENV_MAM_IRC_ACCOUNT = os.getenv("MAM_IRC_ACCOUNT", "")
ENV_MAM_IRC_PASSWORD = os.getenv("MAM_IRC_PASSWORD", "")

# qBittorrent connection — first-run seeds.
ENV_QBIT_URL = os.getenv("QBIT_URL", "")
ENV_QBIT_USERNAME = os.getenv("QBIT_USERNAME", "")
ENV_QBIT_PASSWORD = os.getenv("QBIT_PASSWORD", "")

# qBittorrent download category that Hermeece watches for completed
# torrents. Default matches the OP's existing qBit setup convention
# of `[mam-reseed]` (the bracket characters are part of the category
# name, not a glob — qBit accepts arbitrary strings here).
ENV_QBIT_WATCH_CATEGORY = os.getenv("QBIT_WATCH_CATEGORY", "[mam-reseed]")
ENV_QBIT_TAG = os.getenv("QBIT_TAG", "hermeece-seed")

# Calibre library path (mounted into the container). The library directory
# that contains metadata.db. Empty by default — user configures via Settings.
ENV_CALIBRE_LIBRARY_PATH = os.getenv(
    "CALIBRE_LIBRARY_PATH",
    "/calibre" if IS_DOCKER else "",
)

# Staging directory: where downloaded books are copied for metadata review
# before being added to Calibre.
ENV_STAGING_PATH = os.getenv(
    "STAGING_PATH",
    "/staging" if IS_DOCKER else "",
)

# ntfy endpoint for notifications. Empty disables notifications.
ENV_NTFY_URL = os.getenv("NTFY_URL", "")

# Auth secret — for HTTP-only session cookies. Same handling as AthenaScout:
# env var takes priority, then a file under DATA_DIR, then in-memory fallback.
ENV_AUTH_SECRET = os.getenv("HERMEECE_AUTH_SECRET", "")

# Dry-run mode: connect to real IRC and parse real announces, but never fetch
# .torrent files or talk to qBittorrent. Used for testing without burning
# snatch budget.
ENV_DRY_RUN = os.getenv("HERMEECE_DRY_RUN", "").lower() in ("true", "1", "yes")


# ─── Data directory ──────────────────────────────────────────

_data_dir_env = os.getenv("DATA_DIR", "")
DATA_DIR = Path(_data_dir_env) if _data_dir_env else get_data_dir()
APP_DB_PATH = DATA_DIR / "hermeece.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
AUTH_SECRET_PATH = DATA_DIR / "auth_secret"

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ─── DEFAULT_SETTINGS — canonical source of truth ────────────

DEFAULT_SETTINGS = {
    # ── MAM session ─────────────────────────────────────────
    "mam_session_id": "",
    "mam_last_validated_at": None,
    "mam_validation_ok": False,
    # IRC bot identity (NickServ-registered nick on irc.myanonamouse.net)
    "mam_irc_nick": "",
    "mam_irc_account": "",
    "mam_irc_password": "",
    # Pause the IRC listener entirely (used during cookie expiry, manual stop)
    "mam_irc_enabled": True,

    # ── Filtering ───────────────────────────────────────────
    # Categories Hermeece is interested in. Normalized form (lowercase,
    # punctuation collapsed to single spaces). The user edits this in the
    # Settings UI; the filter consults it on every announce.
    "allowed_categories": [
        "ebooks action adventure",
        "ebooks science fiction",
        "ebooks fantasy",
        "ebooks urban fantasy",
        "ebooks general fiction",
        "ebooks mixed collections",
        "ebooks young adult",
    ],
    # Categories to exclude even when the parent format is allowed.
    # Normalized form. E.g. ["ebooks romance"] to block romance but
    # keep all other ebook subcategories.
    "excluded_categories": [],
    # Format-level gates. The "format" is the MAM category prefix
    # before " - " (e.g. "ebooks", "audiobooks", "comics graphic novels").
    # Empty allowed_formats = accept all formats that pass category gate.
    "allowed_formats": [],
    "excluded_formats": [],
    # Language gate. Normalized lowercase. Empty = accept all languages.
    "allowed_languages": ["english"],

    # ── Grab policy (VIP / freeleech / wedge / ratio) ──────
    # These settings control the economic decision layer that runs
    # AFTER the filter gate says "allow" but BEFORE the actual grab.
    # The policy engine checks whether the torrent is "free" (VIP,
    # global FL, or wedge-applicable) and whether the user's ratio
    # can afford the download if it isn't.

    # If true, only grab VIP torrents (download doesn't count).
    "policy_vip_only": False,
    # If true, only grab torrents that are free (VIP, global FL,
    # personal FL, or wedge-applied). Non-free torrents are skipped.
    "policy_free_only": False,
    # VIP torrents bypass all other policy checks (ratio, wedge logic).
    "policy_vip_always_grab": True,
    # Spend a freeleech wedge to make a non-free torrent free.
    "policy_use_wedge": False,
    # Don't spend wedges if the user's wedge count would drop below
    # this threshold. 0 = spend all wedges freely.
    "policy_min_wedges_reserved": 0,
    # Skip non-free torrents if the user's ratio is below this value.
    # 0 = disable ratio checking (grab regardless of ratio).
    "policy_ratio_floor": 0.0,
    # Whether to look up the torrent's VIP/FL status via the MAM
    # search API when the IRC announce alone isn't enough. Adds one
    # HTTP round-trip per announce that passes the filter.
    "policy_lookup_torrent_info": True,

    # ── Snatch budget (rate limit) ──────────────────────────
    # MAM caps active snatches. New users get 30, OP currently has 200.
    # A "snatch" is in-budget from grab time until the torrent has accumulated
    # 72 hours of seedtime in qBittorrent (or until it's removed from qBit).
    "snatch_budget_cap": 200,
    "snatch_seed_hours_required": 72,
    # Mode when budget is full: "queue" (fetch and hold locally, submit when
    # budget frees) or "drop" (skip the announce entirely, log to review queue).
    "snatch_full_mode": "queue",
    "snatch_queue_max": 100,

    # ── qBittorrent ─────────────────────────────────────────
    "qbit_url": "",
    "qbit_username": "",
    "qbit_password": "",
    "qbit_watch_category": "[mam-reseed]",
    # Comma-separated tag list applied to every torrent Hermeece
    # submits to qBit. Lines up with the user's existing
    # manual-seed / autobrr-seed / hermeece-seed convention so
    # which client added what is visible at a glance in the qBit
    # WebUI. Empty string disables tagging.
    "qbit_tag": "hermeece-seed",
    # Base download directory for qBit. When monthly_download_folders is
    # True, Hermeece creates [YYYY-MM]/ subfolders here and tells qBit
    # to save each download in the current month's folder.
    # This should match the path AS SEEN BY QBIT (inside qBit's container
    # if using Docker). E.g. "/data/[mam-complete]".
    "qbit_download_path": "",
    # Path translation between qBit's container and Hermeece's container.
    # qBit reports save_path using ITS mount paths (e.g. "/data/...").
    # Hermeece needs to translate that to ITS mount paths to find files.
    # qbit_path_prefix: what qBit uses (e.g. "/data")
    # local_path_prefix: what Hermeece sees (e.g. "/downloads")
    # The download watcher replaces qbit_path_prefix with local_path_prefix
    # when reading files, and does the reverse when passing save_path to qBit.
    "qbit_path_prefix": "/data",
    "local_path_prefix": "/downloads",
    # Organize downloads into monthly subfolders ([2026-04]/, [2026-05]/).
    "monthly_download_folders": True,
    # How often to poll qBit for completed torrents and seedtime updates.
    "qbit_poll_interval_seconds": 60,

    # ── Sinks (where completed books go) ────────────────────
    # Default sink: calibre. Per-category overrides via "category_routing".
    "default_sink": "calibre",
    "category_routing": {},  # {"audiobooks fantasy": "folder", ...}
    "folder_sink_path": "",  # for folder sink
    "audiobookshelf_library_path": "",  # for audiobookshelf sink
    # CWA (Calibre-Web-Automated) ingest directory. CWA watches this
    # folder and auto-imports any book files dropped here. Safest
    # Calibre integration — no direct metadata.db writes.
    "cwa_ingest_path": "",

    # ── Calibre integration ─────────────────────────────────
    "calibre_library_path": "",
    # Staging directory where files land before metadata review + calibredb add.
    "staging_path": "",
    # If review queue items aren't decided within N days, auto-add to Calibre
    # with whatever metadata the file ships with (no enrichment).
    "metadata_review_timeout_days": 14,

    # ── Notifications ───────────────────────────────────────
    "ntfy_url": "",
    "ntfy_topic": "hermeece",
    "daily_digest_enabled": True,
    "daily_digest_hour": 9,  # local time, 24h

    # ── Cron / scheduled jobs ───────────────────────────────
    # MAM keeps a session cookie alive as long as we make at least one
    # API call within a 15-day window. Hermeece's cookie auto-rotation
    # only fires when something else triggers a MAM call (an inject,
    # an IRC-driven grab) — if Hermeece sits idle for 15+ days the
    # cookie expires silently. The keep-alive job hits MAM's search
    # endpoint on a fixed schedule WELL inside that window so the
    # rotation handler always has something to chew on.
    #
    # Default 168 hours (7 days) — half the 15-day window gives us a
    # generous safety margin. Even if the container crashes right
    # before the job fires, the next restart still has ~7-8 days of
    # grace before the cookie would actually expire.
    "cookie_keepalive_interval_hours": 168,
    # How often to retry grabs that failed with cookie_expired. The job
    # is a no-op when there are no failed grabs, so this mostly affects
    # latency between cookie rotation and automatic retry.
    "cookie_retry_interval_seconds": 300,
    "cookie_check_interval_hours": 6,
    "weekly_audit_day": "sunday",
    "weekly_audit_hour": 3,

    # ── Operational ─────────────────────────────────────────
    "verbose_logging": False,
    "dry_run": False,  # mirror of HERMEECE_DRY_RUN, runtime-toggleable
    "setup_complete": False,
}


def apply_logging(verbose: bool = False):
    """Configure log levels based on the verbose toggle."""
    level = logging.DEBUG if verbose else logging.INFO
    for name in [
        "hermeece",
        "hermeece.config",
        "hermeece.database",
        "hermeece.mam",
        "hermeece.mam.irc",
        "hermeece.mam.cookie",
        "hermeece.mam.grab",
        "hermeece.filter",
        "hermeece.clients",
        "hermeece.sinks",
        "hermeece.metadata",
        "hermeece.notify",
    ]:
        logging.getLogger(name).setLevel(level)
    # httpx is too noisy at DEBUG.
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("hermeece").info(
        f"Logging set to {'VERBOSE (DEBUG)' if verbose else 'NORMAL (INFO)'}"
    )


# ─── Settings cache ──────────────────────────────────────────
# Same pattern as AthenaScout: cache the parsed dict keyed by the
# settings file's mtime. Any save_settings() bumps the mtime, which
# invalidates the cache on the next load_settings() call automatically.
_settings_cache: dict = {"mtime": object(), "data": None}


def load_settings() -> dict:
    """Load settings.json, merged over DEFAULT_SETTINGS, with mtime cache."""
    try:
        cur_mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else None
    except OSError:
        cur_mtime = None

    if _settings_cache["data"] is not None and cur_mtime == _settings_cache["mtime"]:
        return _settings_cache["data"]

    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH) as f:
                saved = json.load(f)
            merged = {**DEFAULT_SETTINGS, **saved}
            _settings_cache["data"] = merged
            _settings_cache["mtime"] = cur_mtime
            return merged
        except Exception as e:
            _log.warning(f"Failed to read {SETTINGS_PATH}: {e}; falling back to defaults")

    # First run — start from defaults and seed from env vars.
    settings = dict(DEFAULT_SETTINGS)
    _apply_env_overrides(settings)
    save_settings(settings)
    try:
        _settings_cache["mtime"] = SETTINGS_PATH.stat().st_mtime
    except OSError:
        _settings_cache["mtime"] = None
    _settings_cache["data"] = settings
    return settings


def _apply_env_overrides(settings: dict):
    """Seed settings from env vars on first run only."""
    if ENV_MAM_SESSION_ID and not settings.get("mam_session_id"):
        settings["mam_session_id"] = ENV_MAM_SESSION_ID
    if ENV_MAM_IRC_NICK and not settings.get("mam_irc_nick"):
        settings["mam_irc_nick"] = ENV_MAM_IRC_NICK
    if ENV_MAM_IRC_ACCOUNT and not settings.get("mam_irc_account"):
        settings["mam_irc_account"] = ENV_MAM_IRC_ACCOUNT
    if ENV_MAM_IRC_PASSWORD and not settings.get("mam_irc_password"):
        settings["mam_irc_password"] = ENV_MAM_IRC_PASSWORD
    if ENV_QBIT_URL and not settings.get("qbit_url"):
        settings["qbit_url"] = ENV_QBIT_URL
    if ENV_QBIT_USERNAME and not settings.get("qbit_username"):
        settings["qbit_username"] = ENV_QBIT_USERNAME
    if ENV_QBIT_PASSWORD and not settings.get("qbit_password"):
        settings["qbit_password"] = ENV_QBIT_PASSWORD
    # qbit_watch_category has a non-empty default ("[mam-reseed]"), so the
    # usual `not settings.get(...)` guard would silently ignore an env var
    # override. We compare against the default instead so the env var only
    # wins on first run, never overrides a value the user has explicitly
    # changed via the (future) Settings UI.
    if (
        ENV_QBIT_WATCH_CATEGORY
        and settings.get("qbit_watch_category") == DEFAULT_SETTINGS["qbit_watch_category"]
    ):
        settings["qbit_watch_category"] = ENV_QBIT_WATCH_CATEGORY
    # qbit_tag also has a non-empty default ("hermeece-seed"); same
    # rule — env var only wins on first run vs the default.
    if (
        ENV_QBIT_TAG
        and settings.get("qbit_tag") == DEFAULT_SETTINGS["qbit_tag"]
    ):
        settings["qbit_tag"] = ENV_QBIT_TAG
    if ENV_CALIBRE_LIBRARY_PATH and not settings.get("calibre_library_path"):
        settings["calibre_library_path"] = ENV_CALIBRE_LIBRARY_PATH
    if ENV_STAGING_PATH and not settings.get("staging_path"):
        settings["staging_path"] = ENV_STAGING_PATH
    if ENV_NTFY_URL and not settings.get("ntfy_url"):
        settings["ntfy_url"] = ENV_NTFY_URL
    if ENV_VERBOSE_LOGGING and not settings.get("verbose_logging"):
        settings["verbose_logging"] = True
    if ENV_DRY_RUN and not settings.get("dry_run"):
        settings["dry_run"] = True


def save_settings(settings: dict):
    """Persist settings.json and warm the cache."""
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
    try:
        _settings_cache["mtime"] = SETTINGS_PATH.stat().st_mtime
    except OSError:
        _settings_cache["mtime"] = None
    _settings_cache["data"] = dict(settings)
