"""Arrange kit pieces on print plates (shelf packing, one filament per plate)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .pieces import Kit, PieceType


@dataclass
class PlacedCopy:
    piece: PieceType
    x: float          # mm, lower-left corner on the plate
    y: float


@dataclass
class Plate:
    index: int
    material: int
    rgb: tuple[int, int, int]
    items: list[PlacedCopy] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)


def pack_plates(kit: Kit) -> list[Plate]:
    """Pack every copy of every piece onto plates that fit the bed, grouped by material."""
    s = kit.settings
    bx, by = s.bed_mm[0] - 2 * s.bed_margin_mm, s.bed_mm[1] - 2 * s.bed_margin_mm
    gap = s.spacing_mm
    plates: list[Plate] = []
    by_material: dict[int, list[PieceType]] = {}
    for p in kit.pieces:
        by_material.setdefault(p.material, []).append(p)
    index = 0
    for mat in sorted(by_material):
        pieces = by_material[mat]
        rgb = pieces[0].rgb
        items: list[PieceType] = []
        for p in sorted(pieces, key=lambda p: -kit.footprints.get(p.id, (0, 0, 0))[0] * kit.footprints.get(p.id, (0, 0, 0))[1]):
            items.extend([p] * p.count)
        plate: Plate | None = None
        x = y = row_h = 0.0
        for p in items:
            w, d, _h = kit.footprints.get(p.id, (s.unit_mm, s.unit_mm, s.unit_mm))
            if plate is None or (x + w > bx and y + row_h + gap + d > by):
                index += 1
                plate = Plate(index=index, material=mat, rgb=rgb)
                plates.append(plate)
                x = y = row_h = 0.0
            if x + w > bx:
                y += row_h + gap
                x = 0.0
                row_h = 0.0
            plate.items.append(PlacedCopy(p, s.bed_margin_mm + x, s.bed_margin_mm + y))
            x += w + gap
            row_h = max(row_h, d)
    return plates
