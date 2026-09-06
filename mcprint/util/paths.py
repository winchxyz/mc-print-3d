"""Per-user config/cache directories and common OS paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "mc-print-3d"


def config_dir() -> Path:
    """Directory for user settings (printer profiles, filament library, preferences)."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    p = base / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    """Directory for regenerable data (voxel pattern caches, texture caches)."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    p = base / APP_DIR_NAME / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_file(name: str) -> Path:
    """Path of a bundled data file in mcprint/data."""
    return Path(__file__).resolve().parent.parent / "data" / name


def appdata_roaming() -> Path | None:
    if sys.platform.startswith("win"):
        v = os.environ.get("APPDATA")
        return Path(v) if v else None
    return None


def appdata_local() -> Path | None:
    if sys.platform.startswith("win"):
        v = os.environ.get("LOCALAPPDATA")
        return Path(v) if v else None
    return None
