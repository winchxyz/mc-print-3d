"""Biome/redstone tint colors applied to faces with ``tintindex``.

Minecraft hard-codes which blocks are tinted and with what (grass colormap, foliage colormap,
water, redstone power...).  We approximate with the plains biome colors and name-based rules,
which also covers most mod blocks (mod leaves/grass typically use the same colormaps).
"""
from __future__ import annotations

import re
from typing import Optional

from ..schematics.base import BlockState

GRASS_PLAINS = (0x91, 0xBD, 0x59)
FOLIAGE_PLAINS = (0x77, 0xAB, 0x2F)
FOLIAGE_BIRCH = (0x80, 0xA7, 0x55)
FOLIAGE_SPRUCE = (0x61, 0x99, 0x61)
FOLIAGE_MANGROVE = (0x92, 0xBD, 0x59)
WATER = (0x3F, 0x76, 0xE4)
LILY_PAD = (0x20, 0x80, 0x30)
STEM = (0x8A, 0xC9, 0x3A)
REDSTONE_ON = (0xFF, 0x1E, 0x00)
REDSTONE_OFF = (0x4B, 0x00, 0x00)
DEFAULT_TINT = FOLIAGE_PLAINS

# Explicit rules (checked first), keyed by the block path (namespace stripped).
EXPLICIT: dict[str, tuple[int, int, int]] = {
    "grass_block": GRASS_PLAINS, "short_grass": GRASS_PLAINS, "grass": GRASS_PLAINS, "tall_grass": GRASS_PLAINS,
    "fern": GRASS_PLAINS, "large_fern": GRASS_PLAINS, "potted_fern": GRASS_PLAINS, "sugar_cane": GRASS_PLAINS,
    "pink_petals": GRASS_PLAINS, "bush": GRASS_PLAINS,
    "oak_leaves": FOLIAGE_PLAINS, "jungle_leaves": FOLIAGE_PLAINS, "acacia_leaves": FOLIAGE_PLAINS,
    "dark_oak_leaves": FOLIAGE_PLAINS, "vine": FOLIAGE_PLAINS, "mangrove_leaves": FOLIAGE_MANGROVE,
    "birch_leaves": FOLIAGE_BIRCH, "spruce_leaves": FOLIAGE_SPRUCE,
    "water": WATER, "bubble_column": WATER, "water_cauldron": WATER,
    "lily_pad": LILY_PAD,
    "attached_melon_stem": STEM, "attached_pumpkin_stem": STEM, "melon_stem": STEM, "pumpkin_stem": STEM,
}

# Blocks whose textures are already colored (no tint despite the name)
NO_TINT = {"cherry_leaves", "azalea_leaves", "flowering_azalea_leaves", "pale_oak_leaves", "leaf_litter", "dead_bush",
           "nether_sprouts", "warped_roots", "crimson_roots", "weeping_vines", "twisting_vines", "hanging_roots",
           "cave_vines", "cave_vines_plant", "spore_blossom", "sea_grass", "seagrass", "tall_seagrass", "kelp", "kelp_plant",
           "glow_lichen", "moss_block", "moss_carpet", "pale_moss_block"}


def tint_for(state: BlockState, tintindex: int = 0, overrides: Optional[dict[str, tuple[int, int, int]]] = None) -> Optional[tuple[int, int, int]]:
    """Return the multiplier color for a tinted face of ``state`` (None = no tint)."""
    name = state.path
    if overrides and name in overrides:
        return overrides[name]
    if overrides and state.name in overrides:
        return overrides[state.name]
    if name in NO_TINT:
        return None
    if name == "redstone_wire":
        try:
            p = int(state.properties.get("power", "0"))
        except ValueError:
            p = 0
        t = p / 15.0
        return (int(REDSTONE_OFF[0] + (REDSTONE_ON[0] - REDSTONE_OFF[0]) * t),
                int(REDSTONE_OFF[1] + (REDSTONE_ON[1] - REDSTONE_OFF[1]) * t),
                int(REDSTONE_OFF[2] + (REDSTONE_ON[2] - REDSTONE_OFF[2]) * t))
    if name in EXPLICIT:
        return EXPLICIT[name]
    # name based heuristics (mods)
    if re.search(r"(^|_)(leaves|leaf|foliage|vine|vines)($|_)", name):
        if "birch" in name:
            return FOLIAGE_BIRCH
        if "spruce" in name or "pine" in name or "fir" in name:
            return FOLIAGE_SPRUCE
        return FOLIAGE_PLAINS
    if re.search(r"(^|_)(grass|fern|sprout|reed|cane|bush|shrub|sapling|petals)($|_)", name):
        return GRASS_PLAINS
    if "water" in name:
        return WATER
    if "lily" in name:
        return LILY_PAD
    if "stem" in name and ("melon" in name or "pumpkin" in name):
        return STEM
    return DEFAULT_TINT


def apply_tint(rgb, tint: Optional[tuple[int, int, int]]):
    """Multiply an (..., 3) uint8 array by a tint color."""
    if tint is None:
        return rgb
    import numpy as np
    arr = np.asarray(rgb, dtype=np.float32)
    out = arr * (np.asarray(tint, dtype=np.float32) / 255.0)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)
