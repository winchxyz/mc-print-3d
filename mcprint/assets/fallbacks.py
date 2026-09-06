"""Fallback geometry for blocks that have no JSON model geometry.

Vanilla renders chests, beds, signs, banners, heads, shulker boxes, conduits... with block-entity
renderers (``builtin/entity`` models).  Mod blocks may use custom model loaders we cannot parse.
For those we synthesise simple boxes so they still print with a sensible shape and color.

Coordinates are model units (0..16 within the block), (x, y, z) with y up.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..schematics.base import BlockState

Box = tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass
class FallbackShape:
    boxes: list[tuple[Box, Optional[str], Optional[tuple[int, int, int]]]]   # (box, texture resource, flat color)
    y_rotation: int = 0          # degrees, clockwise from above like blockstate 'y'
    reason: str = ""
    elements: list = field(default_factory=list)   # textured Elements (chest, bed) stamped like model geometry


WOOL_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (0xF9, 0xFF, 0xFE), "orange": (0xF9, 0x80, 0x1D), "magenta": (0xC7, 0x4E, 0xBD),
    "light_blue": (0x3A, 0xB3, 0xDA), "yellow": (0xFE, 0xD8, 0x3D), "lime": (0x80, 0xC7, 0x1F),
    "pink": (0xF3, 0x8B, 0xAA), "gray": (0x47, 0x4F, 0x52), "light_gray": (0x9D, 0x9D, 0x97),
    "cyan": (0x16, 0x9C, 0x9C), "purple": (0x89, 0x32, 0xB8), "blue": (0x3C, 0x44, 0xAA),
    "brown": (0x83, 0x54, 0x32), "green": (0x5E, 0x7C, 0x16), "red": (0xB0, 0x2E, 0x26), "black": (0x1D, 0x1D, 0x21),
}

HEAD_COLORS = {
    "skeleton": (0xC1, 0xC1, 0xC1), "wither_skeleton": (0x2B, 0x2B, 0x2B), "zombie": (0x4C, 0x7A, 0x4C),
    "creeper": (0x4C, 0xA5, 0x4A), "player": (0xA0, 0x70, 0x4E), "dragon": (0x2E, 0x2E, 0x2E), "piglin": (0xE8, 0xA4, 0x8E),
}

_FACING_Y = {"north": 0, "east": 90, "south": 180, "west": 270}
WOOD_COLOR = (0xB8, 0x94, 0x5F)
DARK_WOOD = (0x6B, 0x4A, 0x2A)


AIR_LIKE = {"air", "cave_air", "void_air", "light", "barrier", "structure_void", "moving_piston", "piston_extension",
            "bubble_column", "fire", "soul_fire", "jigsaw", "structure_block",
            "command_block", "chain_command_block", "repeating_command_block"}

_BLOCK_ENTITY_RE = re.compile(
    r"(^|_)(chest|shulker_box|bed|sign|hanging_sign|wall_sign|wall_hanging_sign|banner|wall_banner|head|skull|conduit|decorated_pot|bell)$")


def is_air_like(path: str) -> bool:
    return path in AIR_LIKE


def is_block_entity_block(path: str) -> bool:
    """Vanilla blocks drawn by block-entity renderers (their JSON model has no geometry)."""
    return bool(_BLOCK_ENTITY_RE.search(path)) or path in ("end_portal", "end_gateway")


GOLD = (0xF8, 0xC5, 0x3A)

# Blocks whose JSON model is only part of what the game draws; the rest is added by a block-entity
# renderer.  Boxes are (from, to, texture, color) in model units for the default (floor, north) state.
EXTRA_BOXES: dict[str, list[tuple[Box, Optional[str], Optional[tuple[int, int, int]]]]] = {
    "bell": [(((5, 6, 5), (11, 13, 11)), None, GOLD), (((4, 3, 4), (12, 6, 12)), None, GOLD)],
    "enchanting_table": [(((4, 12, 3), (12, 13.5, 13)), None, (0xC9, 0xB0, 0x8A))],   # the floating book
    "lectern": [(((3, 12, 4), (13, 14, 12)), None, (0xE6, 0xDC, 0xC0))],
    "campfire": [(((5, 0, 5), (11, 1, 11)), None, (0xFF, 0x8C, 0x1A))],
}


def extra_boxes_for(state: BlockState) -> list[tuple[Box, Optional[str], Optional[tuple[int, int, int]]]]:
    p = state.path
    if p in EXTRA_BOXES:
        if p == "campfire" and state.properties.get("lit", "true") == "false":
            return []
        return EXTRA_BOXES[p]
    return []


def _color_from_name(path: str) -> Optional[tuple[int, int, int]]:
    for c in sorted(WOOL_COLORS, key=len, reverse=True):
        if path.startswith(c + "_") or ("_" + c + "_") in path or path.endswith("_" + c):
            return WOOL_COLORS[c]
    return None


# --------------------------------------------------------------------------------------
# Textured block-entity shapes.  Vanilla draws chests and beds from entity atlases with the
# classic "box UV" layout of ModelPart (top/bottom squares above four side strips).  We rebuild
# the same boxes as model Elements whose faces carry the atlas rectangles, so the voxelizer
# stamps them exactly like JSON model geometry (per-texel colors, cut-outs, tints).
#
# Both are defined in the game's native orientation (facing SOUTH); ``_entity_y_rotation``
# turns them to the block's ``facing``.

def _uv16(W: int, H: int, u1: float, v1: float, u2: float, v2: float) -> tuple[float, float, float, float]:
    """Atlas pixel rectangle -> model UV space (0..16 spans the whole texture)."""
    return (u1 / W * 16.0, v1 / H * 16.0, u2 / W * 16.0, v2 / H * 16.0)


def _model_part_faces(tex: str, W: int, H: int, u: float, v: float, w: float, h: float, d: float,
                      skip: tuple[str, ...] = ()) -> dict:
    """Face -> Face for a y-up ModelPart box ``addBox(w, h, d)`` at ``texOffs(u, v)`` drawn without
    any mirroring (block-entity renderers).  UV rectangles follow the voxelizer's sampling
    directions, so flipped ranges are intentional (the chest atlas really is upside down)."""
    from .models import Face
    rects = {
        "north": (u + d + w, v + d + h, u + d, v + d),
        "south": (u + 2 * d + 2 * w, v + d + h, u + 2 * d + w, v + d),
        "west": (u + d, v + d + h, u, v + d),
        "east": (u + 2 * d + w, v + d + h, u + d + w, v + d),
        "up": (u + d + w, v + d, u + d + 2 * w, v),
        "down": (u + d, v, u + d + w, v + d),
    }
    return {f: Face(texture=tex, uv=_uv16(W, H, *r)) for f, r in rects.items() if f not in skip}


def _element(lo, hi, faces: dict):
    from .models import Element
    return Element(np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64), faces)


def _entity_y_rotation(props: dict) -> int:
    """Blockstate-style y rotation that turns a south-facing entity model to ``facing``."""
    f = props.get("facing", "south")
    return (_FACING_Y.get(f, 180) + 180) % 360


_CHEST_TEXTURES = {
    "chest": "normal", "trapped_chest": "trapped", "ender_chest": "ender",
    "copper_chest": "copper", "exposed_copper_chest": "copper_exposed",
    "weathered_copper_chest": "copper_weathered", "oxidized_copper_chest": "copper_oxidized",
}


def chest_elements(path: str, props: dict) -> tuple[list, str]:
    """(elements, texture) for vanilla single and double chests (native orientation: facing south)."""
    base = _CHEST_TEXTURES.get(path[len("waxed_"):] if path.startswith("waxed_") else path, "normal")
    kind = props.get("type", "single") if base != "ender" else "single"
    tex = f"minecraft:entity/chest/{base}" + ({"left": "_left", "right": "_right"}.get(kind, ""))
    W = H = 64
    if kind == "single":
        x0, x1, w = 1.0, 15.0, 14
        lock = ((7, 7, 15), (9, 11, 16))
    elif kind == "left":       # west half of a double chest (joined edge at x = 16)
        x0, x1, w = 1.0, 16.0, 15
        lock = ((15, 7, 15), (16, 11, 16))
    else:                      # right: east half
        x0, x1, w = 0.0, 15.0, 15
        lock = ((0, 7, 15), (1, 11, 16))
    els = [
        _element((x0, 0, 1), (x1, 10, 15), _model_part_faces(tex, W, H, 0, 19, w, 10, 14)),   # base
        _element((x0, 9, 1), (x1, 14, 15), _model_part_faces(tex, W, H, 0, 0, w, 5, 14)),     # lid
        _element(lock[0], lock[1], _model_part_faces(tex, W, H, 0, 0, lock[1][0] - lock[0][0], 4, 1)),   # latch
    ]
    return els, tex


def bed_elements(path: str, props: dict) -> tuple[list, str]:
    """(elements, texture) for one half of a vanilla bed (native orientation: head at south)."""
    from .models import Face
    color = path[:-4] if path.endswith("_bed") else "red"
    tex = f"minecraft:entity/bed/{color}"
    W = H = 64
    part = props.get("part", "foot")
    uv = lambda *r: _uv16(W, H, *r)   # noqa: E731
    if part == "head":
        faces = {
            "up": Face(tex, uv(22, 22, 6, 6)),
            "down": Face(tex, uv(28, 6, 44, 22)),
            "west": Face(tex, uv(28, 22, 22, 6), rotation=90),
            "east": Face(tex, uv(0, 6, 6, 22), rotation=90),
            "south": Face(tex, uv(22, 6, 6, 0)),          # head end (pillow side)
        }
        leg_z = (13, 16)
    else:
        faces = {
            "up": Face(tex, uv(22, 44, 6, 28)),
            "down": Face(tex, uv(28, 28, 44, 44)),
            "west": Face(tex, uv(28, 44, 22, 28), rotation=90),
            "east": Face(tex, uv(0, 28, 6, 44), rotation=90),
            "north": Face(tex, uv(22, 28, 38, 22)),        # foot end
        }
        leg_z = (0, 3)
    els = [_element((0, 3, 0), (16, 9, 16), faces)]
    leg_faces = {f: Face(tex, uv(53, 3, 56, 6)) for f in ("north", "south", "west", "east")}
    leg_faces["up"] = Face(tex, uv(53, 0, 56, 3))
    leg_faces["down"] = Face(tex, uv(53, 0, 56, 3))
    for x0 in (0, 13):
        els.append(_element((x0, 0, leg_z[0]), (x0 + 3, 3, leg_z[1]), dict(leg_faces)))
    return els, tex


def fallback_for(state: BlockState, particle_texture: Optional[str] = None, reason: str = "") -> FallbackShape:
    """Best-effort shape for a block without usable model geometry."""
    p = state.path
    props = state.properties
    y = 0
    if "facing" in props and props["facing"] in _FACING_Y:
        y = _FACING_Y[props["facing"]]
    elif "rotation" in props:
        try:
            y = int(round(int(props["rotation"]) * 22.5))
        except ValueError:
            y = 0

    # --- chests -----------------------------------------------------------------------
    if state.namespace == "minecraft" and (p in _CHEST_TEXTURES or p[len("waxed_"):] in _CHEST_TEXTURES):
        els, tex = chest_elements(p, props)
        return FallbackShape([(((1, 0, 1), (15, 14, 15)), tex, (0x9C, 0x6B, 0x3C))], _entity_y_rotation(props),
                             "block entity: chest", elements=els)
    if p.endswith("_chest"):
        return FallbackShape([(((1, 0, 1), (15, 14, 15)), particle_texture, (0x9C, 0x6B, 0x3C))], y, reason or "block entity: chest")
    # --- shulker boxes ----------------------------------------------------------------
    if p.endswith("shulker_box"):
        col = _color_from_name(p) or (0x8C, 0x62, 0x8C)
        return FallbackShape([(((0, 0, 0), (16, 16, 16)), None, col)], 0, "block entity: shulker box")
    # --- beds -------------------------------------------------------------------------
    if p.endswith("_bed") or p == "bed":
        col = _color_from_name(p) or (0xB0, 0x2E, 0x26)
        boxes = [(((0, 3, 0), (16, 9, 16)), None, col),
                 (((0, 0, 0), (3, 3, 3)), None, DARK_WOOD), (((13, 0, 0), (16, 3, 3)), None, DARK_WOOD),
                 (((0, 0, 13), (3, 3, 16)), None, DARK_WOOD), (((13, 0, 13), (16, 3, 16)), None, DARK_WOOD)]
        if state.namespace == "minecraft":
            els, _tex = bed_elements(p, props)
            return FallbackShape(boxes, _entity_y_rotation(props), "block entity: bed", elements=els)
        return FallbackShape(boxes, 0, "block entity: bed")
    # --- signs ------------------------------------------------------------------------
    if p.endswith("_wall_sign") or p == "wall_sign":
        return FallbackShape([(((0, 4.5, 14), (16, 12.5, 16)), particle_texture, WOOD_COLOR)], y, "block entity: wall sign")
    if p.endswith("_wall_hanging_sign") or p.endswith("_hanging_sign"):
        return FallbackShape([(((1, 0, 7), (15, 10, 9)), particle_texture, WOOD_COLOR),
                              (((7, 10, 7.5), (9, 16, 8.5)), None, (0x50, 0x50, 0x50))], y, "block entity: hanging sign")
    if p.endswith("_sign") or p == "sign":
        return FallbackShape([(((7, 0, 7), (9, 9, 9)), particle_texture, WOOD_COLOR),
                              (((0, 9, 7), (16, 16, 9)), particle_texture, WOOD_COLOR)], y, "block entity: sign")
    # --- banners ----------------------------------------------------------------------
    if p.endswith("_wall_banner"):
        col = _color_from_name(p) or WOOL_COLORS["white"]
        return FallbackShape([(((1, 0, 14), (15, 16, 15)), None, col), (((0, 14, 13), (16, 16, 16)), None, DARK_WOOD)], y, "block entity: wall banner")
    if p.endswith("_banner"):
        col = _color_from_name(p) or WOOL_COLORS["white"]
        return FallbackShape([(((7, 0, 7), (9, 16, 9)), None, DARK_WOOD), (((2, 2, 7.5), (14, 16, 8.5)), None, col)], y, "block entity: banner")
    # --- heads ------------------------------------------------------------------------
    if p.endswith("_head") or p.endswith("_skull"):
        base = re.sub(r"_(wall_)?(head|skull)$", "", p)
        col = HEAD_COLORS.get(base, (0xA0, 0x70, 0x4E))
        if "wall" in p:
            return FallbackShape([(((4, 4, 8), (12, 12, 16)), None, col)], y, "block entity: wall head")
        return FallbackShape([(((4, 0, 4), (12, 8, 12)), None, col)], y, "block entity: head")
    # --- misc vanilla block entities --------------------------------------------------
    if p == "conduit":
        return FallbackShape([(((5, 5, 5), (11, 11, 11)), None, (0x8E, 0xD0, 0xC6))], 0, "block entity: conduit")
    if p == "decorated_pot":
        return FallbackShape([(((1, 0, 1), (15, 16, 15)), None, (0xA0, 0x52, 0x2D))], 0, "block entity: decorated pot")
    if p in ("end_portal", "end_gateway"):
        return FallbackShape([(((0, 0, 0), (16, 12, 16)), None, (0x08, 0x08, 0x12))], 0, "block entity: portal")
    if p == "bell":
        return FallbackShape([(((4, 4, 4), (12, 13, 12)), None, (0xF8, 0xC5, 0x3A))], 0, "block entity: bell")
    if p.endswith("_candle_cake") or p == "candle_cake":
        return FallbackShape([(((1, 0, 1), (15, 8, 15)), None, (0xE8, 0xDA, 0xC8))], 0, "cake")
    if p == "piston_head" or p == "moving_piston":
        return FallbackShape([(((0, 0, 0), (16, 16, 16)), particle_texture, (0x9E, 0x83, 0x5A))], 0, "piston")
    # --- default: full cube colored by particle texture / name --------------------------
    col = _color_from_name(p)
    return FallbackShape([(((0, 0, 0), (16, 16, 16)), particle_texture, col)], 0, reason or "no geometry")
