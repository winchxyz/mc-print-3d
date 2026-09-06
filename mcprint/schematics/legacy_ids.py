"""Legacy numeric block id + metadata -> modern (1.20/1.21) flattened block state.

MCEdit / Schematica ``.schematic`` files written by Minecraft 1.12.2 and earlier
store a world as two parallel byte arrays: ``Blocks`` (numeric block id 0-255,
extended to 0-4095 by the optional ``AddBlocks`` nibble array) and ``Data``
(a 4-bit metadata value per block).  The 1.13 "flattening" replaced that scheme
with namespaced block names plus named blockstate properties.

This module performs that translation.  The output names and property *values*
are exactly the ones used by ``assets/minecraft/blockstates/<name>.json`` in a
modern client jar, so the result can be fed straight into a blockstate ->
model -> texture resolver.

Design notes
------------
* Every vanilla 1.12.2 id in 0..255 is covered.  Ids that never existed
  (253, 254) and mod ids fall through to ``minecraft:unknown_legacy_<id>``.
* Only properties a legacy metadata value can actually determine are emitted.
  Purely modern bookkeeping (``waterlogged``, ``distance``, ``persistent``,
  wall/fence/pane connection bits, stair corner shapes, ...) is left out so the
  resolver falls back to the model defaults.
* Metadata is masked to 4 bits, matching the on-disk representation.
* Nothing here raises: a malformed id or metadata degrades to the
  ``unknown_legacy_*`` placeholder.

Deliberately lossy conversions are marked with "LOSSY:" comments below.
"""
from __future__ import annotations

from typing import Callable, Dict, Tuple

__all__ = ["legacy_block", "legacy_block_from_name", "LEGACY_NAME_TO_ID"]

MC = "minecraft:"

Handler = Callable[[int], Tuple[str, Dict[str, str]]]

# --------------------------------------------------------------------------- #
# Shared vocabularies
# --------------------------------------------------------------------------- #

#: Dye order used by wool, glass, terracotta, carpet, concrete, shulker boxes...
#: (1.12 called index 8 "silver"; the flattening renamed it to light_gray.)
COLORS = (
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
    "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black",
)

#: 1.12 BlockPlanks.EnumType order.
WOODS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak")

#: EnumFacing.getIndex() -- pistons, dispensers, droppers, observers, end rods,
#: command blocks, hoppers.
FACING6 = ("down", "up", "north", "south", "west", "east")

#: The same index restricted to the four wall directions.  Blocks that can only
#: face sideways (ladders, chests, furnaces, wall signs, wall banners, wall
#: skulls) never store 0 or 1, so those fold back onto north.
FACING_WALL = {2: "north", 3: "south", 4: "west", 5: "east"}

#: EnumFacing.getHorizontalIndex() -- beds, anvils, cocoa, fence gates,
#: repeaters, comparators, carved pumpkins, end portal frames, glazed terracotta.
FACING_H = ("south", "west", "north", "east")

#: BlockStairs stores ``5 - facing.getIndex()`` in the low two bits.
STAIR_FACING = ("east", "west", "south", "north")

#: BlockDoor's lower half stores ``facing.rotateY().getHorizontalIndex()``.
DOOR_FACING = ("east", "south", "west", "north")

#: BlockTrapDoor's own private facing order.
TRAPDOOR_FACING = ("north", "south", "west", "east")

#: BlockTorch / BlockButton wall metadata -> facing (5 = free standing).
TORCH_WALL = {1: "east", 2: "west", 3: "south", 4: "north"}

#: BlockRail shape order.
RAIL_SHAPES = (
    "north_south", "east_west", "ascending_east", "ascending_west",
    "ascending_north", "ascending_south",
    "south_east", "south_west", "north_west", "north_east",
)

#: BlockLever EnumOrientation -> (face, facing).
LEVER_ORIENTATION = {
    0: ("ceiling", "west"),    # DOWN_X
    1: ("wall", "east"),
    2: ("wall", "west"),
    3: ("wall", "south"),
    4: ("wall", "north"),
    5: ("floor", "north"),     # UP_Z
    6: ("floor", "west"),      # UP_X
    7: ("ceiling", "north"),   # DOWN_Z
}

#: BlockButton metadata -> (face, facing).  LOSSY: a 1.12 floor/ceiling button
#: stored no rotation at all, so the flattening's default of north is used.
BUTTON_FACE = {
    0: ("ceiling", "north"),
    1: ("wall", "east"),
    2: ("wall", "west"),
    3: ("wall", "south"),
    4: ("wall", "north"),
    5: ("floor", "north"),
}

#: BlockStoneSlab.EnumType -> modern slab block.
STONE_SLABS = (
    "smooth_stone_slab", "sandstone_slab", "petrified_oak_slab", "cobblestone_slab",
    "brick_slab", "stone_brick_slab", "nether_brick_slab", "quartz_slab",
)

#: "Seamless" double stone slabs became genuinely smooth full blocks in 1.13.
SEAMLESS_DOUBLE_SLABS = {8: "smooth_stone", 9: "smooth_sandstone", 15: "smooth_quartz"}

DOUBLE_PLANTS = ("sunflower", "lilac", "tall_grass", "large_fern", "rose_bush", "peony")

FLOWERS = (
    "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip",
    "orange_tulip", "white_tulip", "pink_tulip", "oxeye_daisy",
)

STONE_VARIANTS = (
    "stone", "granite", "polished_granite", "diorite",
    "polished_diorite", "andesite", "polished_andesite",
)

STONE_BRICKS = (
    "stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks", "chiseled_stone_bricks",
)

INFESTED = (
    "infested_stone", "infested_cobblestone", "infested_stone_bricks",
    "infested_mossy_stone_bricks", "infested_cracked_stone_bricks",
    "infested_chiseled_stone_bricks",
)

STRUCTURE_MODES = ("save", "load", "corner", "data")

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _at(seq: Tuple[str, ...], index: int) -> str:
    """``seq[index]``, folding out-of-range indices back onto the first entry."""
    return seq[index] if 0 <= index < len(seq) else seq[0]


def _b(flag: object) -> str:
    """Blockstate booleans are the strings ``"true"`` / ``"false"``."""
    return "true" if flag else "false"


def _const(name: str, **props: str) -> Handler:
    """A block whose metadata carries nothing (or a fixed property set)."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, dict(props)
    return handler


def _variants(names: Tuple[str, ...], mask: int = 15) -> Handler:
    """Metadata selects the block itself, e.g. stone -> granite / diorite / ..."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + _at(names, meta & mask), {}
    return handler


def _colored(suffix: str) -> Handler:
    """Sixteen dye colors sharing one id, e.g. 35 -> ``<color>_wool``."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + COLORS[meta & 15] + "_" + suffix, {}
    return handler


def _facing6_block(name: str, extra_bit: str) -> Handler:
    """Pistons, dispensers, droppers, observers, command blocks: facing + flag."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"facing": _at(FACING6, meta & 7), extra_bit: _b(meta & 8)}
    return handler


def _facing6_simple(name: str) -> Handler:
    """Raw EnumFacing index with no extra flag bit (end rods)."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"facing": _at(FACING6, meta & 7)}
    return handler


def _facing_wall(name: str) -> Handler:
    """Ladders, chests, furnaces, wall signs, wall banners: sideways only."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"facing": FACING_WALL.get(meta & 7, "north")}
    return handler


def _facing_h(name: str) -> Handler:
    """Horizontal facing in the low two bits (south / west / north / east)."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"facing": FACING_H[meta & 3]}
    return handler


def _axis_pillar(name: str) -> Handler:
    """BlockRotatedPillar: bits 2-3 hold the axis (0 = y, 4 = x, 8 = z)."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"axis": {0: "y", 4: "x", 8: "z"}.get(meta & 12, "y")}
    return handler


def _aged(name: str, prop: str = "age", limit: int = 15) -> Handler:
    """Crops, fire, cactus, sugar cane, frosted ice, chorus flower, fluids, ..."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {prop: str(min(meta & 15, limit))}
    return handler


def _stairs(species: str) -> Handler:
    """All fourteen legacy stair ids share one metadata layout."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + species + "_stairs", {
            "facing": STAIR_FACING[meta & 3],
            "half": "top" if meta & 4 else "bottom",
            # LOSSY: 1.12 derived the corner shape from the neighbours at render
            # time, so a straight stair is the only honest single-block answer.
            "shape": "straight",
        }
    return handler


def _slab(name: str) -> Handler:
    """Single slab: bit 3 raises it into the top half."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"type": "top" if meta & 8 else "bottom"}
    return handler


def _double_slab(name: str) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"type": "double"}
    return handler


def _wood_slab(double: bool) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        kind = "double" if double else ("top" if meta & 8 else "bottom")
        return MC + _at(WOODS, meta & 7) + "_slab", {"type": kind}
    return handler


def _door(species: str) -> Handler:
    """Doors split their metadata between the two halves of the block."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        name = MC + species + "_door"
        if meta & 8:
            # Upper half: hinge side and powered flag, no facing of its own.
            return name, {
                "half": "upper",
                "hinge": "right" if meta & 1 else "left",
                "powered": _b(meta & 2),
            }
        return name, {
            "half": "lower",
            "facing": DOOR_FACING[meta & 3],
            "open": _b(meta & 4),
        }
    return handler


def _trapdoor(name: str) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {
            "facing": TRAPDOOR_FACING[meta & 3],
            "open": _b(meta & 4),
            "half": "top" if meta & 8 else "bottom",
        }
    return handler


def _fence_gate(species: str) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + species + "_fence_gate", {
            "facing": FACING_H[meta & 3],
            "open": _b(meta & 4),
        }
    return handler


def _torch(ground: str, wall: str, **extra: str) -> Handler:
    """Torches: metadata 1-4 hang on a wall, 5 (and 0) stand on the ground."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        facing = TORCH_WALL.get(meta & 7)
        if facing is None:
            return MC + ground, dict(extra)
        props = {"facing": facing}
        props.update(extra)
        return MC + wall, props
    return handler


def _log(species_table: Tuple[str, ...]) -> Handler:
    """Logs: bits 0-1 species, bits 2-3 axis (12 = bark on every face)."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        species = _at(species_table, meta & 3)
        orientation = meta & 12
        if orientation == 12:
            return MC + species + "_wood", {}
        return MC + species + "_log", {"axis": {0: "y", 4: "x", 8: "z"}[orientation]}
    return handler


def _leaves(species_table: Tuple[str, ...]) -> Handler:
    """Leaves: bits 0-1 species; bits 2-3 are decay bookkeeping we drop."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + _at(species_table, meta & 3) + "_leaves", {}
    return handler


def _rail(name: str, powered: bool) -> Handler:
    """Plain rails use shapes 0-9; the three powered rails use 0-5 plus bit 3."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        if not powered:
            return MC + name, {"shape": _at(RAIL_SHAPES, meta & 15)}
        shape_bits = meta & 7
        shape = RAIL_SHAPES[shape_bits] if shape_bits < 6 else RAIL_SHAPES[0]
        return MC + name, {"shape": shape, "powered": _b(meta & 8)}
    return handler


def _mushroom_block(cap: str) -> Handler:
    """Huge mushrooms: 10 and 15 are all-stem, everything else keeps the cap.

    LOSSY: the modern per-face booleans are left to the resolver; the legacy
    metadata picked one of fourteen fixed face layouts instead.
    """
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        if (meta & 15) in (10, 15):
            return MC + "mushroom_stem", {}
        return MC + cap, {}
    return handler


def _button(species: str) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        face, facing = BUTTON_FACE.get(meta & 7, BUTTON_FACE[0])
        return MC + species + "_button", {"face": face, "facing": facing}
    return handler


def _rotation(name: str) -> Handler:
    """Standing signs and banners: sixteen rotation steps."""
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + name, {"rotation": str(meta & 15)}
    return handler


def _repeater(powered: bool) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + "repeater", {
            "facing": FACING_H[meta & 3],
            "delay": str(((meta >> 2) & 3) + 1),
            "powered": _b(powered),
        }
    return handler


def _comparator(powered: bool) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + "comparator", {
            "facing": FACING_H[meta & 3],
            "mode": "subtract" if meta & 4 else "compare",
            "powered": _b(powered or bool(meta & 8)),
        }
    return handler


def _daylight_detector(inverted: bool) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + "daylight_detector", {
            "inverted": _b(inverted),
            "power": str(meta & 15),
        }
    return handler


def _glazed_terracotta(color: str) -> Handler:
    def handler(meta: int) -> Tuple[str, Dict[str, str]]:
        return MC + color + "_glazed_terracotta", {"facing": FACING_H[meta & 3]}
    return handler


# --------------------------------------------------------------------------- #
# One-off handlers
# --------------------------------------------------------------------------- #


def _bed(meta: int) -> Tuple[str, Dict[str, str]]:
    # LOSSY: 1.12 had exactly one bed block and kept the dye color in the block
    # entity, so red (the vanilla crafting result) stands in for every bed.
    return MC + "red_bed", {
        "facing": FACING_H[meta & 3],
        "part": "head" if meta & 8 else "foot",
    }


def _piston_head(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "piston_head", {
        "facing": _at(FACING6, meta & 7),
        "type": "sticky" if meta & 8 else "normal",
    }


def _moving_piston(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "moving_piston", {
        "facing": _at(FACING6, meta & 7),
        "type": "sticky" if meta & 8 else "normal",
    }


def _stone_slab(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + STONE_SLABS[meta & 7], {"type": "top" if meta & 8 else "bottom"}


def _double_stone_slab(meta: int) -> Tuple[str, Dict[str, str]]:
    if meta & 8:
        smooth = SEAMLESS_DOUBLE_SLABS.get(meta & 15)
        if smooth is not None:
            return MC + smooth, {}
    return MC + STONE_SLABS[meta & 7], {"type": "double"}


def _red_sandstone_double_slab(meta: int) -> Tuple[str, Dict[str, str]]:
    if (meta & 15) == 8:
        return MC + "smooth_red_sandstone", {}
    return MC + "red_sandstone_slab", {"type": "double"}


def _tallgrass(meta: int) -> Tuple[str, Dict[str, str]]:
    # 0 was the "shrub" variant only reachable via commands, 1 the ordinary
    # grass (renamed short_grass in 1.20.3), 2 the fern.
    return MC + _at(("dead_bush", "short_grass", "fern"), meta & 3), {}


def _double_plant(meta: int) -> Tuple[str, Dict[str, str]]:
    if meta & 8:
        # LOSSY: the upper half of a 1.12 double plant stored the block's
        # *facing*, not its species -- the species existed only in the lower
        # half.  Nothing in a single (id, meta) pair can recover it, so the
        # first variant stands in.  Callers holding the whole grid can improve
        # on this by copying the species from the block below.
        return MC + DOUBLE_PLANTS[0], {"half": "upper"}
    return MC + _at(DOUBLE_PLANTS, meta & 7), {"half": "lower"}


def _quartz_block(meta: int) -> Tuple[str, Dict[str, str]]:
    m = meta & 15
    if m == 1:
        return MC + "chiseled_quartz_block", {}
    if m in (2, 3, 4):
        return MC + "quartz_pillar", {"axis": {2: "y", 3: "x", 4: "z"}[m]}
    return MC + "quartz_block", {}


def _anvil(meta: int) -> Tuple[str, Dict[str, str]]:
    name = _at(("anvil", "chipped_anvil", "damaged_anvil"), (meta >> 2) & 3)
    return MC + name, {"facing": FACING_H[meta & 3]}


def _vine(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "vine", {
        "south": _b(meta & 1),
        "west": _b(meta & 2),
        "north": _b(meta & 4),
        "east": _b(meta & 8),
    }


def _cauldron(meta: int) -> Tuple[str, Dict[str, str]]:
    level = meta & 3
    if level == 0:
        return MC + "cauldron", {}
    return MC + "water_cauldron", {"level": str(level)}


def _cocoa(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "cocoa", {
        "facing": FACING_H[meta & 3],
        "age": str(min((meta >> 2) & 3, 2)),
    }


def _skull(meta: int) -> Tuple[str, Dict[str, str]]:
    # LOSSY: the mob type (and a floor skull's rotation) lived in the block
    # entity, so every legacy skull comes back as a skeleton skull.
    if (meta & 15) == 1:
        return MC + "skeleton_skull", {}
    return MC + "skeleton_wall_skull", {"facing": FACING_WALL.get(meta & 7, "north")}


def _lever(meta: int) -> Tuple[str, Dict[str, str]]:
    face, facing = LEVER_ORIENTATION[meta & 7]
    return MC + "lever", {"face": face, "facing": facing, "powered": _b(meta & 8)}


def _snow_layer(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "snow", {"layers": str(min((meta & 7) + 1, 8))}


def _end_portal_frame(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "end_portal_frame", {"facing": FACING_H[meta & 3], "eye": _b(meta & 4)}


def _brewing_stand(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "brewing_stand", {
        "has_bottle_0": _b(meta & 1),
        "has_bottle_1": _b(meta & 2),
        "has_bottle_2": _b(meta & 4),
    }


def _nether_portal(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "nether_portal", {"axis": "z" if (meta & 15) == 2 else "x"}


def _tripwire_hook(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "tripwire_hook", {
        "facing": FACING_H[meta & 3],
        "attached": _b(meta & 4),
        "powered": _b(meta & 8),
    }


def _tripwire(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "tripwire", {
        "powered": _b(meta & 1),
        "attached": _b(meta & 4),
        "disarmed": _b(meta & 8),
    }


def _sapling(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + _at(WOODS, meta & 7) + "_sapling", {"stage": str((meta >> 3) & 1)}


def _hopper(meta: int) -> Tuple[str, Dict[str, str]]:
    # A hopper can never point up, so metadata 1 does not occur in vanilla saves.
    facing = _at(FACING6, meta & 7)
    return MC + "hopper", {"facing": "down" if facing == "up" else facing}


def _structure_block(meta: int) -> Tuple[str, Dict[str, str]]:
    return MC + "structure_block", {"mode": _at(STRUCTURE_MODES, meta & 3)}


# --------------------------------------------------------------------------- #
# Numeric id -> handler.  Every vanilla 1.12.2 block is listed; the gaps
# (253, 254) never existed and fall through to the unknown placeholder.
# --------------------------------------------------------------------------- #

_HANDLERS: Dict[int, Handler] = {
    0: _const("air"),
    1: _variants(STONE_VARIANTS),
    2: _const("grass_block", snowy="false"),
    3: _variants(("dirt", "coarse_dirt", "podzol")),
    4: _const("cobblestone"),
    5: _variants(tuple(w + "_planks" for w in WOODS), mask=7),
    6: _sapling,
    7: _const("bedrock"),
    8: _aged("water", "level"),
    9: _aged("water", "level"),
    10: _aged("lava", "level"),
    11: _aged("lava", "level"),
    12: _variants(("sand", "red_sand")),
    13: _const("gravel"),
    14: _const("gold_ore"),
    15: _const("iron_ore"),
    16: _const("coal_ore"),
    17: _log(WOODS[:4]),
    18: _leaves(WOODS[:4]),
    19: _variants(("sponge", "wet_sponge")),
    20: _const("glass"),
    21: _const("lapis_ore"),
    22: _const("lapis_block"),
    23: _facing6_block("dispenser", "triggered"),
    24: _variants(("sandstone", "chiseled_sandstone", "cut_sandstone")),
    25: _const("note_block"),
    26: _bed,
    27: _rail("powered_rail", powered=True),
    28: _rail("detector_rail", powered=True),
    29: _facing6_block("sticky_piston", "extended"),
    30: _const("cobweb"),
    31: _tallgrass,
    32: _const("dead_bush"),
    33: _facing6_block("piston", "extended"),
    34: _piston_head,
    35: _colored("wool"),
    36: _moving_piston,
    37: _const("dandelion"),
    38: _variants(FLOWERS),
    39: _const("brown_mushroom"),
    40: _const("red_mushroom"),
    41: _const("gold_block"),
    42: _const("iron_block"),
    43: _double_stone_slab,
    44: _stone_slab,
    45: _const("bricks"),
    46: _const("tnt"),
    47: _const("bookshelf"),
    48: _const("mossy_cobblestone"),
    49: _const("obsidian"),
    50: _torch("torch", "wall_torch"),
    51: _aged("fire"),
    52: _const("spawner"),
    53: _stairs("oak"),
    54: _facing_wall("chest"),
    55: _aged("redstone_wire", "power"),
    56: _const("diamond_ore"),
    57: _const("diamond_block"),
    58: _const("crafting_table"),
    59: _aged("wheat", limit=7),
    60: _aged("farmland", "moisture", limit=7),
    61: _facing_wall("furnace"),
    62: _facing_wall("furnace"),
    63: _rotation("oak_sign"),
    64: _door("oak"),
    65: _facing_wall("ladder"),
    66: _rail("rail", powered=False),
    67: _stairs("cobblestone"),
    68: _facing_wall("oak_wall_sign"),
    69: _lever,
    70: _const("stone_pressure_plate"),
    71: _door("iron"),
    72: _const("oak_pressure_plate"),
    73: _const("redstone_ore", lit="false"),
    74: _const("redstone_ore", lit="true"),
    75: _torch("redstone_torch", "redstone_wall_torch", lit="false"),
    76: _torch("redstone_torch", "redstone_wall_torch", lit="true"),
    77: _button("stone"),
    78: _snow_layer,
    79: _const("ice"),
    80: _const("snow_block"),
    81: _aged("cactus"),
    82: _const("clay"),
    83: _aged("sugar_cane"),
    84: _const("jukebox"),
    85: _const("oak_fence"),
    86: _facing_h("carved_pumpkin"),
    87: _const("netherrack"),
    88: _const("soul_sand"),
    89: _const("glowstone"),
    90: _nether_portal,
    91: _facing_h("jack_o_lantern"),
    92: _aged("cake", "bites", limit=6),
    93: _repeater(powered=False),
    94: _repeater(powered=True),
    95: _colored("stained_glass"),
    96: _trapdoor("oak_trapdoor"),
    97: _variants(INFESTED),
    98: _variants(STONE_BRICKS),
    99: _mushroom_block("brown_mushroom_block"),
    100: _mushroom_block("red_mushroom_block"),
    101: _const("iron_bars"),
    102: _const("glass_pane"),
    103: _const("melon"),
    104: _aged("pumpkin_stem", limit=7),
    105: _aged("melon_stem", limit=7),
    106: _vine,
    107: _fence_gate("oak"),
    108: _stairs("brick"),
    109: _stairs("stone_brick"),
    110: _const("mycelium", snowy="false"),
    111: _const("lily_pad"),
    112: _const("nether_bricks"),
    113: _const("nether_brick_fence"),
    114: _stairs("nether_brick"),
    115: _aged("nether_wart", limit=3),
    116: _const("enchanting_table"),
    117: _brewing_stand,
    118: _cauldron,
    119: _const("end_portal"),
    120: _end_portal_frame,
    121: _const("end_stone"),
    122: _const("dragon_egg"),
    123: _const("redstone_lamp", lit="false"),
    124: _const("redstone_lamp", lit="true"),
    125: _wood_slab(double=True),
    126: _wood_slab(double=False),
    127: _cocoa,
    128: _stairs("sandstone"),
    129: _const("emerald_ore"),
    130: _facing_wall("ender_chest"),
    131: _tripwire_hook,
    132: _tripwire,
    133: _const("emerald_block"),
    134: _stairs("spruce"),
    135: _stairs("birch"),
    136: _stairs("jungle"),
    137: _facing6_block("command_block", "conditional"),
    138: _const("beacon"),
    139: _variants(("cobblestone_wall", "mossy_cobblestone_wall")),
    140: _const("flower_pot"),
    141: _aged("carrots", limit=7),
    142: _aged("potatoes", limit=7),
    143: _button("oak"),
    144: _skull,
    145: _anvil,
    146: _facing_wall("trapped_chest"),
    147: _aged("light_weighted_pressure_plate", "power"),
    148: _aged("heavy_weighted_pressure_plate", "power"),
    149: _comparator(powered=False),
    150: _comparator(powered=True),
    151: _daylight_detector(inverted=False),
    152: _const("redstone_block"),
    153: _const("nether_quartz_ore"),
    154: _hopper,
    155: _quartz_block,
    156: _stairs("quartz"),
    157: _rail("activator_rail", powered=True),
    158: _facing6_block("dropper", "triggered"),
    159: _colored("terracotta"),
    160: _colored("stained_glass_pane"),
    161: _leaves(("acacia", "dark_oak")),
    162: _log(("acacia", "dark_oak")),
    163: _stairs("acacia"),
    164: _stairs("dark_oak"),
    165: _const("slime_block"),
    166: _const("barrier"),
    167: _trapdoor("iron_trapdoor"),
    168: _variants(("prismarine", "prismarine_bricks", "dark_prismarine")),
    169: _const("sea_lantern"),
    170: _axis_pillar("hay_block"),
    171: _colored("carpet"),
    172: _const("terracotta"),
    173: _const("coal_block"),
    174: _const("packed_ice"),
    175: _double_plant,
    176: _rotation("white_banner"),
    177: _facing_wall("white_wall_banner"),
    178: _daylight_detector(inverted=True),
    179: _variants(("red_sandstone", "chiseled_red_sandstone", "cut_red_sandstone")),
    180: _stairs("red_sandstone"),
    181: _red_sandstone_double_slab,
    182: _slab("red_sandstone_slab"),
    183: _fence_gate("spruce"),
    184: _fence_gate("birch"),
    185: _fence_gate("jungle"),
    186: _fence_gate("dark_oak"),
    187: _fence_gate("acacia"),
    188: _const("spruce_fence"),
    189: _const("birch_fence"),
    190: _const("jungle_fence"),
    191: _const("dark_oak_fence"),
    192: _const("acacia_fence"),
    193: _door("spruce"),
    194: _door("birch"),
    195: _door("jungle"),
    196: _door("acacia"),
    197: _door("dark_oak"),
    198: _facing6_simple("end_rod"),
    199: _const("chorus_plant"),
    200: _aged("chorus_flower", limit=5),
    201: _const("purpur_block"),
    202: _axis_pillar("purpur_pillar"),
    203: _stairs("purpur"),
    204: _double_slab("purpur_slab"),
    205: _slab("purpur_slab"),
    206: _const("end_stone_bricks"),
    207: _aged("beetroots", limit=3),
    208: _const("dirt_path"),
    209: _const("end_gateway"),
    210: _facing6_block("repeating_command_block", "conditional"),
    211: _facing6_block("chain_command_block", "conditional"),
    212: _aged("frosted_ice", limit=3),
    213: _const("magma_block"),
    214: _const("nether_wart_block"),
    215: _const("red_nether_bricks"),
    216: _axis_pillar("bone_block"),
    217: _const("structure_void"),
    218: _facing6_block("observer", "powered"),
    251: _colored("concrete"),
    252: _colored("concrete_powder"),
    255: _structure_block,
}

# Shulker boxes (219-234) kept their facing in the block entity rather than the
# metadata, so there is nothing to recover; glazed terracotta (235-250) stores a
# horizontal facing.
for _i, _color in enumerate(COLORS):
    _HANDLERS[219 + _i] = _const(_color + "_shulker_box")
    _HANDLERS[235 + _i] = _glazed_terracotta(_color)
del _i, _color

# --------------------------------------------------------------------------- #
# 1.12 registry names
# --------------------------------------------------------------------------- #

_LEGACY_NAMES: Dict[int, str] = {
    0: "air", 1: "stone", 2: "grass", 3: "dirt", 4: "cobblestone", 5: "planks",
    6: "sapling", 7: "bedrock", 8: "flowing_water", 9: "water",
    10: "flowing_lava", 11: "lava", 12: "sand", 13: "gravel", 14: "gold_ore",
    15: "iron_ore", 16: "coal_ore", 17: "log", 18: "leaves", 19: "sponge",
    20: "glass", 21: "lapis_ore", 22: "lapis_block", 23: "dispenser",
    24: "sandstone", 25: "noteblock", 26: "bed", 27: "golden_rail",
    28: "detector_rail", 29: "sticky_piston", 30: "web", 31: "tallgrass",
    32: "deadbush", 33: "piston", 34: "piston_head", 35: "wool",
    36: "piston_extension", 37: "yellow_flower", 38: "red_flower",
    39: "brown_mushroom", 40: "red_mushroom", 41: "gold_block",
    42: "iron_block", 43: "double_stone_slab", 44: "stone_slab",
    45: "brick_block", 46: "tnt", 47: "bookshelf", 48: "mossy_cobblestone",
    49: "obsidian", 50: "torch", 51: "fire", 52: "mob_spawner",
    53: "oak_stairs", 54: "chest", 55: "redstone_wire", 56: "diamond_ore",
    57: "diamond_block", 58: "crafting_table", 59: "wheat", 60: "farmland",
    61: "furnace", 62: "lit_furnace", 63: "standing_sign", 64: "wooden_door",
    65: "ladder", 66: "rail", 67: "stone_stairs", 68: "wall_sign", 69: "lever",
    70: "stone_pressure_plate", 71: "iron_door", 72: "wooden_pressure_plate",
    73: "redstone_ore", 74: "lit_redstone_ore", 75: "unlit_redstone_torch",
    76: "redstone_torch", 77: "stone_button", 78: "snow_layer", 79: "ice",
    80: "snow", 81: "cactus", 82: "clay", 83: "reeds", 84: "jukebox",
    85: "fence", 86: "pumpkin", 87: "netherrack", 88: "soul_sand",
    89: "glowstone", 90: "portal", 91: "lit_pumpkin", 92: "cake",
    93: "unpowered_repeater", 94: "powered_repeater", 95: "stained_glass",
    96: "trapdoor", 97: "monster_egg", 98: "stonebrick",
    99: "brown_mushroom_block", 100: "red_mushroom_block", 101: "iron_bars",
    102: "glass_pane", 103: "melon_block", 104: "pumpkin_stem",
    105: "melon_stem", 106: "vine", 107: "fence_gate", 108: "brick_stairs",
    109: "stone_brick_stairs", 110: "mycelium", 111: "waterlily",
    112: "nether_brick", 113: "nether_brick_fence", 114: "nether_brick_stairs",
    115: "nether_wart", 116: "enchanting_table", 117: "brewing_stand",
    118: "cauldron", 119: "end_portal", 120: "end_portal_frame",
    121: "end_stone", 122: "dragon_egg", 123: "redstone_lamp",
    124: "lit_redstone_lamp", 125: "double_wooden_slab", 126: "wooden_slab",
    127: "cocoa", 128: "sandstone_stairs", 129: "emerald_ore",
    130: "ender_chest", 131: "tripwire_hook", 132: "tripwire",
    133: "emerald_block", 134: "spruce_stairs", 135: "birch_stairs",
    136: "jungle_stairs", 137: "command_block", 138: "beacon",
    139: "cobblestone_wall", 140: "flower_pot", 141: "carrots",
    142: "potatoes", 143: "wooden_button", 144: "skull", 145: "anvil",
    146: "trapped_chest", 147: "light_weighted_pressure_plate",
    148: "heavy_weighted_pressure_plate", 149: "unpowered_comparator",
    150: "powered_comparator", 151: "daylight_detector", 152: "redstone_block",
    153: "quartz_ore", 154: "hopper", 155: "quartz_block", 156: "quartz_stairs",
    157: "activator_rail", 158: "dropper", 159: "stained_hardened_clay",
    160: "stained_glass_pane", 161: "leaves2", 162: "log2",
    163: "acacia_stairs", 164: "dark_oak_stairs", 165: "slime", 166: "barrier",
    167: "iron_trapdoor", 168: "prismarine", 169: "sea_lantern",
    170: "hay_block", 171: "carpet", 172: "hardened_clay", 173: "coal_block",
    174: "packed_ice", 175: "double_plant", 176: "standing_banner",
    177: "wall_banner", 178: "daylight_detector_inverted", 179: "red_sandstone",
    180: "red_sandstone_stairs", 181: "double_stone_slab2", 182: "stone_slab2",
    183: "spruce_fence_gate", 184: "birch_fence_gate", 185: "jungle_fence_gate",
    186: "dark_oak_fence_gate", 187: "acacia_fence_gate", 188: "spruce_fence",
    189: "birch_fence", 190: "jungle_fence", 191: "dark_oak_fence",
    192: "acacia_fence", 193: "spruce_door", 194: "birch_door",
    195: "jungle_door", 196: "acacia_door", 197: "dark_oak_door",
    198: "end_rod", 199: "chorus_plant", 200: "chorus_flower",
    201: "purpur_block", 202: "purpur_pillar", 203: "purpur_stairs",
    204: "purpur_double_slab", 205: "purpur_slab", 206: "end_bricks",
    207: "beetroots", 208: "grass_path", 209: "end_gateway",
    210: "repeating_command_block", 211: "chain_command_block",
    212: "frosted_ice", 213: "magma", 214: "nether_wart_block",
    215: "red_nether_brick", 216: "bone_block", 217: "structure_void",
    218: "observer", 251: "concrete", 252: "concrete_powder",
    255: "structure_block",
}

for _i, _color in enumerate(COLORS):
    # 1.12 spelled light_gray "silver" in registry names.
    _legacy_color = "silver" if _color == "light_gray" else _color
    _LEGACY_NAMES[219 + _i] = _legacy_color + "_shulker_box"
    _LEGACY_NAMES[235 + _i] = _legacy_color + "_glazed_terracotta"
del _i, _color, _legacy_color

#: 1.12 registry name (namespaced) -> numeric block id.
LEGACY_NAME_TO_ID: Dict[str, int] = {MC + n: i for i, n in _LEGACY_NAMES.items()}

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def legacy_block(block_id: int, meta: int) -> Tuple[str, Dict[str, str]]:
    """Return ``(namespaced block name, properties)`` for a legacy id + metadata.

    ``block_id`` is a Minecraft 1.12.2-and-earlier numeric block id (0-4095 once
    the ``AddBlocks`` nibble array has been folded in) and ``meta`` its 4-bit
    metadata value.  Property values are strings, exactly as a blockstate JSON
    spells them.

    Unknown ids return ``("minecraft:unknown_legacy_<id>", {})``.  Never raises.
    """
    try:
        bid = int(block_id)
        m = int(meta) & 0xF
    except (TypeError, ValueError):
        return "%sunknown_legacy_%s" % (MC, block_id), {}
    handler = _HANDLERS.get(bid)
    if handler is None:
        return "%sunknown_legacy_%d" % (MC, bid), {}
    try:
        name, props = handler(m)
    except Exception:  # pragma: no cover - defensive; the tables are exhaustive
        return "%sunknown_legacy_%d" % (MC, bid), {}
    return name, dict(props)


def legacy_block_from_name(name: str, meta: int) -> Tuple[str, Dict[str, str]]:
    """Same as :func:`legacy_block` but keyed by a 1.12 registry name.

    The ``minecraft:`` prefix is optional.  A name that is not a vanilla 1.12
    block -- typically a mod block coming from a Schematica ``SchematicaMapping``
    -- is returned unchanged apart from gaining a namespace, and with no
    properties, because its metadata semantics are unknowable here.
    """
    if not isinstance(name, str):
        return "%sunknown_legacy_%s" % (MC, name), {}
    normalized = name.strip().lower()
    if not normalized:
        return MC + "air", {}
    if ":" not in normalized:
        normalized = MC + normalized
    block_id = LEGACY_NAME_TO_ID.get(normalized)
    if block_id is None:
        return normalized, {}
    return legacy_block(block_id, meta)
