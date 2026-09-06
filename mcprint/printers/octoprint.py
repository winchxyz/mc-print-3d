"""OctoPrint servers (``/api/...``) – bed size, nozzle, state and spool plugins.

Filament colors come from one of the two common spool plugins:

* **SpoolManager** (``/plugin/SpoolManager/loadSpoolsByQuery``) – has real colors.
* **FilamentManager** (``/plugin/filamentmanager/spools``) – has no color field at all, so
  every spool is reported as neutral grey and the user recolors it in the app.

All HTTP access goes through the module level :func:`_get_json` so tests can monkeypatch it.
"""
from __future__ import annotations

import copy
import logging
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
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_PORT = 80
ALT_PORTS = (80, 5000)
DEFAULT_TIMEOUT = 5.0
GREY = "#808080"

SPOOLMANAGER_QUERY = ("/plugin/SpoolManager/loadSpoolsByQuery"
                      "?from=0&to=200&sortColumn=displayName&sortOrder=asc&filterName=all")


# ---------------------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------------------
def _request(url: str, *, timeout: float = DEFAULT_TIMEOUT,
             headers: Optional[dict] = None) -> tuple[int, dict, Any]:
    """Low level GET.  Returns ``(status_code, headers, parsed json or raw text)``."""
    if requests is None:  # pragma: no cover
        raise PrinterError("The 'requests' package is required to talk to OctoPrint.")
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or None)
    except Exception as exc:
        raise PrinterError(f"Cannot reach {url}: {exc}") from exc
    try:
        body: Any = resp.json()
    except Exception:
        body = getattr(resp, "text", "")
    return resp.status_code, dict(getattr(resp, "headers", {}) or {}), body


def _get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[dict] = None) -> Any:
    """GET ``url`` and return the decoded JSON body.  Raises :class:`PrinterError`."""
    status, _hdrs, body = _request(url, timeout=timeout, headers=headers)
    if status >= 400:
        if status in (401, 403):
            raise PrinterError(f"{url} rejected the API key (HTTP {status}).")
        raise PrinterError(f"{url} returned HTTP {status}")
    if not isinstance(body, (dict, list)):
        raise PrinterError(f"{url} did not return JSON")
    return body


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


def _headers(info: PrinterInfo) -> Optional[dict]:
    key = (info.credentials or {}).get("api_key") or ""
    return {"X-Api-Key": key} if key else None


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


# ---------------------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------------------
def probe(host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> Optional[PrinterInfo]:
    """Identify an OctoPrint server.  Returns ``None`` for anything else (incl. PrusaLink)."""
    base = _base_url(host, port)
    try:
        status, headers, body = _request(f"{base}/api/version", timeout=timeout)
    except Exception as exc:
        log.debug("octoprint probe %s failed: %s", base, exc)
        return None
    text = ""
    server = ""
    if isinstance(body, dict):
        text = str(body.get("text") or "")
        server = str(body.get("server") or "")
    server_header = str(headers.get("Server") or headers.get("server") or "")
    blob = " ".join((text, server_header, str(body) if isinstance(body, str) else "")).lower()
    if "prusalink" in blob or "prusa-link" in blob:
        return None  # PrusaLink also answers /api/version - it has its own backend
    if status in (401, 403):
        # authentication required: only claim the host if it announces itself as OctoPrint
        if "octoprint" not in blob:
            return None
    elif status >= 400 or not isinstance(body, dict):
        return None
    elif "octoprint" not in blob and "api" not in body:
        return None

    info = PrinterInfo(
        id=f"octoprint:{host}:{int(port)}",
        kind=OctoPrintBackend.kind,
        name=text or f"OctoPrint at {host}",
        host=host,
        port=int(port),
        firmware=text or server or None,
        status=None,
    )
    info.raw = {"version": body if isinstance(body, dict) else {"text": str(body)},
                "needs_api_key": status in (401, 403)}
    return info


# ---------------------------------------------------------------------------------------
# filament plugins
# ---------------------------------------------------------------------------------------
def _spoolmanager_slots(base: str, timeout: float, headers: Optional[dict],
                        start_index: int) -> list[FilamentSlot]:
    data = _get_json_optional(f"{base}{SPOOLMANAGER_QUERY}", timeout=timeout,
                              headers=headers, default=None)
    if not isinstance(data, dict):
        return []
    spools = data.get("allSpools")
    if not isinstance(spools, list):
        return []
    selected = data.get("selectedSpools")
    selected_ids = set()
    if isinstance(selected, list):
        for sel in selected:
            if isinstance(sel, dict) and sel.get("databaseId") is not None:
                selected_ids.add(sel.get("databaseId"))
    slots: list[FilamentSlot] = []
    for spool in spools:
        if not isinstance(spool, dict):
            continue
        pct = _num(spool.get("remainingPercentage"))
        if pct is None:
            remaining = _num(spool.get("remainingWeight"))
            total = _num(spool.get("totalWeight")) or _num(spool.get("weight"))
            if remaining is not None and total:
                pct = max(0.0, min(100.0, remaining / total * 100.0))
        active = bool(spool.get("isActive")) or spool.get("databaseId") in selected_ids
        slots.append(FilamentSlot(
            index=start_index + len(slots),
            color_hex=normalize_hex(spool.get("color"), GREY),
            material=str(spool.get("material") or ""),
            name=str(spool.get("displayName") or spool.get("colorName") or ""),
            vendor=str(spool.get("vendor") or ""),
            source="octoprint",
            group="SpoolManager",
            loaded=True,
            remaining_pct=pct,
            extra={
                "spool_id": spool.get("databaseId"),
                "color_name": spool.get("colorName"),
                "active": True if active else None,
                "plugin": "SpoolManager",
            },
        ))
    return slots


def _filamentmanager_slots(base: str, timeout: float, headers: Optional[dict],
                           start_index: int) -> list[FilamentSlot]:
    data = _get_json_optional(f"{base}/plugin/filamentmanager/spools", timeout=timeout,
                              headers=headers, default=None)
    if not isinstance(data, dict):
        return []
    spools = data.get("spools")
    if not isinstance(spools, list):
        return []
    active_ids = set()
    sel = _get_json_optional(f"{base}/plugin/filamentmanager/selections", timeout=timeout,
                             headers=headers, default=None)
    if isinstance(sel, dict) and isinstance(sel.get("selections"), list):
        for entry in sel["selections"]:
            spool = entry.get("spool") if isinstance(entry, dict) else None
            if isinstance(spool, dict) and spool.get("id") is not None:
                active_ids.add(spool.get("id"))
    slots: list[FilamentSlot] = []
    for spool in spools:
        if not isinstance(spool, dict):
            continue
        profile = spool.get("profile") if isinstance(spool.get("profile"), dict) else {}
        weight = _num(spool.get("weight"))
        used = _num(spool.get("used")) or 0.0
        pct = None
        if weight:
            pct = max(0.0, min(100.0, (weight - used) / weight * 100.0))
        slots.append(FilamentSlot(
            # FilamentManager has no color field at all - neutral grey, user recolors it.
            index=start_index + len(slots),
            color_hex=normalize_hex(spool.get("color"), GREY),
            material=str(profile.get("material") or ""),
            name=str(spool.get("name") or ""),
            vendor=str(profile.get("vendor") or ""),
            source="octoprint",
            group="FilamentManager",
            loaded=True,
            remaining_pct=pct,
            extra={
                "spool_id": spool.get("id"),
                "active": True if spool.get("id") in active_ids else None,
                "plugin": "FilamentManager",
                "no_color_from_plugin": True,
            },
        ))
    return slots


# ---------------------------------------------------------------------------------------
# backend
# ---------------------------------------------------------------------------------------
class OctoPrintBackend:
    """OctoPrint REST API integration."""

    kind = "octoprint"
    display_name = "OctoPrint"

    def available(self) -> tuple[bool, str]:
        if requests is None:
            return False, "the 'requests' package is not installed"
        return True, ""

    def discover(self, timeout: float = 5.0, progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """OctoPrint servers are found by :mod:`mcprint.printers.discovery` (mDNS / subnet scan)."""
        return []

    # -- connect ------------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        if not info.host:
            raise PrinterError("This OctoPrint server has no IP address or hostname.")
        ok, why = self.available()
        if not ok:
            raise PrinterError(f"Cannot talk to OctoPrint: {why}.")
        base = _base_url(info.host, info.port)
        headers = _headers(info)
        timeout = DEFAULT_TIMEOUT
        out = copy.deepcopy(info)
        out.port = int(info.port or DEFAULT_PORT)
        out.kind = self.kind

        _say(progress, f"Querying OctoPrint at {base} ...")
        version = _get_json(f"{base}/api/version", timeout=timeout, headers=headers)
        if not isinstance(version, dict):
            raise PrinterError(f"{base}/api/version returned an unexpected answer.")
        out.firmware = str(version.get("text") or version.get("server") or "") or out.firmware
        out.vendor = out.vendor or "OctoPrint"

        profiles = _get_json(f"{base}/api/printerprofiles", timeout=timeout, headers=headers)
        current = _current_profile(profiles)
        if current:
            volume = current.get("volume") if isinstance(current.get("volume"), dict) else {}
            bed_x = _num(volume.get("width"))
            bed_y = _num(volume.get("depth"))
            bed_z = _num(volume.get("height"))
            if bed_x:
                out.bed_x = bed_x
            if bed_y:
                out.bed_y = bed_y
            if bed_z:
                out.bed_z = bed_z
            extruder = current.get("extruder") if isinstance(current.get("extruder"), dict) else {}
            count = extruder.get("count")
            try:
                out.extruders = int(count) if count else (out.extruders or 1)
            except (TypeError, ValueError):
                out.extruders = out.extruders or 1
            nozzle = _num(extruder.get("nozzleDiameter"))
            if nozzle:
                out.nozzle_mm = nozzle
            out.model = str(current.get("model") or "") or out.model
            out.name = str(current.get("name") or "") or info.name or f"OctoPrint at {info.host}"
        if not out.name:
            out.name = info.name or f"OctoPrint at {info.host}"
        if not out.slots:
            out.slots = out.extruders or 1

        connection = _get_json_optional(f"{base}/api/connection", timeout=timeout,
                                        headers=headers, default=None)
        state = None
        if isinstance(connection, dict):
            cur = connection.get("current") if isinstance(connection.get("current"), dict) else {}
            state = cur.get("state")
        # /api/printer answers HTTP 409 when the printer is not connected -> offline
        printer = _get_json_optional(f"{base}/api/printer?exclude=temperature", timeout=timeout,
                                     headers=headers, default=None)
        if isinstance(printer, dict):
            pstate = printer.get("state") if isinstance(printer.get("state"), dict) else {}
            state = pstate.get("text") or state
        elif state is None:
            state = "offline"
        out.status = str(state) if state else out.status
        out.online = str(out.status or "").lower() not in ("offline", "closed")

        out.raw = {"version": version, "profile": current or {}, "connection": connection or {}}
        _say(progress, f"Connected to {out.name} ({out.bed_text}).")
        return out

    # -- filaments ----------------------------------------------------------------------
    def sync_filaments(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        if not info.host:
            raise PrinterError("This OctoPrint server has no IP address or hostname.")
        base = _base_url(info.host, info.port)
        headers = _headers(info)
        timeout = DEFAULT_TIMEOUT

        _say(progress, "Looking for the SpoolManager plugin ...")
        slots = _spoolmanager_slots(base, timeout, headers, 0)
        if not slots:
            _say(progress, "Looking for the FilamentManager plugin ...")
            slots = _filamentmanager_slots(base, timeout, headers, 0)
        if not slots:
            raise PrinterError(
                "No filament data from OctoPrint: install the SpoolManager or FilamentManager "
                "plugin (and check the API key), or add filaments manually.")
        _say(progress, f"Found {len(slots)} filament slot(s).")
        return slots

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return [("api_key", "OctoPrint API key", True)]


def _current_profile(profiles: Any) -> Optional[dict]:
    """Pick the ``current: true`` printer profile (falling back to ``default`` / the first)."""
    if not isinstance(profiles, dict):
        return None
    table = profiles.get("profiles")
    if not isinstance(table, dict):
        return None
    fallback = None
    for _key, profile in table.items():
        if not isinstance(profile, dict):
            continue
        if profile.get("current"):
            return profile
        if fallback is None or profile.get("default"):
            fallback = profile
    return fallback


BACKEND = OctoPrintBackend()
register_backend(BACKEND)
