"""Hardware-free tests for the Bambu / Elegoo / serial backends and slicer integration.

Everything here runs against sample payloads or the real machine's slicer configuration - no
printer, no network and no USB device is required.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mcprint.color.slicer_presets import (
    import_slicer_filaments,
    load_json_lenient,
    material_from_name,
    slicer_config_dirs,
)
from mcprint.printers import bambu, elegoo, serial_detect, slicers
from mcprint.printers.base import BACKENDS

# --------------------------------------------------------------------------------------
# sample data
# --------------------------------------------------------------------------------------
#: A trimmed but realistically shaped ``print`` section: two AMS units (one empty tray, one
#: spool with an unknown remaining amount) plus the external spool holder.
SAMPLE_PRINT = {
    "gcode_state": "RUNNING",
    "nozzle_diameter": "0.4",
    "ams": {
        "ams_exist_bits": "3",
        "tray_now": "0",
        "ams": [
            {
                "id": "0",
                "humidity": "4",
                "temp": "27.9",
                "tray": [
                    {
                        "id": "0",
                        "tray_color": "FFFFFFFF",
                        "tray_type": "PLA",
                        "tray_sub_brands": "PLA Basic",
                        "tray_id_name": "A00-W1",
                        "tray_info_idx": "GFA00",
                        "tray_uuid": "9E3F2C1A5B7D4E6F",
                        "tag_uid": "D1B2A39400000000",
                        "remain": 88,
                        "k": 0.019,
                        "cali_idx": -1,
                    },
                    {
                        # empty slot: firmware sends the tray with no colour and no type
                        "id": "1",
                        "tray_color": "",
                        "tray_type": "",
                        "tray_info_idx": "",
                        "remain": -1,
                    },
                    {
                        "id": "2",
                        "tray_color": "000000FF",
                        "tray_type": "PETG",
                        "tray_sub_brands": "PETG HF",
                        "tray_id_name": "G02-K0",
                        "tray_info_idx": "GFG02",
                        "remain": -1,          # third party spool: amount unknown
                    },
                    {
                        "id": "3",
                        "tray_color": "0A2989FF",
                        "tray_type": "PLA",
                        "tray_sub_brands": "",
                        "tray_id_name": "",
                        "tray_info_idx": "",   # no RFID tag -> not a Bambu spool
                        "remain": 40,
                    },
                ],
            },
            {
                "id": "1",
                "humidity": "5",
                "tray": [
                    {
                        "id": "0",
                        "tray_color": "F72323FF",
                        "tray_type": "PLA",
                        "tray_sub_brands": "PLA Matte",
                        "tray_info_idx": "GFA01",
                        "remain": 100,
                    },
                    {"id": "1", "tray_type": "", "tray_color": ""},
                    {"id": "2", "tray_type": "", "tray_color": ""},
                    {"id": "3", "tray_type": "", "tray_color": ""},
                ],
            },
        ],
    },
    "vt_tray": {
        "id": "254",
        "tray_color": "D3B7A7FF",
        "tray_type": "PLA",
        "tray_sub_brands": "PLA Silk",
        "tray_info_idx": "GFA05",
        "remain": -1,
    },
}

SAMPLE_NOTIFY = (
    "NOTIFY * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:2021\r\n"
    "Server: UPnP/1.0\r\n"
    "Location: 192.168.1.42\r\n"
    "NT: urn:bambulab-com:device:3dprinter:1\r\n"
    "NTS: ssdp:alive\r\n"
    "USN: 00M09A351800123\r\n"
    "Cache-Control: max-age=1800\r\n"
    "DevModel.bambu.net: N9\r\n"
    "DevName.bambu.net: Workshop A2L\r\n"
    "DevSignal.bambu.net: -52\r\n"
    "DevConnect.bambu.net: lan\r\n"
    "DevBind.bambu.net: free\r\n"
    "DevVersion.bambu.net: 01.03.01.02\r\n"
    "\r\n"
)


# --------------------------------------------------------------------------------------
# Bambu: AMS payload
# --------------------------------------------------------------------------------------
def test_parse_ams_payload_indices_and_groups():
    slots = bambu.parse_ams_payload(SAMPLE_PRINT)
    assert [s.index for s in slots] == list(range(9))       # 2 x 4 AMS trays + external spool
    assert [s.group for s in slots[:4]] == ["AMS 1"] * 4
    assert [s.group for s in slots[4:8]] == ["AMS 2"] * 4
    assert slots[8].group == "External spool"
    assert {s.source for s in slots[:8]} == {"ams"}
    assert slots[8].source == "external"


def test_parse_ams_payload_first_tray():
    first = bambu.parse_ams_payload(SAMPLE_PRINT)[0]
    assert first.color_hex == "#FFFFFF"          # RRGGBBAA -> #RRGGBB
    assert first.material == "PLA"
    assert first.name == "PLA Basic"             # tray_sub_brands wins over tray_id_name
    assert first.vendor == "Bambu Lab"           # tray_info_idx starts with 'GF'
    assert first.loaded is True
    assert first.remaining_pct == 88
    assert first.extra["tray_info_idx"] == "GFA00"
    assert first.extra["ams_id"] == 0
    assert first.extra["tray_id"] == "0"
    assert first.extra["humidity"] == "4"


def test_parse_ams_payload_empty_and_unknown_remaining():
    slots = bambu.parse_ams_payload(SAMPLE_PRINT)
    empty = slots[1]
    assert empty.loaded is False
    assert empty.material == ""
    assert empty.remaining_pct is None

    unknown = slots[2]                            # remain == -1 means 'no idea'
    assert unknown.loaded is True
    assert unknown.material == "PETG"
    assert unknown.color_hex == "#000000"
    assert unknown.remaining_pct is None

    untagged = slots[3]                           # no tray_info_idx -> not a Bambu spool
    assert untagged.vendor == ""
    assert untagged.name == "PLA"                 # falls back to the material
    assert untagged.color_hex == "#0A2989"


def test_parse_ams_payload_external_spool():
    external = bambu.parse_ams_payload(SAMPLE_PRINT)[-1]
    assert external.index == 8                    # numbered after every AMS tray, not by its id
    assert external.name == "PLA Silk"
    assert external.color_hex == "#D3B7A7"
    assert external.remaining_pct is None
    assert external.extra["ams_id"] is None


@pytest.mark.parametrize("payload", [{}, {"ams": None}, {"ams": {"ams": "nope"}}, None, []])
def test_parse_ams_payload_tolerates_garbage(payload):
    assert bambu.parse_ams_payload(payload) == []


def test_parse_ams_payload_accepts_bare_list_and_missing_keys():
    slots = bambu.parse_ams_payload({"ams": [{"id": "0", "tray": [{}, {"id": "1"}]}]})
    assert [s.index for s in slots] == [0, 1]
    assert all(s.loaded is False for s in slots)


# --------------------------------------------------------------------------------------
# Bambu: SSDP
# --------------------------------------------------------------------------------------
def test_parse_ssdp_packet_lowercases_headers():
    headers = bambu.parse_ssdp_packet(SAMPLE_NOTIFY)
    assert headers is not None
    assert headers["_line"] == "NOTIFY * HTTP/1.1"
    assert headers["usn"] == "00M09A351800123"
    assert headers["devname.bambu.net"] == "Workshop A2L"
    assert headers["devmodel.bambu.net"] == "N9"
    assert headers["location"] == "192.168.1.42"
    assert headers["devversion.bambu.net"] == "01.03.01.02"
    assert headers["devsignal.bambu.net"] == "-52"
    assert headers["devbind.bambu.net"] == "free"


def test_parse_ssdp_packet_rejects_non_ssdp():
    assert bambu.parse_ssdp_packet("") is None
    assert bambu.parse_ssdp_packet("just some bytes") is None
    assert bambu.parse_ssdp_packet("NOTIFY * HTTP/1.1\r\n\r\n") is None


def test_ssdp_packet_becomes_printer_info():
    info = bambu._info_from_ssdp(bambu.parse_ssdp_packet(SAMPLE_NOTIFY))
    assert info is not None
    assert info.kind == "bambu"
    assert info.id == "bambu:00M09A351800123"
    assert info.name == "Workshop A2L"
    assert info.host == "192.168.1.42"
    assert info.port == 8883
    assert info.serial_number == "00M09A351800123"
    assert info.vendor == "Bambu Lab"
    assert "A2L" in info.model
    assert (info.bed_x, info.bed_y, info.bed_z) == (330.0, 320.0, 325.0)
    assert info.slots == 4
    assert info.firmware == "01.03.01.02"


def test_model_fallback_without_database():
    """The built-in table must cover every device code the discovery advertises."""
    model, bed, slots = bambu._model_from_code("O1D")
    assert "H2D" in model
    assert slots == 8
    assert bambu.MODEL_FALLBACK["N1"]["bed"] == (180.0, 180.0, 180.0)
    assert bambu.MODEL_FALLBACK["BL-P001"]["model"] == "X1 Carbon"


def test_bambu_backend_contract():
    assert bambu.BACKEND.kind == "bambu"
    assert bambu.BACKEND.display_name == "Bambu Lab (LAN mode)"
    assert BACKENDS["bambu"] is bambu.BACKEND
    keys = [c[0] for c in bambu.BACKEND.required_credentials()]
    assert keys == ["access_code"]
    assert bambu.BACKEND.required_credentials()[0][2] is True


def test_deep_merge_keeps_partial_updates():
    merged = {"print": {"gcode_state": "IDLE", "ams": {"ams": [{"id": "0", "humidity": "4"}]}}}
    bambu._deep_merge(merged, {"print": {"ams": {"ams": [{"id": "0", "temp": "28"}]}}})
    unit = merged["print"]["ams"]["ams"][0]
    assert unit == {"id": "0", "humidity": "4", "temp": "28"}
    assert merged["print"]["gcode_state"] == "IDLE"


def test_firmware_and_nozzle_extraction():
    info = {"module": [{"name": "esp32", "sw_ver": "9.9"}, {"name": "ota", "sw_ver": "01.08.00.00"}]}
    assert bambu._firmware_from_info(info) == "01.08.00.00"
    assert bambu._firmware_from_info({}) is None
    assert bambu._nozzle_from_print(SAMPLE_PRINT) == 0.4
    assert bambu._nozzle_from_print({"device": {"nozzle": {"info": [{"diameter": "0.6"}]}}}) == 0.6


# --------------------------------------------------------------------------------------
# Elegoo
# --------------------------------------------------------------------------------------
def test_elegoo_sdcp_reply_parsing():
    reply = (
        '{"Id":"a1b2","Data":{"Name":"Centauri Carbon","MachineName":"Centauri Carbon",'
        '"BrandName":"Elegoo","MainboardIP":"192.168.1.77","MainboardID":"0011223344",'
        '"ProtocolVersion":"V3.0.0","FirmwareVersion":"V1.1.25"}}'
    )
    info = elegoo.parse_sdcp_reply(reply)
    assert info is not None
    assert info.kind == "elegoo"
    assert info.id == "elegoo:0011223344"
    assert info.host == "192.168.1.77"
    assert info.port == 3030
    assert info.model == "Centauri Carbon"
    assert info.vendor == "Elegoo"
    assert info.firmware == "V1.1.25"
    assert (info.bed_x, info.bed_y, info.bed_z) == (256.0, 256.0, 256.0)


def test_elegoo_sdcp_reply_rejects_junk():
    assert elegoo.parse_sdcp_reply("not json") is None
    assert elegoo.parse_sdcp_reply('{"Id":"x"}') is None


def test_elegoo_sync_filaments_is_a_dead_end():
    from mcprint.printers.base import PrinterError, PrinterInfo

    info = PrinterInfo(id="elegoo:x", kind="elegoo", name="Centauri")
    with pytest.raises(PrinterError, match="does not expose loaded filament colors"):
        elegoo.BACKEND.sync_filaments(info)
    assert BACKENDS["elegoo"] is elegoo.BACKEND


# --------------------------------------------------------------------------------------
# USB / serial
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "vid, pid, description, vendor, needle",
    [
        (0x2C99, 0x0001, "Original Prusa", "Prusa Research", "MK2"),
        (0x2C99, 0x0002, "Original Prusa", "Prusa Research", "MK3"),
        (0x2C99, 0x000D, "Prusa Buddy", "Prusa Research", "Buddy"),
        (0x1A86, 0x7523, "USB-SERIAL CH340", "Creality", "CH340"),
        (0x1D50, 0x6015, "Smoothieboard", "", "Smoothieboard"),
        (0x10C4, 0xEA60, "Silicon Labs CP210x", "", "CP210x"),
        (0x0483, 0x5740, "STM32 Virtual ComPort", "", "STM32"),
        (0x2E8A, 0x000A, "Board CDC", "", "Pico"),
    ],
)
def test_classify_port_known_boards(vid, pid, description, vendor, needle):
    guessed_vendor, guessed_model = serial_detect.classify_port(vid, pid, description)
    assert guessed_vendor == vendor
    assert needle.lower() in guessed_model.lower()
    assert serial_detect.looks_like_printer(vid, pid, description) is True


def test_classify_port_unknown_device():
    assert serial_detect.classify_port(0xFFFF, 0x0001, "USB Optical Mouse") == ("", "")
    assert serial_detect.classify_port(None, None, "") == ("", "")
    assert serial_detect.looks_like_printer(0xFFFF, 0x0001, "USB Optical Mouse") is False
    # a descriptive name is enough to offer the port even with an unknown VID
    assert serial_detect.looks_like_printer(0xFFFF, 0x0001, "Marlin printer") is True


def test_model_is_definite_only_for_exact_ids():
    assert serial_detect.model_is_definite(0x2C99, 0x0002) is True
    assert serial_detect.model_is_definite(0x1D50, 0x6015) is True
    assert serial_detect.model_is_definite(0x1A86, 0x7523) is False   # CH340 could be anything
    assert serial_detect.model_is_definite(None, None) is False


def test_parse_m115():
    reply = (
        "FIRMWARE_NAME:Marlin 2.0.9.3 (Github) SOURCE_CODE_URL:github.com/MarlinFirmware/Marlin "
        "PROTOCOL_VERSION:1.0 MACHINE_TYPE:Ender-3 V2 EXTRUDER_COUNT:1 "
        "UUID:cede2a2f-41a2-4748-9b12-c55c62f367ff\n"
        "Cap:EEPROM:1\n"
        "ok\n"
    )
    fields = serial_detect.parse_m115(reply)
    assert fields["FIRMWARE_NAME"].startswith("Marlin 2.0.9.3")
    assert fields["MACHINE_TYPE"] == "Ender-3 V2"
    assert fields["EXTRUDER_COUNT"] == "1"
    assert fields["UUID"] == "cede2a2f-41a2-4748-9b12-c55c62f367ff"
    assert fields["CAP_EEPROM"] == "1"
    assert serial_detect.parse_m115("") == {}


def test_parse_m211():
    marlin = "echo:Soft endstops: On  Min: X0.00 Y0.00 Z0.00  Max: X220.00 Y220.00 Z250.00\nok\n"
    assert serial_detect.parse_m211(marlin) == (220.0, 220.0, 250.0)
    terse = "M211 X235.00 Y235.00 Z270.00\nok\n"
    assert serial_detect.parse_m211(terse) == (235.0, 235.0, 270.0)
    assert serial_detect.parse_m211("ok\n") == (None, None, None)
    assert serial_detect.parse_m211("") == (None, None, None)


def test_serial_backend_contract():
    assert serial_detect.BACKEND.kind == "serial"
    assert serial_detect.BACKEND.display_name == "USB / serial (Marlin, RepRap...)"
    assert BACKENDS["serial"] is serial_detect.BACKEND
    assert serial_detect.BACKEND.required_credentials() == [
        ("baud", "Baud rate (115200 / 250000)", False)
    ]
    # discovery never raises and never opens a port, even with no hardware attached
    assert isinstance(serial_detect.BACKEND.discover(timeout=0.1), list)


def test_serial_sync_filaments_is_a_dead_end():
    from mcprint.printers.base import PrinterError, PrinterInfo

    info = PrinterInfo(id="serial:COM3", kind="serial", name="COM3", serial_port="COM3")
    with pytest.raises(PrinterError, match="do not report loaded filament colors"):
        serial_detect.BACKEND.sync_filaments(info)


# --------------------------------------------------------------------------------------
# installed slicers
# --------------------------------------------------------------------------------------
def test_find_slicers_returns_usable_entries():
    apps = slicers.find_slicers()
    assert isinstance(apps, list)
    for app in apps:
        assert app.key and app.name and app.exe
        if not app.exe.startswith("flatpak "):
            assert os.path.exists(app.exe), app.exe


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only install layout")
@pytest.mark.skipif(not Path(r"C:\Program Files\Bambu Studio\bambu-studio.exe").is_file(),
                    reason="Bambu Studio is not installed on this machine")
def test_find_slicers_finds_bambu_studio():
    apps = {app.key: app for app in slicers.find_slicers()}
    assert "bambu_studio" in apps, "Bambu Studio is installed but was not detected"
    app = apps["bambu_studio"]
    assert app.name == "Bambu Studio"
    assert app.exe.lower().endswith("bambu-studio.exe")
    assert os.path.isfile(app.exe)


def test_plain_orca_electron_app_is_not_orcaslicer():
    """The unrelated Electron app called 'Orca' must not be reported as OrcaSlicer."""
    assert slicers._key_for_display_name("Orca") is None
    assert slicers._key_for_display_name("OrcaSlicer") == "orca_slicer"
    assert slicers._key_for_display_name("Orca-Flashforge") == "flashforge_orca"
    assert slicers._key_for_display_name("SuperSlicer") == "super_slicer"
    assert slicers._key_for_display_name("PrusaSlicer 2.8.1") == "prusa_slicer"
    assert slicers._key_for_display_name("UltiMaker Cura 5.7.1") == "cura"


def test_clean_display_icon_strips_index():
    assert slicers._clean_display_icon(r"C:\app\x.exe,0") == r"C:\app\x.exe"
    assert slicers._clean_display_icon('"C:\\app\\x.exe"') == r"C:\app\x.exe"


# --------------------------------------------------------------------------------------
# slicer filament import
# --------------------------------------------------------------------------------------
def test_material_from_name():
    assert material_from_name("Bambu PETG Basic @BBL A2L 0.4 nozzle") == "PETG"
    assert material_from_name("Generic PLA High Speed") == "PLA"
    assert material_from_name("Bambu ABS @BBL X1C") == "ABS"
    assert material_from_name("Bambu TPU 95A") == "TPU"
    assert material_from_name("Something Unlabelled") == "PLA"      # default
    assert material_from_name("Something Unlabelled", default="") == ""


def test_load_json_lenient_ignores_trailing_checksum(tmp_path):
    path = tmp_path / "Slicer.conf"
    path.write_text('{"presets": {"filament_colors": "#FF0000"}}\n# MD5 checksum ABCDEF\n',
                    encoding="utf-8")
    data = load_json_lenient(path)
    assert data == {"presets": {"filament_colors": "#FF0000"}}
    assert load_json_lenient(tmp_path / "missing.conf") is None


def test_slicer_config_dirs_only_returns_existing():
    dirs = slicer_config_dirs()
    assert set(dirs).issubset({"BambuStudio", "OrcaSlicer", "PrusaSlicer", "Cura"})
    for path in dirs.values():
        assert path.is_dir()


def test_import_slicer_filaments_never_raises():
    result = import_slicer_filaments()
    assert isinstance(result, list)
    for filament in result:
        assert filament.source == "slicer"
        assert filament.color_hex.startswith("#") and len(filament.color_hex) == 7
        assert "slicer" in filament.extra and "preset" in filament.extra


@pytest.mark.skipif("BambuStudio" not in slicer_config_dirs(),
                    reason="Bambu Studio configuration not present on this machine")
def test_import_slicer_filaments_returns_current_slot_colors():
    slots = [f for f in import_slicer_filaments()
             if f.extra.get("slicer") == "BambuStudio" and f.extra.get("slot")]
    assert len(slots) >= 2, "expected one entry per assigned filament slot"
    assert [f.extra["slot"] for f in slots[:2]] == [1, 2]
    assert all(f.name.startswith("Slot ") for f in slots)
    assert all(f.vendor == "" for f in slots)


def test_all_backends_register_on_import():
    from mcprint.printers import load_backends

    backends = load_backends()
    for kind in ("bambu", "elegoo", "serial"):
        assert kind in backends
        available, reason = backends[kind].available()
        assert isinstance(available, bool) and isinstance(reason, str)
