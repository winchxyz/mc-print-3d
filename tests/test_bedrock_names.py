"""Tests for the Bedrock Edition -> Java Edition 1.21 block converter.

Every name asserted here was cross-checked against
``assets/minecraft/blockstates/*.json`` in a Minecraft Java 1.21 client jar:
each one exists, and each property value is one the corresponding blockstates
file accepts.
"""
from __future__ import annotations

from mcprint.schematics.bedrock_names import bedrock_to_java


def name(block: str, states: dict | None = None) -> str:
    """Just the Java name for a Bedrock block."""
    return bedrock_to_java(block, states or {})[0]


def props(block: str, states: dict | None = None) -> dict:
    """Just the Java properties for a Bedrock block."""
    return bedrock_to_java(block, states or {})[1]


# --------------------------------------------------------------------------- #
# Coloured families
# --------------------------------------------------------------------------- #


def test_colored_blocks():
    assert bedrock_to_java("minecraft:wool", {"color": "red"}) == ("minecraft:red_wool", {})
    assert name("minecraft:carpet", {"color": "lime"}) == "minecraft:lime_carpet"
    assert name("minecraft:concrete", {"color": "blue"}) == "minecraft:blue_concrete"
    assert name("minecraft:concrete_powder", {"color": "black"}) == "minecraft:black_concrete_powder"
    assert name("minecraft:stained_glass", {"color": "cyan"}) == "minecraft:cyan_stained_glass"
    assert name("minecraft:stained_glass_pane", {"color": "pink"}) == "minecraft:pink_stained_glass_pane"
    assert name("minecraft:shulker_box", {"color": "purple"}) == "minecraft:purple_shulker_box"
    # Education-Edition "hard" glass has no Java block; use the normal one.
    assert name("minecraft:hard_stained_glass", {"color": "green"}) == "minecraft:green_stained_glass"


def test_silver_is_light_gray():
    """Bedrock kept 1.12's "silver"; Java renamed it in the flattening."""
    assert name("minecraft:stained_hardened_clay", {"color": "silver"}) == "minecraft:light_gray_terracotta"
    assert name("minecraft:wool", {"color": "silver"}) == "minecraft:light_gray_wool"
    assert name("minecraft:stained_hardened_clay", {"color": "red"}) == "minecraft:red_terracotta"


# --------------------------------------------------------------------------- #
# Wood families
# --------------------------------------------------------------------------- #


def test_logs_and_wood():
    assert bedrock_to_java("minecraft:log", {"old_log_type": "spruce", "pillar_axis": "x"}) == (
        "minecraft:spruce_log", {"axis": "x"})
    assert name("minecraft:log", {"old_log_type": "jungle"}) == "minecraft:jungle_log"
    assert name("minecraft:log2", {"new_log_type": "acacia"}) == "minecraft:acacia_log"
    assert name("minecraft:log2", {"new_log_type": "dark_oak"}) == "minecraft:dark_oak_log"
    assert name("minecraft:wood", {"wood_type": "birch", "stripped_bit": 0}) == "minecraft:birch_wood"
    assert name("minecraft:wood", {"wood_type": "birch", "stripped_bit": 1}) == "minecraft:stripped_birch_wood"


def test_planks_leaves_saplings_fences():
    assert name("minecraft:planks", {"wood_type": "dark_oak"}) == "minecraft:dark_oak_planks"
    assert name("minecraft:leaves", {"old_leaf_type": "birch"}) == "minecraft:birch_leaves"
    assert name("minecraft:leaves2", {"new_leaf_type": "dark_oak"}) == "minecraft:dark_oak_leaves"
    assert name("minecraft:sapling", {"sapling_type": "acacia"}) == "minecraft:acacia_sapling"
    assert name("minecraft:fence", {"wood_type": "birch"}) == "minecraft:birch_fence"
    assert props("minecraft:leaves", {"old_leaf_type": "oak", "persistent_bit": 1}) == {"persistent": "true"}


# --------------------------------------------------------------------------- #
# Stone-family variants
# --------------------------------------------------------------------------- #


def test_stone_dirt_sand_sponge():
    assert name("minecraft:stone", {"stone_type": "granite_smooth"}) == "minecraft:polished_granite"
    assert name("minecraft:stone", {"stone_type": "andesite"}) == "minecraft:andesite"
    assert name("minecraft:stone", {"stone_type": "diorite_smooth"}) == "minecraft:polished_diorite"
    assert name("minecraft:dirt", {"dirt_type": "coarse"}) == "minecraft:coarse_dirt"
    assert name("minecraft:dirt", {"dirt_type": "normal"}) == "minecraft:dirt"
    assert name("minecraft:sand", {"sand_type": "red"}) == "minecraft:red_sand"
    assert name("minecraft:sponge", {"sponge_type": "wet"}) == "minecraft:wet_sponge"


def test_sandstone():
    # "heiroglyphs" is misspelled in the Bedrock data, and we match it exactly.
    assert name("minecraft:sandstone", {"sand_stone_type": "heiroglyphs"}) == "minecraft:chiseled_sandstone"
    assert name("minecraft:sandstone", {"sand_stone_type": "default"}) == "minecraft:sandstone"
    assert name("minecraft:red_sandstone", {"sand_stone_type": "cut"}) == "minecraft:cut_red_sandstone"
    assert name("minecraft:red_sandstone", {"sand_stone_type": "smooth"}) == "minecraft:smooth_red_sandstone"


def test_stone_bricks_and_infested():
    assert name("minecraft:stonebrick", {"stone_brick_type": "default"}) == "minecraft:stone_bricks"
    assert name("minecraft:stonebrick", {"stone_brick_type": "chiseled"}) == "minecraft:chiseled_stone_bricks"
    assert name("minecraft:monster_egg", {"monster_egg_stone_type": "cracked_stone_brick"}) == \
        "minecraft:infested_cracked_stone_bricks"
    assert name("minecraft:monster_egg", {"monster_egg_stone_type": "stone"}) == "minecraft:infested_stone"


def test_prismarine_quartz_purpur():
    assert name("minecraft:prismarine", {"prismarine_block_type": "dark"}) == "minecraft:dark_prismarine"
    assert name("minecraft:prismarine", {"prismarine_block_type": "bricks"}) == "minecraft:prismarine_bricks"
    assert bedrock_to_java("minecraft:quartz_block", {"chisel_type": "lines", "pillar_axis": "z"}) == (
        "minecraft:quartz_pillar", {"axis": "z"})
    assert name("minecraft:quartz_block", {"chisel_type": "smooth"}) == "minecraft:smooth_quartz"
    # Only the pillar keeps an axis in Java; the others have no properties.
    assert bedrock_to_java("minecraft:quartz_block", {"chisel_type": "chiseled", "pillar_axis": "x"}) == (
        "minecraft:chiseled_quartz_block", {})
    assert name("minecraft:purpur_block", {"chisel_type": "lines"}) == "minecraft:purpur_pillar"


def test_walls():
    assert name("minecraft:cobblestone_wall", {"wall_block_type": "mossy_cobblestone"}) == \
        "minecraft:mossy_cobblestone_wall"
    assert name("minecraft:cobblestone_wall", {"wall_block_type": "end_brick"}) == \
        "minecraft:end_stone_brick_wall"
    # Bedrock's "short" connection is Java's "low".
    assert props("minecraft:cobblestone_wall", {
        "wall_block_type": "granite", "wall_post_bit": 1,
        "wall_connection_type_north": "short", "wall_connection_type_east": "none",
    }) == {"up": "true", "north": "low", "east": "none"}


# --------------------------------------------------------------------------- #
# Slabs and stairs
# --------------------------------------------------------------------------- #


def test_stone_slabs():
    assert bedrock_to_java("minecraft:stone_block_slab", {"stone_slab_type": "quartz", "top_slot_bit": 1}) == (
        "minecraft:quartz_slab", {"type": "top"})
    assert bedrock_to_java("minecraft:double_stone_block_slab", {"stone_slab_type": "smooth_stone"}) == (
        "minecraft:smooth_stone_slab", {"type": "double"})
    assert bedrock_to_java("minecraft:stone_slab", {"stone_slab_type": "wood", "top_slot_bit": 0}) == (
        "minecraft:petrified_oak_slab", {"type": "bottom"})
    assert name("minecraft:stone_block_slab2", {"stone_slab_type_2": "prismarine_dark"}) == \
        "minecraft:dark_prismarine_slab"
    assert name("minecraft:stone_block_slab3", {"stone_slab_type_3": "end_stone_brick"}) == \
        "minecraft:end_stone_brick_slab"
    assert name("minecraft:stone_block_slab4", {"stone_slab_type_4": "smooth_quartz"}) == \
        "minecraft:smooth_quartz_slab"
    # A double slab is a double slab even when the top bit says otherwise.
    assert props("minecraft:double_stone_block_slab4", {"stone_slab_type_4": "stone", "top_slot_bit": 1}) == \
        {"type": "double"}


def test_wooden_slabs():
    assert bedrock_to_java("minecraft:wooden_slab", {"wood_type": "spruce", "top_slot_bit": 1}) == (
        "minecraft:spruce_slab", {"type": "top"})
    assert bedrock_to_java("minecraft:double_wooden_slab", {"wood_type": "oak"}) == (
        "minecraft:oak_slab", {"type": "double"})


def test_stairs():
    assert bedrock_to_java("minecraft:oak_stairs", {"weirdo_direction": 2, "upside_down_bit": 1}) == (
        "minecraft:oak_stairs", {"facing": "south", "half": "top", "shape": "straight"})
    assert props("minecraft:stone_brick_stairs", {"weirdo_direction": 0, "upside_down_bit": 0}) == \
        {"facing": "east", "half": "bottom", "shape": "straight"}
    assert props("minecraft:granite_stairs", {"weirdo_direction": 1}) == \
        {"facing": "west", "shape": "straight"}
    assert props("minecraft:granite_stairs", {"weirdo_direction": 3}) == \
        {"facing": "north", "shape": "straight"}
    # 1.12's "stone stairs" were cobblestone; the real stone ones came later.
    assert name("minecraft:stone_stairs", {"weirdo_direction": 0}) == "minecraft:cobblestone_stairs"
    assert name("minecraft:normal_stone_stairs", {"weirdo_direction": 0}) == "minecraft:stone_stairs"


# --------------------------------------------------------------------------- #
# Doors, trapdoors, beds -- the three incompatible "direction" vocabularies
# --------------------------------------------------------------------------- #


def test_doors():
    assert bedrock_to_java("minecraft:wooden_door", {
        "direction": 1, "open_bit": 1, "upper_block_bit": 0, "door_hinge_bit": 1,
    }) == ("minecraft:oak_door", {
        "facing": "south", "open": "true", "half": "lower", "hinge": "right"})
    assert props("minecraft:spruce_door", {"direction": 0, "upper_block_bit": 1, "door_hinge_bit": 0}) == \
        {"facing": "east", "half": "upper", "hinge": "left"}
    assert props("minecraft:iron_door", {"direction": 2})["facing"] == "west"
    assert props("minecraft:iron_door", {"direction": 3})["facing"] == "north"


def test_trapdoors():
    assert bedrock_to_java("minecraft:trapdoor", {
        "direction": 0, "upside_down_bit": 0, "open_bit": 1,
    }) == ("minecraft:oak_trapdoor", {"facing": "east", "half": "bottom", "open": "true"})
    # Trapdoors number their directions differently from doors: 1 is west here.
    assert props("minecraft:iron_trapdoor", {"direction": 1, "upside_down_bit": 1})["facing"] == "west"
    assert props("minecraft:birch_trapdoor", {"direction": 2})["facing"] == "south"
    assert props("minecraft:birch_trapdoor", {"direction": 3})["facing"] == "north"


def test_bed_and_default_direction():
    assert bedrock_to_java("minecraft:bed", {"direction": 3, "head_piece_bit": 1}) == (
        "minecraft:red_bed", {"facing": "east", "part": "head"})
    assert props("minecraft:bed", {"direction": 0, "head_piece_bit": 0, "occupied_bit": 1}) == \
        {"facing": "south", "part": "foot", "occupied": "true"}
    # Fence gates share the bed's direction order.
    assert props("minecraft:fence_gate", {"direction": 1, "open_bit": 1}) == \
        {"facing": "west", "open": "true"}


# --------------------------------------------------------------------------- #
# Torches
# --------------------------------------------------------------------------- #


def test_torches():
    assert bedrock_to_java("minecraft:torch", {"torch_facing_direction": "top"}) == ("minecraft:torch", {})
    assert bedrock_to_java("minecraft:torch", {"torch_facing_direction": "unknown"}) == ("minecraft:torch", {})
    # torch_facing_direction names the way the torch leans, exactly as Java's
    # facing does -- not the block face it is stuck to.
    assert bedrock_to_java("minecraft:torch", {"torch_facing_direction": "west"}) == (
        "minecraft:wall_torch", {"facing": "west"})
    assert bedrock_to_java("minecraft:redstone_torch", {"torch_facing_direction": "north"}) == (
        "minecraft:redstone_wall_torch", {"facing": "north"})
    assert name("minecraft:soul_torch", {"torch_facing_direction": "east"}) == "minecraft:soul_wall_torch"
    assert name("minecraft:soul_torch", {"torch_facing_direction": "top"}) == "minecraft:soul_torch"


# --------------------------------------------------------------------------- #
# Plants
# --------------------------------------------------------------------------- #


def test_plants():
    assert bedrock_to_java("minecraft:double_plant", {
        "double_plant_type": "syringa", "upper_block_bit": 1,
    }) == ("minecraft:lilac", {"half": "upper"})
    assert name("minecraft:double_plant", {"double_plant_type": "grass"}) == "minecraft:tall_grass"
    assert name("minecraft:double_plant", {"double_plant_type": "paeonia"}) == "minecraft:peony"
    assert name("minecraft:red_flower", {"flower_type": "tulip_pink"}) == "minecraft:pink_tulip"
    assert name("minecraft:red_flower", {"flower_type": "houstonia"}) == "minecraft:azure_bluet"
    assert name("minecraft:red_flower", {"flower_type": "oxeye"}) == "minecraft:oxeye_daisy"
    assert name("minecraft:yellow_flower") == "minecraft:dandelion"
    # 1.20.3 renamed Java's "grass" plant to short_grass.
    assert name("minecraft:tallgrass", {"tall_grass_type": "default"}) == "minecraft:short_grass"
    assert name("minecraft:tallgrass", {"tall_grass_type": "fern"}) == "minecraft:fern"


# --------------------------------------------------------------------------- #
# Coral
# --------------------------------------------------------------------------- #


def test_coral():
    assert bedrock_to_java("minecraft:coral_block", {"coral_color": "blue", "dead_bit": 0}) == (
        "minecraft:tube_coral_block", {})
    assert bedrock_to_java("minecraft:coral_block", {"coral_color": "blue", "dead_bit": 1}) == (
        "minecraft:dead_tube_coral_block", {})
    assert name("minecraft:coral_block", {"coral_color": "pink"}) == "minecraft:brain_coral_block"
    assert name("minecraft:coral", {"coral_color": "purple"}) == "minecraft:bubble_coral"
    assert name("minecraft:coral_fan", {"coral_color": "red"}) == "minecraft:fire_coral_fan"
    assert name("minecraft:coral_fan_dead", {"coral_color": "yellow"}) == "minecraft:dead_horn_coral_fan"


# --------------------------------------------------------------------------- #
# Straight renames and one-offs
# --------------------------------------------------------------------------- #


def test_simple_renames():
    assert bedrock_to_java("minecraft:web", {}) == ("minecraft:cobweb", {})
    assert name("minecraft:grass") == "minecraft:grass_block"
    assert name("minecraft:grass_path") == "minecraft:dirt_path"
    assert name("minecraft:waterlily") == "minecraft:lily_pad"
    assert name("minecraft:deadbush") == "minecraft:dead_bush"
    assert name("minecraft:reeds") == "minecraft:sugar_cane"
    assert name("minecraft:melon_block") == "minecraft:melon"
    assert name("minecraft:noteblock") == "minecraft:note_block"
    assert name("minecraft:mob_spawner") == "minecraft:spawner"
    assert name("minecraft:brick_block") == "minecraft:bricks"
    assert name("minecraft:nether_brick") == "minecraft:nether_bricks"
    assert name("minecraft:end_bricks") == "minecraft:end_stone_bricks"
    assert name("minecraft:hardened_clay") == "minecraft:terracotta"
    assert name("minecraft:magma") == "minecraft:magma_block"
    assert name("minecraft:slime") == "minecraft:slime_block"
    assert name("minecraft:stonecutter_block") == "minecraft:stonecutter"
    assert name("minecraft:undyed_shulker_box") == "minecraft:shulker_box"
    assert name("minecraft:lit_pumpkin") == "minecraft:jack_o_lantern"
    assert name("minecraft:snow") == "minecraft:snow_block"


def test_snow_layers():
    """Bedrock counts snow layers from 0, Java from 1."""
    assert bedrock_to_java("minecraft:snow_layer", {"height": 3}) == ("minecraft:snow", {"layers": "4"})
    assert props("minecraft:snow_layer", {"height": 0}) == {"layers": "1"}
    assert props("minecraft:snow_layer", {"height": 7, "covered_bit": 1}) == {"layers": "8"}


def test_item_frames_become_air():
    """Item frames are entities in Java, so the block goes away entirely."""
    assert bedrock_to_java("minecraft:frame", {"facing_direction": 4, "item_frame_map_bit": 0}) == (
        "minecraft:air", {})
    assert bedrock_to_java("minecraft:glow_frame", {"facing_direction": 2}) == ("minecraft:air", {})


def test_signs_pistons_skulls():
    assert bedrock_to_java("minecraft:wall_sign", {"facing_direction": 4}) == (
        "minecraft:oak_wall_sign", {"facing": "west"})
    assert name("minecraft:standing_sign", {"ground_sign_direction": 9}) == "minecraft:oak_sign"
    assert props("minecraft:standing_sign", {"ground_sign_direction": 9}) == {"rotation": "9"}
    assert name("minecraft:spruce_standing_sign") == "minecraft:spruce_sign"
    assert name("minecraft:darkoak_standing_sign") == "minecraft:dark_oak_sign"
    assert bedrock_to_java("minecraft:sticky_piston_arm_collision", {"facing_direction": 1}) == (
        "minecraft:piston_head", {"facing": "up", "type": "sticky"})
    assert name("minecraft:piston_arm_collision", {"facing_direction": 0}) == "minecraft:piston_head"
    assert bedrock_to_java("minecraft:skull", {"facing_direction": 1}) == ("minecraft:skeleton_skull", {})
    assert bedrock_to_java("minecraft:skull", {"facing_direction": 5}) == (
        "minecraft:skeleton_wall_skull", {"facing": "east"})


def test_redstone():
    assert bedrock_to_java("minecraft:golden_rail", {"rail_direction": 1, "rail_data_bit": 1}) == (
        "minecraft:powered_rail", {"shape": "east_west", "powered": "true"})
    assert props("minecraft:rail", {"rail_direction": 6}) == {"shape": "south_east"}
    assert props("minecraft:rail", {"rail_direction": 4}) == {"shape": "ascending_north"}
    assert bedrock_to_java("minecraft:unpowered_repeater", {"direction": 0, "repeater_delay": 2}) == (
        "minecraft:repeater", {"facing": "south", "delay": "3", "powered": "false"})
    assert props("minecraft:powered_repeater", {"repeater_delay": 0})["powered"] == "true"
    assert bedrock_to_java("minecraft:unpowered_comparator", {
        "direction": 2, "output_subtract_bit": 1, "output_lit_bit": 1,
    }) == ("minecraft:comparator", {"facing": "north", "mode": "subtract", "powered": "true"})
    assert props("minecraft:redstone_wire", {"redstone_signal": 12}) == {"power": "12"}
    assert name("minecraft:lit_redstone_lamp") == "minecraft:redstone_lamp"
    assert props("minecraft:lit_redstone_lamp") == {"lit": "true"}


def test_liquids():
    assert bedrock_to_java("minecraft:flowing_water", {"liquid_depth": 3}) == (
        "minecraft:water", {"level": "3"})
    assert bedrock_to_java("minecraft:water", {"liquid_depth": 0}) == ("minecraft:water", {"level": "0"})
    assert name("minecraft:flowing_lava", {"liquid_depth": 1}) == "minecraft:lava"
    assert name("minecraft:bubble_column", {"drag_down": 1}) == "minecraft:water"


def test_anvil():
    assert name("minecraft:anvil", {"damage": "undamaged"}) == "minecraft:anvil"
    assert name("minecraft:anvil", {"damage": "slightly_damaged"}) == "minecraft:chipped_anvil"
    assert name("minecraft:anvil", {"damage": "very_damaged"}) == "minecraft:damaged_anvil"
    assert props("minecraft:anvil", {"damage": "undamaged", "direction": 2}) == {"facing": "north"}


def test_pumpkins():
    """Java's plain pumpkin has no properties; the carved one keeps a facing."""
    assert bedrock_to_java("minecraft:pumpkin", {"direction": 1}) == ("minecraft:pumpkin", {})
    assert bedrock_to_java("minecraft:pumpkin", {"minecraft:cardinal_direction": "west"}) == (
        "minecraft:pumpkin", {})
    assert bedrock_to_java("minecraft:carved_pumpkin", {"direction": 1}) == (
        "minecraft:carved_pumpkin", {"facing": "west"})
    assert props("minecraft:lit_pumpkin", {"minecraft:cardinal_direction": "north"}) == {"facing": "north"}


# --------------------------------------------------------------------------- #
# Generic property translation
# --------------------------------------------------------------------------- #


def test_vine_bits():
    assert bedrock_to_java("minecraft:vine", {"vine_direction_bits": 5}) == ("minecraft:vine", {
        "north": "true", "south": "true", "east": "false", "west": "false"})
    assert props("minecraft:vine", {"vine_direction_bits": 0}) == {
        "north": "false", "south": "false", "east": "false", "west": "false"}
    assert props("minecraft:vine", {"vine_direction_bits": 10}) == {
        "north": "false", "south": "false", "east": "true", "west": "true"}


def test_multi_face_bits():
    assert props("minecraft:glow_lichen", {"multi_face_direction_bits": 3}) == {
        "down": "true", "up": "true", "north": "false", "south": "false",
        "west": "false", "east": "false"}
    assert props("minecraft:glow_lichen", {"multi_face_direction_bits": 48}) == {
        "down": "false", "up": "false", "north": "false", "south": "false",
        "west": "true", "east": "true"}


def test_namespaced_states():
    assert props("minecraft:furnace", {"minecraft:cardinal_direction": "east"}) == {"facing": "east"}
    assert props("minecraft:barrel", {"minecraft:facing_direction": "up", "open_bit": 0}) == \
        {"facing": "up", "open": "false"}
    assert props("minecraft:oak_slab", {"minecraft:vertical_half": "top"}) == {"half": "top"}
    assert props("minecraft:grindstone", {"minecraft:block_face": "north"}) == {"facing": "north"}


def test_misc_generic_properties():
    assert props("minecraft:cake", {"bite_counter": 3}) == {"bites": "3"}
    assert props("minecraft:farmland", {"moisturized_amount": 7}) == {"moisture": "7"}
    assert props("minecraft:wheat", {"growth": 5}) == {"age": "5"}
    assert props("minecraft:tnt", {"explode_bit": 1}) == {"unstable": "true"}
    assert props("minecraft:lantern", {"hanging": 1}) == {"hanging": "true"}
    assert props("minecraft:candle", {"candles": 2, "lit": 1}) == {"candles": "3", "lit": "true"}
    assert props("minecraft:campfire", {"extinguished": 1, "direction": 0}) == \
        {"lit": "false", "facing": "south"}
    assert props("minecraft:tripwire", {"attached_bit": 1, "powered_bit": 0}) == \
        {"attached": "true", "powered": "false"}
    assert props("minecraft:stone_button", {"button_pressed_bit": 1, "facing_direction": 5}) == \
        {"powered": "true", "facing": "east"}
    assert props("minecraft:hay_block", {"pillar_axis": "x"}) == {"axis": "x"}


def test_boolean_coercion():
    """Bedrock bits arrive as 0/1 bytes, real bools, or strings."""
    assert props("minecraft:trapdoor", {"open_bit": True})["open"] == "true"
    assert props("minecraft:trapdoor", {"open_bit": False})["open"] == "false"
    assert props("minecraft:trapdoor", {"open_bit": "1"})["open"] == "true"
    assert props("minecraft:trapdoor", {"open_bit": "false"})["open"] == "false"


# --------------------------------------------------------------------------- #
# Pass-through and robustness
# --------------------------------------------------------------------------- #


def test_already_flattened_names_pass_through():
    assert bedrock_to_java("minecraft:red_wool", {}) == ("minecraft:red_wool", {})
    assert bedrock_to_java("minecraft:oak_log", {"pillar_axis": "y"}) == ("minecraft:oak_log", {"axis": "y"})
    assert bedrock_to_java("minecraft:granite", {}) == ("minecraft:granite", {})
    assert bedrock_to_java("minecraft:air", {}) == ("minecraft:air", {})
    assert name("minecraft:sea_lantern") == "minecraft:sea_lantern"
    assert name("minecraft:bamboo_sapling") == "minecraft:bamboo_sapling"
    assert name("minecraft:end_rod", {"facing_direction": 1}) == "minecraft:end_rod"


def test_unknown_names_and_states():
    # An unknown namespace keeps its states, merely stringified.
    assert bedrock_to_java("somemod:thing", {"a": 1}) == ("somemod:thing", {"a": "1"})
    assert bedrock_to_java("othermod:block", {"flag": True, "n": 4}) == (
        "othermod:block", {"flag": "true", "n": "4"})
    # A bare name gets the namespace filled in.
    assert name("wool", {"color": "red"}) == "minecraft:red_wool"
    # Bedrock-only states on a vanilla block are dropped: Java would reject them.
    assert bedrock_to_java("minecraft:oak_log", {"pillar_axis": "y", "deprecated": 0}) == (
        "minecraft:oak_log", {"axis": "y"})
    assert bedrock_to_java("minecraft:scaffolding", {"stability": 3, "stability_check": 1}) == (
        "minecraft:scaffolding", {})


def test_never_raises():
    for block, states in [
        ("", {}),
        ("minecraft:wool", None),
        ("minecraft:wool", {"color": None}),
        ("minecraft:log", {"old_log_type": 7}),
        ("minecraft:torch", {"torch_facing_direction": 3}),
        ("minecraft:stone_block_slab", {"stone_slab_type": "nope"}),
        ("minecraft:bed", {"direction": "junk"}),
        ("minecraft:snow_layer", {"height": "junk"}),
        (None, {}),
        (12345, {"x": object()}),
    ]:
        result = bedrock_to_java(block, states)
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], str) and ":" in result[0]
        assert isinstance(result[1], dict)
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in result[1].items())
