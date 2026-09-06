"""Common data types and backend protocol for printer detection and filament synchronization.

Every backend module in ``mcprint.printers`` implements :class:`PrinterBackend` and registers
itself in :data:`BACKENDS` (see :func:`register_backend`).  Backends must never raise on missing
optional dependencies at import time; they report the problem through ``available()`` instead.

Coordinate/size conventions: bed sizes are millimetres, X = left-right, Y = front-back,
Z = build height.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

ProgressFn = Callable[[str], None]


@dataclass
class FilamentSlot:
    """One loaded filament as reported by a printer (AMS tray, MMU gate, spool holder...)."""

    index: int                      # 0-based global slot index on the printer
    color_hex: str                  # '#RRGGBB'
    material: str = ""              # 'PLA', 'PETG', ...
    name: str = ""                  # 'Bambu PLA Basic', spool name, ...
    vendor: str = ""                # 'Bambu Lab', 'Polymaker', ...
    source: str = ""                # 'ams', 'spoolman', 'mmu', 'octoprint', 'manual', ...
    group: str = ""                 # e.g. 'AMS 1', 'External spool', 'CFS A'
    loaded: bool = True             # False if the slot exists but is empty
    remaining_pct: Optional[float] = None
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        parts = [p for p in (self.vendor, self.name or self.material) if p]
        base = " ".join(parts) if parts else "Filament"
        return f"{base} ({self.color_hex})"


@dataclass
class PrinterInfo:
    """Everything we know about one printer.  Serializable to JSON for saved profiles."""

    id: str                                   # unique key, e.g. 'bambu:SERIAL', 'serial:COM3', 'moonraker:192.168.1.5:7125'
    kind: str                                 # backend kind: 'bambu' | 'moonraker' | 'octoprint' | 'prusalink' | 'duet' | 'elegoo' | 'serial' | 'manual'
    name: str                                 # display name
    model: str = ""                           # 'Bambu Lab A2L', 'Prusa MK4S', 'Ender-3 V2', ...
    vendor: str = ""
    host: Optional[str] = None                # IP / hostname for network printers
    port: Optional[int] = None
    serial_port: Optional[str] = None         # 'COM3', '/dev/ttyUSB0'
    serial_number: Optional[str] = None
    bed_x: Optional[float] = None             # mm
    bed_y: Optional[float] = None
    bed_z: Optional[float] = None
    nozzle_mm: Optional[float] = None
    extruders: Optional[int] = None           # physical extruders / tool heads
    slots: Optional[int] = None               # total filament slots available for multi-color (AMS trays, MMU gates)
    filament_slots: list[FilamentSlot] = field(default_factory=list)
    firmware: Optional[str] = None
    status: Optional[str] = None              # 'idle', 'printing', 'offline', ...
    credentials: dict = field(default_factory=dict)   # api_key / access_code / username / password
    raw: dict = field(default_factory=dict)   # backend-specific details (not persisted)
    online: bool = True

    # -- serialization -----------------------------------------------------------------
    def to_dict(self, include_secrets: bool = True) -> dict:
        d = dataclasses.asdict(self)
        d.pop("raw", None)
        if not include_secrets:
            d.pop("credentials", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PrinterInfo":
        d = dict(d)
        slots = [FilamentSlot(**s) for s in d.pop("filament_slots", []) or []]
        known = {f.name for f in dataclasses.fields(cls)}
        clean = {k: v for k, v in d.items() if k in known}
        info = cls(**clean)
        info.filament_slots = slots
        return info

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @property
    def bed_text(self) -> str:
        if self.bed_x and self.bed_y and self.bed_z:
            return f"X {self.bed_x:g} x Y {self.bed_y:g} x Z {self.bed_z:g} mm"
        return "unknown bed size"

    @property
    def multi_color_slots(self) -> int:
        """How many distinct filaments this printer can use in a single print."""
        if self.slots:
            return self.slots
        if self.filament_slots:
            return len(self.filament_slots)
        if self.extruders:
            return self.extruders
        return 1


class PrinterBackend(Protocol):
    """Interface implemented by each printer integration."""

    kind: str          # 'bambu', 'moonraker', ...
    display_name: str  # 'Bambu Lab (LAN)', 'Klipper / Moonraker', ...

    def available(self) -> tuple[bool, str]:
        """(True, '') if optional dependencies are present, else (False, reason)."""
        ...

    def discover(self, timeout: float = 5.0, progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """Find printers on the LAN / USB without credentials.  Never raises; returns [] on failure."""
        ...

    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        """Query the printer for details (bed size, nozzle, firmware, status, loaded filaments).

        Uses ``info.credentials`` where needed.  Returns an updated copy.  Raises
        :class:`PrinterError` with a human readable message on failure.
        """
        ...

    def sync_filaments(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        """Return the filaments currently loaded (colors!) or raise :class:`PrinterError`."""
        ...

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        """List of (key, human label, is_secret) needed by connect()/sync_filaments()."""
        ...


class PrinterError(Exception):
    """Human readable printer communication error."""


BACKENDS: dict[str, PrinterBackend] = {}


def register_backend(backend: PrinterBackend) -> PrinterBackend:
    BACKENDS[backend.kind] = backend
    return backend


def get_backend(kind: str) -> Optional[PrinterBackend]:
    return BACKENDS.get(kind)


def normalize_hex(value: Any, default: str = "#808080") -> str:
    """Accepts 'RRGGBB', '#RRGGBB', 'RRGGBBAA' (Bambu AMS style), ints, or (r,g,b) tuples."""
    try:
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            r, g, b = (int(max(0, min(255, round(float(c))))) for c in value[:3])
            return f"#{r:02X}{g:02X}{b:02X}"
        if isinstance(value, int):
            return f"#{(value >> 16) & 255:02X}{(value >> 8) & 255:02X}{value & 255:02X}"
        if isinstance(value, str):
            s = value.strip().lstrip("#")
            if len(s) in (6, 8) and all(c in "0123456789abcdefABCDEF" for c in s):
                return "#" + s[:6].upper()
            if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
                return "#" + "".join(c * 2 for c in s).upper()
    except Exception:
        pass
    return default
