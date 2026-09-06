"""Tests for the built-in printer database and filament catalog."""
from __future__ import annotations

import pytest

from mcprint.color.catalog import (CATALOG, catalog_filaments, catalog_materials,
                                   catalog_vendors)
from mcprint.color.filament import Filament
from mcprint.printers.database import (PRINTER_SPECS, find_spec, spec_for_code,
                                       spec_to_printer_info, specs_for_vendor, vendors)
from mcprint.util.color import parse_hex


# ---------------------------------------------------------------------------------------------
# find_spec
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [
    ("bambu lab a2l 0.4 nozzle", "Bambu Lab A2L"),
    ("A2L", "Bambu Lab A2L"),
    ("X1C", "Bambu Lab X1 Carbon"),
    ("Ender-3 V2", "Ender-3 V2"),
    ("MK4S", "Prusa MK4S"),
])
def test_find_spec_known_names(query, expected):
    spec = find_spec(query)
    assert spec is not None, f"no match for {query!r}"
    assert spec.model == expected


def test_find_spec_is_case_and_punctuation_insensitive():
    assert find_spec("ENDER 3 V2") is find_spec("ender-3_v2")
    assert find_spec("bambu lab x1 carbon") is find_spec("X1C")


def test_find_spec_does_not_confuse_close_model_names():
    # MK4S must not fall back to the MK4, and Ender-3 V2 must not grab the V2 Neo.
    assert find_spec("MK4").model == "Prusa MK4"
    assert find_spec("MK4S").model == "Prusa MK4S"
    assert find_spec("Ender-3").model == "Ender-3"
    assert find_spec("Ender-3 V2 Neo").model == "Ender-3 V2 Neo"


def test_find_spec_matches_vendor_codes():
    assert find_spec("N9").model == "Bambu Lab A2L"


@pytest.mark.parametrize("query", ["", "   ", "not a printer at all", "banana slicer 9000"])
def test_find_spec_returns_none_for_implausible_names(query):
    assert find_spec(query) is None


# ---------------------------------------------------------------------------------------------
# spec_for_code
# ---------------------------------------------------------------------------------------------

def test_spec_for_code_a2l():
    assert spec_for_code("N9").model == "Bambu Lab A2L"


def test_spec_for_code_is_case_insensitive_and_forgiving():
    assert spec_for_code(" bl-p001 ").model == "Bambu Lab X1 Carbon"
    assert spec_for_code("C12").model == "Bambu Lab P1S"
    assert spec_for_code("nope") is None
    assert spec_for_code("") is None


def test_every_code_is_unique():
    seen = {}
    for spec in PRINTER_SPECS:
        for code in spec.codes:
            key = code.upper()
            assert key not in seen, f"code {code} used by {seen.get(key)} and {spec.model}"
            seen[key] = spec.model


# ---------------------------------------------------------------------------------------------
# Database integrity
# ---------------------------------------------------------------------------------------------

def test_database_is_reasonably_large():
    assert len(PRINTER_SPECS) >= 80
    # The task requires 70+ non-Bambu machines on top of the Bambu Lab line-up.
    assert sum(1 for s in PRINTER_SPECS if s.vendor != "Bambu Lab") >= 70


def test_every_spec_has_positive_bed_dimensions():
    for spec in PRINTER_SPECS:
        assert spec.bed_x > 0, spec.model
        assert spec.bed_y > 0, spec.model
        assert spec.bed_z > 0, spec.model


def test_every_spec_has_sane_hardware_counts():
    for spec in PRINTER_SPECS:
        assert spec.nozzle_mm > 0, spec.model
        assert spec.extruders >= 1, spec.model
        assert spec.slots >= 1, spec.model
        assert spec.slots >= spec.extruders, spec.model


def test_no_duplicate_vendor_model():
    keys = [(s.vendor, s.model) for s in PRINTER_SPECS]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate specs: {sorted(dupes)}"


def test_kind_hints_are_known_backend_kinds():
    known = {"", "bambu", "moonraker", "prusalink", "octoprint", "duet", "elegoo", "serial"}
    for spec in PRINTER_SPECS:
        assert spec.kind_hint in known, f"{spec.model}: {spec.kind_hint}"


def test_bambu_specs_match_the_known_values():
    x1c = find_spec("X1 Carbon")
    assert (x1c.bed_x, x1c.bed_y, x1c.bed_z) == (256, 256, 256)
    assert x1c.kind_hint == "bambu" and x1c.slots == 4

    a2l = spec_for_code("N9")
    assert (a2l.bed_x, a2l.bed_y, a2l.bed_z) == (330, 320, 325)

    a1_mini = spec_for_code("N1")
    assert (a1_mini.bed_x, a1_mini.bed_y, a1_mini.bed_z) == (180, 180, 180)

    # Dual-nozzle H2 machines reach 8 filaments with an AMS.
    for code in ("O1D", "O1E", "O1C2"):
        spec = spec_for_code(code)
        assert spec.extruders == 2 and spec.slots == 8, spec.model


def test_uncertain_specs_are_flagged_in_notes():
    for code in ("N6", "O1C2", "O1S"):
        assert spec_for_code(code).notes, code


# ---------------------------------------------------------------------------------------------
# vendors / specs_for_vendor / spec_to_printer_info
# ---------------------------------------------------------------------------------------------

def test_vendors_are_sorted_and_complete():
    names = vendors()
    assert names == sorted(names)
    assert len(names) == len(set(names))
    for expected in ("Bambu Lab", "Prusa", "Creality", "Anycubic", "Elegoo", "Generic"):
        assert expected in names


def test_specs_for_vendor_is_case_insensitive():
    assert specs_for_vendor("bambu lab") == specs_for_vendor("Bambu Lab")
    assert len(specs_for_vendor("Bambu Lab")) >= 14
    assert specs_for_vendor("no such vendor") == []
    for spec in specs_for_vendor("Prusa"):
        assert spec.vendor == "Prusa"


def test_spec_to_printer_info():
    spec = find_spec("A2L")
    info = spec_to_printer_info(spec)
    assert info.kind == "manual"
    assert info.id == "manual:Bambu Lab:Bambu Lab A2L"
    assert info.name == "Bambu Lab A2L"
    assert info.model == "Bambu Lab A2L"
    assert info.vendor == "Bambu Lab"
    assert (info.bed_x, info.bed_y, info.bed_z) == (330, 320, 325)
    assert info.nozzle_mm == 0.4
    assert info.extruders == 1
    assert info.slots == 4
    assert info.multi_color_slots == 4
    # Round-trips through the persistence format used for saved profiles.
    assert type(info).from_dict(info.to_dict()).id == info.id


def test_spec_to_printer_info_custom_name():
    info = spec_to_printer_info(find_spec("MK4S"), name="Workshop Prusa")
    assert info.name == "Workshop Prusa"
    assert info.model == "Prusa MK4S"


# ---------------------------------------------------------------------------------------------
# Filament catalog
# ---------------------------------------------------------------------------------------------

def test_catalog_size():
    assert len(CATALOG) >= 250


def test_every_catalog_color_parses():
    for f in CATALOG:
        r, g, b = parse_hex(f.color_hex)
        assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255
        # __post_init__ normalizes to '#RRGGBB'.
        assert f.color_hex.startswith("#") and len(f.color_hex) == 7
        assert f.color_hex == f.color_hex.upper()


def test_every_catalog_entry_is_labelled():
    for f in CATALOG:
        assert f.source == "catalog"
        assert f.vendor and f.product and f.name and f.material
        assert f.label


def test_catalog_vendors():
    names = catalog_vendors()
    assert names == sorted(names)
    for expected in ("Bambu Lab", "Polymaker", "Generic"):
        assert expected in names


def test_catalog_materials():
    assert "PLA" in catalog_materials()
    assert "PETG" in catalog_materials()
    assert catalog_materials("Generic") == ["PLA"]
    assert catalog_materials("no such vendor") == []


def test_catalog_filaments_filters():
    bambu = catalog_filaments("Bambu Lab")
    assert bambu and all(f.vendor == "Bambu Lab" for f in bambu)
    assert catalog_filaments("bambu lab") and len(catalog_filaments("bambu lab")) == len(bambu)

    petg = catalog_filaments(material="PETG")
    assert petg and all(f.material == "PETG" for f in petg)

    both = catalog_filaments("Bambu Lab", "PETG")
    assert both and all(f.vendor == "Bambu Lab" and f.material == "PETG" for f in both)
    assert len(both) < len(bambu)

    assert catalog_filaments("nobody") == []
    assert len(catalog_filaments()) == len(CATALOG)


def test_catalog_filaments_returns_copies():
    first = catalog_filaments("Generic")[0]
    first.name = "mutated"
    assert catalog_filaments("Generic")[0].name != "mutated"


def test_minecraft_wool_colors_are_exact():
    wool = {f.name: f.color_hex for f in catalog_filaments("Generic")}
    assert wool["Minecraft White"] == "#F9FFFE"
    assert wool["Minecraft Black"] == "#1D1D21"
    assert wool["Minecraft Light Blue"] == "#3AB3DA"
    assert len(wool) == 16


def test_known_bambu_swatches():
    basic = {f.name: f.color_hex for f in catalog_filaments("Bambu Lab")
             if f.product == "PLA Basic"}
    assert basic["Jade White"] == "#FFFFFF"
    assert basic["Bambu Green"] == "#00AE42"
    assert basic["Sunflower Yellow"] == "#FEC600"
    assert len(basic) >= 30


def test_catalog_entries_are_usable_filaments():
    for f in CATALOG[:20]:
        assert isinstance(f, Filament)
        assert len(f.rgb) == 3
