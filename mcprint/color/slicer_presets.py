"""Import filament colors from the slicers installed on this machine.

Two very different sources are read:

* the **user filament presets** (Bambu Studio / OrcaSlicer ``user/<id>/filament/*.json``,
  PrusaSlicer ``filament/*.ini``) - spools the user defined by hand, and
* the **currently assigned slot colors** in the application config (``BambuStudio.conf``
  ``presets.filament_colors``, ``PrusaSlicer.ini`` ``[presets]``) - what is loaded *right now*.

Everything here is best effort: unreadable, half-written or unexpectedly shaped files are
skipped, and no function in this module raises.
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from ..util.color import parse_hex, to_hex
from ..util.paths import appdata_roaming
from .filament import Filament

log = logging.getLogger(__name__)

#: Slicers whose config layout we understand, in the order results are returned.
BAMBU_LIKE = ("BambuStudio", "OrcaSlicer")

_MATERIAL_RE = re.compile(
    r"\b(PETG|PET|PLA|ABS|ASA|TPU|TPE|PVA|PCTG|PAHT|PAHTCF|PA6|PA12|PA|PC|PPS|PPA|PP|HIPS|PEEK|PEI)\b",
    re.I,
)


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------
def _first(value: Any) -> str:
    """Slicer JSON stores most settings as a one element list; normalise to a string."""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = str(item).strip()
            if text:
                return text
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _clean_hex(value: Any) -> Optional[str]:
    """Return ``'#RRGGBB'`` for anything parseable, else ``None``."""
    text = _first(value)
    if not text:
        return None
    try:
        return to_hex(parse_hex(text))
    except Exception:
        return None


def material_from_name(name: str, default: str = "PLA") -> str:
    """Guess the material from a preset name such as ``'Bambu PETG Basic @BBL A2L'``."""
    match = _MATERIAL_RE.search(name or "")
    if not match:
        return default
    token = match.group(1).upper()
    return {"PAHTCF": "PAHT-CF"}.get(token, token)


def load_json_lenient(path: Path) -> Optional[dict]:
    """Read a slicer JSON file, ignoring trailing junk.

    Bambu Studio appends a ``# MD5 checksum ...`` line to ``BambuStudio.conf``, which strict
    :func:`json.loads` rejects, so the first JSON value is decoded and the rest discarded.
    """
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        try:
            data, _ = json.JSONDecoder().raw_decode(text)
        except Exception:
            log.debug("could not parse %s as JSON", path, exc_info=True)
            return None
    return data if isinstance(data, dict) else None


def _read_ini(path: Path) -> Optional[configparser.ConfigParser]:
    """Read a PrusaSlicer style ini, tolerating key/value lines outside any section."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keep the original key case
    try:
        parser.read_string("[__root__]\n" + text)
    except Exception:
        log.debug("could not parse %s as ini", path, exc_info=True)
        return None
    return parser


# --------------------------------------------------------------------------------------
# configuration directories
# --------------------------------------------------------------------------------------
def _candidate_dirs() -> dict[str, list[Path]]:
    """Per-slicer list of plausible configuration directories for this platform."""
    home = Path.home()
    if sys.platform.startswith("win"):
        roaming = appdata_roaming() or (home / "AppData" / "Roaming")
        return {
            "BambuStudio": [roaming / "BambuStudio"],
            "OrcaSlicer": [roaming / "OrcaSlicer"],
            "PrusaSlicer": [roaming / "PrusaSlicer"],
            "Cura": [roaming / "cura", roaming / "Cura"],
        }
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        return {
            "BambuStudio": [support / "BambuStudio"],
            "OrcaSlicer": [support / "OrcaSlicer"],
            "PrusaSlicer": [support / "PrusaSlicer"],
            "Cura": [support / "cura", support / "Cura"],
        }
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    share = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return {
        "BambuStudio": [config / "BambuStudio"],
        "OrcaSlicer": [config / "OrcaSlicer"],
        "PrusaSlicer": [config / "PrusaSlicer"],
        "Cura": [config / "cura", share / "cura"],
    }


def slicer_config_dirs() -> dict[str, Path]:
    """Configuration directories of the slicers installed on this machine.

    Only directories that actually exist are returned, keyed by ``'BambuStudio'``,
    ``'OrcaSlicer'``, ``'PrusaSlicer'`` and ``'Cura'``.
    """
    found: dict[str, Path] = {}
    try:
        candidates = _candidate_dirs()
    except Exception:  # pragma: no cover - defensive
        log.debug("could not build slicer config candidates", exc_info=True)
        return {}
    for name, paths in candidates.items():
        for path in paths:
            try:
                if path.is_dir():
                    found[name] = path
                    break
            except OSError:
                continue
    return found


# --------------------------------------------------------------------------------------
# Bambu Studio / OrcaSlicer
# --------------------------------------------------------------------------------------
def _bambu_user_presets(slicer: str, root: Path) -> list[Filament]:
    """Filaments from ``user/<id>/filament/*.json`` (including ``user/default``)."""
    out: list[Filament] = []
    user_dir = root / "user"
    try:
        profile_dirs = [p for p in user_dir.iterdir() if p.is_dir()]
    except OSError:
        return out
    for profile in profile_dirs:
        filament_dir = profile / "filament"
        try:
            files = sorted(filament_dir.glob("*.json"))
        except OSError:
            continue
        for path in files:
            data = load_json_lenient(path)
            if not data:
                continue
            color = _clean_hex(data.get("default_filament_colour")) or \
                _clean_hex(data.get("filament_colour"))
            if not color:
                continue                     # a preset without a colour is not a spool
            preset = _first(data.get("name")) or path.stem
            out.append(Filament(
                color_hex=color,
                name=preset,
                material=_first(data.get("filament_type")) or material_from_name(preset),
                vendor=_first(data.get("filament_vendor")),
                source="slicer",
                extra={"slicer": slicer, "preset": preset, "path": str(path)},
            ))
    return out


def _bambu_slot_colors(slicer: str, root: Path) -> list[Filament]:
    """The colours currently assigned to the slots, from ``<Slicer>.conf``."""
    conf_path = root / ("%s.conf" % slicer)
    if not conf_path.is_file():
        return []
    data = load_json_lenient(conf_path)
    if not data:
        return []

    sections: list[dict] = []
    for key in ("presets", "app"):
        node = data.get(key)
        if isinstance(node, dict):
            sections.append(node)
    sections.append(data)

    colors_raw = ""
    presets_node: Any = None
    for section in sections:
        if not colors_raw:
            colors_raw = _first(section.get("filament_colors")) or \
                _first(section.get("filament_multi_colors"))
        if presets_node is None:
            candidate = section.get("filaments")
            if candidate is None:
                candidate = section.get("filament")
            if isinstance(candidate, (list, tuple)) and candidate:
                presets_node = list(candidate)
            elif isinstance(candidate, str) and candidate.strip():
                presets_node = [p.strip() for p in candidate.split(";") if p.strip()]
    if not colors_raw:
        return []

    names = presets_node or []
    out: list[Filament] = []
    for index, token in enumerate(colors_raw.split(",")):
        color = _clean_hex(token)
        if not color:
            continue
        preset = str(names[index]).strip() if index < len(names) else ""
        label = "Slot %d (%s)" % (index + 1, preset) if preset else "Slot %d" % (index + 1)
        out.append(Filament(
            color_hex=color,
            name=label,
            material=material_from_name(preset),
            vendor="",
            source="slicer",
            extra={"slicer": slicer, "preset": preset, "slot": index + 1,
                   "path": str(conf_path)},
        ))
    return out


# --------------------------------------------------------------------------------------
# PrusaSlicer
# --------------------------------------------------------------------------------------
def _prusa_active_presets(root: Path) -> set[str]:
    """Filament preset names selected in ``PrusaSlicer.ini`` ``[presets]``."""
    ini = root / "PrusaSlicer.ini"
    if not ini.is_file():
        return set()
    parser = _read_ini(ini)
    if parser is None:
        return set()
    active: set[str] = set()
    for section in ("presets", "__root__"):
        if not parser.has_section(section):
            continue
        for key, value in parser.items(section):
            if key == "filament" or key.startswith("filament_"):
                name = (value or "").strip().strip('"')
                if name and not name.replace(".", "").isdigit():
                    active.add(name)
    return active


def _prusa_user_presets(root: Path) -> list[Filament]:
    """Filaments from the user presets in ``PrusaSlicer/filament/*.ini``.

    The bundled vendor profiles under ``vendor/*.ini`` are deliberately ignored: they are huge
    and describe every filament Prusa ships, not what the user owns.
    """
    filament_dir = root / "filament"
    try:
        files = sorted(filament_dir.glob("*.ini"))
    except OSError:
        return []
    active = _prusa_active_presets(root)
    out: list[Filament] = []
    for path in files:
        parser = _read_ini(path)
        if parser is None:
            continue
        values: dict[str, str] = {}
        for section in parser.sections():
            for key, value in parser.items(section):
                values.setdefault(key.strip().lower(), (value or "").strip())
        color = _clean_hex(values.get("filament_colour") or values.get("filament_color"))
        if not color:
            continue
        preset = path.stem
        out.append(Filament(
            color_hex=color,
            name=preset,
            material=values.get("filament_type") or material_from_name(preset),
            vendor=values.get("filament_vendor", ""),
            source="slicer",
            extra={"slicer": "PrusaSlicer", "preset": preset, "path": str(path),
                   "active": preset in active},
        ))
    return out


# --------------------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------------------
def import_slicer_filaments() -> list[Filament]:
    """Collect filament colours from every slicer configuration found on this machine.

    Returns user presets (a colour the user saved on a spool) plus one entry per currently
    assigned slot colour, tagged with ``extra['slicer']`` and ``extra['preset']``.  Never raises.
    """
    out: list[Filament] = []
    dirs = slicer_config_dirs()

    for slicer in BAMBU_LIKE:
        root = dirs.get(slicer)
        if root is None:
            continue
        for loader in (_bambu_user_presets, _bambu_slot_colors):
            try:
                out.extend(loader(slicer, root))
            except Exception:  # pragma: no cover - defensive, never raise
                log.debug("%s import from %s failed", loader.__name__, root, exc_info=True)

    prusa_root = dirs.get("PrusaSlicer")
    if prusa_root is not None:
        try:
            out.extend(_prusa_user_presets(prusa_root))
        except Exception:  # pragma: no cover - defensive
            log.debug("PrusaSlicer import from %s failed", prusa_root, exc_info=True)

    # Cura keeps its material colours in per-material XML/FDM files rather than in the
    # configuration directory; slicer_config_dirs() still reports the directory so the GUI can
    # point at it, but nothing is imported from it here.
    return out
