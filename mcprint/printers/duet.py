"""Duet boards running RepRapFirmware (Duet 2 / Duet 3, Duet Web Control).

Two flavours are supported transparently:

* **standalone** RRF3 – the object model is read with ``/rr_model?flags=d99fn``;
* **SBC mode** (DuetPi / DSF) – the same model comes from ``/machine/status``.

RepRapFirmware knows the *name* of the filament loaded in each tool but never its color, so
:meth:`DuetBackend.sync_filaments` returns neutral grey slots that the user recolors.

All HTTP access goes through the module level :func:`_get_json` so tests can monkeypatch it.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Optional
from urllib.parse import quote

from .base import (
    FilamentSlot,
    PrinterError,
    PrinterInfo,
    ProgressFn,
    register_backend,
)

log = logging.getLogger(__name__)

try:  # optional dependency – reported through available()
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 5.0
GREY = "#808080"

#: RRF status letters (older firmware) mapped to words.
_STATUS_WORDS = {
    "I": "idle", "P": "printing", "S": "paused", "B": "busy", "D": "decelerating",
    "R": "resuming", "H": "halted", "F": "flashing", "C": "configuring", "A": "paused",
    "M": "simulating", "T": "changing tool", "O": "off",
}

#: Used to guess a material out of a free-form RRF filament name ('Prusament PETG' -> 'PETG').
_MATERIALS = ("PLA", "PETG", "PCTG", "ABS", "ASA", "TPU", "TPE", "PVA", "HIPS", "PC", "PA",
              "NYLON", "PP", "PEEK", "PEI")


def _material_of(filament_name: str) -> str:
    for token in re.split(r"[\s_\-+/]+", filament_name.upper()):
        if token in _MATERIALS:
            return token
    return ""


# ---------------------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------------------
def _get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[dict] = None) -> Any:
    """GET ``url`` and return the decoded JSON body.  Raises :class:`PrinterError`."""
    if requests is None:  # pragma: no cover
        raise PrinterError("The 'requests' package is required to talk to a Duet.")
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or None)
    except Exception as exc:
        raise PrinterError(f"Cannot reach {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise PrinterError(f"{url} returned HTTP {resp.status_code}")
    try:
        return resp.json()
    except Exception as exc:
        raise PrinterError(f"{url} did not return JSON") from exc


def _get_json_optional(url: str, *, timeout: float = DEFAULT_TIMEOUT,
                       headers: Optional[dict] = None, default: Any = None) -> Any:
    try:
        return _get_json(url, timeout=timeout, headers=headers)
    except Exception as exc:
        log.debug("optional GET %s failed: %s", url, exc)
        return default


def _base_url(host: str, port: Optional[int]) -> str:
    port = int(port or DEFAULT_PORT)
    if port == 80:
        return f"http://{host}"
    return f"http://{host}:{port}"


def _say(progress: Optional[ProgressFn], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:
            log.debug("progress callback raised", exc_info=True)


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_object_model(data: Any) -> bool:
    return isinstance(data, dict) and any(k in data for k in ("boards", "move", "state", "network"))


def fetch_object_model(base: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    """Read the RRF object model, standalone first, then SBC mode.  ``None`` if neither answers."""
    payload = _get_json_optional(f"{base}/rr_model?flags=d99fn", timeout=timeout, default=None)
    model = payload.get("result") if isinstance(payload, dict) and "result" in payload else payload
    if _is_object_model(model):
        return model  # type: ignore[return-value]
    payload = _get_json_optional(f"{base}/machine/status", timeout=timeout, default=None)
    model = payload.get("result") if isinstance(payload, dict) and "result" in payload else payload
    if _is_object_model(model):
        return model  # type: ignore[return-value]
    return None


def _connect_session(base: str, info: PrinterInfo, timeout: float) -> None:
    """RRF standalone wants ``/rr_connect`` before anything else when a password is set."""
    password = (info.credentials or {}).get("password") or ""
    if not password:
        return
    _get_json_optional(f"{base}/rr_connect?password={quote(str(password), safe='')}",
                       timeout=timeout, default=None)


def _axes_sizes(model: dict) -> dict[str, float]:
    """``{'X': 230.0, 'Y': 210.0, 'Z': 200.0}`` from ``move.axes[]`` (max - min)."""
    move = model.get("move") if isinstance(model.get("move"), dict) else {}
    axes = move.get("axes") if isinstance(move.get("axes"), list) else []
    out: dict[str, float] = {}
    for axis in axes:
        if not isinstance(axis, dict):
            continue
        letter = str(axis.get("letter") or "").upper()
        if letter not in ("X", "Y", "Z"):
            continue
        hi = _num(axis.get("max"))
        lo = _num(axis.get("min")) or 0.0
        if hi is None:
            continue
        size = hi - lo
        if size > 0:
            out[letter] = size
    return out


def _status_word(model: dict) -> Optional[str]:
    state = model.get("state") if isinstance(model.get("state"), dict) else {}
    status = state.get("status")
    if isinstance(status, str):
        return _STATUS_WORDS.get(status, status) if len(status) == 1 else status
    return None


# ---------------------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------------------
def probe(host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> Optional[PrinterInfo]:
    """Identify a Duet / RepRapFirmware controller.  Returns ``None`` for anything else."""
    base = _base_url(host, port)
    try:
        model = fetch_object_model(base, timeout=timeout)
    except Exception as exc:
        log.debug("duet probe %s failed: %s", base, exc)
        return None
    if not model:
        return None
    network = model.get("network") if isinstance(model.get("network"), dict) else {}
    boards = model.get("boards") if isinstance(model.get("boards"), list) else []
    board = boards[0] if boards and isinstance(boards[0], dict) else {}
    name = str(network.get("name") or board.get("name") or f"Duet at {host}")
    firmware = " ".join(str(x) for x in (board.get("firmwareName"), board.get("firmwareVersion")) if x)
    info = PrinterInfo(
        id=f"duet:{host}:{int(port)}",
        kind=DuetBackend.kind,
        name=name,
        model=str(board.get("name") or ""),
        vendor="Duet3D",
        host=host,
        port=int(port),
        firmware=firmware or None,
        status=_status_word(model),
    )
    info.raw = {"board": board, "network": network}
    return info


# ---------------------------------------------------------------------------------------
# backend
# ---------------------------------------------------------------------------------------
class DuetBackend:
    """RepRapFirmware / Duet Web Control integration."""

    kind = "duet"
    display_name = "Duet / RepRapFirmware"

    def available(self) -> tuple[bool, str]:
        if requests is None:
            return False, "the 'requests' package is not installed"
        return True, ""

    def discover(self, timeout: float = 5.0, progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """Duet boards are found by :mod:`mcprint.printers.discovery` (mDNS / subnet scan)."""
        return []

    # -- connect ------------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        if not info.host:
            raise PrinterError("This Duet has no IP address or hostname.")
        ok, why = self.available()
        if not ok:
            raise PrinterError(f"Cannot talk to the Duet: {why}.")
        base = _base_url(info.host, info.port)
        timeout = DEFAULT_TIMEOUT
        out = copy.deepcopy(info)
        out.port = int(info.port or DEFAULT_PORT)
        out.kind = self.kind
        out.vendor = out.vendor or "Duet3D"

        _say(progress, f"Querying the Duet at {base} ...")
        _connect_session(base, info, timeout)
        model = fetch_object_model(base, timeout=timeout)
        if not model:
            raise PrinterError(f"No RepRapFirmware object model at {base} "
                               "(wrong address, or a password is required).")

        sizes = _axes_sizes(model)
        if sizes.get("X"):
            out.bed_x = sizes["X"]
        if sizes.get("Y"):
            out.bed_y = sizes["Y"]
        if sizes.get("Z"):
            out.bed_z = sizes["Z"]

        tools = model.get("tools") if isinstance(model.get("tools"), list) else []
        tools = [t for t in tools if isinstance(t, dict)]
        if tools:
            out.extruders = len(tools)
            out.slots = len(tools)
        else:
            out.extruders = out.extruders or 1
            out.slots = out.slots or 1

        boards = model.get("boards") if isinstance(model.get("boards"), list) else []
        board = boards[0] if boards and isinstance(boards[0], dict) else {}
        firmware = " ".join(str(x) for x in (board.get("firmwareName"), board.get("firmwareVersion")) if x)
        if firmware:
            out.firmware = firmware
        if board.get("name"):
            out.model = str(board["name"])

        network = model.get("network") if isinstance(model.get("network"), dict) else {}
        if network.get("name"):
            out.name = str(network["name"])
        if not out.name:
            out.name = info.name or f"Duet at {info.host}"

        status = _status_word(model)
        if status:
            out.status = status
        out.online = str(out.status or "").lower() not in ("off", "offline", "halted")
        out.raw = {"board": board, "network": network, "tools": tools,
                   "state": model.get("state") if isinstance(model.get("state"), dict) else {}}
        _say(progress, f"Connected to {out.name} ({out.bed_text}).")
        return out

    # -- filaments ----------------------------------------------------------------------
    def sync_filaments(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        if not info.host:
            raise PrinterError("This Duet has no IP address or hostname.")
        base = _base_url(info.host, info.port)
        timeout = DEFAULT_TIMEOUT
        _connect_session(base, info, timeout)
        model = fetch_object_model(base, timeout=timeout)
        if not model:
            raise PrinterError(f"No RepRapFirmware object model at {base} "
                               "(wrong address, or a password is required).")
        tools = model.get("tools") if isinstance(model.get("tools"), list) else []
        slots: list[FilamentSlot] = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            filament = str(tool.get("filament") or "").strip()
            if not filament:
                continue
            slots.append(FilamentSlot(
                # RepRapFirmware has no color for a filament - neutral grey, the user recolors it.
                index=len(slots),
                color_hex=GREY,
                material=_material_of(filament),
                name=filament,
                vendor="",
                source="duet",
                group="Tools",
                loaded=True,
                extra={"tool": tool.get("number", index), "tool_name": tool.get("name"),
                       "no_color_from_firmware": True},
            ))
        if not slots:
            raise PrinterError("No filament loaded in any Duet tool "
                               "(RepRapFirmware reports filament names only, never colors).")
        _say(progress, f"Found {len(slots)} filament slot(s).")
        return slots

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return [("password", "Duet password (if set)", True)]


BACKEND = DuetBackend()
register_backend(BACKEND)
