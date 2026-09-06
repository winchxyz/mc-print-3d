"""Klipper printers driven through the Moonraker HTTP API.

Covers anything running Mainsail / Fluidd / KlipperScreen, plus the Creality K1 / K2 family
whose stock firmware also exposes Moonraker on port 7125.

Filament colors can come from three places (all optional, all concatenated in this order):

1. **Spoolman** – Moonraker's spool manager integration (``/server/spoolman/...``).
2. **Happy Hare** – the ``mmu`` Klipper object used by ERCF / Tradrack / Box Turtle / 3MS.
3. **Creality CFS** – the ``box*`` objects on a K2 Plus with a CFS unit attached.

Network discovery lives in :mod:`mcprint.printers.discovery`; this module only exposes
:func:`probe` so that discovery can identify a host.

All HTTP access goes through the module level :func:`_get_json` so tests can monkeypatch it.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Optional

from .base import (
    FilamentSlot,
    PrinterError,
    PrinterInfo,
    ProgressFn,
    normalize_hex,
    register_backend,
)

log = logging.getLogger(__name__)

try:  # optional dependency – reported through available()
    import requests
except Exception:  # pragma: no cover - requests is a hard dep of the project
    requests = None  # type: ignore[assignment]

DEFAULT_PORT = 7125
DEFAULT_TIMEOUT = 5.0

_EXTRUDER_RE = re.compile(r"^extruder\d*$")

#: Color names used by Happy Hare's ``gate_color`` (and by a few other firmwares).
COLOR_NAMES: dict[str, str] = {
    "red": "#FF0000",
    "green": "#008000",
    "blue": "#0000FF",
    "black": "#000000",
    "white": "#FFFFFF",
    "yellow": "#FFFF00",
    "orange": "#FFA500",
    "purple": "#800080",
    "violet": "#EE82EE",
    "pink": "#FFC0CB",
    "grey": "#808080",
    "gray": "#808080",
    "brown": "#A52A2A",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "lime": "#00FF00",
    "natural": "#F0E6D2",
    "clear": "#F0E6D2",
    "transparent": "#F0E6D2",
    "silver": "#C0C0C0",
    "gold": "#FFD700",
    "beige": "#F5F5DC",
    "olive": "#808000",
    "navy": "#000080",
    "teal": "#008080",
    "maroon": "#800000",
}

_UNSET = object()


# ---------------------------------------------------------------------------------------
# HTTP plumbing (single patch point for tests)
# ---------------------------------------------------------------------------------------
def _get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[dict] = None) -> Any:
    """GET ``url`` and return the decoded JSON body.  Raises :class:`PrinterError`."""
    if requests is None:  # pragma: no cover
        raise PrinterError("The 'requests' package is required to talk to Moonraker.")
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
    """Like :func:`_get_json` but returns ``default`` instead of raising."""
    try:
        return _get_json(url, timeout=timeout, headers=headers)
    except Exception as exc:
        log.debug("optional GET %s failed: %s", url, exc)
        return default


def _result(payload: Any) -> Any:
    """Moonraker wraps every REST answer in ``{"result": ...}``."""
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _base_url(host: str, port: Optional[int]) -> str:
    return f"http://{host}:{int(port or DEFAULT_PORT)}"


def _headers(info: PrinterInfo) -> Optional[dict]:
    key = (info.credentials or {}).get("api_key") or ""
    return {"X-Api-Key": key} if key else None


def _say(progress: Optional[ProgressFn], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:  # never let a UI callback break a sync
            log.debug("progress callback raised", exc_info=True)


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _color_from_name_or_hex(value: Any, default: str = "#808080") -> str:
    """Happy Hare gates hold either a color name ('red') or a hex string ('FF0000')."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        vals = [_num(v) or 0.0 for v in value[:3]]
        if all(0.0 <= v <= 1.0 for v in vals):  # Happy Hare's gate_color_rgb is 0..1
            vals = [v * 255.0 for v in vals]
        return normalize_hex(vals, default)
    if isinstance(value, str):
        key = value.strip().lower()
        if key in COLOR_NAMES:
            return COLOR_NAMES[key]
    return normalize_hex(value, default)


# ---------------------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------------------
def probe(host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> Optional[PrinterInfo]:
    """Identify a Moonraker instance at ``host:port``.  Returns ``None`` when it is not one."""
    base = _base_url(host, port)
    try:
        server = _result(_get_json(f"{base}/server/info", timeout=timeout))
    except Exception as exc:
        log.debug("moonraker probe %s failed: %s", base, exc)
        return None
    if not isinstance(server, dict):
        return None
    if "klippy_state" not in server and "klippy_connected" not in server:
        return None
    printer = _get_json_optional(f"{base}/printer/info", timeout=timeout, default=None)
    printer = _result(printer) if printer is not None else {}
    if not isinstance(printer, dict):
        printer = {}
    hostname = printer.get("hostname") or host
    info = PrinterInfo(
        id=f"moonraker:{host}:{int(port)}",
        kind=MoonrakerBackend.kind,
        name=str(hostname),
        host=host,
        port=int(port),
        firmware=printer.get("software_version") or server.get("moonraker_version"),
        status=printer.get("state") or server.get("klippy_state"),
    )
    info.raw = {"server_info": server, "printer_info": printer}
    return info


# ---------------------------------------------------------------------------------------
# object-model helpers
# ---------------------------------------------------------------------------------------
def _axis_size(settings: dict, axis: str) -> Optional[float]:
    stepper = settings.get(f"stepper_{axis}")
    if not isinstance(stepper, dict):
        return None
    pmax = _num(stepper.get("position_max"))
    if pmax is None:
        return None
    pmin = _num(stepper.get("position_min")) or 0.0
    size = pmax - pmin
    return size if size > 0 else None


def _bed_from_query(configfile: dict, toolhead: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    settings = configfile.get("settings") if isinstance(configfile, dict) else None
    settings = settings if isinstance(settings, dict) else {}
    bed = [_axis_size(settings, "x"), _axis_size(settings, "y"), _axis_size(settings, "z")]
    axis_max = toolhead.get("axis_maximum") if isinstance(toolhead, dict) else None
    if isinstance(axis_max, (list, tuple)):
        axis_min = toolhead.get("axis_minimum") if isinstance(toolhead, dict) else None
        for i in range(3):
            if bed[i] is None and i < len(axis_max):
                hi = _num(axis_max[i])
                lo = _num(axis_min[i]) if isinstance(axis_min, (list, tuple)) and i < len(axis_min) else 0.0
                if hi is not None:
                    size = hi - (lo or 0.0)
                    bed[i] = size if size > 0 else None
    return bed[0], bed[1], bed[2]


def _find_spec(name: str):
    """Look the printer up in the (optional, concurrently written) printer database."""
    if not name:
        return None
    try:
        from . import database  # local import: the module may not exist yet
    except Exception:
        return None
    try:
        return database.find_spec(name)
    except Exception:
        log.debug("database.find_spec(%r) failed", name, exc_info=True)
        return None


# ---------------------------------------------------------------------------------------
# filament sources
# ---------------------------------------------------------------------------------------
def _spoolman_url(base: str, info: PrinterInfo, timeout: float, headers: Optional[dict]) -> Optional[str]:
    override = (info.credentials or {}).get("spoolman_url")
    if override:
        return str(override).rstrip("/")
    status = _result(_get_json_optional(f"{base}/server/spoolman/status", timeout=timeout,
                                        headers=headers, default=None))
    if not isinstance(status, dict):
        return None
    if not status.get("spoolman_connected", False):
        return None
    url = status.get("spoolman_url")
    if not url:
        cfg = _result(_get_json_optional(f"{base}/server/config", timeout=timeout,
                                         headers=headers, default=None))
        if isinstance(cfg, dict):
            conf = cfg.get("config") if isinstance(cfg.get("config"), dict) else cfg
            spool_cfg = conf.get("spoolman") if isinstance(conf, dict) else None
            if isinstance(spool_cfg, dict):
                url = spool_cfg.get("server") or spool_cfg.get("url")
    return str(url).rstrip("/") if url else None


def _spoolman_slots(base: str, info: PrinterInfo, timeout: float, headers: Optional[dict],
                    start_index: int) -> list[FilamentSlot]:
    url = _spoolman_url(base, info, timeout, headers)
    if not url:
        return []
    spools = _get_json_optional(f"{url}/api/v1/spool", timeout=timeout, default=None)
    if isinstance(spools, dict):  # some deployments wrap the list
        spools = spools.get("items") or spools.get("spools") or []
    if not isinstance(spools, list):
        return []
    active_id = None
    active = _result(_get_json_optional(f"{base}/server/spoolman/spool_id", timeout=timeout,
                                        headers=headers, default=None))
    if isinstance(active, dict):
        active_id = active.get("spool_id")
    elif isinstance(active, int):
        active_id = active

    slots: list[FilamentSlot] = []
    for spool in spools:
        if not isinstance(spool, dict) or spool.get("archived"):
            continue
        filament = spool.get("filament") if isinstance(spool.get("filament"), dict) else {}
        color = filament.get("color_hex")
        if not color:
            multi = filament.get("multi_color_hexes")
            if isinstance(multi, str):
                color = multi.split(",")[0]
            elif isinstance(multi, (list, tuple)) and multi:
                color = multi[0]
        vendor = filament.get("vendor")
        vendor_name = vendor.get("name") if isinstance(vendor, dict) else (vendor or "")
        remaining = _num(spool.get("remaining_weight"))
        initial = _num(spool.get("initial_weight")) or _num(filament.get("weight"))
        pct = None
        if remaining is not None and initial:
            pct = max(0.0, min(100.0, remaining / initial * 100.0))
        slot = FilamentSlot(
            index=start_index + len(slots),
            color_hex=normalize_hex(color),
            material=str(filament.get("material") or ""),
            name=str(filament.get("name") or ""),
            vendor=str(vendor_name or ""),
            source="spoolman",
            group="Spoolman",
            loaded=True,
            remaining_pct=pct,
            extra={"spool_id": spool.get("id"), "location": spool.get("location")},
        )
        if active_id is not None and spool.get("id") == active_id:
            slot.extra["active"] = True
        slots.append(slot)
    return slots


def _mmu_slots(base: str, objects: list[str], timeout: float, headers: Optional[dict],
               start_index: int) -> list[FilamentSlot]:
    """Happy Hare (ERCF / Tradrack / Box Turtle / 3MS ...) gate table."""
    if "mmu" not in objects:
        return []
    payload = _get_json_optional(f"{base}/printer/objects/query?mmu", timeout=timeout,
                                 headers=headers, default=None)
    result = _result(payload) or {}
    status = result.get("status") if isinstance(result, dict) else None
    mmu = status.get("mmu") if isinstance(status, dict) else None
    if not isinstance(mmu, dict):
        return []
    colors = mmu.get("gate_color") or mmu.get("gate_color_rgb") or []
    materials = mmu.get("gate_material") or []
    states = mmu.get("gate_status") or []
    spool_ids = mmu.get("gate_spool_id") or []
    names = mmu.get("gate_filament_name") or []
    speeds = mmu.get("gate_speed_override") or []
    tables = [x for x in (colors, materials, states, spool_ids, names) if isinstance(x, (list, tuple))]
    count = max((len(x) for x in tables), default=0)
    if not count:
        return []

    def at(seq: Any, i: int, default: Any = None) -> Any:
        if isinstance(seq, (list, tuple)) and i < len(seq):
            return seq[i]
        return default

    slots: list[FilamentSlot] = []
    for gate in range(count):
        state = at(states, gate, -1)
        try:
            state_i = int(state)
        except (TypeError, ValueError):
            state_i = -1
        spool_id = at(spool_ids, gate, -1)
        slots.append(FilamentSlot(
            index=start_index + gate,
            color_hex=_color_from_name_or_hex(at(colors, gate)),
            material=str(at(materials, gate, "") or ""),
            name=str(at(names, gate, "") or ""),
            vendor="",
            source="mmu",
            group="MMU",
            loaded=state_i != 0,
            extra={
                "gate": gate,
                "gate_status": state_i,
                "spool_id": spool_id if spool_id not in (-1, None) else None,
                "speed_override": at(speeds, gate),
            },
        ))
    return slots


def _iter_trays(node: Any):
    """Yield dicts that look like a filament tray (they carry 'color' and/or 'material')."""
    if isinstance(node, dict):
        if "color" in node or "material" in node:
            yield node
            return
        for value in node.values():
            yield from _iter_trays(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_trays(value)


def _cfs_slots(base: str, objects: list[str], timeout: float, headers: Optional[dict],
               start_index: int) -> list[FilamentSlot]:
    """Creality K2 Plus CFS ('box' objects).  The payload shape varies, so stay tolerant."""
    boxes = [o for o in objects if str(o).lower().startswith("box")]
    if not boxes:
        return []
    query = "&".join(str(o).replace(" ", "%20") for o in boxes)
    payload = _get_json_optional(f"{base}/printer/objects/query?{query}", timeout=timeout,
                                 headers=headers, default=None)
    result = _result(payload) or {}
    status = result.get("status") if isinstance(result, dict) else None
    if not isinstance(status, dict):
        return []
    slots: list[FilamentSlot] = []
    for box_name in boxes:
        box = status.get(box_name)
        for tray in _iter_trays(box):
            color = tray.get("color")
            state = tray.get("state", tray.get("status"))
            loaded = True
            if isinstance(state, (int, float)) and not isinstance(state, bool):
                loaded = int(state) != 0
            elif isinstance(state, str):
                loaded = state.strip().lower() not in ("", "empty", "none", "0")
            if color is None and not tray.get("material"):
                continue
            slots.append(FilamentSlot(
                index=start_index + len(slots),
                color_hex=_color_from_name_or_hex(color),
                material=str(tray.get("material") or tray.get("type") or ""),
                name=str(tray.get("name") or tray.get("filament_name") or ""),
                vendor=str(tray.get("vendor") or tray.get("brand") or ""),
                source="cfs",
                group=f"CFS {box_name}" if len(boxes) > 1 else "CFS",
                loaded=loaded,
                extra={"box": box_name, "raw": tray},
            ))
    return slots


# ---------------------------------------------------------------------------------------
# backend
# ---------------------------------------------------------------------------------------
class MoonrakerBackend:
    """Klipper / Moonraker integration."""

    kind = "moonraker"
    display_name = "Klipper / Moonraker"

    def available(self) -> tuple[bool, str]:
        if requests is None:
            return False, "the 'requests' package is not installed"
        return True, ""

    def discover(self, timeout: float = 5.0, progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """Moonraker printers are found by :mod:`mcprint.printers.discovery` (mDNS / subnet scan)."""
        return []

    # -- connect ------------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        if not info.host:
            raise PrinterError("This Moonraker printer has no IP address or hostname.")
        ok, why = self.available()
        if not ok:
            raise PrinterError(f"Cannot talk to Moonraker: {why}.")
        base = _base_url(info.host, info.port)
        headers = _headers(info)
        timeout = DEFAULT_TIMEOUT
        out = copy.deepcopy(info)
        out.port = int(info.port or DEFAULT_PORT)
        out.kind = self.kind

        _say(progress, f"Querying Moonraker at {base} ...")
        printer = _result(_get_json(f"{base}/printer/info", timeout=timeout, headers=headers))
        if not isinstance(printer, dict):
            raise PrinterError(f"{base}/printer/info returned an unexpected answer.")
        hostname = str(printer.get("hostname") or info.host)
        out.name = info.name or hostname
        out.firmware = printer.get("software_version") or out.firmware
        out.status = printer.get("state") or out.status
        out.online = True

        objects = _result(_get_json_optional(f"{base}/printer/objects/list", timeout=timeout,
                                             headers=headers, default=None)) or {}
        obj_names = objects.get("objects") if isinstance(objects, dict) else objects
        obj_names = [str(o) for o in obj_names] if isinstance(obj_names, (list, tuple)) else []

        _say(progress, "Reading the Klipper configuration ...")
        query = _result(_get_json(
            f"{base}/printer/objects/query?configfile&toolhead&extruder&print_stats",
            timeout=timeout, headers=headers))
        status = query.get("status") if isinstance(query, dict) else None
        status = status if isinstance(status, dict) else {}
        configfile = status.get("configfile") if isinstance(status.get("configfile"), dict) else {}
        toolhead = status.get("toolhead") if isinstance(status.get("toolhead"), dict) else {}
        print_stats = status.get("print_stats") if isinstance(status.get("print_stats"), dict) else {}
        settings = configfile.get("settings") if isinstance(configfile.get("settings"), dict) else {}

        bed_x, bed_y, bed_z = _bed_from_query(configfile, toolhead)
        if bed_x:
            out.bed_x = bed_x
        if bed_y:
            out.bed_y = bed_y
        if bed_z:
            out.bed_z = bed_z

        extruder_cfg = settings.get("extruder") if isinstance(settings.get("extruder"), dict) else {}
        nozzle = _num(extruder_cfg.get("nozzle_diameter"))
        if nozzle:
            out.nozzle_mm = nozzle

        count = len([o for o in obj_names if _EXTRUDER_RE.match(o)])
        if not count:
            count = len([k for k in settings if _EXTRUDER_RE.match(str(k))])
        out.extruders = count or out.extruders or 1

        state = print_stats.get("state")
        if state:
            out.status = str(state)

        # Model: the kinematics is not a model name - use the hostname / MCU instead.
        spec = _find_spec(hostname) or _find_spec(info.model or "")
        if spec is not None:
            out.model = out.model or getattr(spec, "model", "") or ""
            out.vendor = out.vendor or getattr(spec, "vendor", "") or ""
            for attr in ("bed_x", "bed_y", "bed_z", "nozzle_mm", "extruders", "slots"):
                if getattr(out, attr, None) in (None, 0):
                    value = getattr(spec, attr, None)
                    if value:
                        setattr(out, attr, value)
        mcu = settings.get("mcu") if isinstance(settings.get("mcu"), dict) else {}
        if not out.model:
            out.model = str(printer.get("app_name") or "").strip()
        if not out.slots:
            out.slots = out.extruders

        out.raw = {
            "printer_info": printer,
            "objects": obj_names,
            "status": status,
            "mcu": mcu,
        }
        _say(progress, f"Connected to {out.name} ({out.bed_text}).")
        return out

    # -- filaments ----------------------------------------------------------------------
    def sync_filaments(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        if not info.host:
            raise PrinterError("This Moonraker printer has no IP address or hostname.")
        base = _base_url(info.host, info.port)
        headers = _headers(info)
        timeout = DEFAULT_TIMEOUT
        slots: list[FilamentSlot] = []

        _say(progress, "Looking for Spoolman ...")
        try:
            slots.extend(_spoolman_slots(base, info, timeout, headers, len(slots)))
        except Exception:
            log.debug("spoolman lookup failed", exc_info=True)

        objects = _result(_get_json_optional(f"{base}/printer/objects/list", timeout=timeout,
                                             headers=headers, default=None)) or {}
        obj_names = objects.get("objects") if isinstance(objects, dict) else objects
        obj_names = [str(o) for o in obj_names] if isinstance(obj_names, (list, tuple)) else []

        _say(progress, "Looking for an MMU ...")
        try:
            slots.extend(_mmu_slots(base, obj_names, timeout, headers, len(slots)))
        except Exception:
            log.debug("happy hare lookup failed", exc_info=True)

        try:
            slots.extend(_cfs_slots(base, obj_names, timeout, headers, len(slots)))
        except Exception:
            log.debug("CFS lookup failed", exc_info=True)

        if not slots:
            raise PrinterError("No filament data: connect Spoolman or an MMU, or add filaments manually.")
        _say(progress, f"Found {len(slots)} filament slot(s).")
        return slots

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return [
            ("api_key", "Moonraker API key (only if authentication is enabled)", True),
            ("spoolman_url", "Spoolman URL (optional override)", False),
        ]


BACKEND = MoonrakerBackend()
register_backend(BACKEND)
