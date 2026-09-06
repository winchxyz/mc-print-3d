"""Prusa printers running PrusaLink (MK4 / MK4S / MK3.9 / MK3.5 / MK3S+ / MINI / XL / Core One).

PrusaLink exposes both the legacy OctoPrint-compatible ``/api/...`` endpoints and the newer
``/api/v1/...`` ones.  Authentication is either an API key (``X-Api-Key``) or HTTP digest auth
with the printer's user name and password (older PrusaLink 0.7 on the MK3S Einsy board).

PrusaLink reports *no filament colors at all*, so :meth:`PrusaLinkBackend.sync_filaments`
always raises with an explanation.

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
    register_backend,
)

log = logging.getLogger(__name__)

try:  # optional dependency – reported through available()
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 5.0

#: X = left-right, Y = front-back, Z = build height (mm).
BED_TABLE: dict[str, tuple[float, float, float]] = {
    "MK4S": (250.0, 210.0, 220.0),
    "MK4": (250.0, 210.0, 220.0),
    "MK3.9": (250.0, 210.0, 220.0),
    "MK3.5": (250.0, 210.0, 220.0),
    "MK3S": (250.0, 210.0, 220.0),
    "MINI": (180.0, 180.0, 180.0),
    "XL": (360.0, 360.0, 360.0),
    "CORE ONE": (250.0, 220.0, 270.0),
}

#: Longest / most specific patterns first – 'MK4S' must win over 'MK4'.
_MODEL_PATTERNS: list[tuple[str, str]] = [
    (r"CORE\s*-?\s*ONE", "CORE ONE"),
    (r"\bMK\s*4\s*S\b", "MK4S"),
    (r"\bMK\s*3\.9\b", "MK3.9"),
    (r"\bMK\s*3\.5\b", "MK3.5"),
    (r"\bMK\s*3\s*S\b", "MK3S"),
    (r"\bMK\s*4\b", "MK4"),
    (r"\bMK\s*3\b", "MK3S"),
    (r"\bXL\b", "XL"),
    (r"\bMINI\b", "MINI"),
]


# ---------------------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------------------
def _request(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[dict] = None,
             auth: Any = None) -> tuple[int, dict, Any]:
    """Low level GET.  Returns ``(status_code, headers, parsed json or raw text)``."""
    if requests is None:  # pragma: no cover
        raise PrinterError("The 'requests' package is required to talk to PrusaLink.")
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or None, auth=auth)
    except Exception as exc:
        raise PrinterError(f"Cannot reach {url}: {exc}") from exc
    try:
        body: Any = resp.json()
    except Exception:
        body = getattr(resp, "text", "")
    return resp.status_code, dict(getattr(resp, "headers", {}) or {}), body


def _get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[dict] = None,
              auth: Any = None) -> Any:
    """GET ``url`` and return the decoded JSON body.  Raises :class:`PrinterError`."""
    status, _hdrs, body = _request(url, timeout=timeout, headers=headers, auth=auth)
    if status in (401, 403):
        raise PrinterError(f"{url} rejected the credentials (HTTP {status}). "
                           "Check the PrusaLink API key / password.")
    if status >= 400:
        raise PrinterError(f"{url} returned HTTP {status}")
    if not isinstance(body, (dict, list)):
        raise PrinterError(f"{url} did not return JSON")
    return body


def _get_json_optional(url: str, *, timeout: float = DEFAULT_TIMEOUT, headers: Optional[dict] = None,
                       auth: Any = None, default: Any = None) -> Any:
    try:
        return _get_json(url, timeout=timeout, headers=headers, auth=auth)
    except Exception as exc:
        log.debug("optional GET %s failed: %s", url, exc)
        return default


def _base_url(host: str, port: Optional[int]) -> str:
    port = int(port or DEFAULT_PORT)
    if port == 80:
        return f"http://{host}"
    return f"http://{host}:{port}"


def _auth_for(info: PrinterInfo) -> tuple[Optional[dict], Any]:
    """Return ``(headers, auth)``: API key first, digest auth as the fallback."""
    creds = info.credentials or {}
    key = creds.get("api_key") or ""
    if key:
        return {"X-Api-Key": str(key)}, None
    user = creds.get("username") or ""
    password = creds.get("password") or ""
    if user and password and requests is not None:
        try:
            return None, requests.auth.HTTPDigestAuth(str(user), str(password))
        except Exception:  # pragma: no cover
            log.debug("cannot build digest auth", exc_info=True)
    return None, None


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


def detect_model(*texts: Any) -> str:
    """Find a Prusa model name in any mix of firmware / hostname / product strings."""
    blob = " ".join(str(t) for t in texts if t).upper()
    for pattern, model in _MODEL_PATTERNS:
        if re.search(pattern, blob):
            return model
    return ""


# ---------------------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------------------
def probe(host: str, port: int = DEFAULT_PORT, timeout: float = 2.0) -> Optional[PrinterInfo]:
    """Identify a PrusaLink printer.  Returns ``None`` for anything else."""
    base = _base_url(host, port)
    try:
        status, headers, body = _request(f"{base}/api/version", timeout=timeout)
    except Exception as exc:
        log.debug("prusalink probe %s failed: %s", base, exc)
        return None
    text = str(body.get("text") or "") if isinstance(body, dict) else str(body or "")
    server_header = str(headers.get("Server") or headers.get("server") or "")
    blob = " ".join((text, server_header)).lower()
    if "prusalink" not in blob and "prusa-link" not in blob and "prusa" not in blob:
        return None
    version = body if isinstance(body, dict) else {}
    hostname = str(version.get("hostname") or "") or host
    model = detect_model(text, hostname, version.get("firmware"), version.get("printer_model"))
    info = PrinterInfo(
        id=f"prusalink:{host}:{int(port)}",
        kind=PrusaLinkBackend.kind,
        name=hostname,
        model=model,
        vendor="Prusa Research",
        host=host,
        port=int(port),
        firmware=str(version.get("firmware") or version.get("server") or text) or None,
    )
    info.raw = {"version": version, "needs_credentials": status in (401, 403)}
    return info


def _find_spec(name: str):
    """Look the printer up in the (optional, concurrently written) printer database."""
    if not name:
        return None
    try:
        from . import database
    except Exception:
        return None
    try:
        return database.find_spec(name)
    except Exception:
        log.debug("database.find_spec(%r) failed", name, exc_info=True)
        return None


# ---------------------------------------------------------------------------------------
# backend
# ---------------------------------------------------------------------------------------
class PrusaLinkBackend:
    """PrusaLink integration (MK4/MK4S/MK3.9/MK3.5/MK3S, MINI, XL, Core One)."""

    kind = "prusalink"
    display_name = "Prusa (PrusaLink)"

    def available(self) -> tuple[bool, str]:
        if requests is None:
            return False, "the 'requests' package is not installed"
        return True, ""

    def discover(self, timeout: float = 5.0, progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """PrusaLink printers are found by :mod:`mcprint.printers.discovery` (mDNS / subnet scan)."""
        return []

    # -- connect ------------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        if not info.host:
            raise PrinterError("This PrusaLink printer has no IP address or hostname.")
        ok, why = self.available()
        if not ok:
            raise PrinterError(f"Cannot talk to PrusaLink: {why}.")
        base = _base_url(info.host, info.port)
        headers, auth = _auth_for(info)
        timeout = DEFAULT_TIMEOUT
        out = copy.deepcopy(info)
        out.port = int(info.port or DEFAULT_PORT)
        out.kind = self.kind
        out.vendor = out.vendor or "Prusa Research"

        _say(progress, f"Querying PrusaLink at {base} ...")
        version = _get_json(f"{base}/api/version", timeout=timeout, headers=headers, auth=auth)
        if not isinstance(version, dict):
            raise PrinterError(f"{base}/api/version returned an unexpected answer.")

        api_info = _get_json_optional(f"{base}/api/v1/info", timeout=timeout, headers=headers,
                                      auth=auth, default=None)
        api_info = api_info if isinstance(api_info, dict) else {}
        status_doc = _get_json_optional(f"{base}/api/v1/status", timeout=timeout, headers=headers,
                                        auth=auth, default=None)
        status_doc = status_doc if isinstance(status_doc, dict) else {}
        legacy = _get_json_optional(f"{base}/api/printer", timeout=timeout, headers=headers,
                                    auth=auth, default=None)
        legacy = legacy if isinstance(legacy, dict) else {}

        hostname = str(api_info.get("hostname") or version.get("hostname") or "") or info.host
        out.name = str(api_info.get("name") or "") or info.name or hostname
        out.serial_number = str(api_info.get("serial") or "") or out.serial_number

        printer_state = status_doc.get("printer") if isinstance(status_doc.get("printer"), dict) else {}
        state = printer_state.get("state")
        if not state and isinstance(legacy.get("state"), dict):
            state = legacy["state"].get("text")
        if state:
            out.status = str(state)
        firmware = (version.get("firmware") or status_doc.get("firmware")
                    or api_info.get("firmware") or version.get("server") or version.get("text"))
        if firmware:
            out.firmware = str(firmware)

        nozzle = _num(api_info.get("nozzle_diameter")) or _num(version.get("nozzle_diameter"))
        if nozzle:
            out.nozzle_mm = nozzle

        model = detect_model(out.firmware, hostname, version.get("text"), out.name,
                             api_info.get("printer_model"), version.get("printer_model"), info.model)
        if model:
            out.model = model
        mmu = bool(api_info.get("mmu"))

        spec = _find_spec(model or out.model or hostname)
        if spec is not None:
            out.model = getattr(spec, "model", "") or out.model
            out.vendor = getattr(spec, "vendor", "") or out.vendor
            for attr in ("bed_x", "bed_y", "bed_z", "nozzle_mm", "extruders", "slots"):
                if getattr(out, attr, None) in (None, 0):
                    value = getattr(spec, attr, None)
                    if value:
                        setattr(out, attr, value)
        if not (out.bed_x and out.bed_y and out.bed_z):
            bed = BED_TABLE.get(model or "")
            if bed:
                out.bed_x, out.bed_y, out.bed_z = bed
        if not out.nozzle_mm:
            out.nozzle_mm = 0.4
        out.extruders = out.extruders or 1
        # What the printer itself reports wins over the catalog default (which assumes an MMU3).
        out.slots = 5 if (mmu or model == "XL") else 1
        out.online = str(out.status or "").upper() not in ("OFFLINE", "ERROR")
        out.raw = {"version": version, "info": api_info, "status": status_doc,
                   "legacy": legacy, "mmu": mmu, "detected_model": model}
        _say(progress, f"Connected to {out.name} ({out.bed_text}).")
        return out

    # -- filaments ----------------------------------------------------------------------
    def sync_filaments(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        raise PrinterError("PrusaLink does not report filament colors; import your PrusaSlicer "
                           "filament presets or add manually.")

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return [
            ("api_key", "PrusaLink API key", True),
            ("username", "Username (digest auth, older PrusaLink)", False),
            ("password", "Password", True),
        ]


BACKEND = PrusaLinkBackend()
register_backend(BACKEND)
