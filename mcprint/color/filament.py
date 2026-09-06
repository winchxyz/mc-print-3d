"""Filament definitions, the user's filament library, and print palettes.

A :class:`Filament` is a spool the user owns (or a catalog entry).  A :class:`PrintPalette` is the
ordered list of filaments loaded for one print (slot 1..N) – it is what block/texture colors get
mapped onto.
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..util.color import parse_hex, to_hex
from ..util.paths import config_dir


@dataclass
class Filament:
    color_hex: str                 # '#RRGGBB'
    name: str = ""                 # 'Jade White'
    material: str = "PLA"          # 'PLA', 'PETG', ...
    vendor: str = ""               # 'Bambu Lab'
    product: str = ""              # 'PLA Basic'
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "manual"         # 'manual' | 'catalog' | 'ams' | 'spoolman' | 'slicer' | ...
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.color_hex = to_hex(parse_hex(self.color_hex))

    @property
    def rgb(self) -> tuple[int, int, int]:
        return parse_hex(self.color_hex)

    @property
    def label(self) -> str:
        bits = [b for b in (self.vendor, self.product, self.name) if b]
        if not bits:
            bits = [self.material or "Filament"]
        return " ".join(bits)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Filament":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class FilamentLibrary:
    """Persistent list of the user's filaments (JSON file in the config dir)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (config_dir() / "filaments.json")
        self.filaments: list[Filament] = []
        self.load()

    def load(self) -> None:
        self.filaments = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for d in data.get("filaments", []):
                    try:
                        self.filaments.append(Filament.from_dict(d))
                    except Exception:
                        continue
            except Exception:
                self.filaments = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"filaments": [f.to_dict() for f in self.filaments]}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, filament: Filament, dedupe: bool = True) -> Filament:
        if dedupe:
            for f in self.filaments:
                if (f.color_hex == filament.color_hex and f.material == filament.material
                        and f.vendor == filament.vendor and f.name == filament.name and f.product == filament.product):
                    return f
        self.filaments.append(filament)
        return filament

    def add_many(self, filaments: Iterable[Filament]) -> int:
        n = len(self.filaments)
        for f in filaments:
            self.add(f)
        return len(self.filaments) - n

    def remove(self, filament_id: str) -> None:
        self.filaments = [f for f in self.filaments if f.id != filament_id]

    def get(self, filament_id: str) -> Optional[Filament]:
        for f in self.filaments:
            if f.id == filament_id:
                return f
        return None


@dataclass
class PrintPalette:
    """Filaments loaded for a print, in slot order (slot 1 = index 0)."""

    filaments: list[Filament] = field(default_factory=list)

    @property
    def colors_rgb(self) -> list[tuple[int, int, int]]:
        return [f.rgb for f in self.filaments]

    @property
    def hexes(self) -> list[str]:
        return [f.color_hex for f in self.filaments]

    def __len__(self) -> int:
        return len(self.filaments)

    @classmethod
    def from_hexes(cls, hexes: Iterable[str], material: str = "PLA") -> "PrintPalette":
        return cls([Filament(color_hex=h, name=h, material=material) for h in hexes])

    @classmethod
    def from_slots(cls, slots) -> "PrintPalette":
        """Build from a list of :class:`mcprint.printers.base.FilamentSlot`."""
        fils = []
        for s in slots:
            if not getattr(s, "loaded", True):
                continue
            fils.append(Filament(color_hex=s.color_hex, name=s.name or "", material=s.material or "PLA",
                                 vendor=s.vendor or "", product="", source=s.source or "printer",
                                 extra={"slot_index": s.index, "group": s.group}))
        return cls(fils)

    def to_dict(self) -> dict:
        return {"filaments": [f.to_dict() for f in self.filaments]}

    @classmethod
    def from_dict(cls, d: dict) -> "PrintPalette":
        return cls([Filament.from_dict(x) for x in d.get("filaments", [])])
