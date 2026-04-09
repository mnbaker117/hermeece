"""
Runtime environment detection — Docker vs standalone, OS type, default
data directory paths.

Mirrors AthenaScout's `app/runtime.py` for suite cohesion. Kept free of
any in-app imports so it can be imported early by `app.config` without
circular dependency risk.
"""
import os
import platform as _platform
from pathlib import Path


def _detect_runtime_mode() -> str:
    """Detect Docker vs standalone.

    Priority:
      1. HERMEECE_MODE env var (explicit override: 'docker' or 'standalone')
      2. Presence of /.dockerenv (Docker's marker file)
      3. /proc/1/cgroup contains 'docker' or 'containerd'
      4. Default: standalone
    """
    override = os.getenv("HERMEECE_MODE", "").lower().strip()
    if override in ("docker", "standalone"):
        return override

    if Path("/.dockerenv").exists():
        return "docker"

    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text()
            if "docker" in text or "containerd" in text:
                return "docker"
    except (PermissionError, OSError):
        pass

    return "standalone"


def _get_os_type() -> str:
    """Normalized OS type: 'linux', 'macos', or 'windows'."""
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    return system


# Computed once at import time.
RUNTIME_MODE = _detect_runtime_mode()
OS_TYPE = _get_os_type()
IS_DOCKER = RUNTIME_MODE == "docker"
IS_STANDALONE = RUNTIME_MODE == "standalone"


def get_data_dir() -> Path:
    """Where Hermeece stores its database, settings, and auth secret.

    Docker: /app/data (set by Dockerfile via DATA_DIR env var)
    Linux standalone: $XDG_DATA_HOME/hermeece or ~/.local/share/hermeece
    macOS standalone: ~/Library/Application Support/Hermeece
    Windows standalone: %LOCALAPPDATA%/Hermeece
    """
    if IS_DOCKER:
        return Path("/app/data")

    if OS_TYPE == "windows":
        base = os.environ.get("LOCALAPPDATA", "")
        if base:
            return Path(base) / "Hermeece"
        return Path.home() / "AppData" / "Local" / "Hermeece"

    if OS_TYPE == "macos":
        return Path.home() / "Library" / "Application Support" / "Hermeece"

    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "hermeece"
