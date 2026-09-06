"""Network (and USB) discovery shared by every printer backend.

Three independent strategies, all of which are safe to call blind and never raise:

* :func:`discover_mdns` – zeroconf/Bonjour browsing for the service types printers announce;
* :func:`scan_subnet`  – a TCP connect sweep of the local /24 followed by HTTP probes;
* :func:`probe_host`   – every HTTP backend probe against one known address.

:func:`discover_all` glues them together with the Bambu (SSDP), Elegoo (SDCP) and USB serial
backends written by the other modules, and de-duplicates the result.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Optional

from .base import PrinterInfo, ProgressFn

log = logging.getLogger(__name__)

#: mDNS service types that 3D printers / print servers announce.
SERVICE_TYPES: tuple[str, ...] = (
    "_octoprint._tcp.local.",
    "_moonraker._tcp.local.",
    "_http._tcp.local.",
    "_printer._tcp.local.",
    "_prusalink._tcp.local.",
    "_bambulab._tcp.local.",
    "_elg._tcp.local.",
)

#: Substrings in a generic ``_http`` / ``_printer`` service name worth probing.
NAME_HINTS: tuple[str, ...] = (
    "prusalink", "prusa", "octoprint", "mainsail", "fluidd", "moonraker", "klipper",
    "duet", "creality", "elegoo", "anycubic",
)

#: Ports probed by :func:`scan_subnet` (Moonraker, HTTP, OctoPrint alt, Bambu MQTT, Elegoo SDCP, ...).
DEFAULT_SCAN_PORTS: tuple[int, ...] = (7125, 80, 5000, 8883, 3030, 4409, 4408)

BAMBU_PORT = 8883
ELEGOO_PORT = 3030


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def _say(progress: Optional[ProgressFn], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:
            log.debug("progress callback raised", exc_info=True)


def _dedupe(infos: Iterable[PrinterInfo]) -> list[PrinterInfo]:
    """Keep the first entry per id, and per (kind, host) when a host is known."""
    seen_ids: set[str] = set()
    seen_hosts: set[tuple[str, str]] = set()
    out: list[PrinterInfo] = []
    for info in infos:
        if info is None:
            continue
        if info.id in seen_ids:
            continue
        host_key = (info.kind, str(info.host)) if info.host else None
        if host_key and host_key in seen_hosts:
            continue
        seen_ids.add(info.id)
        if host_key:
            seen_hosts.add(host_key)
        out.append(info)
    return out


def _backend_module(name: str):
    """Import a sibling backend module, tolerating the ones that do not exist yet."""
    try:
        from importlib import import_module

        return import_module(f".{name}", __package__)
    except Exception as exc:
        log.debug("printer backend module %r unavailable: %s", name, exc)
        return None


def _probe(module_name: str, host: str, port: int, timeout: float) -> Optional[PrinterInfo]:
    module = _backend_module(module_name)
    probe_fn: Optional[Callable[..., Optional[PrinterInfo]]] = getattr(module, "probe", None)
    if probe_fn is None:
        return None
    try:
        return probe_fn(host, port=port, timeout=timeout)
    except Exception as exc:
        log.debug("%s probe of %s:%s failed: %s", module_name, host, port, exc)
        return None


def bambu_candidate(host: str, port: int = BAMBU_PORT) -> PrinterInfo:
    """A Bambu printer we can see but cannot query without a serial + LAN access code."""
    info = PrinterInfo(
        id=f"bambu:{host}",
        kind="bambu",
        name=f"Bambu printer at {host}",
        vendor="Bambu Lab",
        host=host,
        port=int(port),
        status="unknown",
    )
    info.raw = {"needs": ["serial_number", "access_code"], "source": "scan"}
    return info


def elegoo_candidate(host: str, port: int = ELEGOO_PORT) -> PrinterInfo:
    """An Elegoo (SDCP) printer seen on the network."""
    info = PrinterInfo(
        id=f"elegoo:{host}",
        kind="elegoo",
        name=f"Elegoo printer at {host}",
        vendor="Elegoo",
        host=host,
        port=int(port),
        status="unknown",
    )
    info.raw = {"source": "scan"}
    return info


# ---------------------------------------------------------------------------------------
# mDNS
# ---------------------------------------------------------------------------------------
class _MdnsCollector:
    """Minimal zeroconf listener: remember the (type, name) pairs, resolve them later."""

    def __init__(self) -> None:
        self.services: list[tuple[str, str]] = []

    def add_service(self, zc: Any, type_: str, name: str) -> None:  # noqa: D102 - zeroconf API
        if (type_, name) not in self.services:
            self.services.append((type_, name))

    def update_service(self, zc: Any, type_: str, name: str) -> None:  # noqa: D102
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Any, type_: str, name: str) -> None:  # noqa: D102
        return None


def _service_addresses(service: Any) -> list[str]:
    try:
        addresses = list(service.parsed_addresses())
    except Exception:
        addresses = []
        for raw in getattr(service, "addresses", None) or []:
            try:
                addresses.append(socket.inet_ntoa(raw))
            except Exception:
                continue
    out = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.version == 4 and not ip.is_loopback:
            out.append(str(ip))
    return out


def _service_properties(service: Any) -> dict[str, str]:
    props: dict[str, str] = {}
    for key, value in (getattr(service, "properties", None) or {}).items():
        try:
            k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
            v = value.decode("utf-8", "replace") if isinstance(value, bytes) else ("" if value is None else str(value))
            props[k] = v
        except Exception:
            continue
    return props


def _classify_service(type_: str, name: str, host: str, port: int, props: dict[str, str],
                      timeout: float) -> list[PrinterInfo]:
    """Turn one resolved mDNS service into zero or more :class:`PrinterInfo`."""
    lname = f"{name} {props.get('name', '')} {props.get('model', '')}".lower()
    found: list[PrinterInfo] = []

    if type_.startswith("_moonraker"):
        info = _probe("moonraker", host, port or 7125, timeout)
        return [info] if info else []

    if type_.startswith("_prusalink"):
        info = _probe("prusalink", host, port or 80, timeout)
        return [info] if info else []

    if type_.startswith("_octoprint"):
        # PrusaLink advertises _octoprint._tcp too - it wins when /api/version says so.
        info = _probe("prusalink", host, port or 80, timeout)
        if info:
            return [info]
        info = _probe("octoprint", host, port or 80, timeout)
        return [info] if info else []

    if type_.startswith("_bambulab"):
        serial = props.get("SN") or props.get("serial") or props.get("dev_sn") or ""
        info = bambu_candidate(host)
        if serial:
            info.id = f"bambu:{serial}"
            info.serial_number = serial
        model = props.get("dev_product_name") or props.get("model") or ""
        if model:
            info.model = model
            info.name = f"{model} ({host})"
        info.raw["mdns"] = props
        return [info]

    if type_.startswith("_elg"):
        info = elegoo_candidate(host)
        model = props.get("model") or props.get("MachineName") or ""
        if model:
            info.model = model
            info.name = f"{model} ({host})"
        info.raw["mdns"] = props
        return [info]

    # _http / _printer: only worth probing when the instance name hints at a 3D printer
    if any(hint in lname for hint in NAME_HINTS):
        found.extend(probe_host(host, timeout=timeout))
    return found


def discover_mdns(timeout: float = 4.0, progress: Optional[ProgressFn] = None) -> list[PrinterInfo]:
    """Browse the LAN for printer services with zeroconf.  Returns [] when zeroconf is missing."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except Exception as exc:
        log.debug("zeroconf unavailable: %s", exc)
        _say(progress, "mDNS discovery skipped (the 'zeroconf' package is not installed).")
        return []

    _say(progress, "Browsing the network for printers (mDNS) ...")
    collector = _MdnsCollector()
    zc = None
    browser = None
    try:
        zc = Zeroconf()
        browser = ServiceBrowser(zc, list(SERVICE_TYPES), collector)
        time.sleep(max(0.5, float(timeout)))
        services = list(collector.services)
        results: list[PrinterInfo] = []
        for type_, name in services:
            try:
                service = zc.get_service_info(type_, name, timeout=1500)
            except Exception:
                service = None
            if service is None:
                continue
            addresses = _service_addresses(service)
            if not addresses:
                continue
            props = _service_properties(service)
            port = int(getattr(service, "port", 0) or 0)
            short = name.split(".")[0]
            _say(progress, f"Found {short} at {addresses[0]} - identifying ...")
            for host in addresses[:1]:
                try:
                    results.extend(_classify_service(type_, name, host, port, props, timeout=2.0))
                except Exception:
                    log.debug("classify %s failed", name, exc_info=True)
        return _dedupe(results)
    except Exception as exc:
        log.debug("mDNS discovery failed: %s", exc, exc_info=True)
        return []
    finally:
        try:
            if browser is not None:
                stop = getattr(browser, "cancel", None) or getattr(browser, "close", None)
                if stop is not None:
                    stop()
        except Exception:
            pass
        try:
            if zc is not None:
                zc.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------------------
# host probing
# ---------------------------------------------------------------------------------------
def probe_host(host: str, timeout: float = 2.0) -> list[PrinterInfo]:
    """Try every HTTP backend probe on the usual ports for one address."""
    attempts: tuple[tuple[str, int], ...] = (
        ("moonraker", 7125),
        ("prusalink", 80),
        ("octoprint", 80),
        ("octoprint", 5000),
        ("moonraker", 80),
        ("duet", 80),
    )
    found: list[PrinterInfo] = []
    for module_name, port in attempts:
        # do not probe octoprint/duet on :80 again when PrusaLink already claimed the host
        if any(i.host == host and i.port == port for i in found):
            continue
        info = _probe(module_name, host, port, timeout)
        if info is not None:
            found.append(info)
    return _dedupe(found)


# ---------------------------------------------------------------------------------------
# subnet scan
# ---------------------------------------------------------------------------------------
def local_ipv4s() -> list[str]:
    """Best-effort list of this machine's IPv4 addresses, primary interface first."""
    found: list[str] = []

    def add(address: Any) -> None:
        try:
            ip = ipaddress.ip_address(str(address))
        except ValueError:
            return
        if ip.version != 4 or ip.is_loopback or ip.is_link_local:
            return
        if str(ip) not in found:
            found.append(str(ip))

    sock = None
    try:  # the classic 'which interface would reach the internet' trick (sends nothing)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        add(sock.getsockname()[0])
    except Exception:
        log.debug("primary interface lookup failed", exc_info=True)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(res[4][0])
    except Exception:
        log.debug("getaddrinfo(hostname) failed", exc_info=True)
    return found


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


def scan_subnet(timeout: float = 6.0, progress: Optional[ProgressFn] = None,
                ports: Iterable[int] = DEFAULT_SCAN_PORTS, max_workers: int = 64) -> list[PrinterInfo]:
    """TCP-connect sweep of every local /24, then identify the hosts that answered."""
    ports = tuple(int(p) for p in ports)
    deadline = time.monotonic() + max(1.0, float(timeout))
    results: list[PrinterInfo] = []
    networks: list[str] = []
    for address in local_ipv4s():
        try:
            prefix = str(ipaddress.ip_network(f"{address}/24", strict=False).network_address)
        except Exception:
            continue
        if prefix not in networks:
            networks.append(prefix)
    if not networks:
        _say(progress, "Subnet scan skipped (no local IPv4 network found).")
        return []

    open_ports: dict[str, set[int]] = {}
    for prefix in networks:
        base = prefix.rsplit(".", 1)[0]
        _say(progress, f"Scanning {base}.0/24 on ports {', '.join(str(p) for p in ports)} ...")
        targets = [(f"{base}.{n}", port) for n in range(1, 255) for port in ports]
        try:
            with ThreadPoolExecutor(max_workers=max(4, int(max_workers))) as pool:
                futures = {pool.submit(_port_open, host, port): (host, port) for host, port in targets}
                try:
                    for future in as_completed(futures, timeout=max(0.5, deadline - time.monotonic())):
                        host, port = futures[future]
                        try:
                            if future.result():
                                open_ports.setdefault(host, set()).add(port)
                        except Exception:
                            continue
                except Exception:
                    log.debug("subnet scan hit its time budget", exc_info=True)
                for future in futures:
                    future.cancel()
        except Exception:
            log.debug("subnet scan failed on %s", base, exc_info=True)
        if time.monotonic() >= deadline:
            break

    if not open_ports:
        _say(progress, "Subnet scan found no printer ports open.")
        return []

    _say(progress, f"{len(open_ports)} host(s) answered - identifying ...")
    for host in sorted(open_ports, key=lambda h: tuple(int(p) for p in h.split("."))):
        found = open_ports[host]
        if BAMBU_PORT in found:
            results.append(bambu_candidate(host))
        if ELEGOO_PORT in found:
            results.append(elegoo_candidate(host))
        if 7125 in found:
            info = _probe("moonraker", host, 7125, 2.0)
            if info:
                results.append(info)
        for port in (80, 5000):
            if port not in found:
                continue
            info = _probe("prusalink", host, port, 2.0)
            if info is None:
                info = _probe("octoprint", host, port, 2.0)
            if info is None:
                info = _probe("duet", host, port, 2.0)
            if info is not None:
                results.append(info)
    return _dedupe(results)


# ---------------------------------------------------------------------------------------
# everything at once
# ---------------------------------------------------------------------------------------
def discover_all(timeout: float = 6.0, progress: Optional[ProgressFn] = None,
                 use_subnet_scan: bool = False) -> list[PrinterInfo]:
    """mDNS + Bambu SSDP + Elegoo + USB serial (+ optional subnet scan), de-duplicated by id."""
    results: list[PrinterInfo] = []
    try:
        results.extend(discover_mdns(timeout=min(float(timeout), 5.0), progress=progress))
    except Exception:
        log.debug("mDNS discovery raised", exc_info=True)

    for module_name, label in (("bambu", "Bambu Lab"), ("elegoo", "Elegoo"),
                               ("serial_detect", "USB")):
        module = _backend_module(module_name)
        backend = getattr(module, "BACKEND", None)
        if backend is None:
            continue
        try:
            ok, why = backend.available()
        except Exception:
            ok, why = True, ""
        if not ok:
            _say(progress, f"{label} discovery skipped ({why}).")
            continue
        _say(progress, f"Looking for {label} printers ...")
        try:
            found = backend.discover(timeout=float(timeout), progress=progress) or []
            results.extend(found)
        except Exception:
            log.debug("%s discovery raised", module_name, exc_info=True)

    if use_subnet_scan:
        try:
            results.extend(scan_subnet(timeout=float(timeout), progress=progress))
        except Exception:
            log.debug("subnet scan raised", exc_info=True)

    final = _dedupe(results)
    _say(progress, f"Discovery finished: {len(final)} printer(s).")
    return final
