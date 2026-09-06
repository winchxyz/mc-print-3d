"""All user-facing conversion settings in one serialisable object (shared by CLI and GUI)."""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .util.paths import config_dir

DEFAULT_SOLID_TEXTURES = ["glass", "tinted_glass", "ice", "frosted_ice", "slime", "honey", "water", "lava", "leaves"]


@dataclass
class ConversionSettings:
    # ---- scale -------------------------------------------------------------------------
    block_mm: float = 5.0                     # size of one Minecraft block in the print
    target_size_mm: Optional[float] = None    # if set: scale so the longest horizontal side equals this
    fit_to_bed: bool = False                  # if set (and a bed is known): scale to fill the bed
    resolution: int = 0                       # sub-voxels per block; 0 = automatic from block_mm / nozzle
    nozzle_mm: float = 0.4
    min_thickness_mm: float = 0.8             # thin planes / wires are padded to this
    cutout_dilation: int = 1                  # grow cut-out textures by N texels (keeps flower stems printable)
    alpha_threshold: int = 96
    relief_mm: float = 0.5                    # surface relief carved from texture pixels (bricks, planks...); 0 = off
    relief_mode: str = "auto"                 # auto | heightmap | pattern | dark | light
    style: str = "textured"                   # 'textured' (relief) | 'flat' (smooth faces) | 'cubes' (every block a plain cube)
    build_type: str = "solid"                 # 'solid' (one model) | 'kit' (modular pieces with studs and sockets)
    # ---- modular kit ------------------------------------------------------------------
    kit_fit_mm: float = 0.15                  # stud/socket clearance per side
    kit_max_len: int = 6                      # longest merged bar (units)
    kit_textured: bool = False                # keep textures/relief on kit pieces
    kit_baseplate: bool = True
    kit_detailed: bool = True                 # non-cube blocks keep their geometry (else cubes)
    kit_alternate: bool = True                # alternate merge direction per layer (brick bond)
    # ---- content -----------------------------------------------------------------------
    include_fluids: bool = False
    unknown_policy: str = "cube"              # 'cube' | 'skip' for blocks without usable models
    solid_textures: list[str] = field(default_factory=lambda: list(DEFAULT_SOLID_TEXTURES))
    translucent_as_solid: bool = True
    remove_islands: bool = True
    island_min_blocks: int = 2
    keep_ground_only: bool = False
    fill_cavities: bool = True
    base_plate_mm: float = 0.0
    base_margin_blocks: int = 0
    crop: bool = True
    infer_connections: bool = True
    rotate_deg: int = 0                       # rotate the print around Z (0/90/180/270)
    # ---- colors -----------------------------------------------------------------------
    color_mode: str = "block"                 # 'block' (flat color per block type) | 'texel' (per texture pixel) | 'single'
    max_colors: int = 4                       # clusters / filament slots
    filaments: list[dict] = field(default_factory=list)   # PrintPalette (serialised Filament dicts)
    auto_assign: bool = True                  # map clusters to nearest filament automatically
    single_color_hex: str = "#C8C8C8"
    # ---- export -----------------------------------------------------------------------
    export_format: str = "3mf"                # see mcprint.export.EXPORT_FORMATS
    split_to_bed: bool = False
    bed_mm: Optional[list[float]] = None      # [x, y, z]
    bed_margin_mm: float = 5.0
    output_dir: str = ""
    # ---- assets -----------------------------------------------------------------------
    instance_name: str = ""                   # chosen Minecraft instance (by label)
    include_mods: bool = True
    use_resource_packs: bool = True
    extra_asset_paths: list[str] = field(default_factory=list)

    # ---- derived -----------------------------------------------------------------------
    def effective_resolution(self, block_mm: Optional[float] = None) -> int:
        if self.resolution and self.resolution > 0:
            return int(max(1, min(32, self.resolution)))
        bm = block_mm or self.block_mm
        voxel_mm = max(self.nozzle_mm * 1.2, 0.25)
        n = int(round(bm / voxel_mm))
        return int(max(1, min(16, n)))

    def min_thickness_units(self, block_mm: Optional[float] = None) -> float:
        bm = block_mm or self.block_mm
        return float(self.min_thickness_mm / bm * 16.0)

    def relief_units(self, block_mm: Optional[float] = None) -> float:
        if self.style != "textured":
            return 0.0
        bm = block_mm or self.block_mm
        return float(max(0.0, self.relief_mm) / bm * 16.0)

    # ---- persistence ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConversionSettings":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or (config_dir() / "settings.json")
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ConversionSettings":
        path = path or (config_dir() / "settings.json")
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except Exception:
            return cls()
