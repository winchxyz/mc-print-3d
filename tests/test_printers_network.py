"""Unit tests for the network printer backends.

No sockets are opened: every backend funnels its HTTP access through a module level
``_get_json(url, ...)`` helper, and each test monkeypatches that helper with a small router
that answers canned JSON per URL.
"""
from __future__ import annotations

import json

import pytest

from mcprint.printers import duet, moonraker, octoprint, prusalink
from mcprint.printers.base import PrinterError, PrinterInfo
from mcprint.printers.manager import PrinterProfiles


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def route(module, monkeypatch, table: dict):
    """Monkeypatch ``module._get_json`` with a router matching URL substrings.

    Keys are matched as substrings of the requested URL (longest key first, so
    '/printer/objects/query?mmu' beats '/printer/objects'); unmatched URLs raise
    :class:`PrinterError`, which is what a real 404 does.
    """
    keys = sorted(table, key=len, reverse=True)

    def fake_get_json(url, **kwargs):
        for key in keys:
            if key in url:
                value = table[key]
                if isinstance(value, Exception):
                    raise value
                return value
        raise PrinterError(f"no route for {url}")

    monkeypatch.setattr(module, "_get_json", fake_get_json)
    return fake_get_json


def moonraker_info(**kwargs) -> PrinterInfo:
    return PrinterInfo(id="moonraker:10.0.0.5:7125", kind="moonraker", name="voron",
                       host="10.0.0.5", port=7125, **kwargs)


# ---------------------------------------------------------------------------------------
# Moonraker: connect
# ---------------------------------------------------------------------------------------
MOONRAKER_QUERY = {
    "result": {
        "eventtime": 1234.5,
        "status": {
            "configfile": {
                "settings": {
                    "stepper_x": {"position_min": 0, "position_max": 250},
                    "stepper_y": {"position_min": 0, "position_max": 220},
                    "stepper_z": {"position_min": 0, "position_max": 240},
                    "extruder": {"nozzle_diameter": 0.4, "filament_diameter": 1.75},
                    "extruder1": {"nozzle_diameter": 0.4},
                    "printer": {"kinematics": "corexy", "max_velocity": 300},
                    "mcu": {"serial": "/dev/serial/by-id/usb-Klipper_stm32"},
                },
            },
            "toolhead": {"axis_minimum": [0, 0, 0, 0], "axis_maximum": [250, 220, 240, 0]},
            "extruder": {"temperature": 24.0, "target": 0.0},
            "print_stats": {"state": "printing", "filename": "castle.gcode"},
        },
    }
}

MOONRAKER_BASE_ROUTES = {
    "/printer/info": {"result": {"state": "ready", "hostname": "voron24",
                                 "software_version": "v0.12.0-123"}},
    "/printer/objects/list": {"result": {"objects": ["configfile", "toolhead", "extruder",
                                                     "extruder1", "extruder_stepper foo",
                                                     "print_stats", "gcode_move"]}},
    "/printer/objects/query?configfile": MOONRAKER_QUERY,
}


def test_moonraker_connect_parses_bed_nozzle_and_extruders(monkeypatch):
    route(moonraker, monkeypatch, MOONRAKER_BASE_ROUTES)
    out = moonraker.BACKEND.connect(moonraker_info())

    assert (out.bed_x, out.bed_y, out.bed_z) == (250.0, 220.0, 240.0)  # X x Y x Z in mm
    assert out.nozzle_mm == 0.4
    assert out.extruders == 2          # 'extruder' + 'extruder1', not 'extruder_stepper foo'
    assert out.status == "printing"
    assert out.firmware == "v0.12.0-123"
    assert out.name == "voron"


def test_moonraker_connect_falls_back_to_toolhead_axis_maximum(monkeypatch):
    routes = dict(MOONRAKER_BASE_ROUTES)
    query = json.loads(json.dumps(MOONRAKER_QUERY))
    query["result"]["status"]["configfile"]["settings"].pop("stepper_x")
    query["result"]["status"]["configfile"]["settings"].pop("stepper_y")
    query["result"]["status"]["configfile"]["settings"].pop("stepper_z")
    routes["/printer/objects/query?configfile"] = query
    route(moonraker, monkeypatch, routes)

    out = moonraker.BACKEND.connect(moonraker_info())
    assert (out.bed_x, out.bed_y, out.bed_z) == (250.0, 220.0, 240.0)


def test_moonraker_connect_requires_a_host():
    with pytest.raises(PrinterError):
        moonraker.BACKEND.connect(PrinterInfo(id="x", kind="moonraker", name="x"))


def test_moonraker_probe_returns_none_for_a_non_moonraker_host(monkeypatch):
    route(moonraker, monkeypatch, {"/server/info": {"result": {"nothing": True}}})
    assert moonraker.probe("10.0.0.9") is None


def test_moonraker_probe_identifies_klipper(monkeypatch):
    route(moonraker, monkeypatch, {
        "/server/info": {"result": {"klippy_connected": True, "klippy_state": "ready",
                                    "moonraker_version": "v0.8.0"}},
        "/printer/info": {"result": {"state": "ready", "hostname": "k1max",
                                     "software_version": "v0.11.0"}},
    })
    info = moonraker.probe("10.0.0.7", port=7125)
    assert info is not None
    assert info.id == "moonraker:10.0.0.7:7125"
    assert (info.kind, info.name, info.firmware, info.status) == \
        ("moonraker", "k1max", "v0.11.0", "ready")


# ---------------------------------------------------------------------------------------
# Moonraker: Spoolman
# ---------------------------------------------------------------------------------------
SPOOLMAN_SPOOLS = [
    {
        "id": 7,
        "remaining_weight": 750.0,
        "initial_weight": 1000.0,
        "location": "Shelf A",
        "filament": {
            "id": 3, "name": "Galaxy Black", "material": "PLA",
            "color_hex": "1A1A1A", "vendor": {"id": 1, "name": "Polymaker"},
        },
    },
    {
        "id": 8,
        "remaining_weight": 500.0,
        "initial_weight": 1000.0,
        "location": "Shelf B",
        "filament": {
            "id": 4, "name": "Dual Silk", "material": "PLA",
            "multi_color_hexes": "FF0000,00FF00", "vendor": {"name": "Eryone"},
        },
    },
    {"id": 9, "archived": True, "filament": {"name": "old", "color_hex": "FFFFFF"}},
]


def test_moonraker_spoolman_spool_parsing(monkeypatch):
    routes = dict(MOONRAKER_BASE_ROUTES)
    routes.update({
        "/server/spoolman/status": {"result": {"spoolman_connected": True,
                                               "spoolman_url": "http://10.0.0.5:7912"}},
        "/server/spoolman/spool_id": {"result": {"spool_id": 8}},
        "/api/v1/spool": SPOOLMAN_SPOOLS,
    })
    route(moonraker, monkeypatch, routes)

    slots = moonraker.BACKEND.sync_filaments(moonraker_info())
    assert len(slots) == 2                       # the archived spool is skipped
    first, second = slots
    assert first.color_hex == "#1A1A1A"
    assert (first.material, first.name, first.vendor) == ("PLA", "Galaxy Black", "Polymaker")
    assert first.group == "Spoolman" and first.source == "spoolman"
    assert first.remaining_pct == pytest.approx(75.0)
    assert first.extra["spool_id"] == 7 and first.extra["location"] == "Shelf A"
    assert "active" not in first.extra
    assert second.color_hex == "#FF0000"         # first of multi_color_hexes
    assert second.extra["active"] is True        # /server/spoolman/spool_id
    assert [s.index for s in slots] == [0, 1]


def test_moonraker_spoolman_url_credential_override(monkeypatch):
    routes = dict(MOONRAKER_BASE_ROUTES)
    routes.update({
        "http://spools.local/api/v1/spool": SPOOLMAN_SPOOLS[:1],
        "/server/spoolman/spool_id": {"result": {"spool_id": None}},
    })
    route(moonraker, monkeypatch, routes)

    info = moonraker_info(credentials={"spoolman_url": "http://spools.local/"})
    slots = moonraker.BACKEND.sync_filaments(info)
    assert [s.name for s in slots] == ["Galaxy Black"]


# ---------------------------------------------------------------------------------------
# Moonraker: Happy Hare MMU
# ---------------------------------------------------------------------------------------
def _mmu_routes(gate_color):
    routes = dict(MOONRAKER_BASE_ROUTES)
    routes["/printer/objects/list"] = {
        "result": {"objects": ["configfile", "toolhead", "extruder", "print_stats", "mmu"]}}
    routes["/printer/objects/query?mmu"] = {
        "result": {"status": {"mmu": {
            "gate_color": gate_color,
            "gate_material": ["PLA", "PETG", "ABS", ""],
            "gate_status": [1, 2, 0, -1],
            "gate_spool_id": [11, 12, -1, -1],
            "gate_filament_name": ["Red PLA", "Green PETG", "", ""],
            "gate_speed_override": [100, 100, 100, 100],
        }}}}
    return routes


def test_moonraker_happy_hare_gates_with_color_names(monkeypatch):
    route(moonraker, monkeypatch, _mmu_routes(["red", "green", "grey", ""]))
    slots = moonraker.BACKEND.sync_filaments(moonraker_info())

    assert [s.color_hex for s in slots] == ["#FF0000", "#008000", "#808080", "#808080"]
    assert [s.material for s in slots] == ["PLA", "PETG", "ABS", ""]
    assert [s.loaded for s in slots] == [True, True, False, True]   # 0 = empty gate
    assert [s.group for s in slots] == ["MMU"] * 4
    assert slots[0].source == "mmu"
    assert slots[0].extra["gate"] == 0 and slots[0].extra["spool_id"] == 11
    assert slots[2].extra["spool_id"] is None


def test_moonraker_happy_hare_gates_with_hex_colors(monkeypatch):
    route(moonraker, monkeypatch, _mmu_routes(["FF8800", "#00FFEE", "112233", "zzz"]))
    slots = moonraker.BACKEND.sync_filaments(moonraker_info())
    assert [s.color_hex for s in slots] == ["#FF8800", "#00FFEE", "#112233", "#808080"]


def test_moonraker_sync_without_any_source_raises(monkeypatch):
    route(moonraker, monkeypatch, MOONRAKER_BASE_ROUTES)
    with pytest.raises(PrinterError) as excinfo:
        moonraker.BACKEND.sync_filaments(moonraker_info())
    assert "No filament data" in str(excinfo.value)


def test_moonraker_creality_cfs_box_object(monkeypatch):
    routes = dict(MOONRAKER_BASE_ROUTES)
    routes["/printer/objects/list"] = {"result": {"objects": ["configfile", "extruder", "box"]}}
    routes["/printer/objects/query?box"] = {"result": {"status": {"box": {
        "trays": [
            {"id": 0, "color": "00AAFF", "material": "PLA", "state": 1},
            {"id": 1, "color": "FFFFFF", "material": "PETG", "state": 0},
        ]}}}}
    route(moonraker, monkeypatch, routes)

    slots = moonraker.BACKEND.sync_filaments(moonraker_info())
    assert [s.color_hex for s in slots] == ["#00AAFF", "#FFFFFF"]
    assert [s.material for s in slots] == ["PLA", "PETG"]
    assert [s.loaded for s in slots] == [True, False]
    assert slots[0].source == "cfs"


# ---------------------------------------------------------------------------------------
# OctoPrint
# ---------------------------------------------------------------------------------------
OCTOPRINT_ROUTES = {
    "/api/version": {"api": "0.1", "server": "1.9.3", "text": "OctoPrint 1.9.3"},
    "/api/printerprofiles": {"profiles": {
        "_default": {"id": "_default", "name": "Old profile", "model": "Generic",
                     "current": False, "default": True,
                     "volume": {"width": 200, "depth": 200, "height": 200},
                     "extruder": {"count": 1, "nozzleDiameter": 0.6}},
        "ender3": {"id": "ender3", "name": "Ender 3 V2", "model": "Ender-3 V2",
                   "current": True, "default": False,
                   "volume": {"width": 235, "depth": 235, "height": 250, "formFactor": "rectangular"},
                   "extruder": {"count": 1, "nozzleDiameter": 0.4, "sharedNozzle": False}},
    }},
    "/api/connection": {"current": {"state": "Operational", "port": "/dev/ttyUSB0"}},
    "/api/printer": {"state": {"text": "Operational", "flags": {"operational": True}}},
}


def octoprint_info(**kwargs) -> PrinterInfo:
    return PrinterInfo(id="octoprint:10.0.0.6:80", kind="octoprint", name="", host="10.0.0.6",
                       port=80, credentials={"api_key": "ABC"}, **kwargs)


def test_octoprint_connect_uses_the_current_profile(monkeypatch):
    route(octoprint, monkeypatch, OCTOPRINT_ROUTES)
    out = octoprint.BACKEND.connect(octoprint_info())

    assert (out.bed_x, out.bed_y, out.bed_z) == (235.0, 235.0, 250.0)
    assert out.nozzle_mm == 0.4
    assert out.extruders == 1
    assert out.model == "Ender-3 V2"
    assert out.name == "Ender 3 V2"
    assert out.status == "Operational"
    assert out.firmware == "OctoPrint 1.9.3"
    assert out.online is True


def test_octoprint_connect_marks_offline_when_printer_endpoint_fails(monkeypatch):
    routes = dict(OCTOPRINT_ROUTES)
    routes.pop("/api/connection")
    routes["/api/printer"] = PrinterError("HTTP 409")   # OctoPrint's 'printer not connected'
    route(octoprint, monkeypatch, routes)

    out = octoprint.BACKEND.connect(octoprint_info())
    assert out.status == "offline"
    assert out.online is False


SPOOLMANAGER_PAYLOAD = {
    "totalItemCount": 2,
    "selectedSpools": [{"databaseId": 2, "displayName": "Silver PETG"}],
    "allSpools": [
        {"databaseId": 1, "displayName": "Galaxy Black", "vendor": "Polymaker",
         "material": "PLA", "colorName": "black", "color": "#1A1A1A",
         "totalWeight": 1000, "remainingWeight": 250},
        {"databaseId": 2, "displayName": "Silver PETG", "vendor": "Prusa",
         "material": "PETG", "colorName": "silver", "color": "C0C0C0",
         "remainingPercentage": 60, "isActive": True},
    ],
}


def test_octoprint_spoolmanager_parsing(monkeypatch):
    routes = dict(OCTOPRINT_ROUTES)
    routes["/plugin/SpoolManager/loadSpoolsByQuery"] = SPOOLMANAGER_PAYLOAD
    route(octoprint, monkeypatch, routes)

    slots = octoprint.BACKEND.sync_filaments(octoprint_info())
    assert [s.color_hex for s in slots] == ["#1A1A1A", "#C0C0C0"]
    assert [s.name for s in slots] == ["Galaxy Black", "Silver PETG"]
    assert [s.vendor for s in slots] == ["Polymaker", "Prusa"]
    assert [s.material for s in slots] == ["PLA", "PETG"]
    assert slots[0].remaining_pct == pytest.approx(25.0)
    assert slots[1].remaining_pct == pytest.approx(60.0)
    assert slots[0].extra["active"] is None
    assert slots[1].extra["active"] is True
    assert slots[0].group == "SpoolManager"


def test_octoprint_filamentmanager_fallback_has_no_colors(monkeypatch):
    routes = dict(OCTOPRINT_ROUTES)
    routes["/plugin/filamentmanager/spools"] = {"spools": [
        {"id": 1, "name": "Red PLA", "weight": 1000, "used": 200,
         "profile": {"material": "PLA", "vendor": "Das Filament"}},
    ]}
    routes["/plugin/filamentmanager/selections"] = {"selections": [
        {"tool": 0, "spool": {"id": 1}}]}
    route(octoprint, monkeypatch, routes)

    slots = octoprint.BACKEND.sync_filaments(octoprint_info())
    assert len(slots) == 1
    assert slots[0].color_hex == "#808080"        # FilamentManager has no color field
    assert slots[0].name == "Red PLA"
    assert slots[0].vendor == "Das Filament"
    assert slots[0].remaining_pct == pytest.approx(80.0)
    assert slots[0].extra["active"] is True
    assert slots[0].group == "FilamentManager"


def test_octoprint_sync_without_plugins_raises(monkeypatch):
    route(octoprint, monkeypatch, OCTOPRINT_ROUTES)
    with pytest.raises(PrinterError):
        octoprint.BACKEND.sync_filaments(octoprint_info())


# ---------------------------------------------------------------------------------------
# PrusaLink
# ---------------------------------------------------------------------------------------
def prusalink_info(**kwargs) -> PrinterInfo:
    return PrinterInfo(id="prusalink:10.0.0.8:80", kind="prusalink", name="", host="10.0.0.8",
                       port=80, credentials={"api_key": "KEY"}, **kwargs)


def _prusalink_routes(*, text="PrusaLink 2.1.2", mmu=False, hostname="prusa-mk4s",
                      firmware="6.1.3", nozzle=0.4):
    return {
        "/api/version": {"api": "2.0.0", "server": "2.1.2", "text": text,
                         "hostname": hostname, "capabilities": {"upload-by-put": True}},
        "/api/v1/info": {"name": hostname, "location": "Workshop", "serial": "SN12345",
                         "hostname": hostname, "nozzle_diameter": nozzle, "mmu": mmu,
                         "min_extrusion_temp": 170, "firmware": firmware},
        "/api/v1/status": {"printer": {"state": "IDLE", "temp_nozzle": 24.9, "temp_bed": 23.5}},
        "/api/printer": {"telemetry": {"temp-bed": 23.5, "temp-nozzle": 24.9},
                         "state": {"text": "Operational"}},
    }


def test_prusalink_connect_mk4s(monkeypatch):
    route(prusalink, monkeypatch, _prusalink_routes(text="PrusaLink 2.1.2 MK4S"))
    out = prusalink.BACKEND.connect(prusalink_info())

    # the catalog may rewrite the model to its canonical name ('Prusa MK4S')
    assert "MK4S" in out.model
    assert out.raw["detected_model"] == "MK4S"
    assert (out.bed_x, out.bed_y, out.bed_z) == (250.0, 210.0, 220.0)
    assert out.nozzle_mm == 0.4
    assert out.slots == 1           # no MMU reported by /api/v1/info
    assert out.extruders == 1
    assert out.status == "IDLE"
    assert out.serial_number == "SN12345"
    assert out.vendor.startswith("Prusa")   # 'Prusa Research', or the catalog's 'Prusa'


def test_prusalink_connect_with_mmu_has_five_slots(monkeypatch):
    route(prusalink, monkeypatch, _prusalink_routes(text="PrusaLink 2.1.2 MK4",
                                                    hostname="prusa-mk4", mmu=True))
    out = prusalink.BACKEND.connect(prusalink_info())
    assert out.raw["detected_model"] == "MK4"
    assert (out.bed_x, out.bed_y, out.bed_z) == (250.0, 210.0, 220.0)
    assert out.slots == 5


def test_prusalink_connect_xl_and_mini_bed_table(monkeypatch):
    route(prusalink, monkeypatch, _prusalink_routes(text="PrusaLink 2.1.2", hostname="prusa-xl"))
    out = prusalink.BACKEND.connect(prusalink_info())
    assert out.raw["detected_model"] == "XL"
    assert (out.bed_x, out.bed_y, out.bed_z) == (360.0, 360.0, 360.0)
    assert out.slots == 5           # up to five tool heads

    route(prusalink, monkeypatch, _prusalink_routes(hostname="prusa-mini"))
    out = prusalink.BACKEND.connect(prusalink_info())
    assert out.raw["detected_model"] == "MINI"
    assert (out.bed_x, out.bed_y, out.bed_z) == (180.0, 180.0, 180.0)
    assert out.slots == 1


def test_prusalink_detect_model_variants():
    assert prusalink.detect_model("PrusaLink 2.1.2", "prusa-core-one") == "CORE ONE"
    assert prusalink.detect_model("MK3.9 firmware") == "MK3.9"
    assert prusalink.detect_model("MK3.5") == "MK3.5"
    assert prusalink.detect_model("Original Prusa MK3S+") == "MK3S"
    assert prusalink.detect_model("nothing here") == ""


def test_prusalink_core_one_bed(monkeypatch):
    route(prusalink, monkeypatch, _prusalink_routes(text="PrusaLink 2.2.0 CORE ONE",
                                                    hostname="prusa-core-one"))
    out = prusalink.BACKEND.connect(prusalink_info())
    assert out.raw["detected_model"] == "CORE ONE"
    assert (out.bed_x, out.bed_y, out.bed_z) == (250.0, 220.0, 270.0)


def test_prusalink_sync_filaments_explains_why_it_cannot(monkeypatch):
    with pytest.raises(PrinterError) as excinfo:
        prusalink.BACKEND.sync_filaments(prusalink_info())
    assert "does not report filament colors" in str(excinfo.value)


# ---------------------------------------------------------------------------------------
# Duet / RepRapFirmware
# ---------------------------------------------------------------------------------------
DUET_MODEL = {
    "boards": [{"name": "Duet 3 MB6HC", "firmwareName": "RepRapFirmware for Duet 3 MB6HC",
                "firmwareVersion": "3.5.1"}],
    "move": {"axes": [
        {"letter": "X", "min": 0, "max": 300, "homed": True},
        {"letter": "Y", "min": -5, "max": 295, "homed": True},
        {"letter": "Z", "min": 0, "max": 400, "homed": True},
        {"letter": "U", "min": 0, "max": 100},
    ]},
    "network": {"name": "big-duet", "hostname": "big-duet"},
    "state": {"status": "idle"},
    "tools": [
        {"number": 0, "name": "T0", "filament": "Prusament PETG"},
        {"number": 1, "name": "T1", "filament": ""},
    ],
}


def duet_info(**kwargs) -> PrinterInfo:
    return PrinterInfo(id="duet:10.0.0.4:80", kind="duet", name="", host="10.0.0.4", port=80,
                       **kwargs)


def test_duet_connect_object_model(monkeypatch):
    route(duet, monkeypatch, {"/rr_model": {"key": "", "flags": "d99fn", "result": DUET_MODEL}})
    out = duet.BACKEND.connect(duet_info())

    assert (out.bed_x, out.bed_y, out.bed_z) == (300.0, 300.0, 400.0)   # Y is max - min
    assert out.extruders == 2 and out.slots == 2
    assert out.firmware == "RepRapFirmware for Duet 3 MB6HC 3.5.1"
    assert out.name == "big-duet"
    assert out.model == "Duet 3 MB6HC"
    assert out.status == "idle"


def test_duet_connect_sbc_mode(monkeypatch):
    route(duet, monkeypatch, {"/machine/status": DUET_MODEL})
    out = duet.BACKEND.connect(duet_info())
    assert out.bed_x == 300.0 and out.name == "big-duet"


def test_duet_sync_filaments_returns_grey_named_slots(monkeypatch):
    route(duet, monkeypatch, {"/rr_model": {"result": DUET_MODEL}})
    slots = duet.BACKEND.sync_filaments(duet_info())

    assert len(slots) == 1                      # the empty tool is skipped
    assert slots[0].color_hex == "#808080"
    assert slots[0].name == "Prusament PETG"
    assert slots[0].material == "PETG"
    assert slots[0].group == "Tools"


def test_duet_sync_filaments_without_filament_raises(monkeypatch):
    model = json.loads(json.dumps(DUET_MODEL))
    for tool in model["tools"]:
        tool["filament"] = ""
    route(duet, monkeypatch, {"/rr_model": {"result": model}})
    with pytest.raises(PrinterError):
        duet.BACKEND.sync_filaments(duet_info())


def test_duet_connect_without_object_model_raises(monkeypatch):
    route(duet, monkeypatch, {})
    with pytest.raises(PrinterError):
        duet.BACKEND.connect(duet_info())


# ---------------------------------------------------------------------------------------
# PrinterProfiles
# ---------------------------------------------------------------------------------------
def test_printer_profiles_round_trip(tmp_path):
    path = tmp_path / "printers.json"
    profiles = PrinterProfiles(path)
    assert profiles.all() == []
    assert profiles.selected_id is None

    info = PrinterInfo(id="moonraker:10.0.0.5:7125", kind="moonraker", name="voron",
                       host="10.0.0.5", port=7125, bed_x=250, bed_y=220, bed_z=240,
                       nozzle_mm=0.4, extruders=1, slots=4,
                       credentials={"api_key": "secret"})
    profiles.add_or_update(info)
    assert path.exists()
    assert profiles.selected_id == info.id       # first printer becomes the selection

    reloaded = PrinterProfiles(path)
    assert len(reloaded) == 1
    stored = reloaded.get(info.id)
    assert stored is not None
    assert (stored.bed_x, stored.bed_y, stored.bed_z) == (250, 220, 240)
    assert stored.credentials["api_key"] == "secret"
    assert reloaded.selected_id == info.id

    # updating keeps previously stored credentials the caller did not supply
    updated = PrinterInfo(id=info.id, kind="moonraker", name="voron 2.4", host="10.0.0.5",
                          port=7125)
    reloaded.add_or_update(updated)
    assert len(reloaded) == 1
    assert reloaded.get(info.id).name == "voron 2.4"
    assert reloaded.get(info.id).credentials["api_key"] == "secret"

    reloaded.selected_id = None
    assert PrinterProfiles(path).selected_id is None

    reloaded.remove(info.id)
    assert PrinterProfiles(path).all() == []


def test_printer_profiles_survive_a_corrupt_file(tmp_path):
    path = tmp_path / "printers.json"
    path.write_text("{not json", encoding="utf-8")
    profiles = PrinterProfiles(path)
    assert profiles.all() == []


def test_printer_profiles_filament_slots_round_trip(tmp_path):
    from mcprint.printers.base import FilamentSlot

    path = tmp_path / "printers.json"
    profiles = PrinterProfiles(path)
    info = PrinterInfo(id="manual:1", kind="manual", name="Manual printer")
    info.filament_slots = [FilamentSlot(index=0, color_hex="#FF0000", material="PLA",
                                        name="Red", source="manual")]
    profiles.add_or_update(info)

    stored = PrinterProfiles(path).get("manual:1")
    assert stored is not None
    assert len(stored.filament_slots) == 1
    assert stored.filament_slots[0].color_hex == "#FF0000"


# ---------------------------------------------------------------------------------------
# package / dispatch
# ---------------------------------------------------------------------------------------
def test_load_backends_never_raises_and_registers_the_http_backends():
    import mcprint.printers as printers

    table = printers.load_backends()
    for kind in ("moonraker", "octoprint", "prusalink", "duet"):
        assert kind in table
        assert table[kind].available()[0] is True


def test_manager_dispatch_and_manual_kind():
    from mcprint.printers import manager

    assert manager.get_backend("moonraker") is moonraker.BACKEND
    assert manager.get_backend("nope") is None

    manual = PrinterInfo(id="manual:1", kind="manual", name="Manual")
    assert manager.connect_printer(manual) is manual
    assert manager.sync_filaments(manual) == []

    kinds = [b.kind for b in manager.backends()]
    for kind in ("moonraker", "octoprint", "prusalink", "duet"):
        assert kind in kinds
    known = [k for k in kinds if k in manager.BACKEND_ORDER]
    assert known == sorted(known, key=manager.BACKEND_ORDER.index)

    with pytest.raises(PrinterError):
        manager.connect_printer(PrinterInfo(id="x:1", kind="nope", name="x"))


def test_discovery_imports_and_never_raises(monkeypatch):
    from mcprint.printers import discovery

    monkeypatch.setattr(discovery, "discover_mdns", lambda **kw: [])
    seen: list[str] = []
    infos = discovery.discover_all(timeout=0.1, progress=seen.append, use_subnet_scan=False)
    assert isinstance(infos, list)
    assert seen and "Discovery finished" in seen[-1]

    candidate = discovery.bambu_candidate("192.168.1.50")
    assert candidate.kind == "bambu" and candidate.port == 8883
    assert discovery.elegoo_candidate("192.168.1.51").kind == "elegoo"


def test_discovery_dedupes_by_id_and_host():
    from mcprint.printers import discovery

    a = PrinterInfo(id="moonraker:1.2.3.4:7125", kind="moonraker", name="a", host="1.2.3.4")
    b = PrinterInfo(id="moonraker:1.2.3.4:80", kind="moonraker", name="b", host="1.2.3.4")
    c = PrinterInfo(id="octoprint:1.2.3.4:80", kind="octoprint", name="c", host="1.2.3.4")
    out = discovery._dedupe([a, b, c, a])
    assert [i.id for i in out] == [a.id, c.id]
