"""Block entities drawn from the game's own models: chests, beds, shulker boxes, heads and skulls.

Vanilla renders these blocks with ``ModelPart`` geometry (``ChestModel``, ``BedModel``, ``ShulkerModel``,
``SkullModel``) instead of JSON block models.  ``vanilla_models.json`` carries those layers, and each
renderer's pose chain is reproduced here as one affine transform (model units -> block units, y up),
so the mob rasterizer can stamp the block exactly like the game draws it: one joined double chest
with a single lock, a bed whose pillow sits at the head end, a shulker box hanging from a wall,
a skull turned to its ``rotation``.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..assets.models import rotation_matrix
from ..schematics.base import BlockState
from .models import MobModel, _layer_parts, vanilla_layers

E = "minecraft:entity/"
_YROT = {"south": 0.0, "west": 90.0, "north": 180.0, "east": 270.0}      # Direction.toYRot()
_STEP = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}
_DATA2D = {"south": 0, "west": 1, "north": 2, "east": 3}               # Direction.get2DDataValue()

_CHEST_TEXTURES = {
    "chest": "normal", "trapped_chest": "trapped", "ender_chest": "ender",
    "copper_chest": "copper", "exposed_copper_chest": "copper_exposed",
    "weathered_copper_chest": "copper_weathered", "oxidized_copper_chest": "copper_oxidized",
}
_HEADS = {
    "skeleton": ("skeleton_skull", E + "skeleton/skeleton"),
    "wither_skeleton": ("wither_skeleton_skull", E + "skeleton/wither_skeleton"),
    "zombie": ("zombie_head", E + "zombie/zombie"),
    "creeper": ("creeper_head", E + "creeper/creeper"),
    "player": ("player_head", E + "player/wide/steve"),
    "piglin": ("piglin_head", E + "piglin/piglin"),
    "dragon": ("dragon_skull", E + "enderdragon/dragon"),
}
_SHULKER_COLORS = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
                   "purple", "blue", "brown", "green", "red", "black")


@dataclass
class EntityBlock:
    model: MobModel
    A: np.ndarray        # 3x3: model units -> block units (y up)
    b: np.ndarray        # translation, units
    note: str


def _rx(deg: float) -> np.ndarray:
    return rotation_matrix("x", deg)


def _ry(deg: float) -> np.ndarray:
    return rotation_matrix("y", deg)


def _rz(deg: float) -> np.ndarray:
    return rotation_matrix("z", deg)


def _model(layer_name: str, texture: str, **kw) -> Optional[MobModel]:
    lay = vanilla_layers().get(layer_name)
    if lay is None:
        return None
    parts = _layer_parts(lay, **kw)
    return MobModel(layer_name, texture, parts, scale=float(lay.get("root_scale", 1.0)), tex_size=tuple(lay["texture_size"]),
                    label=layer_name, layer=layer_name)


def _about(R: np.ndarray, centre) -> tuple[np.ndarray, np.ndarray]:
    """Rotation ``R`` about ``centre``: p' = R (p - c) + c."""
    c = np.asarray(centre, dtype=np.float64)
    return R, c - R @ c


# ---------------------------------------------------------------------------------------
def _chest(state: BlockState) -> Optional[EntityBlock]:
    p = state.path[len("waxed_"):] if state.path.startswith("waxed_") else state.path
    base = _CHEST_TEXTURES.get(p)
    if base is None:
        return None
    kind = state.properties.get("type", "single")
    layer = {"left": "double_chest_left", "right": "double_chest_right"}.get(kind, "chest")
    tex = E + "chest/" + base + {"left": "_left", "right": "_right"}.get(kind, "")
    model = _model(layer, tex)
    if model is None:
        return None
    # ChestRenderer: translate(.5,.5,.5) · rotateY(-facing.toYRot()) · translate(-.5,-.5,-.5)
    A, b = _about(_ry(-_YROT.get(state.properties.get("facing", "north"), 180.0)), (8.0, 8.0, 8.0))
    return EntityBlock(model, A, b, f"block entity: {'double ' if kind != 'single' else ''}chest (game model)")


def _bed(state: BlockState) -> Optional[EntityBlock]:
    p = state.path
    if not (p.endswith("_bed") or p == "bed"):
        return None
    color = p[:-4] if p.endswith("_bed") else "red"
    part = state.properties.get("part", "foot")
    model = _model("bed_head" if part == "head" else "bed_foot", E + "bed/" + color)
    if model is None:
        return None
    # BedRenderer.renderPiece: translate(0, 9/16, 0) · rotateX(90) · translate(.5,.5,.5) · rotateZ(180 + facing.toYRot()) · translate(-.5,-.5,-.5)
    Rz, tz = _about(_rz(180.0 + _YROT.get(state.properties.get("facing", "north"), 180.0)), (8.0, 8.0, 8.0))
    Rx = _rx(90.0)
    A = Rx @ Rz
    b = Rx @ tz + np.array([0.0, 9.0, 0.0])
    return EntityBlock(model, A, b, "block entity: bed (game model)")


def _shulker(state: BlockState) -> Optional[EntityBlock]:
    p = state.path
    if not p.endswith("shulker_box"):
        return None
    color = p[:-len("_shulker_box")] if p != "shulker_box" else ""
    tex = E + "shulker/shulker" + (f"_{color}" if color in _SHULKER_COLORS else "")
    model = _model("shulker_box", tex)
    if model is None:
        return None
    facing = state.properties.get("facing", "up")
    # Direction.getRotation(): quaternion rotationXYZ(x, y, z) = Rx · Ry · Rz
    rot = {"up": (0, 0, 0), "down": (180, 0, 0), "north": (90, 0, 180), "south": (90, 0, 0), "west": (90, 0, 90), "east": (90, 0, -90)}[facing]
    Rdir = _rx(rot[0]) @ _ry(rot[1]) @ _rz(rot[2])
    # ShulkerBoxRenderer: translate(.5,.5,.5) · Rdir · scale(1,-1,-1) · translate(0,-1,0)
    A = Rdir @ np.diag([1.0, -1.0, -1.0])
    b = np.array([8.0, 8.0, 8.0]) + A @ np.array([0.0, -16.0, 0.0])
    return EntityBlock(model, A, b, "block entity: shulker box (game model)")


def _head(state: BlockState) -> Optional[EntityBlock]:
    m = re.match(r"^(.*?)_(wall_)?(head|skull)$", state.path)
    if not m or m.group(1) not in _HEADS:
        return None
    layer, tex = _HEADS[m.group(1)]
    model = _model(layer, tex)
    if model is None:
        return None
    wall = bool(m.group(2))
    S = np.diag([-1.0, -1.0, 1.0])
    if wall:
        facing = state.properties.get("facing", "north")
        sx, sz = _STEP.get(facing, (0, -1))
        f = 90.0 * (2 + _DATA2D.get(facing, 2))
        b0 = np.array([8.0 - sx * 4.0, 4.0, 8.0 - sz * 4.0])
    else:
        try:
            f = 22.5 * int(state.properties.get("rotation", "0"))
        except ValueError:
            f = 0.0
        b0 = np.array([8.0, 0.0, 8.0])
    # SkullBlockRenderer: translate · scale(-1,-1,1) · head.yRot = f
    A = S @ _ry(f)
    return EntityBlock(model, A, b0, f"block entity: {m.group(1)} {'wall ' if wall else ''}{m.group(3)} (game model)")


def entity_block(state: BlockState) -> Optional[EntityBlock]:
    """The game-model rendering of a block entity block, or None when the block is not one we know."""
    if state.namespace != "minecraft":
        return None
    for fn in (_chest, _bed, _shulker, _head):
        eb = fn(state)
        if eb is not None:
            return eb
    return None


__all__ = ["EntityBlock", "entity_block"]
