"""Built-in catalog of real, purchasable filament colors.

Offline data only.  The catalog seeds the filament picker so a user can build a palette that
matches spools they actually own, without typing hex codes by hand, and it gives the color mapper
a realistic set of targets when no printer is connected.

Hex values are the vendor's published swatch colors where those are available (Bambu Lab and
Prusament publish exact values; most other vendors only show photographs, so those entries are
close visual matches rather than measured values).  Every entry uses ``source="catalog"``.

Silk, metallic and glow filaments are listed with their base color: their appearance depends on
the sheen, so treat those hexes as approximate.  Support, PVA and other non-visible materials are
deliberately excluded -- they are never part of a color palette.
"""
from __future__ import annotations

from typing import Optional

from .filament import Filament

__all__ = ["CATALOG", "catalog_filaments", "catalog_vendors", "catalog_materials"]


def _f(vendor: str, product: str, material: str, name: str, color_hex: str) -> Filament:
    return Filament(color_hex=color_hex, name=name, material=material, vendor=vendor,
                    product=product, source="catalog")


def _line(vendor: str, product: str, material: str, colors: list[tuple[str, str]]) -> list[Filament]:
    """Build one product line from ``(name, hex)`` pairs."""
    return [_f(vendor, product, material, name, color_hex) for name, color_hex in colors]


# =============================================================================================
# Bambu Lab
# =============================================================================================

_BAMBU_PLA_BASIC = [
    ("Jade White", "#FFFFFF"), ("Black", "#000000"), ("Bambu Green", "#00AE42"),
    ("Red", "#C12E1F"), ("Blue", "#0A2989"), ("Yellow", "#F4EE2A"),
    ("Orange", "#FF6A13"), ("Gray", "#8E9089"), ("Brown", "#9D432C"),
    ("Purple", "#5E43B7"), ("Pink", "#F55A74"), ("Cyan", "#0086D6"),
    ("Magenta", "#EC008C"), ("Light Gray", "#D1D3D5"), ("Dark Gray", "#545454"),
    ("Cocoa Brown", "#6F5034"), ("Beige", "#F7E6DE"), ("Gold", "#E4BD68"),
    ("Silver", "#A6A9AA"), ("Bronze", "#847D48"), ("Mistletoe Green", "#3F8E43"),
    ("Hot Pink", "#F5547C"), ("Sunflower Yellow", "#FEC600"), ("Turquoise", "#00B1B7"),
    ("Indigo Purple", "#482960"), ("Bright Green", "#BECF00"), ("Maroon Red", "#9D2235"),
    ("Cobalt Blue", "#0056B8"), ("Blue Gray", "#5B6579"), ("Pumpkin Orange", "#FF9016"),
]

_BAMBU_PLA_MATTE = [
    ("Ivory White", "#FFFFFF"), ("Charcoal", "#000000"), ("Bone White", "#CBC6B8"),
    ("Latte Brown", "#D3B7A7"), ("Scarlet Red", "#DE4343"), ("Marine Blue", "#0078BF"),
    ("Grass Green", "#61C680"), ("Lilac Purple", "#AE96D4"), ("Sakura Pink", "#E8AFCF"),
    ("Lemon Yellow", "#F7D959"), ("Mandarin Orange", "#F99963"), ("Dark Blue", "#042F56"),
    ("Dark Red", "#BB3D43"), ("Dark Green", "#68724D"), ("Ash Gray", "#9B9EA0"),
    ("Nardo Gray", "#757575"), ("Terracotta", "#B15533"), ("Desert Tan", "#E8DBB7"),
    ("Plum", "#950051"), ("Caramel", "#AE835B"), ("Apple Green", "#C3E5A0"),
    ("Sky Blue", "#A2C3E5"), ("Ice Blue", "#A3D8E1"), ("Dark Brown", "#7D6556"),
]

_BAMBU_PLA_SILK = [
    ("Silk Gold", "#F4A925"), ("Silk Silver", "#C0C0C0"), ("Silk Titan Gray", "#5F6367"),
    ("Silk White", "#FFFFFF"), ("Silk Blue", "#008BDA"), ("Silk Purple", "#8E44AD"),
    ("Silk Pink", "#F5A0B5"), ("Silk Green", "#018814"), ("Silk Candy Green", "#4CE4A0"),
    ("Silk Candy Red", "#D02727"),
]

_BAMBU_PETG_HF = [
    ("Jade White", "#FFFFFF"), ("Black", "#000000"), ("Gray", "#8E9089"),
    ("Dark Gray", "#545454"), ("Titan Gray", "#5F6367"), ("Red", "#E02E24"),
    ("Orange", "#FF6A13"), ("Yellow", "#F4EE2A"), ("Green", "#00AE42"),
    ("Forest Green", "#2A6737"), ("Lime Green", "#9FCF3A"), ("Blue", "#0A2989"),
    ("Lake Blue", "#1E88C7"), ("Cream", "#F8E8CF"), ("Peanut Brown", "#9D432C"),
]

_BAMBU_PETG_BASIC = [
    ("White", "#FFFFFF"), ("Black", "#000000"), ("Gray", "#8E9089"),
    ("Red", "#C12E1F"), ("Orange", "#FF6A13"), ("Yellow", "#F4EE2A"),
    ("Green", "#00AE42"), ("Blue", "#0A2989"),
]

_BAMBU_PLA_TOUGH = [
    ("White", "#FFFFFF"), ("Black", "#000000"), ("Gray", "#8E9089"),
    ("Red", "#C12E1F"), ("Blue", "#0A2989"), ("Yellow", "#F4EE2A"),
]

_BAMBU_ABS = [
    ("White", "#FFFFFF"), ("Black", "#000000"), ("Silver", "#A6A9AA"),
    ("Bambu Green", "#00AE42"), ("Red", "#D32020"), ("Blue", "#0A2989"),
    ("Azure", "#489FDF"), ("Navy Blue", "#0C2340"), ("Orange", "#FF6A13"),
    ("Yellow", "#F4EE2A"),
]

# =============================================================================================
# Polymaker
# =============================================================================================

_POLYTERRA_PLA = [
    ("Cotton White", "#F2F0E9"), ("Marble White", "#E9E7E0"), ("Ice", "#DDE7EA"),
    ("Fossil Grey", "#9C9A97"), ("Charcoal Black", "#1C1C1C"), ("Army Red", "#9C3B36"),
    ("Lava Red", "#C8102E"), ("Peach", "#F5B6A0"), ("Sunrise Orange", "#E96C24"),
    ("Savannah Yellow", "#E4B84A"), ("Banana", "#F2DC6B"), ("Muted Yellow", "#E0C98A"),
    ("Lime Green", "#A0C63F"), ("Forrest Green", "#2C5F3E"), ("Army Green", "#6E7A4F"),
    ("Teal", "#1C7C7C"), ("Sapphire Blue", "#1F4E9C"), ("Muted Blue", "#6E8CA8"),
    ("Lavender Purple", "#9B8AC4"), ("Army Beige", "#C8B79B"),
]

_POLYLITE_PLA = [
    ("White", "#F5F5F5"), ("Black", "#1A1A1A"), ("Grey", "#808285"),
    ("Light Grey", "#C6C7C8"), ("Silver", "#B5B7B8"), ("Red", "#C1272D"),
    ("Orange", "#F26722"), ("Yellow", "#F5D000"), ("Green", "#00994D"),
    ("Teal", "#009CA6"), ("Blue", "#1F5FAF"), ("Purple", "#7A4FA3"),
]

_POLYLITE_PETG = [
    ("White", "#F5F5F5"), ("Black", "#1A1A1A"), ("Grey", "#808285"),
    ("Red", "#C1272D"), ("Orange", "#F26722"), ("Yellow", "#F5D000"),
    ("Green", "#00994D"), ("Blue", "#1F5FAF"),
]

# =============================================================================================
# eSUN
# =============================================================================================

_ESUN_PLA_PLUS = [
    ("Cold White", "#F4F4F4"), ("Natural", "#EDE6D6"), ("Bone White", "#E8DFCB"),
    ("Black", "#1B1B1B"), ("Grey", "#808080"), ("Light Grey", "#C3C3C3"),
    ("Silver", "#B9BABC"), ("Red", "#C8102E"), ("Fire Engine Red", "#B2262B"),
    ("Orange", "#F26722"), ("Yellow", "#FFD500"), ("Green", "#009F4D"),
    ("Peak Green", "#00A66E"), ("Light Blue", "#59B7E3"), ("Blue", "#0057A8"),
    ("Purple", "#7A3E98"), ("Pink", "#F49AC1"), ("Brown", "#6B4423"),
    ("Beige", "#E5C8A6"), ("Gold", "#C6A03C"),
]

_ESUN_PETG = [
    ("Solid White", "#F4F4F4"), ("Solid Black", "#1B1B1B"), ("Grey", "#808080"),
    ("Red", "#C8102E"), ("Orange", "#F26722"), ("Yellow", "#FFD500"),
    ("Green", "#009F4D"), ("Blue", "#0057A8"),
]

# =============================================================================================
# SUNLU
# =============================================================================================

_SUNLU_PLA_PLUS = [
    ("White", "#F2F2F2"), ("Black", "#1A1A1A"), ("Grey", "#7E7E7E"),
    ("Light Grey", "#BFBFBF"), ("Silver", "#B6B8BA"), ("Red", "#C9262C"),
    ("Blue", "#1B5FA8"), ("Sky Blue", "#67B7E1"), ("Green", "#1E9B4B"),
    ("Yellow", "#F5D31A"), ("Orange", "#F07C1B"), ("Purple", "#7C3F9B"),
    ("Pink", "#F19FBB"), ("Brown", "#6E4326"),
]

_SUNLU_PLA_SILK = [
    ("Silk Gold", "#D7A93A"), ("Silk Silver", "#C3C6C8"), ("Silk Copper", "#B36A3A"),
    ("Silk Blue", "#2A7FC9"), ("Silk Purple", "#8B5FBF"), ("Silk Red", "#C43A3A"),
]

# =============================================================================================
# Overture / Hatchbox / Inland
# =============================================================================================

_OVERTURE_PLA = [
    ("White", "#F5F5F5"), ("Black", "#1A1A1A"), ("Gray", "#808080"),
    ("Space Gray", "#4A4A4A"), ("Red", "#C3232B"), ("Blue", "#1B4F9C"),
    ("Sky Blue", "#66C2E5"), ("Green", "#17A44E"), ("Yellow", "#F6D435"),
    ("Orange", "#F07E22"), ("Purple", "#7A4B9E"), ("Pink", "#F09CB6"),
]

_HATCHBOX_PLA = [
    ("White", "#F7F7F7"), ("Black", "#1A1A1A"), ("Gray", "#8C8C8C"),
    ("Silver", "#BCBEC0"), ("Red", "#C41E25"), ("Orange", "#F26522"),
    ("Yellow", "#FFE01B"), ("Green", "#00A651"), ("True Blue", "#0055A5"),
    ("Light Blue", "#6DC8E8"), ("Purple", "#6C3F98"), ("Brown", "#6A452B"),
]

_INLAND_PLA = [
    ("White", "#F6F6F6"), ("Black", "#1B1B1B"), ("Gray", "#888B8D"),
    ("Silver", "#BEC0C2"), ("Red", "#C6202A"), ("Blue", "#1F5AA8"),
    ("Green", "#17A055"), ("Yellow", "#F8D62B"), ("Orange", "#F27B1E"),
    ("Purple", "#7A4A9F"),
]

# =============================================================================================
# Prusament
# =============================================================================================

_PRUSAMENT_PLA = [
    ("Prusa Orange", "#FA6831"), ("Jet Black", "#010101"), ("Vanilla White", "#F4EEE1"),
    ("Lipstick Red", "#D02A3D"), ("Prusa Galaxy Black", "#2E2B2E"),
    ("Prusa Galaxy Silver", "#9A9EA2"), ("Prusa Galaxy Purple", "#5B4B7E"),
    ("Royal Blue", "#2A46A0"), ("Azure Blue", "#2A5CAA"), ("Mystic Green", "#2C6E4B"),
    ("Ms. Pink", "#E4468A"), ("Pearl Mouse", "#B3ADA3"), ("Gravity Grey", "#6C7276"),
    ("Simply Green", "#2D9E5B"),
]

_PRUSAMENT_PETG = [
    ("Jet Black", "#101010"), ("Signal White", "#F2F2F0"), ("Anthracite Grey", "#4C4E50"),
    ("Prusa Orange", "#FA6831"), ("Carmine Red", "#B01B2E"), ("Ultramarine Blue", "#2A3C8F"),
    ("Jungle Green", "#2F6B44"), ("Urban Grey", "#8A8D8F"),
]

# =============================================================================================
# Creality / Elegoo
# =============================================================================================

_CREALITY_HYPER_PLA = [
    ("White", "#F5F5F5"), ("Black", "#1A1A1A"), ("Grey", "#8B8D8E"),
    ("Red", "#C7282D"), ("Blue", "#1D5FA9"), ("Green", "#159F52"),
    ("Yellow", "#F6D22B"), ("Orange", "#F0801C"), ("Pink", "#F29CB4"),
    ("Purple", "#7B4BA0"),
]

_ELEGOO_PLA = [
    ("White", "#F4F4F4"), ("Black", "#191919"), ("Grey", "#838383"),
    ("Red", "#C42B2B"), ("Blue", "#1E5FB3"), ("Sky Blue", "#6BC0E8"),
    ("Green", "#23A455"), ("Yellow", "#F7D02C"), ("Orange", "#F2801F"),
    ("Purple", "#7E4AA4"), ("Pink", "#F2A0BC"), ("Brown", "#6E4A2E"),
]

# =============================================================================================
# Generic - Minecraft wool colors (exact in-game values)
# =============================================================================================

_GENERIC_MINECRAFT = [
    ("Minecraft White", "#F9FFFE"), ("Minecraft Orange", "#F9801D"),
    ("Minecraft Magenta", "#C74EBD"), ("Minecraft Light Blue", "#3AB3DA"),
    ("Minecraft Yellow", "#FED83D"), ("Minecraft Lime", "#80C71F"),
    ("Minecraft Pink", "#F38BAA"), ("Minecraft Gray", "#474F52"),
    ("Minecraft Light Gray", "#9D9D97"), ("Minecraft Cyan", "#169C9C"),
    ("Minecraft Purple", "#8932B8"), ("Minecraft Blue", "#3C44AA"),
    ("Minecraft Brown", "#835432"), ("Minecraft Green", "#5E7C16"),
    ("Minecraft Red", "#B02E26"), ("Minecraft Black", "#1D1D21"),
]


CATALOG: list[Filament] = [
    *_line("Bambu Lab", "PLA Basic", "PLA", _BAMBU_PLA_BASIC),
    *_line("Bambu Lab", "PLA Matte", "PLA", _BAMBU_PLA_MATTE),
    *_line("Bambu Lab", "PLA Silk", "PLA", _BAMBU_PLA_SILK),
    *_line("Bambu Lab", "PLA Tough", "PLA", _BAMBU_PLA_TOUGH),
    *_line("Bambu Lab", "PETG HF", "PETG", _BAMBU_PETG_HF),
    *_line("Bambu Lab", "PETG Basic", "PETG", _BAMBU_PETG_BASIC),
    *_line("Bambu Lab", "ABS", "ABS", _BAMBU_ABS),
    *_line("Polymaker", "PolyTerra PLA", "PLA", _POLYTERRA_PLA),
    *_line("Polymaker", "PolyLite PLA", "PLA", _POLYLITE_PLA),
    *_line("Polymaker", "PolyLite PETG", "PETG", _POLYLITE_PETG),
    *_line("eSUN", "PLA+", "PLA", _ESUN_PLA_PLUS),
    *_line("eSUN", "PETG", "PETG", _ESUN_PETG),
    *_line("SUNLU", "PLA+", "PLA", _SUNLU_PLA_PLUS),
    *_line("SUNLU", "PLA Silk", "PLA", _SUNLU_PLA_SILK),
    *_line("Overture", "PLA", "PLA", _OVERTURE_PLA),
    *_line("Hatchbox", "PLA", "PLA", _HATCHBOX_PLA),
    *_line("Inland", "PLA", "PLA", _INLAND_PLA),
    *_line("Prusament", "PLA", "PLA", _PRUSAMENT_PLA),
    *_line("Prusament", "PETG", "PETG", _PRUSAMENT_PETG),
    *_line("Creality", "Hyper PLA", "PLA", _CREALITY_HYPER_PLA),
    *_line("Elegoo", "PLA", "PLA", _ELEGOO_PLA),
    *_line("Generic", "Minecraft Wool", "PLA", _GENERIC_MINECRAFT),
]


def _copy(f: Filament) -> Filament:
    """Hand out copies so callers can edit a picked filament without touching the catalog."""
    return Filament.from_dict(f.to_dict())


def catalog_filaments(vendor: Optional[str] = None, material: Optional[str] = None) -> list[Filament]:
    """Catalog entries, optionally filtered by vendor and/or material (both case-insensitive)."""
    want_vendor = vendor.strip().lower() if vendor else None
    want_material = material.strip().lower() if material else None
    out = []
    for f in CATALOG:
        if want_vendor and f.vendor.lower() != want_vendor:
            continue
        if want_material and f.material.lower() != want_material:
            continue
        out.append(_copy(f))
    return out


def catalog_vendors() -> list[str]:
    """All filament vendors in the catalog, sorted."""
    return sorted({f.vendor for f in CATALOG})


def catalog_materials(vendor: Optional[str] = None) -> list[str]:
    """All materials in the catalog, sorted; optionally limited to one vendor."""
    want_vendor = vendor.strip().lower() if vendor else None
    return sorted({f.material for f in CATALOG
                   if not want_vendor or f.vendor.lower() == want_vendor})
