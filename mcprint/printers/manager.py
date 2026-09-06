"""Saved printer profiles and backend dispatch.

:class:`PrinterProfiles` persists the printers the user added to
``config_dir()/printers.json`` (credentials included – the file is per-user), and
:func:`connect_printer` / :func:`sync_filaments` route a :class:`PrinterInfo` to the backend
that owns its ``kind``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ..util.paths import config_dir
from .base import (
    BACKENDS,
    FilamentSlot,
    PrinterBackend,
    PrinterError,
    PrinterInfo,
    ProgressFn,
)

log = logging.getLogger(__name__)

#: Presentation order of the backends (unknown kinds are appended at the end).
BACKEND_ORDER: tuple[str, ...] = ("bambu", "moonraker", "octoprint", "prusalink", "duet",
                                  "elegoo", "serial")

PROFILES_FILENAME = "printers.json"


class PrinterProfiles:
    """The user's saved printers, stored as JSON in the per-user config directory."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else (config_dir() / PROFILES_FILENAME)
        self.printers: list[PrinterInfo] = []
        self._selected_id: Optional[str] = None
        self.load()

    # -- persistence --------------------------------------------------------------------
    def load(self) -> None:
        """(Re)read the profile file.  A missing or broken file yields an empty list."""
        self.printers = []
        self._selected_id = None
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("cannot read printer profiles at %s - starting empty", self.path, exc_info=True)
            return
        if not isinstance(data, dict):
            return
        for entry in data.get("printers", []) or []:
            try:
                self.printers.append(PrinterInfo.from_dict(entry))
            except Exception:
                log.debug("skipping unreadable printer profile entry", exc_info=True)
        selected = data.get("selected_id")
        self._selected_id = str(selected) if selected else None

    def save(self) -> None:
        """Write the profiles atomically (credentials included)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "printers": [p.to_dict() for p in self.printers],
            "selected_id": self._selected_id,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- collection ---------------------------------------------------------------------
    def add_or_update(self, info: PrinterInfo) -> PrinterInfo:
        """Insert ``info`` or replace the stored printer with the same id."""
        for i, existing in enumerate(self.printers):
            if existing.id == info.id:
                merged = dict(existing.credentials or {})
                merged.update(info.credentials or {})
                info.credentials = merged
                self.printers[i] = info
                break
        else:
            self.printers.append(info)
        if self._selected_id is None:
            self._selected_id = info.id
        self.save()
        return info

    def remove(self, printer_id: str) -> None:
        self.printers = [p for p in self.printers if p.id != printer_id]
        if self._selected_id == printer_id:
            self._selected_id = self.printers[0].id if self.printers else None
        self.save()

    def get(self, printer_id: str) -> Optional[PrinterInfo]:
        for printer in self.printers:
            if printer.id == printer_id:
                return printer
        return None

    def all(self) -> list[PrinterInfo]:
        return list(self.printers)

    # -- selection ----------------------------------------------------------------------
    @property
    def selected_id(self) -> Optional[str]:
        """Id of the printer the user is working with (persisted)."""
        return self._selected_id

    @selected_id.setter
    def selected_id(self, value: Optional[str]) -> None:
        self._selected_id = str(value) if value else None
        self.save()

    @property
    def selected(self) -> Optional[PrinterInfo]:
        return self.get(self._selected_id) if self._selected_id else None

    def __len__(self) -> int:
        return len(self.printers)

    def __iter__(self):
        return iter(self.printers)


# ---------------------------------------------------------------------------------------
# backend dispatch
# ---------------------------------------------------------------------------------------
def backends() -> list[PrinterBackend]:
    """Import every backend module (guarded) and return them in a stable display order."""
    from . import load_backends

    table = load_backends()
    ordered = [table[kind] for kind in BACKEND_ORDER if kind in table]
    ordered.extend(backend for kind, backend in table.items() if kind not in BACKEND_ORDER)
    return ordered


def get_backend(kind: str) -> Optional[PrinterBackend]:
    """Backend for ``kind``, importing the backend modules on first use."""
    if kind not in BACKENDS:
        from . import load_backends

        load_backends()
    return BACKENDS.get(kind)


def _backend_or_raise(info: PrinterInfo) -> PrinterBackend:
    backend = get_backend(info.kind)
    if backend is None:
        known = ", ".join(sorted(BACKENDS)) or "none"
        raise PrinterError(f"No backend for printer type '{info.kind}' (available: {known}).")
    try:
        ok, why = backend.available()
    except Exception:
        ok, why = True, ""
    if not ok:
        raise PrinterError(f"{getattr(backend, 'display_name', info.kind)} is unavailable: {why}.")
    return backend


def connect_printer(info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
    """Query a printer for its details.  Manually configured printers are returned unchanged."""
    if info.kind == "manual":
        return info
    return _backend_or_raise(info).connect(info, progress=progress)


def sync_filaments(info: PrinterInfo, progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
    """Read the filaments currently loaded in a printer."""
    if info.kind == "manual":
        return list(info.filament_slots)
    return _backend_or_raise(info).sync_filaments(info, progress=progress)
