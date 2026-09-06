"""USB / serial printers (Marlin, RepRapFirmware, Klipper's own USB port, ...).

Discovery only *enumerates* the serial ports and guesses which of them look like a printer
board from the USB vendor/product id - it never opens a port, because opening a serial port
resets most 8/32 bit boards (DTR toggle) and would interrupt a running print.

:meth:`SerialBackend.connect` does open the port: it waits out the reset, sends ``M115`` and
``M211`` and parses the answers.  The GUI is expected to warn the user before calling it.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Optional

from .base import (
    FilamentSlot,
    PrinterError,
    PrinterInfo,
    ProgressFn,
    register_backend,
)

log = logging.getLogger(__name__)

DEFAULT_BAUD = 115200
FALLBACK_BAUDS = (250000, 57600, 230400, 500000)

#: USB vendor id -> (vendor guess, model/chip guess).  These chips are used by many boards, so
#: the model is a family hint only - :meth:`SerialBackend.connect` replaces it with the real
#: ``MACHINE_TYPE`` reported by ``M115``.
VID_TABLE: dict[int, tuple[str, str]] = {
    0x1A86: ("Creality", "Ender-3 family (CH340)"),      # QinHeng CH340/CH341
    0x10C4: ("", "Silicon Labs CP210x board"),           # CP2102/CP2104
    0x0483: ("", "STM32 virtual COM board (BTT SKR / Duet)"),
    0x2C99: ("Prusa Research", "Prusa (Buddy board)"),
    0x2341: ("", "Arduino Mega / RAMPS"),
    0x2A03: ("", "Arduino Mega / RAMPS"),
    0x16C0: ("", "Teensy / VOTI board"),
    0x1D50: ("", "OpenMoko RepRap board"),
    0x0403: ("", "FTDI serial board"),
    0x03EB: ("Ultimaker", "Atmel / Ultimaker board"),
    0x1209: ("", "pid.codes board"),
    0x2E8A: ("", "Raspberry Pi Pico (BTT SKR Pico)"),
    0x1FC9: ("", "NXP board (Duet 3 / Smoothie)"),
    0x0525: ("", "Linux USB gadget (Klipper / RepRap)"),
    0x239A: ("", "Adafruit board"),
}

#: Exact (vid, pid) pairs where the model really is known.
PID_MODELS: dict[tuple[int, int], str] = {
    (0x2C99, 0x0001): "Original Prusa i3 MK2",
    (0x2C99, 0x0002): "Original Prusa i3 MK3/MK3S",
    (0x1D50, 0x6015): "Smoothieboard",
}

#: Vendor ids whose family guess is specific enough to put into ``PrinterInfo.model``.
DEFINITE_VIDS = frozenset({0x2C99})

_PRINTER_HINT = re.compile(
    r"marlin|reprap|klipper|printer|ender|prusa|creality|anycubic|artillery|sovol|"
    r"elegoo|voron|duet|skr|ramps|makerbase|flsun|qidi|snapmaker",
    re.I,
)
_M115_KEY = re.compile(r"([A-Z][A-Z0-9_]{2,}):")
_M211_MAX = re.compile(
    r"Max\s*:?\s*X\s*(-?\d+(?:\.\d+)?)\s+Y\s*(-?\d+(?:\.\d+)?)\s+Z\s*(-?\d+(?:\.\d+)?)", re.I)
_M211_BARE = re.compile(
    r"X\s*(-?\d+(?:\.\d+)?)\s+Y\s*(-?\d+(?:\.\d+)?)\s+Z\s*(-?\d+(?:\.\d+)?)", re.I)


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


def _report(progress: Optional[ProgressFn], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:  # pragma: no cover - a UI callback must never break discovery
            log.debug("progress callback raised", exc_info=True)


# --------------------------------------------------------------------------------------
# pure helpers (unit tested without hardware)
# --------------------------------------------------------------------------------------
def classify_port(vid: Optional[int], pid: Optional[int],
                  description: str = "") -> tuple[str, str]:
    """Guess ``(vendor, model)`` for a serial port from its USB ids and description.

    The model is a *family* hint for the generic USB-serial chips (a CH340 could be any of a
    hundred Creality-style boards); only exact vid/pid pairs identify a real machine.
    """
    if vid is not None and pid is not None:
        model = PID_MODELS.get((int(vid), int(pid)))
        if model:
            vendor = VID_TABLE.get(int(vid), ("", ""))[0]
            return vendor, model
    if vid is not None and int(vid) in VID_TABLE:
        return VID_TABLE[int(vid)]
    text = description or ""
    if _PRINTER_HINT.search(text):
        return "", text.strip()
    return "", ""


def model_is_definite(vid: Optional[int], pid: Optional[int]) -> bool:
    """True when :func:`classify_port` returns a model specific enough to trust."""
    if vid is None:
        return False
    if pid is not None and (int(vid), int(pid)) in PID_MODELS:
        return True
    return int(vid) in DEFINITE_VIDS


def looks_like_printer(vid: Optional[int], pid: Optional[int], description: str = "",
                       hwid: str = "") -> bool:
    """True when a port is worth showing to the user as a possible printer."""
    if vid is not None and int(vid) in VID_TABLE:
        return True
    return bool(_PRINTER_HINT.search("%s %s" % (description or "", hwid or "")))


def parse_m115(text: str) -> dict[str, str]:
    """Parse an ``M115`` capabilities report into ``{KEY: value}``.

    ``M115`` packs several ``KEY:value`` pairs into one line and values may contain spaces, so
    each value runs up to the next ``KEY:`` token.
    """
    result: dict[str, str] = {}
    for line in (text or "").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or line.lower().startswith("cap:"):
            if line.lower().startswith("cap:"):
                body = line[4:].strip()
                if ":" in body:
                    name, _, value = body.partition(":")
                    result.setdefault("CAP_" + name.strip().upper(), value.strip())
            continue
        matches = list(_M115_KEY.finditer(line))
        if not matches:
            continue
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            key = match.group(1).upper()
            value = line[start:end].strip()
            if key not in result and value:
                result[key] = value
    return result


def parse_m211(text: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Parse the soft-endstop report of ``M211`` into ``(max_x, max_y, max_z)`` in mm."""
    for line in (text or "").replace("\r", "\n").split("\n"):
        match = _M211_MAX.search(line)
        if match:
            return tuple(float(v) for v in match.groups())  # type: ignore[return-value]
    for line in (text or "").replace("\r", "\n").split("\n"):
        if "min" in line.lower() and "max" not in line.lower():
            continue
        match = _M211_BARE.search(line)
        if match:
            values = tuple(float(v) for v in match.groups())
            if all(v > 0 for v in values):
                return values  # type: ignore[return-value]
    return None, None, None


# --------------------------------------------------------------------------------------
# backend
# --------------------------------------------------------------------------------------
class SerialBackend:
    """USB serial printers: port enumeration plus an optional ``M115`` probe."""

    kind = "serial"
    display_name = "USB / serial (Marlin, RepRap...)"

    # -- capability -------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            import serial  # noqa: F401
            import serial.tools.list_ports  # noqa: F401
        except Exception:
            return False, "pyserial is not installed (pip install pyserial)"
        return True, ""

    def required_credentials(self) -> list[tuple[str, str, bool]]:
        return [("baud", "Baud rate (115200 / 250000)", False)]

    # -- discovery --------------------------------------------------------------------
    def discover(self, timeout: float = 5.0,
                 progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
        """List serial ports that look like printer boards.  Ports are never opened."""
        try:
            from serial.tools import list_ports
        except Exception:
            log.info("pyserial missing; skipping USB printer detection")
            return []

        _report(progress, "Scanning USB serial ports...")
        found: list[PrinterInfo] = []
        try:
            ports = list(list_ports.comports())
        except Exception:  # pragma: no cover - platform dependent
            log.debug("list_ports.comports() failed", exc_info=True)
            return []

        for port in ports:
            try:
                info = self._info_for_port(port)
            except Exception:  # pragma: no cover - defensive, discover must not raise
                log.debug("could not classify port %r", getattr(port, "device", "?"),
                          exc_info=True)
                continue
            if info is not None:
                found.append(info)
                _report(progress, "Found %s" % info.name)
        return found

    def _info_for_port(self, port) -> Optional[PrinterInfo]:
        device = getattr(port, "device", "") or ""
        if not device:
            return None
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        description = getattr(port, "description", "") or ""
        hwid = getattr(port, "hwid", "") or ""
        if not looks_like_printer(vid, pid, description, hwid):
            return None

        vendor, model_guess = classify_port(vid, pid, description)
        model = model_guess if model_is_definite(vid, pid) else ""
        label = description or model_guess or "Serial port"
        return PrinterInfo(
            id="serial:%s" % device,
            kind="serial",
            name="%s (%s)" % (label, device),
            model=model,
            vendor=vendor,
            serial_port=device,
            raw={
                "vid": vid,
                "pid": pid,
                "hwid": hwid,
                "manufacturer": getattr(port, "manufacturer", None),
                "product": getattr(port, "product", None),
                "description": description,
                "model_guess": model_guess,
            },
            online=True,
        )

    # -- live query -------------------------------------------------------------------
    def connect(self, info: PrinterInfo, progress: Optional[ProgressFn] = None) -> PrinterInfo:
        """Open the port and ask the firmware who it is.

        Warning: opening a USB serial port resets most printer boards.  Never call this while a
        print is running - the GUI must confirm with the user first.
        """
        ok, reason = self.available()
        if not ok:
            raise PrinterError(reason)
        import serial

        device = (info.serial_port or "").strip()
        if not device:
            raise PrinterError("No serial port selected.")

        credentials = info.credentials or {}
        try:
            preferred = int(str(credentials.get("baud") or DEFAULT_BAUD).strip())
        except (TypeError, ValueError):
            preferred = DEFAULT_BAUD
        bauds = [preferred] + [b for b in FALLBACK_BAUDS if b != preferred]

        last_error: Optional[Exception] = None
        for baud in bauds:
            _report(progress, "Probing %s at %d baud (this resets the board)..." % (device, baud))
            try:
                fields, m211_text = self._probe(serial, device, baud)
            except Exception as exc:
                last_error = exc
                log.debug("probe of %s at %d failed", device, baud, exc_info=True)
                continue
            if not fields:
                last_error = last_error or PrinterError(
                    "%s did not answer M115 at %d baud." % (device, baud))
                continue
            return self._apply(info, fields, m211_text, baud)

        if isinstance(last_error, PrinterError):
            raise last_error
        raise PrinterError(
            "Could not talk to the printer on %s. Check that the cable is connected, that no "
            "other program (slicer, Pronterface, OctoPrint) has the port open, and try a "
            "different baud rate (115200 or 250000). Last error: %s"
            % (device, last_error or "no reply")
        )

    def _probe(self, serial_module, device: str, baud: int) -> tuple[dict[str, str], str]:
        """Open the port, send ``M115``/``M211`` and return the parsed answer plus raw M211."""
        import time

        connection = serial_module.Serial(port=device, baudrate=baud, timeout=0.5,
                                          write_timeout=2.0)
        try:
            time.sleep(2.0)                     # wait out the board reset / boot banner
            try:
                connection.reset_input_buffer()
            except Exception:  # pragma: no cover - driver dependent
                pass
            connection.write(b"\nM115\n")
            connection.flush()
            banner = self._read_for(connection, 3.0, stop_token="FIRMWARE_NAME")
            fields = parse_m115(banner)
            m211_text = ""
            if fields:
                try:
                    connection.write(b"M211\n")
                    connection.flush()
                    m211_text = self._read_for(connection, 1.5, stop_token="Max")
                except Exception:  # pragma: no cover - not every firmware knows M211
                    log.debug("M211 failed on %s", device, exc_info=True)
            return fields, m211_text
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - defensive
                pass

    @staticmethod
    def _read_for(connection, seconds: float, stop_token: str = "") -> str:
        """Read lines for at most *seconds*, stopping early once *stop_token* was seen."""
        import time

        deadline = time.time() + seconds
        chunks: list[str] = []
        seen = False
        while time.time() < deadline:
            try:
                raw = connection.readline()
            except Exception:  # pragma: no cover - driver dependent
                break
            if not raw:
                if seen:
                    break
                continue
            text = raw.decode("utf-8", "replace")
            chunks.append(text)
            if stop_token and stop_token in text:
                seen = True
        return "".join(chunks)

    def _apply(self, info: PrinterInfo, fields: dict[str, str], m211_text: str,
               baud: int) -> PrinterInfo:
        """Fold an ``M115``/``M211`` answer into a copy of *info*."""
        result = copy.deepcopy(info)
        result.raw = dict(result.raw or {})
        result.raw["m115"] = dict(fields)
        if m211_text:
            result.raw["m211"] = m211_text
        result.credentials = dict(result.credentials or {})
        result.credentials["baud"] = str(baud)

        firmware = fields.get("FIRMWARE_NAME")
        if firmware:
            result.firmware = firmware
        machine = fields.get("MACHINE_TYPE")
        if machine:
            result.model = machine
        uuid = fields.get("UUID")
        if uuid:
            result.serial_number = uuid
        count = fields.get("EXTRUDER_COUNT")
        if count:
            try:
                result.extruders = int(float(count))
            except (TypeError, ValueError):
                pass

        if machine:
            spec = _find_spec(machine)
            if spec is not None:
                result.bed_x = getattr(spec, "bed_x", None) or result.bed_x
                result.bed_y = getattr(spec, "bed_y", None) or result.bed_y
                result.bed_z = getattr(spec, "bed_z", None) or result.bed_z
                result.nozzle_mm = result.nozzle_mm or getattr(spec, "nozzle_mm", None)
                result.vendor = result.vendor or getattr(spec, "vendor", "")

        max_x, max_y, max_z = parse_m211(m211_text)
        if max_x:
            result.bed_x = max_x
        if max_y:
            result.bed_y = max_y
        if max_z:
            result.bed_z = max_z

        if machine and result.name.startswith(("USB", "Serial")):
            result.name = machine
        result.status = result.status or "idle"
        result.online = True
        return result

    def sync_filaments(self, info: PrinterInfo,
                       progress: Optional[ProgressFn] = None) -> list[FilamentSlot]:
        raise PrinterError(
            "USB printers do not report loaded filament colors; import from your slicer or add "
            "them manually."
        )


BACKEND = SerialBackend()
register_backend(BACKEND)
