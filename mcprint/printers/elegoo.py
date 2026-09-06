"""Elegoo printers speaking SDCP (Centauri, Centauri Carbon, Neptune 4 with SDCP firmware).

Discovery is a single UDP broadcast of the literal string ``M99999`` to port 3000; every SDCP
capable machine answers with a small JSON document describing itself::

    {"Id": "...", "Data": {"Name": "...", "MachineName": "Centauri Carbon",
                           "BrandName": "Elegoo", "MainboardIP": "192.168.1.50",
                           "MainboardID": "...", "ProtocolVersion": "V3.0.0",
                           "FirmwareVersion": "V1.1.25"}}

SDCP's websocket API (port 3030) reports print progress and temperatures but *not* the colour of
the loaded filament, so :meth:`ElegooBackend.sync_filaments` is a dead end by design.
"""
from __future__ import annotations

import json
import logging
import socket
import time
from typing import Optional

from .base import (
    FilamentSlot,
    PrinterError,
    PrinterInfo,
    ProgressFn,
    register_backend,
)

log = logging.getLogger(__name__)

SDCP_DISCOVERY_PORT = 3000       # where the 'M99999' probe goes
SDCP_SERVICE_PORT = 3030         # websocket/HTTP service port on the printer
SDCP_PROBE = b"M99999"

#: Model name -> bed size (X, Y, Z mm).  Used when the printer database is unavailable.
BED_FALLBACK: dict[str, tuple[float, float, float]] = {
    "Centauri Carbon": (256.0, 256.0, 256.0),
    "Centauri": (256.0, 256.0, 256.0),
    "Neptune 4": (225.0, 225.0, 265.0),
    "Neptune 4 Pro": (225.0, 225.0, 265.0),
    "Neptune 4 Plus": (320.0, 320.0, 385.0),
    "Neptune 4 Max": (420.0, 420.0, 480.0),
}


def _find_spec(name: str):
    """Fuzzy printer-database lookup; ``None`` when the database module is missing."""
    if not name:
        return None
    try:
        from . import database  # local import: the module may not exist yet
    except Exception:  # pragma: no cover - depends on a sibling module
        return None
    try:
        return database.find_spec(name)
    except Exception:  # pragma: no cover - defensive
        log.debug("database.find_spec(%r) failed", name, exc_info=True)
        return None


def _bed_for(model: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Bed size for an Elegoo model name, from the database or the built-in fallback."""
    spec = _find_spec(model)
    if spec is not None:
        return (getattr(spec, "bed_x", None), getattr(spec, "bed_y", None),
                getattr(spec, "bed_z", None))
    bed = BED_FALLBACK.get(model)
    if bed is None:
        key = (model or "").strip().lower()
        for name, value in BED_FALLBACK.items():
            if name.lower() == key:
                bed = value
                break
    return bed if bed else (None, None, None)


def _report(progress: Optional[ProgressFn], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:  # pragma: no cover - a UI callback must never break discovery
            log.debug("progress callback raised", exc_info=True)


def parse_sdcp_reply(payload: str, fallback_host: Optional[str] = None) -> Optional[PrinterInfo]:
    """Turn one JSON SDCP discovery reply into a :class:`PrinterInfo` (``None`` if unusable)."""
    try:
        message = json.loads(payload)
    except Exception:
        return None
    if not isinstance(message, dict):
        return None
    data = message.get("Data")
    if not isinstance(data, dict):
        return None

    board_id = str(data.get("MainboardID") or message.get("Id") or "").strip()
    host = str(data.get("MainboardIP") or fallback_host or "").strip() or None
    model = str(data.get("MachineName") or "").strip()
    name = str(data.get("Name") or "").strip() or model or host or "Elegoo printer"
    vendor = str(data.get("BrandName") or "").strip() or "Elegoo"
    firmware = str(data.get("FirmwareVersion") or "").strip() or None
    if not board_id and not host:
        return None

    bed_x, bed_y, bed_z = _bed_for(model)
    return PrinterInfo(
        id="elegoo:%s" % (board_id or host),
        kind="elegoo",
        name=name,
        model=model,
        vendor=vendor,
        host=host,
        port=SDCP_SERVICE_PORT,
        serial_number=board_id or None,
        bed_x=bed_x,
        bed_y=bed_y,
        bed_z=bed_z,
        firmware=firmware,
        raw={"sdcp": message},
        online=True,
    )


class ElegooBackend:
    """Elegoo SDCP printers found by UDP broadcast."""

    kind = "elegoo"
    display_name = "Elegoo (SDCP)"

    # -- capability -------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        return True, ""

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return []

    # -- discovery --------------------------------------------------------------------
    def discover(self, timeout: float = 5.0,
                 progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """Broadcast ``M99999`` on UDP 3000 and collect the JSON answers."""
        found: dict[str, PrinterInfo] = {}
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        except OSError:
            log.debug("could not create the SDCP socket", exc_info=True)
            return []

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", 0))
            sock.settimeout(0.4)
        except OSError:
            log.debug("could not configure the SDCP socket", exc_info=True)
            sock.close()
            return []

        _report(progress, "Searching for Elegoo printers (SDCP)...")
        deadline = time.time() + max(0.5, float(timeout))
        last_probe = 0.0
        try:
            while time.time() < deadline:
                if time.time() - last_probe > 1.5:
                    for target in ("255.255.255.255", "<broadcast>"):
                        try:
                            sock.sendto(SDCP_PROBE, (target, SDCP_DISCOVERY_PORT))
                            break
                        except OSError:
                            continue
                    last_probe = time.time()
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                info = parse_sdcp_reply(data.decode("utf-8", "replace"),
                                       addr[0] if addr else None)
                if info is None:
                    continue
                key = info.serial_number or info.host or info.id
                if key in found:
                    continue
                found[key] = info
                _report(progress, "Found %s (%s) at %s"
                        % (info.name, info.model or "Elegoo", info.host))
        finally:
            sock.close()
        return list(found.values())

    # -- live query -------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        """Refresh what discovery can tell us; SDCP needs no credentials.

        A short re-discovery is run so an existing profile picks up a new IP address or firmware
        version.  When the printer does not answer the info is returned unchanged and marked
        offline rather than raising - the user may simply have it powered off.
        """
        import copy

        result = copy.deepcopy(info)
        try:
            for fresh in self.discover(timeout=3.0, progress=progress):
                same = (fresh.serial_number and fresh.serial_number == info.serial_number) or \
                       (fresh.host and fresh.host == info.host)
                if not same:
                    continue
                result.host = fresh.host or result.host
                result.port = fresh.port or result.port
                result.model = fresh.model or result.model
                result.vendor = fresh.vendor or result.vendor
                result.firmware = fresh.firmware or result.firmware
                result.bed_x = fresh.bed_x if fresh.bed_x is not None else result.bed_x
                result.bed_y = fresh.bed_y if fresh.bed_y is not None else result.bed_y
                result.bed_z = fresh.bed_z if fresh.bed_z is not None else result.bed_z
                result.raw = dict(result.raw or {})
                result.raw.update(fresh.raw or {})
                result.online = True
                return result
        except Exception:  # pragma: no cover - discovery is already defensive
            log.debug("Elegoo re-discovery failed", exc_info=True)
        result.online = bool(result.host)
        return result

    def sync_filaments(self, info: PrinterInfo,
                       progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        raise PrinterError(
            "Elegoo SDCP does not expose loaded filament colors; add them manually or import "
            "from your slicer."
        )


BACKEND = ElegooBackend()
register_backend(BACKEND)
