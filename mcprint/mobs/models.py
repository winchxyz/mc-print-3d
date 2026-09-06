"""Mob geometry in Minecraft's own ModelPart conventions.

Every part is written exactly like the vanilla ``LayerDefinition`` code: coordinates in model units
(1/16 block) with **y pointing down**, ``PartPose`` offsets and rotations (radians, applied as
rotation Z·Y·X), boxes as ``addBox(x, y, z, w, h, d)`` with ``texOffs(u, v)`` and the classic box UV
layout (top/bottom squares above four side strips).  The renderer's ``scale(-1, -1, 1)`` and the
``180 - yaw`` turn are reproduced in :mod:`mcprint.mobs.raster`, so a model copied from the game
code prints the right way round.

Textures are resource paths under ``textures/`` (``minecraft:entity/creeper/creeper``).  Overlays
(hat / jacket layers) are baked onto the box they cover instead of being printed as paper-thin
shells.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional

PI = math.pi
HALF_PI = math.pi / 2

# dye order used by sheep 'Color' bytes and wool names
DYE_NAMES = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
             "purple", "blue", "brown", "green", "red", "black"]
DYE_RGB: dict[str, tuple[int, int, int]] = {
    "white": (0xF9, 0xFF, 0xFE), "orange": (0xF9, 0x80, 0x1D), "magenta": (0xC7, 0x4E, 0xBD),
    "light_blue": (0x3A, 0xB3, 0xDA), "yellow": (0xFE, 0xD8, 0x3D), "lime": (0x80, 0xC7, 0x1F),
    "pink": (0xF3, 0x8B, 0xAA), "gray": (0x47, 0x4F, 0x52), "light_gray": (0x9D, 0x9D, 0x97),
    "cyan": (0x16, 0x9C, 0x9C), "purple": (0x89, 0x32, 0xB8), "blue": (0x3C, 0x44, 0xAA),
    "brown": (0x83, 0x54, 0x32), "green": (0x5E, 0x7C, 0x16), "red": (0xB0, 0x2E, 0x26), "black": (0x1D, 0x1D, 0x21),
}


@dataclass
class MobBox:
    origin: tuple[float, float, float]          # addBox x, y, z (part-relative, y down)
    size: tuple[float, float, float]            # w, h, d
    uv: tuple[float, float] = (0.0, 0.0)        # texOffs
    mirror: bool = False
    inflate: float = 0.0                        # CubeDeformation
    texture: Optional[str] = None               # override the model texture
    tint: Optional[tuple[int, int, int]] = None  # multiply (wool, slime...)
    translucent: bool = False                   # printed as the 'clear filament' color
    overlay_uv: Optional[tuple[float, float]] = None   # second skin layer baked where opaque
    overlay_texture: Optional[str] = None       # e.g. enderman eyes
    face_textures: Optional[dict] = None        # face -> (block texture resource, pixel rect or None): pumpkin heads


@dataclass
class MobPart:
    name: str
    boxes: list[MobBox] = field(default_factory=list)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)   # radians x, y, z (PartPose)
    parent: Optional[str] = None


@dataclass
class MobModel:
    id: str
    texture: str
    parts: list[MobPart]
    scale: float = 1.0                          # renderer scale (ghast 4.5, wither skeleton 1.2...)
    label: str = ""
    width: float = 1.0                          # hitbox width in blocks (informational)
    height: float = 1.0

    def part(self, name: str) -> MobPart:
        for p in self.parts:
            if p.name == name:
                return p
        raise KeyError(name)


def _box(x, y, z, w, h, d, u=0, v=0, **kw) -> MobBox:
    return MobBox((float(x), float(y), float(z)), (float(w), float(h), float(d)), (float(u), float(v)), **kw)


def _part(name, boxes, offset=(0, 0, 0), rotation=(0, 0, 0), parent=None) -> MobPart:
    return MobPart(name, list(boxes), tuple(float(v) for v in offset), tuple(float(v) for v in rotation), parent)


# ======================================================================================
# humanoids (HumanoidModel / PlayerModel / ZombieModel / SkeletonModel)
# ======================================================================================
def humanoid(mob_id: str, texture: str, *, layered: bool = True, slim: bool = False, arm_pose: str = "down",
             arm_w: float = 4.0, leg_w: float = 4.0, scale: float = 1.0, label: str = "") -> MobModel:
    """``layered``: 64x64 skin with hat/jacket/sleeve/pants layers (players, zombies) vs 64x32 (skeleton)."""
    aw = 3.0 if slim else arm_w
    arm_y = 2.5 if slim else 2.0
    if arm_pose == "zombie":
        arm_rot = (-PI / 2.2, -0.1, 0.0)
        arm_rot_l = (-PI / 2.2, 0.1, 0.0)
    else:
        arm_rot = arm_rot_l = (0.0, 0.0, 0.0)
    head = _box(-4, -8, -4, 8, 8, 8, 0, 0, overlay_uv=(32, 0) if layered else None)
    body = _box(-4, 0, -2, 8, 12, 4, 16, 16, overlay_uv=(16, 32) if layered else None)
    r_arm = _box(-aw + 1, -2, -2, aw, 12, 4, 40, 16, overlay_uv=(40, 32) if layered else None) if not slim else \
        _box(-2, -2, -2, 3, 12, 4, 40, 16, overlay_uv=(40, 32))
    if layered:
        l_arm = _box(-1, -2, -2, aw, 12, 4, 32, 48, overlay_uv=(48, 48))
        r_leg = _box(-2, 0, -2, leg_w, 12, 4, 0, 16, overlay_uv=(0, 32))
        l_leg = _box(-2, 0, -2, leg_w, 12, 4, 16, 48, overlay_uv=(0, 48))
    else:
        l_arm = _box(-1, -2, -2, aw, 12, 4, 40, 16, mirror=True)
        r_leg = _box(-2, 0, -2, leg_w, 12, 4, 0, 16)
        l_leg = _box(-2, 0, -2, leg_w, 12, 4, 0, 16, mirror=True)
    if arm_w != 4.0 and not slim:        # skeleton: 2-wide limbs centred on the shoulder
        r_arm = _box(-1, -2, -1, 2, 12, 2, 40, 16)
        l_arm = _box(-1, -2, -1, 2, 12, 2, 40, 16, mirror=True)
        r_leg = _box(-1, 0, -1, 2, 12, 2, 0, 16)
        l_leg = _box(-1, 0, -1, 2, 12, 2, 0, 16, mirror=True)
    leg_x = 1.9 if leg_w == 4.0 else 2.0
    parts = [
        _part("head", [head]),
        _part("body", [body]),
        _part("right_arm", [r_arm], (-5, arm_y, 0), arm_rot),
        _part("left_arm", [l_arm], (5, arm_y, 0), arm_rot_l),
        _part("right_leg", [r_leg], (-leg_x, 12, 0)),
        _part("left_leg", [l_leg], (leg_x, 12, 0)),
    ]
    return MobModel(mob_id, texture, parts, scale=scale, label=label or mob_id.replace("_", " "), width=0.6, height=1.95 * scale)


def creeper() -> MobModel:
    legs = [((-2, 18, 4), "right_hind_leg"), ((2, 18, 4), "left_hind_leg"), ((-2, 18, -4), "right_front_leg"), ((2, 18, -4), "left_front_leg")]
    parts = [
        _part("head", [_box(-4, -8, -4, 8, 8, 8, 0, 0)], (0, 6, 0)),
        _part("body", [_box(-4, 0, -2, 8, 12, 4, 16, 16)], (0, 6, 0)),
    ] + [_part(n, [_box(-2, 0, -2, 4, 6, 4, 0, 16)], off) for off, n in legs]
    return MobModel("creeper", "minecraft:entity/creeper/creeper", parts, label="creeper", width=0.6, height=1.7)


def enderman() -> MobModel:
    eyes = "minecraft:entity/enderman/enderman_eyes"
    parts = [
        _part("head", [_box(-4, -8, -4, 8, 8, 8, 0, 0, overlay_uv=(0, 0), overlay_texture=eyes)], (0, -13, 0)),
        _part("body", [_box(-4, 0, -2, 8, 12, 4, 32, 16)], (0, -14, 0)),
        _part("right_arm", [_box(-1, -2, -1, 2, 30, 2, 56, 0)], (-5, -12, 0)),
        _part("left_arm", [_box(-1, -2, -1, 2, 30, 2, 56, 0, mirror=True)], (5, -12, 0)),
        _part("right_leg", [_box(-1, 0, -1, 2, 30, 2, 56, 0)], (-2, -5, 0)),
        _part("left_leg", [_box(-1, 0, -1, 2, 30, 2, 56, 0, mirror=True)], (2, -5, 0)),
    ]
    return MobModel("enderman", "minecraft:entity/enderman/enderman", parts, label="enderman", width=0.6, height=2.9)


def villager() -> MobModel:
    parts = [
        _part("head", [_box(-4, -10, -4, 8, 10, 8, 0, 0)]),
        _part("nose", [_box(-1, -1, -6, 2, 4, 2, 24, 0)], (0, -2, 0), parent="head"),
        _part("body", [_box(-4, 0, -3, 8, 12, 6, 16, 20), _box(-4, 0, -3, 8, 18, 6, 0, 38, inflate=0.5)]),
        _part("arms", [_box(-8, -2, -2, 4, 8, 4, 44, 22), _box(4, -2, -2, 4, 8, 4, 44, 22, mirror=True),
                       _box(-4, 2, -2, 8, 4, 4, 40, 38)], (0, 2, 0), (-0.75, 0, 0)),
        _part("right_leg", [_box(-2, 0, -2, 4, 12, 4, 0, 22)], (-2, 12, 0)),
        _part("left_leg", [_box(-2, 0, -2, 4, 12, 4, 0, 22, mirror=True)], (2, 12, 0)),
    ]
    return MobModel("villager", "minecraft:entity/villager/villager", parts, label="villager", width=0.6, height=1.95)


def iron_golem() -> MobModel:
    parts = [
        _part("head", [_box(-4, -12, -5.5, 8, 10, 8, 0, 0), _box(-1, -5, -7.5, 2, 4, 2, 24, 0)], (0, -7, -2)),
        _part("body", [_box(-9, -2, -6, 18, 12, 11, 0, 40), _box(-4.5, 10, -3, 9, 5, 6, 0, 70, inflate=0.5)], (0, -7, 0)),
        _part("right_arm", [_box(-13, -2.5, -3, 4, 30, 6, 60, 21)], (0, -7, 0)),
        _part("left_arm", [_box(9, -2.5, -3, 4, 30, 6, 60, 58)], (0, -7, 0)),
        _part("right_leg", [_box(-3.5, -3, -3, 6, 16, 5, 37, 0)], (-4, 11, 0)),
        _part("left_leg", [_box(-3.5, -3, -3, 6, 16, 5, 60, 0, mirror=True)], (5, 11, 0)),
    ]
    return MobModel("iron_golem", "minecraft:entity/iron_golem/iron_golem", parts, label="iron golem", width=1.4, height=2.7)


def snow_golem() -> MobModel:
    pumpkin = {
        "north": ("minecraft:block/carved_pumpkin", None), "south": ("minecraft:block/pumpkin_side", None),
        "west": ("minecraft:block/pumpkin_side", None), "east": ("minecraft:block/pumpkin_side", None),
        "up": ("minecraft:block/pumpkin_top", None), "down": ("minecraft:block/pumpkin_top", None),
    }
    parts = [
        _part("head", [_box(-4, -16, -4, 8, 8, 8, 0, 0, inflate=-0.5), _box(-4, -16, -4, 8, 8, 8, 0, 0, face_textures=pumpkin)], (0, 4, 0)),
        _part("upper_body", [_box(-5, -10, -5, 10, 10, 10, 0, 16, inflate=-0.5)], (0, 13, 0)),
        _part("lower_body", [_box(-6, -12, -6, 12, 12, 12, 0, 36, inflate=-0.5)], (0, 24, 0)),
        _part("right_arm", [_box(-1, 0, -1, 12, 2, 2, 32, 0, inflate=-0.5)], (0, 6, 0), (0, 0, 1)),
        _part("left_arm", [_box(-1, 0, -1, 12, 2, 2, 32, 0, inflate=-0.5)], (0, 6, 0), (0, PI, -1)),
    ]
    return MobModel("snow_golem", "minecraft:entity/snow_golem", parts, label="snow golem", width=0.7, height=1.9)


# ======================================================================================
# quadrupeds
# ======================================================================================
def _quadruped(mob_id, texture, head_boxes, head_off, body_boxes, body_off, leg_box, leg_offs, extra=(), label="", width=0.9, height=0.9, scale=1.0):
    parts = [_part("head", head_boxes, head_off), _part("body", body_boxes, body_off, (HALF_PI, 0, 0))]
    names = ["right_hind_leg", "left_hind_leg", "right_front_leg", "left_front_leg"]
    for n, off in zip(names, leg_offs):
        parts.append(_part(n, [replace(leg_box)], off))
    parts.extend(extra)
    return MobModel(mob_id, texture, parts, scale=scale, label=label or mob_id.replace("_", " "), width=width, height=height)


def pig(texture: str = "minecraft:entity/pig/temperate_pig") -> MobModel:
    return _quadruped("pig", texture,
                      [_box(-4, -4, -8, 8, 8, 8, 0, 0), _box(-2, 0, -9, 4, 3, 1, 16, 16)], (0, 12, -6),
                      [_box(-5, -10, -7, 10, 16, 8, 28, 8)], (0, 11, 2),
                      _box(-2, 0, -2, 4, 6, 4, 0, 16), [(-3, 18, 7), (3, 18, 7), (-3, 18, -5), (3, 18, -5)],
                      label="pig", width=0.9, height=0.9)


def cow(texture: str = "minecraft:entity/cow/temperate_cow", mob_id: str = "cow") -> MobModel:
    return _quadruped(mob_id, texture,
                      [_box(-4, -4, -6, 8, 8, 6, 0, 0), _box(-5, -5, -4, 1, 3, 1, 22, 0), _box(4, -5, -4, 1, 3, 1, 22, 0)], (0, 4, -8),
                      [_box(-6, -10, -7, 12, 18, 10, 18, 4), _box(-2, 2, -8, 4, 6, 1, 52, 0)], (0, 5, 2),
                      _box(-2, 0, -2, 4, 12, 4, 0, 16), [(-4, 12, 7), (4, 12, 7), (-4, 12, -6), (4, 12, -6)],
                      label=mob_id, width=0.9, height=1.4)


def sheep(color: str = "white", sheared: bool = False) -> MobModel:
    wool = "minecraft:entity/sheep/sheep_wool"
    tint = DYE_RGB.get(color, DYE_RGB["white"])
    head = [_box(-3, -4, -4, 6, 6, 8, 0, 0)]
    body = [_box(-4, -10, -7, 8, 16, 6, 28, 8)]
    leg = _box(-2, 0, -2, 4, 12, 4, 0, 16)
    if not sheared:
        head.append(_box(-3, -4, -4, 6, 6, 6, 0, 0, inflate=0.6, texture=wool, tint=tint))
        body.append(_box(-4, -10, -7, 8, 16, 6, 28, 8, inflate=1.75, texture=wool, tint=tint))
    m = _quadruped("sheep", "minecraft:entity/sheep/sheep", head, (0, 6, -8), body, (0, 5, 2), leg,
                   [(-3, 12, 7), (3, 12, 7), (-3, 12, -5), (3, 12, -5)], label="sheep", width=0.9, height=1.3)
    if not sheared:
        for p in m.parts:
            if p.name.endswith("_leg"):
                p.boxes.append(_box(-2, 0, -2, 4, 6, 4, 0, 16, inflate=0.5, texture=wool, tint=tint))
    return m


def chicken(texture: str = "minecraft:entity/chicken/temperate_chicken") -> MobModel:
    parts = [
        _part("head", [_box(-2, -6, -2, 4, 6, 3, 0, 0), _box(-2, -4, -4, 4, 2, 2, 14, 0), _box(-1, -2, -3, 2, 2, 2, 14, 4)], (0, 15, -4)),
        _part("body", [_box(-3, -4, -3, 6, 8, 6, 0, 9)], (0, 16, 0), (HALF_PI, 0, 0)),
        _part("right_leg", [_box(-1, 0, -3, 3, 5, 3, 26, 0)], (-2, 19, 1)),
        _part("left_leg", [_box(-1, 0, -3, 3, 5, 3, 26, 0)], (1, 19, 1)),
        _part("right_wing", [_box(0, 0, -3, 1, 4, 6, 24, 13)], (-4, 13, 0)),
        _part("left_wing", [_box(-1, 0, -3, 1, 4, 6, 24, 13)], (4, 13, 0)),
    ]
    return MobModel("chicken", texture, parts, label="chicken", width=0.4, height=0.7)


def wolf(texture: str = "minecraft:entity/wolf/wolf") -> MobModel:
    leg = lambda: _box(0, 0, -1, 2, 8, 2, 0, 18)   # noqa: E731
    parts = [
        _part("head", [_box(-2, -3, -2, 6, 6, 4, 0, 0), _box(-2, -5, 0, 2, 2, 1, 16, 14), _box(2, -5, 0, 2, 2, 1, 16, 14),
                       _box(-0.5, 0, -5, 3, 3, 4, 0, 10)], (-1, 13.5, -7)),
        _part("body", [_box(-3, -2, -3, 6, 9, 6, 18, 14)], (0, 14, 2), (HALF_PI, 0, 0)),
        _part("upper_body", [_box(-3, -3, -3, 8, 6, 7, 21, 0)], (-1, 14, -3), (HALF_PI, 0, 0)),
        _part("right_hind_leg", [leg()], (-2.5, 16, 7)),
        _part("left_hind_leg", [leg()], (0.5, 16, 7)),
        _part("right_front_leg", [leg()], (-2.5, 16, -4)),
        _part("left_front_leg", [leg()], (0.5, 16, -4)),
        _part("tail", [_box(0, 0, -1, 2, 8, 2, 9, 18)], (-1, 12, 8), (0.62831855, 0, 0)),
    ]
    return MobModel("wolf", texture, parts, label="wolf", width=0.6, height=0.85)


def cat(texture: str = "minecraft:entity/cat/tabby", mob_id: str = "cat") -> MobModel:
    parts = [
        _part("head", [_box(-2.5, -2, -3, 5, 4, 5, 0, 0), _box(-1.5, 0, -4, 3, 2, 2, 0, 24),
                       _box(-2, -3, 0, 1, 1, 2, 0, 10), _box(1, -3, 0, 1, 1, 2, 6, 10)], (0, 15, -9)),
        _part("body", [_box(-2, 3, -8, 4, 16, 6, 20, 0)], (0, 12, -10), (HALF_PI, 0, 0)),
        _part("left_hind_leg", [_box(-1, 0, 1, 2, 6, 2, 8, 13)], (1.1, 18, 5)),
        _part("right_hind_leg", [_box(-1, 0, 1, 2, 6, 2, 8, 13)], (-1.1, 18, 5)),
        _part("left_front_leg", [_box(-1, 0, 0, 2, 10, 2, 40, 0)], (1.2, 14.1, -5)),
        _part("right_front_leg", [_box(-1, 0, 0, 2, 10, 2, 40, 0)], (-1.2, 14.1, -5)),
        _part("tail1", [_box(-0.5, 0, 0, 1, 8, 1, 0, 15)], (0.5, 15, 8), (0.9, 0, 0)),
        _part("tail2", [_box(-0.5, 0, 0, 1, 8, 1, 4, 15)], (0.5, 20, 14)),
    ]
    return MobModel(mob_id, texture, parts, label=mob_id, width=0.6, height=0.7)


# ======================================================================================
# others
# ======================================================================================
def spider(texture: str = "minecraft:entity/spider/spider", mob_id: str = "spider", scale: float = 1.0) -> MobModel:
    f, f2 = PI / 4, PI / 8
    legs = [  # name, side (-1 right / +1 left), z, zRot magnitude factor, yRot
        ("right_hind_leg", -1, 2, 1.0, 2 * f2), ("left_hind_leg", 1, 2, 1.0, -2 * f2),
        ("right_middle_hind_leg", -1, 1, 0.74, f2), ("left_middle_hind_leg", 1, 1, 0.74, -f2),
        ("right_middle_front_leg", -1, 0, 0.74, -f2), ("left_middle_front_leg", 1, 0, 0.74, f2),
        ("right_front_leg", -1, -1, 1.0, -2 * f2), ("left_front_leg", 1, -1, 1.0, 2 * f2),
    ]
    parts = [
        _part("head", [_box(-4, -4, -8, 8, 8, 8, 32, 4)], (0, 15, -3)),
        _part("body0", [_box(-3, -3, -3, 6, 6, 6, 0, 0)], (0, 15, 0)),
        _part("body1", [_box(-5, -4, -6, 10, 8, 12, 0, 12)], (0, 15, 9)),
    ]
    for name, side, z, k, yrot in legs:
        box = _box(-15, -1, -1, 16, 2, 2, 18, 0) if side < 0 else _box(-1, -1, -1, 16, 2, 2, 18, 0, mirror=True)
        parts.append(_part(name, [box], (4 * side, 15, z), (0, yrot, -f * k * side)))
    return MobModel(mob_id, texture, parts, scale=scale, label=mob_id.replace("_", " "), width=1.4 * scale, height=0.9 * scale)


def slime(size: int = 1) -> MobModel:
    parts = [
        _part("cube", [_box(-4, 16, -4, 8, 8, 8, 0, 16, translucent=True)]),
        _part("inner", [_box(-3, 17, -3, 6, 6, 6, 0, 0), _box(-3.25, 18, -3.5, 2, 2, 2, 32, 0),
                        _box(1.25, 18, -3.5, 2, 2, 2, 32, 4), _box(0, 21, -3.5, 1, 1, 1, 32, 8)]),
    ]
    s = float(max(1, size + 1))
    return MobModel("slime", "minecraft:entity/slime/slime", parts, scale=s, label="slime", width=0.51 * s, height=0.51 * s)


def ghast() -> MobModel:
    lengths = [8, 13, 9, 11, 11, 10, 12, 9, 12]   # RandomSource(1660).nextInt(7) + 8, like GhastModel
    parts = [_part("body", [_box(-8, -8, -8, 16, 16, 16, 0, 0)], (0, 17.6, 0))]
    for i, ln in enumerate(lengths):
        x = (((i % 3) - (i // 3 % 2) * 0.5 + 0.25) / 2.0 * 2.0 - 1.0) * 5.0
        z = ((i // 3) / 2.0 * 2.0 - 1.0) * 5.0
        parts.append(_part(f"tentacle{i}", [_box(-1, 0, -1, 2, ln, 2, 0, 0)], (x, 24.6, z)))
    return MobModel("ghast", "minecraft:entity/ghast/ghast", parts, scale=4.5, label="ghast", width=4.0, height=4.0)


def bee() -> MobModel:
    parts = [
        _part("bone", [_box(-3.5, -4, -5, 7, 7, 10, 0, 0), _box(0, -1, 5, 0, 1, 2, 26, 7),
                       _box(-5, 0, 0, 7, 2, 0, 26, 1), _box(-5, 0, 2, 7, 2, 0, 26, 3), _box(-5, 0, 4, 7, 2, 0, 26, 5)], (0, 19, 0)),
        _part("antennae", [_box(1.5, -2, -3, 1, 2, 3, 2, 0), _box(-2.5, -2, -3, 1, 2, 3, 2, 3)], (0, -2, -5), parent="bone"),
        _part("right_wing", [_box(-9, 0, 0, 9, 0, 6, 0, 18)], (-1.5, -4, -3), (0, -0.2618, 0), parent="bone"),
        _part("left_wing", [_box(0, 0, 0, 9, 0, 6, 0, 18, mirror=True)], (1.5, -4, -3), (0, 0.2618, 0), parent="bone"),
    ]
    return MobModel("bee", "minecraft:entity/bee/bee", parts, label="bee", width=0.7, height=0.6)


# ======================================================================================
# registry
# ======================================================================================
def _player(mob_id: str, tex: str, slim: bool, label: str) -> MobModel:
    return humanoid(mob_id, tex, layered=True, slim=slim, label=label)


BUILDERS: dict[str, "callable"] = {
    "creeper": creeper,
    "zombie": lambda: humanoid("zombie", "minecraft:entity/zombie/zombie", arm_pose="zombie", label="zombie"),
    "husk": lambda: humanoid("husk", "minecraft:entity/zombie/husk", arm_pose="zombie", label="husk"),
    "drowned": lambda: humanoid("drowned", "minecraft:entity/zombie/drowned", arm_pose="zombie", label="drowned"),
    "skeleton": lambda: humanoid("skeleton", "minecraft:entity/skeleton/skeleton", layered=False, arm_w=2.0, leg_w=2.0, label="skeleton"),
    "stray": lambda: humanoid("stray", "minecraft:entity/skeleton/stray", layered=False, arm_w=2.0, leg_w=2.0, label="stray"),
    "bogged": lambda: humanoid("bogged", "minecraft:entity/skeleton/bogged", layered=False, arm_w=2.0, leg_w=2.0, label="bogged"),
    "wither_skeleton": lambda: humanoid("wither_skeleton", "minecraft:entity/skeleton/wither_skeleton", layered=False, arm_w=2.0, leg_w=2.0, scale=1.2, label="wither skeleton"),
    "player": lambda: _player("player", "minecraft:entity/player/wide/steve", False, "player (Steve)"),
    "player_slim": lambda: _player("player_slim", "minecraft:entity/player/slim/alex", True, "player (Alex, slim)"),
    "villager": villager,
    "enderman": enderman,
    "iron_golem": iron_golem,
    "snow_golem": snow_golem,
    "spider": spider,
    "cave_spider": lambda: spider("minecraft:entity/spider/cave_spider", "cave_spider", 0.7),
    "slime": slime,
    "ghast": ghast,
    "pig": pig,
    "cow": cow,
    "mooshroom": lambda: cow("minecraft:entity/cow/red_mooshroom", "mooshroom"),
    "sheep": sheep,
    "chicken": chicken,
    "wolf": wolf,
    "cat": cat,
    "ocelot": lambda: cat("minecraft:entity/cat/ocelot", "ocelot"),
    "bee": bee,
}

MOB_IDS = sorted(BUILDERS)

# legacy / alias entity ids -> registry ids
ALIASES = {
    "villagergolem": "iron_golem", "snowman": "snow_golem", "ozelot": "cat", "mushroomcow": "mooshroom",
    "pigzombie": "zombie", "zombie_pigman": "zombie", "zombified_piglin": "zombie", "steve": "player", "alex": "player_slim",
    "cavespider": "cave_spider", "witherskeleton": "wither_skeleton", "irongolem": "iron_golem", "zombie_villager": "villager",
}

_VARIANT_TEXTURES = {
    "pig": "minecraft:entity/pig/{}_pig", "cow": "minecraft:entity/cow/{}_cow", "chicken": "minecraft:entity/chicken/{}_chicken",
}
_WOLF_TEXTURES = {"pale": "wolf", "ashen": "wolf_ashen", "black": "wolf_black", "chestnut": "wolf_chestnut", "rusty": "wolf_rusty",
                  "snowy": "wolf_snowy", "spotted": "wolf_spotted", "striped": "wolf_striped", "woods": "wolf_woods"}


def normalize_mob_id(raw: str) -> str:
    s = str(raw).strip().lower()
    if ":" in s:
        s = s.split(":", 1)[1]
    s = s.replace(" ", "_")
    return ALIASES.get(s, s)


def mob_model(mob_id: str, props: Optional[dict] = None) -> Optional[MobModel]:
    """Build the model for an entity id (``minecraft:creeper``, ``Creeper``...) with optional NBT-derived
    properties: ``color`` (sheep), ``sheared``, ``size`` (slime), ``variant`` (pig/cow/chicken/wolf/cat),
    ``texture`` (override resource or PNG path), ``slim`` (player)."""
    props = props or {}
    mid = normalize_mob_id(mob_id)
    if mid == "player" and props.get("slim"):
        mid = "player_slim"
    builder = BUILDERS.get(mid)
    if builder is None:
        return None
    if mid == "sheep":
        color = props.get("color", "white")
        if isinstance(color, int) or (isinstance(color, str) and color.isdigit()):
            color = DYE_NAMES[int(color) % 16]
        m = sheep(str(color), bool(props.get("sheared", False)))
    elif mid == "slime":
        m = slime(int(props.get("size", 1)))
    else:
        m = builder()
    variant = str(props.get("variant", "")).split(":")[-1]
    if variant:
        if mid in _VARIANT_TEXTURES and variant in ("temperate", "cold", "warm"):
            m.texture = _VARIANT_TEXTURES[mid].format(variant)
        elif mid == "wolf" and variant in _WOLF_TEXTURES:
            m.texture = f"minecraft:entity/wolf/{_WOLF_TEXTURES[variant]}"
        elif mid == "cat":
            m.texture = f"minecraft:entity/cat/{variant}"
    if props.get("texture"):
        m.texture = str(props["texture"])
    return m


__all__ = ["MobBox", "MobPart", "MobModel", "BUILDERS", "MOB_IDS", "ALIASES", "DYE_NAMES", "DYE_RGB",
           "mob_model", "normalize_mob_id", "humanoid"]
