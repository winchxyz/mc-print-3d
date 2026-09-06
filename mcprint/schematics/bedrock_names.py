"""Bedrock Edition block names + states -> Java Edition 1.21 names + properties.

A ``.mcstructure`` palette stores the *Bedrock* view of a block, which differs
from Java's in three ways:

1. Bedrock kept a number of pre-flattening "compound" blocks that Java split
   apart in 1.13 -- ``wool[color=red]`` instead of ``red_wool``,
   ``log[old_log_type=spruce]`` instead of ``spruce_log``, and so on.
2. A pile of blocks simply have a different id (``web``/``cobweb``,
   ``grass``/``grass_block``, ``magma``/``magma_block``, ...).
3. Nearly every *property* is spelled differently: booleans are 0/1 ints with a
   ``_bit`` suffix, directions are ints in one of four mutually incompatible
   orders, and a few values differ outright (``silver``/``light_gray``, a wall
   connection of ``short``/``low``).

:func:`bedrock_to_java` performs all three translations.  Output names and
property values are exactly the ones accepted by
``assets/minecraft/blockstates/<name>.json`` in a Java 1.21 client jar, so the
result can be fed straight into the blockstate -> model -> texture resolver.

Design notes
------------
* Nothing here raises.  Anything unrecognised degrades gracefully: an unknown
  *vanilla* name passes through with the properties this module understands,
  while an unknown *namespace* (a mod or add-on) passes through with its states
  merely stringified, so a mod-aware resolver downstream still sees them.
* Properties are only emitted where Bedrock actually stores them.  Bookkeeping
  Java derives from neighbours (``waterlogged``, fence/pane/wall connections,
  leaf ``distance``) is left off so the resolver falls back to model defaults.
* Deliberately lossy conversions are marked with "LOSSY:" below.
"""
from __future__ import annotations

from typing import Any, Dict, Set, Tuple

__all__ = ["bedrock_to_java"]

MC = "minecraft:"

Props = Dict[str, str]
Block = Tuple[str, Props]

# --------------------------------------------------------------------------- #
# Value coercion
# --------------------------------------------------------------------------- #
# Bedrock NBT gives us bytes for booleans, ints for enumerated directions and
# strings for everything else; a palette that has been through JSON may hand us
# any of those as a string instead.


def _int(value: Any, default: int = 0) -> int:
    """Best-effort int, for the numeric Bedrock states."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _flag(value: Any) -> bool:
    """Truthiness of a Bedrock ``*_bit`` state (0/1 int, bool, or string)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    if isinstance(value, bool):
        return value
    return _int(value) != 0


def _b(value: Any) -> str:
    """A Bedrock ``*_bit`` state as a Java boolean property value."""
    return "true" if _flag(value) else "false"


def _word(value: Any, default: str = "") -> str:
    """A Bedrock enum state, normalised for table lookup."""
    if value is None:
        return default
    text = str(value).strip().lower()
    return text or default


def _stringify(value: Any) -> str:
    """Property value for a name we do not understand: keep it, verbatim-ish."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --------------------------------------------------------------------------- #
# Direction vocabularies
# --------------------------------------------------------------------------- #

#: ``facing_direction`` (int) -- the six-way order shared by signs, piston
#: heads, buttons, levers, end rods and the old numeric torches.
_FACING_6 = ("down", "up", "north", "south", "west", "east")

#: ``direction`` on doors.
_DIR_DOOR = ("east", "south", "west", "north")

#: ``direction`` on trapdoors -- and ``weirdo_direction`` on stairs, which uses
#: the very same order despite the different name.
_DIR_TRAPDOOR = ("east", "west", "south", "north")

#: ``direction`` everywhere else: fence gates, beds, pumpkins, anvils,
#: repeaters, comparators.  The default when a block is not a door or trapdoor.
_DIR_DEFAULT = ("south", "west", "north", "east")

#: ``rail_direction`` -> Java rail ``shape``.
_RAIL_SHAPES = (
    "north_south", "east_west", "ascending_east", "ascending_west",
    "ascending_north", "ascending_south", "south_east", "south_west",
    "north_west", "north_east",
)

#: ``vine_direction_bits`` -- a bitmask, not an index.
_VINE_BITS = ((1, "south"), (2, "west"), (4, "north"), (8, "east"))

#: ``multi_face_direction_bits`` (glow lichen, sculk vein, resin clump).
_MULTI_FACE_BITS = (
    (1, "down"), (2, "up"), (4, "north"), (8, "south"), (16, "west"), (32, "east"),
)

#: Java's wall connection values.  Bedrock spells the middle one "short".
_WALL_CONNECTION = {"none": "none", "short": "low", "low": "low", "tall": "tall"}

#: States that carry no information Java can use, or whose information this
#: module has already consumed while picking the block *name*.  Listed
#: explicitly so a future reader can tell "known but useless" from "unknown";
#: unrecognised states on a vanilla name are dropped either way.
_DROPPED = frozenset((
    "infiniburn_bit",       # netherrack-style perpetual fire; not a Java state
    "update_bit",           # Bedrock-internal tick bookkeeping
    "huge_mushroom_bits",   # LOSSY: Java models mushroom blocks with six faces
    "coral_hang_type_bit",  # consumed when naming a coral wall fan
    "coral_direction",      # LOSSY: Bedrock's wall-fan rotation, order unclear
    "deprecated",           # Bedrock-internal
    "stability", "stability_check",   # scaffolding physics, not rendered
    "brewing_stand_slot_a_bit", "brewing_stand_slot_b_bit",
    "brewing_stand_slot_c_bit",       # Java draws bottles from the block entity
    "item_frame_map_bit", "item_frame_photo_bit",
    "covered_bit",          # snow layer "is under a block"; Java has no such
    "stripped_bit", "dead_bit", "color",   # consumed when naming the block
    "wood_type", "old_log_type", "new_log_type", "chisel_type",
))


def _direction_order(java_path: str) -> Tuple[str, ...]:
    """Which ``direction`` vocabulary a block uses, keyed off its Java name."""
    if java_path.endswith("trapdoor"):
        return _DIR_TRAPDOOR
    if java_path.endswith("_door"):
        return _DIR_DOOR
    return _DIR_DEFAULT


def _from_order(order: Tuple[str, ...], value: Any) -> str:
    """Index a direction vocabulary, wrapping rather than failing."""
    return order[_int(value) % len(order)]


# --------------------------------------------------------------------------- #
# Generic property translation
# --------------------------------------------------------------------------- #


def _generic_props(java_path: str, states: Dict[str, Any], skip: Set[str]) -> Props:
    """Translate the states any block may carry.

    ``java_path`` is the already-resolved Java block name (without namespace);
    a few states -- ``direction`` above all -- mean different things depending
    on which block they are attached to.  ``skip`` holds the states a family
    rule has already consumed and does not want re-interpreted here.
    """
    out: Props = {}
    for raw_key, value in states.items():
        key = str(raw_key)
        if key in skip:
            continue
        # Newer Bedrock namespaces some states.  Namespaced or not it is the
        # same state, except that the namespaced spellings are always strings.
        if key.startswith(MC):
            key = key[len(MC):]
        if key in skip or key in _DROPPED:
            continue

        if key == "pillar_axis":
            out["axis"] = _word(value, "y")
        elif key == "facing_direction":
            # Plain int in legacy palettes, cardinal string when namespaced.
            out["facing"] = _word(value) if isinstance(value, str) else _from_order(_FACING_6, value)
        elif key in ("cardinal_direction", "block_face"):
            out["facing"] = _word(value)
        elif key == "vertical_half":
            out["half"] = _word(value)
        elif key == "direction":
            out["facing"] = _from_order(_direction_order(java_path), value)
        elif key == "weirdo_direction":
            out["facing"] = _from_order(_DIR_TRAPDOOR, value)
        elif key == "door_hinge_bit":
            out["hinge"] = "right" if _flag(value) else "left"
        elif key == "open_bit":
            out["open"] = _b(value)
        elif key == "upper_block_bit":
            out["half"] = "upper" if _flag(value) else "lower"
        elif key == "upside_down_bit":
            out["half"] = "top" if _flag(value) else "bottom"
        elif key == "top_slot_bit":
            out["type"] = "top" if _flag(value) else "bottom"
        elif key in ("powered_bit", "rail_data_bit", "toggle_bit",
                     "button_pressed_bit", "output_lit_bit"):
            out["powered"] = _b(value)
        elif key == "output_subtract_bit":
            out["mode"] = "subtract" if _flag(value) else "compare"
        elif key == "repeater_delay":
            out["delay"] = str(_int(value) + 1)
        elif key in ("age", "growth"):
            out["age"] = str(_int(value))
        elif key == "attached_bit":
            out["attached"] = _b(value)
        elif key == "rail_direction":
            # Only the plain rail can curve; the powered/detector/activator
            # rails reject the four corner shapes (Bedrock never writes them
            # for those blocks either, but clamp rather than emit a bad state).
            limit = len(_RAIL_SHAPES) if java_path == "rail" else 6
            out["shape"] = _RAIL_SHAPES[_int(value) % limit]
        elif key == "ground_sign_direction":
            out["rotation"] = str(_int(value))
        elif key == "vine_direction_bits":
            bits = _int(value)
            for bit, side in _VINE_BITS:
                out[side] = "true" if bits & bit else "false"
        elif key == "multi_face_direction_bits":
            bits = _int(value)
            for bit, side in _MULTI_FACE_BITS:
                out[side] = "true" if bits & bit else "false"
        elif key == "liquid_depth":
            out["level"] = str(_int(value))
        elif key == "redstone_signal":
            out["power"] = str(_int(value))
        elif key == "height" and java_path == "snow":
            # Bedrock counts snow layers from 0, Java from 1.
            out["layers"] = str(_int(value) + 1)
        elif key == "bite_counter":
            out["bites"] = str(_int(value))
        elif key == "moisturized_amount":
            out["moisture"] = str(_int(value))
        elif key == "candles":
            out["candles"] = str(_int(value) + 1)
        elif key == "lit":
            out["lit"] = _b(value)
        elif key == "extinguished":
            out["lit"] = "false" if _flag(value) else "true"
        elif key == "hanging":
            out["hanging"] = _b(value)
        elif key == "persistent_bit":
            out["persistent"] = _b(value)
        elif key == "explode_bit":
            out["unstable"] = _b(value)
        elif key == "head_piece_bit":
            out["part"] = "head" if _flag(value) else "foot"
        elif key == "occupied_bit":
            out["occupied"] = _b(value)
        elif key == "wall_post_bit":
            out["up"] = _b(value)
        elif key.startswith("wall_connection_type_"):
            side = key[len("wall_connection_type_"):]
            if side in ("north", "east", "south", "west"):
                out[side] = _WALL_CONNECTION.get(_word(value), "none")
        # Anything else is a Bedrock-only state; Java would reject it.
    return out


# --------------------------------------------------------------------------- #
# Shared vocabularies for the compound (pre-flattening) families
# --------------------------------------------------------------------------- #

#: Java's sixteen dye names.  Bedrock still writes the 1.12 spelling "silver".
_JAVA_COLORS = frozenset((
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
    "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black",
))

_JAVA_WOODS = frozenset(("oak", "spruce", "birch", "jungle", "acacia", "dark_oak"))


def _color(states: Dict[str, Any]) -> str:
    """The ``color`` state as a Java dye name."""
    value = _word(states.get("color"), "white")
    if value == "silver":
        return "light_gray"
    return value if value in _JAVA_COLORS else "white"


def _wood(states: Dict[str, Any], key: str = "wood_type", default: str = "oak") -> str:
    """A wood-type state as a Java wood name."""
    value = _word(states.get(key), default).replace("darkoak", "dark_oak")
    return value if value in _JAVA_WOODS else default


#: Blocks that are just ``<color>_<something>`` in Java.
_COLORED = {
    "wool": "{c}_wool",
    "carpet": "{c}_carpet",
    "concrete": "{c}_concrete",
    "concrete_powder": "{c}_concrete_powder",
    "stained_glass": "{c}_stained_glass",
    "stained_glass_pane": "{c}_stained_glass_pane",
    "stained_hardened_clay": "{c}_terracotta",
    "shulker_box": "{c}_shulker_box",
    # LOSSY: the Education-Edition "hard" glass has no Java counterpart.
    "hard_stained_glass": "{c}_stained_glass",
    "hard_stained_glass_pane": "{c}_stained_glass_pane",
}

#: ``stone[stone_type]``.
_STONE_TYPES = {
    "stone": "stone",
    "granite": "granite", "granite_smooth": "polished_granite",
    "diorite": "diorite", "diorite_smooth": "polished_diorite",
    "andesite": "andesite", "andesite_smooth": "polished_andesite",
}

#: ``sandstone``/``red_sandstone[sand_stone_type]``.  "heiroglyphs" is spelled
#: exactly that way in the Bedrock data, typo and all.
_SANDSTONE_TYPES = {
    "default": "{s}", "heiroglyphs": "chiseled_{s}", "cut": "cut_{s}",
    "smooth": "smooth_{s}",
}

#: The four ``stone_slab_type*`` vocabularies, in Bedrock's own order (the
#: first entry of each doubles as the fallback).  Values are the Java *material*
#: name; the block is that plus ``_slab``.
_SLAB_TYPES = {
    "stone_slab_type": {
        "smooth_stone": "smooth_stone", "sandstone": "sandstone",
        "wood": "petrified_oak", "cobblestone": "cobblestone", "brick": "brick",
        "stone_brick": "stone_brick", "quartz": "quartz",
        "nether_brick": "nether_brick",
    },
    "stone_slab_type_2": {
        "red_sandstone": "red_sandstone", "purpur": "purpur",
        "prismarine_rough": "prismarine", "prismarine_dark": "dark_prismarine",
        "prismarine_brick": "prismarine_brick",
        "mossy_cobblestone": "mossy_cobblestone",
        "smooth_sandstone": "smooth_sandstone",
        "red_nether_brick": "red_nether_brick",
    },
    "stone_slab_type_3": {
        "end_stone_brick": "end_stone_brick",
        "smooth_red_sandstone": "smooth_red_sandstone",
        "polished_andesite": "polished_andesite", "andesite": "andesite",
        "diorite": "diorite", "polished_diorite": "polished_diorite",
        "granite": "granite", "polished_granite": "polished_granite",
    },
    "stone_slab_type_4": {
        "mossy_stone_brick": "mossy_stone_brick", "smooth_quartz": "smooth_quartz",
        "stone": "stone", "cut_sandstone": "cut_sandstone",
        "cut_red_sandstone": "cut_red_sandstone",
    },
}

#: Every spelling Bedrock has used for the stone slab blocks.  ``stone_slab*``
#: are the 1.12-era ids, ``stone_block_slab*`` the ids they were renamed to.
_STONE_SLABS = frozenset(
    base + suffix
    for base in ("stone_slab", "stone_block_slab",
                 "double_stone_slab", "double_stone_block_slab")
    for suffix in ("", "2", "3", "4")
)

#: Stairs whose Bedrock id is not the Java one.  Everything else ending in
#: ``_stairs`` already matches.
_STAIR_RENAMES = {
    "stone_stairs": "cobblestone_stairs",     # the 1.12 trap: "stone" = cobble
    "normal_stone_stairs": "stone_stairs",
    "end_brick_stairs": "end_stone_brick_stairs",
    "prismarine_bricks_stairs": "prismarine_brick_stairs",
    "prismarine_rough_stairs": "prismarine_stairs",
}

#: (standing, wall) Java names per Bedrock torch block.
_TORCHES = {
    "torch": ("torch", "wall_torch"),
    "redstone_torch": ("redstone_torch", "redstone_wall_torch"),
    "unlit_redstone_torch": ("redstone_torch", "redstone_wall_torch"),
    "soul_torch": ("soul_torch", "soul_wall_torch"),
    # LOSSY: Bedrock's underwater torch is a plain torch in Java.
    "underwater_torch": ("torch", "wall_torch"),
}

_QUARTZ_TYPES = {
    "default": "quartz_block", "chiseled": "chiseled_quartz_block",
    "lines": "quartz_pillar", "smooth": "smooth_quartz",
}

_PURPUR_TYPES = {
    "default": "purpur_block", "chiseled": "purpur_block", "lines": "purpur_pillar",
}

_PRISMARINE_TYPES = {
    "default": "prismarine", "dark": "dark_prismarine", "bricks": "prismarine_bricks",
}

_MONSTER_EGG_TYPES = {
    "stone": "infested_stone", "cobblestone": "infested_cobblestone",
    "stone_brick": "infested_stone_bricks",
    "mossy_stone_brick": "infested_mossy_stone_bricks",
    "cracked_stone_brick": "infested_cracked_stone_bricks",
    "chiseled_stone_brick": "infested_chiseled_stone_bricks",
}

_STONE_BRICK_TYPES = {
    "default": "stone_bricks", "mossy": "mossy_stone_bricks",
    "cracked": "cracked_stone_bricks", "chiseled": "chiseled_stone_bricks",
    # LOSSY: 1.12's "smooth stonebrick" was never given a Java 1.13+ block.
    "smooth": "stone_bricks",
}

_WALL_TYPES = {
    "cobblestone": "cobblestone", "mossy_cobblestone": "mossy_cobblestone",
    "granite": "granite", "diorite": "diorite", "andesite": "andesite",
    "sandstone": "sandstone", "brick": "brick", "stone_brick": "stone_brick",
    "mossy_stone_brick": "mossy_stone_brick", "nether_brick": "nether_brick",
    "end_brick": "end_stone_brick", "prismarine": "prismarine",
    "red_sandstone": "red_sandstone", "red_nether_brick": "red_nether_brick",
}

_ANVIL_DAMAGE = {
    "undamaged": "anvil", "slightly_damaged": "chipped_anvil",
    "very_damaged": "damaged_anvil",
    # LOSSY: a "broken" anvil is an item drop in Java, not a block.
    "broken": "damaged_anvil",
}

_TALLGRASS_TYPES = {
    "default": "short_grass", "tall": "short_grass", "fern": "fern", "snow": "fern",
}

_DOUBLE_PLANT_TYPES = {
    "sunflower": "sunflower", "syringa": "lilac", "grass": "tall_grass",
    "fern": "large_fern", "rose": "rose_bush", "paeonia": "peony",
}

_RED_FLOWER_TYPES = {
    "poppy": "poppy", "orchid": "blue_orchid", "allium": "allium",
    "houstonia": "azure_bluet", "tulip_red": "red_tulip",
    "tulip_orange": "orange_tulip", "tulip_white": "white_tulip",
    "tulip_pink": "pink_tulip", "oxeye": "oxeye_daisy", "cornflower": "cornflower",
    "lily_of_the_valley": "lily_of_the_valley",
}

#: ``coral_color`` -> Java coral species.
_CORAL_COLORS = {
    "blue": "tube", "pink": "brain", "purple": "bubble", "red": "fire",
    "yellow": "horn",
}

#: The species each ``coral_fan_hang*`` block can hold, indexed by
#: ``coral_hang_type_bit``.
_CORAL_HANG = {
    "coral_fan_hang": ("tube", "brain"),
    "coral_fan_hang2": ("bubble", "fire"),
    "coral_fan_hang3": ("horn", "horn"),
}

#: Straight renames: same block, different id.
_RENAMES = {
    "web": "cobweb",
    "grass": "grass_block",
    "grass_path": "dirt_path",
    "waterlily": "lily_pad",
    "deadbush": "dead_bush",
    "yellow_flower": "dandelion",
    "snow_layer": "snow",          # ``height`` -> ``layers`` in _generic_props
    "snow": "snow_block",
    "reeds": "sugar_cane",
    "melon_block": "melon",
    "noteblock": "note_block",
    "mob_spawner": "spawner",
    "lit_pumpkin": "jack_o_lantern",
    "brick_block": "bricks",
    "nether_brick": "nether_bricks",
    "red_nether_brick": "red_nether_bricks",
    "end_bricks": "end_stone_bricks",
    "hardened_clay": "terracotta",
    "golden_rail": "powered_rail",
    "trapdoor": "oak_trapdoor",
    "wooden_door": "oak_door",
    "wooden_button": "oak_button",
    "wooden_pressure_plate": "oak_pressure_plate",
    "fence_gate": "oak_fence_gate",
    "standing_sign": "oak_sign",
    "wall_sign": "oak_wall_sign",
    "magma": "magma_block",
    "slime": "slime_block",
    "stonecutter_block": "stonecutter",
    "undyed_shulker_box": "shulker_box",
    "flowing_water": "water",
    "flowing_lava": "lava",
    # LOSSY: Java has no bubble column block; the water it lives in is closest.
    "bubble_column": "water",
}

#: Renames that also pin down a property the Bedrock *id* encoded.
_RENAMED_PROPS = {
    "sticky_piston_arm_collision": ("piston_head", {"type": "sticky"}),
    "piston_arm_collision": ("piston_head", {"type": "normal"}),
    "lit_furnace": ("furnace", {"lit": "true"}),
    "lit_blast_furnace": ("blast_furnace", {"lit": "true"}),
    "lit_smoker": ("smoker", {"lit": "true"}),
    "lit_redstone_lamp": ("redstone_lamp", {"lit": "true"}),
    "lit_redstone_ore": ("redstone_ore", {"lit": "true"}),
    "lit_deepslate_redstone_ore": ("deepslate_redstone_ore", {"lit": "true"}),
    "powered_repeater": ("repeater", {"powered": "true"}),
    "unpowered_repeater": ("repeater", {"powered": "false"}),
}


# --------------------------------------------------------------------------- #
# Name resolution
# --------------------------------------------------------------------------- #


def _resolve(path: str, states: Dict[str, Any]) -> Tuple[str, Props, Set[str]]:
    """Pick the Java block name for a Bedrock id.

    Returns the Java name (no namespace), the properties the *name itself*
    determined, and the states those rules consumed -- which
    :func:`_generic_props` must therefore not interpret a second time.
    """
    # Item frames are entities in Java, so the block simply disappears.
    if path in ("frame", "glow_frame"):
        return "air", {}, {str(k) for k in states}

    template = _COLORED.get(path)
    if template is not None:
        return template.format(c=_color(states)), {}, set()

    if path in _STONE_SLABS:
        key = "stone_slab_type"
        for digit in ("2", "3", "4"):
            if path.endswith(digit):
                key = "stone_slab_type_" + digit
        table = _SLAB_TYPES[key]
        material = table.get(_word(states.get(key)), next(iter(table.values())))
        props: Props = {"type": "double"} if path.startswith("double_") else {}
        return material + "_slab", props, {key}

    if path in ("wooden_slab", "double_wooden_slab"):
        props = {"type": "double"} if path.startswith("double_") else {}
        return _wood(states) + "_slab", props, set()

    if path.endswith("_stairs"):
        # Java derives a stair's corner shape from its neighbours; "straight"
        # is both the model default and what an isolated block looks like.
        return _STAIR_RENAMES.get(path, path), {"shape": "straight"}, set()

    if path in _TORCHES:
        standing, wall = _TORCHES[path]
        props = {"lit": "false"} if path == "unlit_redstone_torch" else {}
        face = _word(states.get("torch_facing_direction"), "top")
        if face in ("north", "south", "east", "west"):
            # Bedrock's torch_facing_direction names the direction the torch
            # *points*, and so does Java's facing: a torch_facing_direction of
            # "west" sits on the east face of its neighbour and leans west,
            # which is exactly facing=west.  (It is NOT the face it is stuck
            # to, tempting though that reading is.)
            props["facing"] = face
            return wall, props, {"torch_facing_direction"}
        return standing, props, {"torch_facing_direction"}

    if path in ("log", "log2"):
        key = "old_log_type" if path == "log" else "new_log_type"
        return _wood(states, key, "oak" if path == "log" else "acacia") + "_log", {}, set()

    if path in ("leaves", "leaves2"):
        key = "old_leaf_type" if path == "leaves" else "new_leaf_type"
        return _wood(states, key, "oak" if path == "leaves" else "acacia") + "_leaves", {}, set()

    if path == "wood":
        prefix = "stripped_" if _flag(states.get("stripped_bit")) else ""
        return prefix + _wood(states) + "_wood", {}, set()
    if path == "planks":
        return _wood(states) + "_planks", {}, set()
    if path == "sapling":
        return _wood(states, "sapling_type") + "_sapling", {}, set()
    if path == "fence":
        return _wood(states) + "_fence", {}, set()

    if path == "stone":
        return _STONE_TYPES.get(_word(states.get("stone_type")), "stone"), {}, set()
    if path == "dirt":
        coarse = _word(states.get("dirt_type")) == "coarse"
        return ("coarse_dirt" if coarse else "dirt"), {}, set()
    if path == "sand":
        red = _word(states.get("sand_type")) == "red"
        return ("red_sand" if red else "sand"), {}, set()
    if path in ("sandstone", "red_sandstone"):
        kind = _word(states.get("sand_stone_type"), "default")
        return _SANDSTONE_TYPES.get(kind, "{s}").format(s=path), {}, set()
    if path == "sponge":
        wet = _word(states.get("sponge_type")) == "wet"
        return ("wet_sponge" if wet else "sponge"), {}, set()
    if path == "prismarine":
        kind = _word(states.get("prismarine_block_type"), "default")
        return _PRISMARINE_TYPES.get(kind, "prismarine"), {}, set()
    if path == "monster_egg":
        kind = _word(states.get("monster_egg_stone_type"))
        return _MONSTER_EGG_TYPES.get(kind, "infested_stone"), {}, set()
    if path == "stonebrick":
        kind = _word(states.get("stone_brick_type"), "default")
        return _STONE_BRICK_TYPES.get(kind, "stone_bricks"), {}, set()
    if path == "cobblestone_wall":
        kind = _word(states.get("wall_block_type"))
        return _WALL_TYPES.get(kind, "cobblestone") + "_wall", {}, set()
    if path == "anvil":
        kind = _word(states.get("damage"), "undamaged")
        return _ANVIL_DAMAGE.get(kind, "anvil"), {}, set()
    if path == "tallgrass":
        kind = _word(states.get("tall_grass_type"), "default")
        return _TALLGRASS_TYPES.get(kind, "short_grass"), {}, set()
    if path == "double_plant":
        kind = _word(states.get("double_plant_type"))
        return _DOUBLE_PLANT_TYPES.get(kind, "sunflower"), {}, set()
    if path == "red_flower":
        kind = _word(states.get("flower_type"))
        return _RED_FLOWER_TYPES.get(kind, "poppy"), {}, set()

    if path in ("quartz_block", "purpur_block"):
        table = _QUARTZ_TYPES if path == "quartz_block" else _PURPUR_TYPES
        java = table.get(_word(states.get("chisel_type"), "default"), table["default"])
        if java.endswith("_pillar"):
            return java, {}, set()        # pillar_axis -> axis, generically
        return java, {}, {"pillar_axis"}  # the other variants have no axis

    if path in ("coral", "coral_block", "coral_fan", "coral_fan_dead"):
        species = _CORAL_COLORS.get(_word(states.get("coral_color")), "tube")
        dead = _flag(states.get("dead_bit")) or path == "coral_fan_dead"
        kind = "coral_fan" if path == "coral_fan_dead" else path
        return ("dead_" if dead else "") + species + "_" + kind, {}, set()

    if path in _CORAL_HANG:
        species = _CORAL_HANG[path][1 if _flag(states.get("coral_hang_type_bit")) else 0]
        dead = "dead_" if _flag(states.get("dead_bit")) else ""
        return dead + species + "_coral_wall_fan", {}, set()

    if path == "skull":
        # Bedrock has one skull block; Java splits floor and wall skulls.
        # LOSSY: which mob the skull is comes from the block entity, and the
        # rotation of a floor skull is not in the block state at all.
        facing = states.get("facing_direction")
        if facing is not None and _int(facing) >= 2:
            return "skeleton_wall_skull", {}, set()
        return "skeleton_skull", {}, {"facing_direction"}

    if path == "seagrass":
        kind = _word(states.get("sea_grass_type"), "default")
        if kind in ("double_top", "double_bot"):
            half = "upper" if kind == "double_top" else "lower"
            return "tall_seagrass", {"half": half}, set()
        return "seagrass", {}, set()

    if path == "pumpkin":
        # Java's plain pumpkin has no properties; only the carved one and the
        # jack o'lantern keep a facing.
        return "pumpkin", {}, {"direction", "cardinal_direction"}

    if path in ("powered_comparator", "unpowered_comparator"):
        # output_lit_bit is the better source for "powered" when present.
        powered = {} if "output_lit_bit" in states else {
            "powered": _b(path.startswith("powered"))}
        return "comparator", powered, set()

    if path == "bed":
        # LOSSY: Bedrock keeps the bed's colour in its block entity, so every
        # bed comes back red (the colour Java's item/model defaults to).
        return "red_bed", {}, set()

    renamed = _RENAMED_PROPS.get(path)
    if renamed is not None:
        return renamed[0], dict(renamed[1]), set()

    if path in _RENAMES:
        return _RENAMES[path], {}, set()

    if path.endswith("_standing_sign"):
        return path[: -len("_standing_sign")] + "_sign", {}, set()

    # Already-flattened modern Bedrock ids (red_wool, oak_log, granite, ...)
    # are spelled exactly like Java's, so they need no rename at all.
    return path, {}, set()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _convert(name: Any, states: Any) -> Block:
    text = str(name).strip()
    if not text:
        return MC + "air", {}
    if not isinstance(states, dict):
        states = {}

    namespace, sep, path = text.rpartition(":")
    if sep and namespace.lower() != "minecraft":
        # A mod or add-on block.  Keep the id and every state verbatim so a
        # mod-aware resolver downstream still has all of it to work with.
        return text, {str(k): _stringify(v) for k, v in states.items()}

    path = path.lower()
    # ``darkoak_*`` is Bedrock's spelling of Java's ``dark_oak_*``.
    if path.startswith("darkoak_"):
        path = "dark_oak_" + path[len("darkoak_"):]

    java_path, family_props, consumed = _resolve(path, states)
    props = _generic_props(java_path, states, consumed)
    # The name-derived properties win: they are the ones Bedrock encoded in the
    # block id rather than in a state, so nothing generic can contradict them.
    props.update(family_props)
    return MC + java_path, props


def bedrock_to_java(name: str, states: Dict[str, Any]) -> Block:
    """Convert one Bedrock palette entry into a Java 1.21 block.

    ``name`` is a Bedrock block id (``minecraft:wool``, ``minecraft:oak_log``,
    or a bare ``wool``); ``states`` is that entry's Bedrock state compound,
    whose values may be ints, bytes, bools or strings
    (``{"color": "red"}``, ``{"direction": 2, "upside_down_bit": 1}``).

    Returns ``(java_name, properties)`` -- a namespaced Java 1.21 block id and
    its properties as strings, ready to look up in
    ``assets/minecraft/blockstates/``.  Unknown ids pass through with the
    namespace filled in; unknown *namespaces* keep their states, stringified.

    Never raises: a palette entry this module cannot parse degrades to the id
    it was given rather than failing the whole import.
    """
    try:
        return _convert(name, states)
    except Exception:
        try:
            text = str(name).strip() or (MC + "air")
        except Exception:
            text = MC + "air"
        return (text if ":" in text else MC + text), {}
