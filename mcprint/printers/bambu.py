"""Bambu Lab printers on the local network (LAN mode).

Two protocols are used:

* **SSDP** for discovery.  Bambu machines periodically ``NOTIFY`` the multicast group
  ``239.255.255.250:2021`` and answer ``M-SEARCH`` requests sent to port 1990/2021.  The
  interesting data lives in vendor headers (``DevName.bambu.net`` and friends).
* **MQTT over TLS** on port 8883 for the live machine report (``device/<serial>/report``).
  The printer presents a self-signed certificate, so verification is disabled; the credentials
  are the fixed user ``bblp`` plus the LAN access code shown on the printer screen.

Nothing here needs a Bambu cloud account.  ``paho-mqtt`` is imported lazily so the module still
imports on machines that do not have it; :meth:`BambuBackend.available` reports the problem.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
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

SSDP_ADDR = "239.255.255.250"
SSDP_NOTIFY_PORT = 2021          # printers broadcast NOTIFY here
SSDP_SEARCH_PORT = 1990          # printers answer M-SEARCH here
BAMBU_ST = "urn:bambulab-com:device:3dprinter:1"
MQTT_PORT = 8883
MQTT_USER = "bblp"

#: Device code (``DevModel.bambu.net``) -> model name, bed size (X, Y, Z mm) and filament slots.
#: Only consulted when :mod:`mcprint.printers.database` is missing or does not know the code.
MODEL_FALLBACK: dict[str, dict[str, Any]] = {
    "BL-P001": {"model": "X1 Carbon", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "BL-P002": {"model": "X1", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "C13": {"model": "X1E", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "C11": {"model": "P1P", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "C12": {"model": "P1S", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "N1": {"model": "A1 mini", "bed": (180.0, 180.0, 180.0), "slots": 4},
    "N2S": {"model": "A1", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "N7": {"model": "P2S", "bed": (256.0, 256.0, 256.0), "slots": 4},
    "N9": {"model": "A2L", "bed": (330.0, 320.0, 325.0), "slots": 4},
    "N6": {"model": "X2D", "bed": (None, None, None), "slots": 4},
    "O1D": {"model": "H2D", "bed": (350.0, 320.0, 325.0), "slots": 8},
    "O1E": {"model": "H2D Pro", "bed": (350.0, 320.0, 325.0), "slots": 8},
    "O1C2": {"model": "H2C", "bed": (None, None, None), "slots": 8},
    "O1S": {"model": "H2S", "bed": (None, None, None), "slots": 4},
}

_GCODE_STATE_MAP = {
    "IDLE": "idle",
    "RUNNING": "running",
    "PAUSE": "pause",
    "FINISH": "finish",
    "FAILED": "failed",
    "PREPARE": "prepare",
    "SLICING": "slicing",
    "INIT": "idle",
    "OFFLINE": "offline",
}


# --------------------------------------------------------------------------------------
# printer database (written by a sibling module; imported lazily and defensively)
# --------------------------------------------------------------------------------------
def _spec_for_code(code: str):
    """Look up a ``PrinterSpec`` by Bambu device code, or ``None`` when unavailable."""
    if not code:
        return None
    try:
        from . import database  # local import: the module may not exist yet
    except Exception:  # pragma: no cover - depends on a sibling module
        return None
    try:
        return database.spec_for_code(code)
    except Exception:  # pragma: no cover - defensive
        log.debug("database.spec_for_code(%r) failed", code, exc_info=True)
        return None


# --------------------------------------------------------------------------------------
# SSDP
# --------------------------------------------------------------------------------------
def parse_ssdp_packet(text: str) -> Optional[dict]:
    """Parse an SSDP ``NOTIFY`` / ``HTTP/1.1 200 OK`` packet into a header dict.

    Header names are lower-cased so callers need not care about the (inconsistent)
    capitalisation Bambu firmware uses; the request/status line is kept under ``"_line"``.
    Returns ``None`` when *text* does not look like an SSDP packet.
    """
    if not text:
        return None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    first = lines[0].strip()
    upper = first.upper()
    if not (upper.startswith("NOTIFY") or upper.startswith("M-SEARCH") or "HTTP/1." in upper):
        return None
    headers: dict[str, str] = {"_line": first}
    for line in lines[1:]:
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key:
            headers[key] = value.strip()
    return headers if len(headers) > 1 else None


def _model_from_code(code: str) -> tuple[str, tuple, Optional[int]]:
    """Resolve a device code to ``(model, (bed_x, bed_y, bed_z), slots)``."""
    code = (code or "").strip()
    spec = _spec_for_code(code)
    if spec is not None:
        bed = (getattr(spec, "bed_x", None), getattr(spec, "bed_y", None),
               getattr(spec, "bed_z", None))
        return getattr(spec, "model", "") or code, bed, getattr(spec, "slots", None) or 4
    entry = MODEL_FALLBACK.get(code) or MODEL_FALLBACK.get(code.upper())
    if entry:
        return entry["model"], tuple(entry["bed"]), entry["slots"]
    return code, (None, None, None), 4


def _info_from_ssdp(headers: dict) -> Optional[PrinterInfo]:
    """Build a :class:`PrinterInfo` from parsed SSDP headers (``None`` if not a Bambu printer)."""
    serial = (headers.get("usn") or "").strip()
    devname = (headers.get("devname.bambu.net") or "").strip()
    code = (headers.get("devmodel.bambu.net") or "").strip()
    if not serial and not devname and not code:
        return None
    target = (headers.get("nt") or headers.get("st") or "").lower()
    if target and "bambu" not in target and not (devname or code):
        return None

    host = (headers.get("location") or "").strip()
    if "//" in host:                       # tolerate 'http://1.2.3.4:80/' style values
        host = host.split("//", 1)[1]
    host = host.split("/", 1)[0].split(":", 1)[0] or None

    model, bed, slots = _model_from_code(code)
    firmware = (headers.get("devversion.bambu.net") or "").strip() or None
    name = devname or (f"Bambu Lab {model}" if model else "") or serial or host or "Bambu printer"

    return PrinterInfo(
        id=f"bambu:{serial or host or name}",
        kind="bambu",
        name=name,
        model=model,
        vendor="Bambu Lab",
        host=host,
        port=MQTT_PORT,
        serial_number=serial or None,
        bed_x=bed[0],
        bed_y=bed[1],
        bed_z=bed[2],
        slots=slots,
        firmware=firmware,
        raw=dict(headers),
        online=True,
    )


def _make_m_search(port: int) -> bytes:
    """The M-SEARCH datagram Bambu firmware answers."""
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: " + SSDP_ADDR + ":" + str(port) + "\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "MX: 1\r\n"
        "ST: " + BAMBU_ST + "\r\n"
        "\r\n"
    ).encode("utf-8")


def _report(progress: Optional[ProgressFn], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:  # pragma: no cover - a UI callback must never break discovery
            log.debug("progress callback raised", exc_info=True)


def _local_addresses() -> list[str]:
    """Best effort list of local IPv4 addresses, used for per-interface multicast joins."""
    addrs: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip:
                addrs.append(ip)
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = entry[4][0]
            if ip and not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    return addrs


def _open_listener(progress: Optional[ProgressFn]) -> Optional[socket.socket]:
    """UDP socket bound to the NOTIFY port and joined to the SSDP multicast group."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reuse_port = getattr(socket, "SO_REUSEPORT", None)
        if reuse_port is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
            except OSError:
                pass
        sock.bind(("0.0.0.0", SSDP_NOTIFY_PORT))
    except OSError as exc:
        sock.close()
        message = ("Could not listen on UDP %d (%s); another program (Bambu Studio?) owns it - "
                   "falling back to active search only." % (SSDP_NOTIFY_PORT, exc))
        log.info(message)
        _report(progress, message)
        return None

    joined = False
    for iface in _local_addresses():
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(iface))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            joined = True
        except OSError:
            continue
    if not joined:
        try:
            mreq = struct.pack("4sl", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            log.debug("could not join multicast group %s", SSDP_ADDR, exc_info=True)
    sock.setblocking(False)
    return sock


def _open_sender() -> Optional[socket.socket]:
    """UDP socket used to send M-SEARCH and to receive the unicast answers."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass
        sock.bind(("0.0.0.0", 0))
        sock.setblocking(False)
        return sock
    except OSError:
        log.debug("could not create the SSDP sender socket", exc_info=True)
        return None


def _send_searches(sock: socket.socket) -> None:
    """Multicast and broadcast an M-SEARCH on both ports Bambu firmware listens on."""
    targets = [
        (SSDP_ADDR, SSDP_SEARCH_PORT),
        (SSDP_ADDR, SSDP_NOTIFY_PORT),
        ("255.255.255.255", SSDP_NOTIFY_PORT),
        ("255.255.255.255", SSDP_SEARCH_PORT),
    ]
    for host, port in targets:
        try:
            sock.sendto(_make_m_search(port), (host, port))
        except OSError:
            log.debug("M-SEARCH to %s:%s failed", host, port, exc_info=True)


# --------------------------------------------------------------------------------------
# MQTT report parsing
# --------------------------------------------------------------------------------------
def _merge_lists(dst: list, src: list) -> list:
    """Merge two lists of dicts by their ``id`` field; replace outright when that fails."""
    if not src or not all(isinstance(x, dict) and "id" in x for x in src):
        return list(src)
    if not all(isinstance(x, dict) and "id" in x for x in dst):
        return list(src)
    out = [dict(x) for x in dst]
    by_id = {str(x.get("id")): x for x in out}
    for item in src:
        key = str(item.get("id"))
        if key in by_id:
            _deep_merge(by_id[key], item)
        else:
            fresh = dict(item)
            out.append(fresh)
            by_id[key] = fresh
    return out


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursively merge *src* into *dst*: Bambu report messages are partial updates."""
    for key, value in src.items():
        old = dst.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            _deep_merge(old, value)
        elif isinstance(value, list) and isinstance(old, list):
            dst[key] = _merge_lists(old, value)
        else:
            dst[key] = value
    return dst


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _tray_color(tray: dict) -> str:
    """Raw ``RRGGBBAA`` colour string of a tray; ``''`` when the tray is empty."""
    raw = tray.get("tray_color")
    if not raw:
        cols = tray.get("cols")
        if isinstance(cols, list) and cols:
            raw = cols[0]
    return str(raw or "").strip()


def _slot_from_tray(tray: dict, index: int, group: str, ams_id: Any, source: str) -> FilamentSlot:
    """Convert one AMS tray (or the external ``vt_tray``) into a :class:`FilamentSlot`."""
    color_raw = _tray_color(tray)
    material = str(tray.get("tray_type") or "").strip()
    sub_brand = str(tray.get("tray_sub_brands") or "").strip()
    id_name = str(tray.get("tray_id_name") or "").strip()
    info_idx = str(tray.get("tray_info_idx") or "").strip()
    remain = _as_float(tray.get("remain"))
    return FilamentSlot(
        index=index,
        color_hex=normalize_hex(color_raw) if color_raw else "#808080",
        material=material,
        name=sub_brand or id_name or material,
        vendor="Bambu Lab" if info_idx.upper().startswith("GF") else "",
        source=source,
        group=group,
        loaded=bool(material or color_raw),
        remaining_pct=remain if (remain is not None and remain >= 0) else None,
        extra={
            "tray_info_idx": info_idx,
            "tag_uid": str(tray.get("tag_uid") or ""),
            "tray_uuid": str(tray.get("tray_uuid") or ""),
            "ams_id": ams_id,
            "tray_id": tray.get("id"),
            "k": tray.get("k"),
            "cali_idx": tray.get("cali_idx"),
        },
    )


def parse_ams_payload(print_dict: dict) -> list[FilamentSlot]:
    """Extract the loaded filaments from the ``print`` section of a Bambu MQTT report.

    Handles the classic ``print.ams.ams[].tray[]`` layout, the newer dual-nozzle firmware where
    each AMS unit also carries ``extruder`` / ``info`` members, and the external spool
    (``print.vt_tray`` - a dict, or a list of them on H2 machines).  Missing keys are tolerated
    everywhere: the function never raises on malformed input.
    """
    if not isinstance(print_dict, dict):
        return []

    ams_root = print_dict.get("ams")
    if isinstance(ams_root, dict):
        units = ams_root.get("ams")
    elif isinstance(ams_root, list):
        units = ams_root
    else:
        units = None
    if not isinstance(units, list):
        units = []

    slots: list[FilamentSlot] = []
    for position, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        ams_index = _as_int(unit.get("id"), position)
        if ams_index is None:
            ams_index = position
        group = "AMS %d" % (ams_index + 1)
        humidity = unit.get("humidity")
        trays = unit.get("tray")
        if not isinstance(trays, list):
            trays = []
        for tray_position, tray in enumerate(trays):
            if not isinstance(tray, dict):
                continue
            tray_index = _as_int(tray.get("id"), tray_position)
            if tray_index is None:
                tray_index = tray_position
            slot = _slot_from_tray(tray, ams_index * 4 + tray_index, group, ams_index, "ams")
            if humidity is not None:
                slot.extra["humidity"] = humidity
            if unit.get("extruder") is not None:
                slot.extra["extruder"] = unit.get("extruder")
            slots.append(slot)

    next_index = max((s.index for s in slots), default=-1) + 1
    vt = print_dict.get("vt_tray")
    vt_trays = vt if isinstance(vt, list) else ([vt] if isinstance(vt, dict) else [])
    for offset, tray in enumerate(vt_trays):
        if not isinstance(tray, dict):
            continue
        slots.append(_slot_from_tray(tray, next_index + offset, "External spool", None, "external"))

    slots.sort(key=lambda s: s.index)
    return slots


def _nozzle_from_print(print_dict: dict) -> Optional[float]:
    """Nozzle diameter in mm, tolerating the multi-nozzle firmware layout."""
    value = _as_float(print_dict.get("nozzle_diameter"))
    if value:
        return value
    device = print_dict.get("device")
    if isinstance(device, dict):
        for key in ("nozzle", "extruder"):
            node = device.get(key)
            if not isinstance(node, dict):
                continue
            infos = node.get("info")
            if isinstance(infos, dict):
                infos = list(infos.values())
            if not isinstance(infos, list):
                continue
            for item in infos:
                if isinstance(item, dict):
                    value = _as_float(item.get("diameter") or item.get("nozzle_diameter"))
                    if value:
                        return value
    return None


def _firmware_from_info(info_dict: dict) -> Optional[str]:
    """``sw_ver`` of the ``ota`` module in a ``get_version`` reply."""
    modules = info_dict.get("module") if isinstance(info_dict, dict) else None
    if not isinstance(modules, list):
        return None
    for module in modules:
        if isinstance(module, dict) and str(module.get("name", "")).lower() == "ota":
            version = str(module.get("sw_ver") or "").strip()
            if version:
                return version
    return None


# --------------------------------------------------------------------------------------
# paho helpers
# --------------------------------------------------------------------------------------
def _make_client(mqtt, client_id: str):
    """Create a paho client, preferring the v2 callback API and falling back to v1."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except (AttributeError, TypeError):  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


def _apply_tls(client) -> None:
    """Bambu printers use a self-signed certificate: encrypt, but do not verify."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        context.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:  # pragma: no cover - depends on the OpenSSL build
        pass
    client.tls_set_context(context)
    try:
        client.tls_insecure_set(True)
    except Exception:  # pragma: no cover - defensive
        pass


def _rc_is_failure(rc: Any) -> bool:
    """True when a CONNACK reason code (paho v1 int or v2 ReasonCode) means failure."""
    if rc is None:
        return False
    is_failure = getattr(rc, "is_failure", None)
    if is_failure is not None:
        return bool(is_failure)
    try:
        return int(rc) != 0
    except (TypeError, ValueError):
        return False


def _rc_message(rc: Any) -> str:
    """Human readable explanation for a CONNACK failure."""
    text = str(rc).lower()
    code = None
    try:
        code = int(getattr(rc, "value", rc))
    except (TypeError, ValueError):
        pass
    if "not authorized" in text or "bad user" in text or code in (4, 5, 0x86, 0x87):
        return ("The printer rejected the LAN access code. Re-check the 8 character code on the "
                "printer screen (Settings > WLAN / General); it changes whenever LAN Mode is "
                "toggled off and on.")
    if "unavailable" in text or code == 3:
        return "The printer's MQTT service is not ready yet; try again in a few seconds."
    return "The printer refused the MQTT connection (%s)." % (rc,)


# --------------------------------------------------------------------------------------
# backend
# --------------------------------------------------------------------------------------
class BambuBackend:
    """Bambu Lab printers in LAN mode: SSDP discovery plus the MQTT machine report."""

    kind = "bambu"
    display_name = "Bambu Lab (LAN mode)"

    # -- capability -------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            import paho.mqtt.client  # noqa: F401
        except Exception:
            return False, "paho-mqtt is not installed (pip install paho-mqtt)"
        return True, ""

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return [("access_code", "LAN access code (printer screen: Settings > WLAN/General)", True)]

    # -- discovery --------------------------------------------------------------------
    def discover(self, timeout: float = 5.0,
                 progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """Listen for SSDP NOTIFY packets and actively M-SEARCH for Bambu printers."""
        import select

        found: dict[str, PrinterInfo] = {}
        listener = _open_listener(progress)
        sender = _open_sender()
        socks = [s for s in (listener, sender) if s is not None]
        if not socks:
            log.warning("no usable UDP socket for Bambu discovery")
            return []

        _report(progress, "Searching for Bambu Lab printers (SSDP)...")
        deadline = time.time() + max(0.5, float(timeout))
        last_search = 0.0
        try:
            while time.time() < deadline:
                if sender is not None and time.time() - last_search > 2.0:
                    _send_searches(sender)
                    last_search = time.time()
                try:
                    ready, _, _ = select.select(socks, [], [], 0.3)
                except (OSError, ValueError):
                    break
                for sock in ready:
                    try:
                        data, addr = sock.recvfrom(65535)
                    except OSError:
                        continue
                    headers = parse_ssdp_packet(data.decode("utf-8", "replace"))
                    if not headers:
                        continue
                    info = _info_from_ssdp(headers)
                    if info is None:
                        continue
                    if not info.host and addr:
                        info.host = addr[0]
                    key = info.serial_number or info.host or info.id
                    if key in found:
                        continue
                    found[key] = info
                    _report(progress, "Found %s (%s) at %s"
                            % (info.name, info.model or "Bambu", info.host))
        finally:
            for sock in socks:
                try:
                    sock.close()
                except OSError:
                    pass
        return list(found.values())

    # -- live query -------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        """Fetch the full machine report over MQTT and return an updated copy of *info*."""
        merged = self._fetch_report(info, progress)
        result = copy.deepcopy(info)
        result.raw = dict(result.raw or {})
        result.raw["bambu"] = merged

        print_dict = merged.get("print") if isinstance(merged.get("print"), dict) else {}
        info_dict = merged.get("info") if isinstance(merged.get("info"), dict) else {}

        state = str(print_dict.get("gcode_state") or "").strip().upper()
        if state:
            result.status = _GCODE_STATE_MAP.get(state, state.lower())
        nozzle = _nozzle_from_print(print_dict)
        if nozzle:
            result.nozzle_mm = nozzle
        firmware = _firmware_from_info(info_dict)
        if firmware:
            result.firmware = firmware

        result.filament_slots = parse_ams_payload(print_dict)
        ams_slots = [s for s in result.filament_slots if s.source == "ams"]
        if ams_slots and not result.slots:
            result.slots = len(ams_slots)
        result.vendor = result.vendor or "Bambu Lab"
        result.port = result.port or MQTT_PORT
        result.online = True
        return result

    def sync_filaments(self, info: PrinterInfo,
                       progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        return self.connect(info, progress).filament_slots

    # -- internals --------------------------------------------------------------------
    def _fetch_report(self, info: PrinterInfo, progress: Optional[ProgressFn]) -> dict:
        """Connect over MQTT, ask for a full push and merge the report messages."""
        ok, reason = self.available()
        if not ok:
            raise PrinterError(reason)
        import paho.mqtt.client as mqtt

        host = (info.host or "").strip()
        if not host:
            raise PrinterError("No IP address for this printer - run discovery or type the IP.")
        credentials = info.credentials or {}
        serial = str(info.serial_number or credentials.get("serial_number") or "").strip()
        if not serial:
            raise PrinterError(
                "The printer serial number is required for the MQTT topic. Run discovery, or "
                "enter it manually (printer screen: Settings > Device)."
            )
        access_code = str(credentials.get("access_code") or "").strip()
        if not access_code:
            raise PrinterError(
                "LAN access code missing. On the printer, Settings > WLAN (or General) shows an "
                "8 character access code; LAN Mode must be enabled first."
            )

        merged: dict = {}
        lock = threading.Lock()
        connected = threading.Event()
        got_ams = threading.Event()
        failure: list[str] = []

        report_topic = "device/%s/report" % serial
        request_topic = "device/%s/request" % serial

        def on_connect(client, userdata, *args):
            rc = args[1] if len(args) >= 2 else None
            if _rc_is_failure(rc):
                failure.append(_rc_message(rc))
                connected.set()
                return
            try:
                client.subscribe(report_topic, qos=0)
                client.publish(request_topic, json.dumps(
                    {"pushing": {"sequence_id": "0", "command": "pushall",
                                 "version": 1, "push_target": 1}}), qos=0)
                client.publish(request_topic, json.dumps(
                    {"info": {"sequence_id": "0", "command": "get_version"}}), qos=0)
            except Exception as exc:  # pragma: no cover - network dependent
                failure.append("Could not subscribe to the report topic: %s" % exc)
            connected.set()

        def on_message(client, userdata, message, *args):
            try:
                payload = json.loads(message.payload.decode("utf-8", "replace"))
            except Exception:
                return
            if not isinstance(payload, dict):
                return
            with lock:
                _deep_merge(merged, payload)
                has_ams = isinstance(merged.get("print"), dict) and "ams" in merged["print"]
            if has_ams:
                got_ams.set()

        client = _make_client(mqtt, "mcprint-%d-%d" % (os.getpid(), int(time.time()) % 100000))
        client.username_pw_set(MQTT_USER, access_code)
        _apply_tls(client)
        client.on_connect = on_connect
        client.on_message = on_message

        _report(progress, "Connecting to %s:%d (MQTT)..." % (host, MQTT_PORT))
        try:
            client.connect(host, MQTT_PORT, keepalive=60)
        except (socket.timeout, TimeoutError) as exc:
            raise PrinterError(
                "Timed out connecting to %s:%d. Check the IP address and that the printer is on "
                "the same network with LAN Mode enabled." % (host, MQTT_PORT)
            ) from exc
        except ConnectionRefusedError as exc:
            raise PrinterError(
                "%s refused the connection on port %d. Enable LAN Mode (and Developer Mode on "
                "newer X1/P1/H2 firmware) in the printer settings." % (host, MQTT_PORT)
            ) from exc
        except ssl.SSLError as exc:
            raise PrinterError("TLS handshake with %s failed: %s" % (host, exc)) from exc
        except OSError as exc:
            raise PrinterError("Could not reach %s:%d: %s" % (host, MQTT_PORT, exc)) from exc

        try:
            client.loop_start()
            if not connected.wait(timeout=10.0):
                raise PrinterError(
                    "%s accepted the TCP connection but never completed the MQTT handshake." % host
                )
            if failure:
                raise PrinterError(failure[0])
            _report(progress, "Waiting for the machine report...")
            got_ams.wait(timeout=8.0)
        finally:
            try:
                client.loop_stop()
            except Exception:  # pragma: no cover - defensive
                pass
            try:
                client.disconnect()
            except Exception:  # pragma: no cover - defensive
                pass

        with lock:
            snapshot = copy.deepcopy(merged)
        if not snapshot:
            raise PrinterError(
                "Connected to %s but the printer sent no report. Newer firmware needs 'LAN Mode' "
                "plus 'Developer Mode' enabled before it publishes MQTT data." % host
            )
        return snapshot


BACKEND = BambuBackend()
register_backend(BACKEND)
