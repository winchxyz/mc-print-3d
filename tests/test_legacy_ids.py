"""Tests for the legacy (1.12.2 and earlier) block id + metadata converter.

The expected names and property values were cross-checked against
``assets/minecraft/blockstates/*.json`` from a Minecraft 1.21 client jar: every
name this module can emit exists there, and every property value it emits is one
the corresponding blockstates file accepts.
"""
from __future__ import annotations

import pytest

from mcprint.schematics.legacy_ids import (
    LEGACY_NAME_TO_ID,
    legacy_block,
    legacy_block_from_name,
)


def name(block_id: int, meta: int) -> str:
    """Just the namespaced name of a legacy (id, meta) pair."""
    return legacy_block(block_id, meta)[0]


def props(block_id: int, meta: int) -> dict:
    """Just the properties of a legacy (id, meta) pair."""
    return legacy_block(block_id, meta)[1]


# --------------------------------------------------------------------------- #
# Stone-family variants
# --------------------------------------------------------------------------- #


def test_stone_variants():
    assert legacy_block(1, 0) == ("minecraft:stone", {})
    assert legacy_block(1, 1) == ("minecraft:granite", {})
    assert legacy_block(1, 2) == ("minecraft:polished_granite", {})
    assert legacy_block(1, 3) == ("minecraft:diorite", {})
    assert legacy_block(1, 6) == ("minecraft:polished_andesite", {})
    # 1.12 never wrote metadata above 6 for stone; fold back onto plain stone.
    assert name(1, 9) == "minecraft:stone"


def test_dirt_sand_and_sponge_variants():
    assert name(3, 0) == "minecraft:dirt"
    assert name(3, 1) == "minecraft:coarse_dirt"
    assert name(3, 2) == "minecraft:podzol"
    assert name(12, 0) == "minecraft:sand"
    assert name(12, 1) == "minecraft:red_sand"
    assert name(19, 0) == "minecraft:sponge"
    assert name(19, 1) == "minecraft:wet_sponge"


def test_sandstone_smooth_variant_became_cut_sandstone():
    assert name(24, 0) == "minecraft:sandstone"
    assert name(24, 1) == "minecraft:chiseled_sandstone"
    assert name(24, 2) == "minecraft:cut_sandstone"
    assert name(179, 0) == "minecraft:red_sandstone"
    assert name(179, 1) == "minecraft:chiseled_red_sandstone"
    assert name(179, 2) == "minecraft:cut_red_sandstone"


def test_stone_bricks_and_infested_blocks():
    assert name(98, 0) == "minecraft:stone_bricks"
    assert name(98, 1) == "minecraft:mossy_stone_bricks"
    assert name(98, 2) == "minecraft:cracked_stone_bricks"
    assert name(98, 3) == "minecraft:chiseled_stone_bricks"
    assert name(97, 0) == "minecraft:infested_stone"
    assert name(97, 3) == "minecraft:infested_mossy_stone_bricks"


def test_renamed_blocks():
    assert name(30, 0) == "minecraft:cobweb"
    assert name(52, 0) == "minecraft:spawner"
    assert name(103, 0) == "minecraft:melon"
    assert name(111, 0) == "minecraft:lily_pad"
    assert name(112, 0) == "minecraft:nether_bricks"
    assert name(165, 0) == "minecraft:slime_block"
    assert name(172, 0) == "minecraft:terracotta"
    assert name(206, 0) == "minecraft:end_stone_bricks"
    assert legacy_block(208, 0) == ("minecraft:dirt_path", {})
    assert name(213, 0) == "minecraft:magma_block"
    assert name(215, 0) == "minecraft:red_nether_bricks"
    assert name(153, 0) == "minecraft:nether_quartz_ore"
    assert name(80, 0) == "minecraft:snow_block"


# --------------------------------------------------------------------------- #
# Wood
# --------------------------------------------------------------------------- #


def test_planks():
    assert name(5, 0) == "minecraft:oak_planks"
    assert legacy_block(5, 2) == ("minecraft:birch_planks", {})
    assert name(5, 5) == "minecraft:dark_oak_planks"


def test_log_species_and_axis():
    assert legacy_block(17, 0) == ("minecraft:oak_log", {"axis": "y"})
    assert legacy_block(17, 5) == ("minecraft:spruce_log", {"axis": "x"})
    assert legacy_block(17, 10) == ("minecraft:birch_log", {"axis": "z"})
    # Metadata 12 is the "bark on every face" variant, i.e. the modern _wood.
    assert legacy_block(17, 12) == ("minecraft:oak_wood", {})
    assert legacy_block(17, 15) == ("minecraft:jungle_wood", {})
    assert legacy_block(162, 0) == ("minecraft:acacia_log", {"axis": "y"})
    assert legacy_block(162, 9) == ("minecraft:dark_oak_log", {"axis": "z"})
    assert legacy_block(162, 13) == ("minecraft:dark_oak_wood", {})


def test_leaves_ignore_the_decay_bits():
    assert legacy_block(18, 0) == ("minecraft:oak_leaves", {})
    assert legacy_block(18, 5) == ("minecraft:spruce_leaves", {})
    assert legacy_block(18, 12) == ("minecraft:oak_leaves", {})
    assert name(161, 1) == "minecraft:dark_oak_leaves"


def test_saplings():
    assert name(6, 0) == "minecraft:oak_sapling"
    assert name(6, 4) == "minecraft:acacia_sapling"
    assert props(6, 9)["stage"] == "1"


# --------------------------------------------------------------------------- #
# Colors
# --------------------------------------------------------------------------- #


def test_wool_and_other_colored_blocks():
    assert legacy_block(35, 0) == ("minecraft:white_wool", {})
    assert legacy_block(35, 14) == ("minecraft:red_wool", {})
    # 1.12's "silver" is the modern light_gray.
    assert name(35, 8) == "minecraft:light_gray_wool"
    assert name(95, 3) == "minecraft:light_blue_stained_glass"
    assert legacy_block(159, 11) == ("minecraft:blue_terracotta", {})
    assert name(160, 15) == "minecraft:black_stained_glass_pane"
    assert name(171, 6) == "minecraft:pink_carpet"
    assert legacy_block(251, 5) == ("minecraft:lime_concrete", {})
    assert name(252, 13) == "minecraft:green_concrete_powder"
    assert name(227, 0) == "minecraft:light_gray_shulker_box"
    assert legacy_block(235, 3) == (
        "minecraft:white_glazed_terracotta", {"facing": "east"}
    )


# --------------------------------------------------------------------------- #
# Slabs and stairs
# --------------------------------------------------------------------------- #


def test_stone_slabs():
    assert legacy_block(44, 0) == ("minecraft:smooth_stone_slab", {"type": "bottom"})
    assert legacy_block(44, 8) == ("minecraft:smooth_stone_slab", {"type": "top"})
    assert legacy_block(44, 1) == ("minecraft:sandstone_slab", {"type": "bottom"})
    assert legacy_block(44, 2) == ("minecraft:petrified_oak_slab", {"type": "bottom"})
    assert legacy_block(44, 10) == ("minecraft:petrified_oak_slab", {"type": "top"})
    assert name(44, 5) == "minecraft:stone_brick_slab"
    assert name(44, 7) == "minecraft:quartz_slab"


def test_double_stone_slabs_and_the_seamless_smooth_blocks():
    assert legacy_block(43, 0) == ("minecraft:smooth_stone_slab", {"type": "double"})
    assert legacy_block(43, 3) == ("minecraft:cobblestone_slab", {"type": "double"})
    # The "seamless" double slabs became real smooth blocks in 1.13.
    assert legacy_block(43, 8) == ("minecraft:smooth_stone", {})
    assert legacy_block(43, 9) == ("minecraft:smooth_sandstone", {})
    assert legacy_block(43, 15) == ("minecraft:smooth_quartz", {})
    assert legacy_block(181, 8) == ("minecraft:smooth_red_sandstone", {})


def test_wooden_red_sandstone_and_purpur_slabs():
    assert legacy_block(126, 1) == ("minecraft:spruce_slab", {"type": "bottom"})
    assert legacy_block(126, 13) == ("minecraft:dark_oak_slab", {"type": "top"})
    assert legacy_block(125, 2) == ("minecraft:birch_slab", {"type": "double"})
    assert legacy_block(182, 0) == ("minecraft:red_sandstone_slab", {"type": "bottom"})
    assert legacy_block(182, 8) == ("minecraft:red_sandstone_slab", {"type": "top"})
    assert legacy_block(181, 0) == ("minecraft:red_sandstone_slab", {"type": "double"})
    assert legacy_block(205, 8) == ("minecraft:purpur_slab", {"type": "top"})
    assert legacy_block(204, 0) == ("minecraft:purpur_slab", {"type": "double"})


def test_stairs():
    assert legacy_block(53, 0) == (
        "minecraft:oak_stairs", {"facing": "east", "half": "bottom", "shape": "straight"}
    )
    assert legacy_block(53, 6) == (
        "minecraft:oak_stairs", {"facing": "south", "half": "top", "shape": "straight"}
    )
    assert props(53, 1)["facing"] == "west"
    assert props(53, 3)["facing"] == "north"
    assert props(53, 4)["half"] == "top"
    # 1.12's id 67 "stone_stairs" is really the cobblestone stair.
    assert name(67, 0) == "minecraft:cobblestone_stairs"
    assert name(108, 0) == "minecraft:brick_stairs"
    assert name(109, 0) == "minecraft:stone_brick_stairs"
    assert name(114, 0) == "minecraft:nether_brick_stairs"
    assert name(128, 0) == "minecraft:sandstone_stairs"
    assert name(134, 0) == "minecraft:spruce_stairs"
    assert name(156, 0) == "minecraft:quartz_stairs"
    assert name(164, 0) == "minecraft:dark_oak_stairs"
    assert name(180, 0) == "minecraft:red_sandstone_stairs"
    assert name(203, 0) == "minecraft:purpur_stairs"


# --------------------------------------------------------------------------- #
# Doors, trapdoors, gates, fences
# --------------------------------------------------------------------------- #


def test_doors():
    assert legacy_block(64, 0) == (
        "minecraft:oak_door", {"half": "lower", "facing": "east", "open": "false"}
    )
    assert props(64, 1)["facing"] == "south"
    assert props(64, 2)["facing"] == "west"
    assert props(64, 3)["facing"] == "north"
    assert props(64, 4)["open"] == "true"
    upper = props(64, 9)
    assert name(64, 9) == "minecraft:oak_door"
    assert upper["half"] == "upper"
    assert upper["hinge"] == "right"
    assert props(64, 8)["hinge"] == "left"
    assert name(71, 0) == "minecraft:iron_door"
    assert name(193, 0) == "minecraft:spruce_door"
    assert name(196, 0) == "minecraft:acacia_door"
    assert name(197, 0) == "minecraft:dark_oak_door"


def test_trapdoors():
    assert legacy_block(96, 15) == (
        "minecraft:oak_trapdoor", {"facing": "east", "open": "true", "half": "top"}
    )
    assert legacy_block(96, 0) == (
        "minecraft:oak_trapdoor", {"facing": "north", "open": "false", "half": "bottom"}
    )
    assert props(96, 2)["facing"] == "west"
    assert name(167, 0) == "minecraft:iron_trapdoor"


def test_fence_gates_and_fences():
    assert legacy_block(107, 0) == (
        "minecraft:oak_fence_gate", {"facing": "south", "open": "false"}
    )
    assert props(107, 1)["facing"] == "west"
    assert props(107, 6)["open"] == "true"
    assert name(186, 0) == "minecraft:dark_oak_fence_gate"
    assert name(187, 0) == "minecraft:acacia_fence_gate"
    assert name(85, 0) == "minecraft:oak_fence"
    assert name(113, 0) == "minecraft:nether_brick_fence"
    assert name(191, 0) == "minecraft:dark_oak_fence"


# --------------------------------------------------------------------------- #
# Orientation families
# --------------------------------------------------------------------------- #


def test_torches():
    assert legacy_block(50, 5) == ("minecraft:torch", {})
    assert legacy_block(50, 1) == ("minecraft:wall_torch", {"facing": "east"})
    assert legacy_block(50, 4) == ("minecraft:wall_torch", {"facing": "north"})
    assert legacy_block(76, 5) == ("minecraft:redstone_torch", {"lit": "true"})
    assert legacy_block(75, 5) == ("minecraft:redstone_torch", {"lit": "false"})
    assert legacy_block(76, 3) == (
        "minecraft:redstone_wall_torch", {"facing": "south", "lit": "true"}
    )


def test_wall_mounted_blocks_only_face_sideways():
    assert legacy_block(54, 3) == ("minecraft:chest", {"facing": "south"})
    assert props(54, 2)["facing"] == "north"
    assert props(54, 4)["facing"] == "west"
    assert props(54, 5)["facing"] == "east"
    assert legacy_block(65, 5) == ("minecraft:ladder", {"facing": "east"})
    assert legacy_block(61, 2) == ("minecraft:furnace", {"facing": "north"})
    assert legacy_block(62, 2) == ("minecraft:furnace", {"facing": "north"})
    assert legacy_block(68, 4) == ("minecraft:oak_wall_sign", {"facing": "west"})
    assert name(130, 3) == "minecraft:ender_chest"
    assert name(146, 3) == "minecraft:trapped_chest"
    # A furnace can never face up or down, so bogus metadata folds to north.
    assert props(61, 0)["facing"] == "north"


def test_pistons_dispensers_and_other_six_way_blocks():
    assert legacy_block(33, 0) == (
        "minecraft:piston", {"facing": "down", "extended": "false"}
    )
    assert legacy_block(29, 9) == (
        "minecraft:sticky_piston", {"facing": "up", "extended": "true"}
    )
    assert props(33, 2)["facing"] == "north"
    assert props(33, 5)["facing"] == "east"
    assert legacy_block(34, 12) == (
        "minecraft:piston_head", {"facing": "west", "type": "sticky"}
    )
    assert legacy_block(34, 4) == (
        "minecraft:piston_head", {"facing": "west", "type": "normal"}
    )
    assert name(36, 0) == "minecraft:moving_piston"
    assert legacy_block(23, 11) == (
        "minecraft:dispenser", {"facing": "south", "triggered": "true"}
    )
    assert props(158, 1)["facing"] == "up"
    assert props(198, 1)["facing"] == "up"
    assert props(218, 0)["facing"] == "down"
    assert props(137, 5)["facing"] == "east"
    assert name(210, 0) == "minecraft:repeating_command_block"
    assert name(211, 0) == "minecraft:chain_command_block"
    # Hoppers cannot point up; 1.12 never wrote metadata 1 for them.
    assert legacy_block(154, 0) == ("minecraft:hopper", {"facing": "down"})
    assert legacy_block(154, 4) == ("minecraft:hopper", {"facing": "west"})
    assert props(154, 1)["facing"] == "down"


def test_horizontal_facing_blocks():
    assert legacy_block(86, 0) == ("minecraft:carved_pumpkin", {"facing": "south"})
    assert legacy_block(86, 1) == ("minecraft:carved_pumpkin", {"facing": "west"})
    assert legacy_block(91, 2) == ("minecraft:jack_o_lantern", {"facing": "north"})
    assert props(86, 3)["facing"] == "east"


def test_axis_pillars():
    assert legacy_block(170, 0) == ("minecraft:hay_block", {"axis": "y"})
    assert legacy_block(170, 4) == ("minecraft:hay_block", {"axis": "x"})
    assert legacy_block(216, 8) == ("minecraft:bone_block", {"axis": "z"})
    assert legacy_block(202, 4) == ("minecraft:purpur_pillar", {"axis": "x"})
    assert name(201, 0) == "minecraft:purpur_block"


def test_quartz_block_variants():
    assert legacy_block(155, 0) == ("minecraft:quartz_block", {})
    assert legacy_block(155, 1) == ("minecraft:chiseled_quartz_block", {})
    assert legacy_block(155, 2) == ("minecraft:quartz_pillar", {"axis": "y"})
    assert legacy_block(155, 3) == ("minecraft:quartz_pillar", {"axis": "x"})
    assert legacy_block(155, 4) == ("minecraft:quartz_pillar", {"axis": "z"})


# --------------------------------------------------------------------------- #
# Rails
# --------------------------------------------------------------------------- #


def test_plain_rail_uses_all_ten_shapes():
    assert legacy_block(66, 0) == ("minecraft:rail", {"shape": "north_south"})
    assert legacy_block(66, 1) == ("minecraft:rail", {"shape": "east_west"})
    assert legacy_block(66, 2) == ("minecraft:rail", {"shape": "ascending_east"})
    assert legacy_block(66, 5) == ("minecraft:rail", {"shape": "ascending_south"})
    assert legacy_block(66, 6) == ("minecraft:rail", {"shape": "south_east"})
    assert legacy_block(66, 9) == ("minecraft:rail", {"shape": "north_east"})


def test_powered_rails_use_bit_three_for_power():
    assert legacy_block(27, 9) == (
        "minecraft:powered_rail", {"shape": "east_west", "powered": "true"}
    )
    assert legacy_block(27, 0) == (
        "minecraft:powered_rail", {"shape": "north_south", "powered": "false"}
    )
    assert legacy_block(28, 2) == (
        "minecraft:detector_rail", {"shape": "ascending_east", "powered": "false"}
    )
    assert props(157, 12)["powered"] == "true"
    assert name(157, 0) == "minecraft:activator_rail"


# --------------------------------------------------------------------------- #
# Plants
# --------------------------------------------------------------------------- #


def test_tallgrass_and_flowers():
    assert legacy_block(31, 0) == ("minecraft:dead_bush", {})
    assert legacy_block(31, 1) == ("minecraft:short_grass", {})
    assert legacy_block(31, 2) == ("minecraft:fern", {})
    assert legacy_block(32, 0) == ("minecraft:dead_bush", {})
    assert legacy_block(37, 0) == ("minecraft:dandelion", {})
    assert legacy_block(38, 0) == ("minecraft:poppy", {})
    assert legacy_block(38, 1) == ("minecraft:blue_orchid", {})
    assert legacy_block(38, 4) == ("minecraft:red_tulip", {})
    assert legacy_block(38, 8) == ("minecraft:oxeye_daisy", {})


def test_double_plants():
    assert legacy_block(175, 0) == ("minecraft:sunflower", {"half": "lower"})
    assert legacy_block(175, 1) == ("minecraft:lilac", {"half": "lower"})
    assert legacy_block(175, 2) == ("minecraft:tall_grass", {"half": "lower"})
    assert legacy_block(175, 3) == ("minecraft:large_fern", {"half": "lower"})
    assert legacy_block(175, 5) == ("minecraft:peony", {"half": "lower"})
    # The upper half of a 1.12 double plant stored the block's facing rather
    # than its species, so the species is unrecoverable from (id, meta) alone.
    # We document that by falling back to the first variant, sunflower.
    assert legacy_block(175, 8) == ("minecraft:sunflower", {"half": "upper"})
    assert legacy_block(175, 11) == ("minecraft:sunflower", {"half": "upper"})


def test_crops_and_growth():
    assert legacy_block(59, 7) == ("minecraft:wheat", {"age": "7"})
    assert legacy_block(141, 3) == ("minecraft:carrots", {"age": "3"})
    assert legacy_block(142, 0) == ("minecraft:potatoes", {"age": "0"})
    assert legacy_block(207, 3) == ("minecraft:beetroots", {"age": "3"})
    assert legacy_block(115, 3) == ("minecraft:nether_wart", {"age": "3"})
    assert legacy_block(104, 4) == ("minecraft:pumpkin_stem", {"age": "4"})
    assert legacy_block(105, 7) == ("minecraft:melon_stem", {"age": "7"})
    assert legacy_block(60, 7) == ("minecraft:farmland", {"moisture": "7"})
    assert legacy_block(200, 5) == ("minecraft:chorus_flower", {"age": "5"})
    assert name(199, 0) == "minecraft:chorus_plant"


def test_cocoa_and_vine():
    assert legacy_block(127, 9) == ("minecraft:cocoa", {"facing": "west", "age": "2"})
    assert legacy_block(127, 0) == ("minecraft:cocoa", {"facing": "south", "age": "0"})
    assert legacy_block(106, 5) == (
        "minecraft:vine",
        {"south": "true", "west": "false", "north": "true", "east": "false"},
    )
    assert props(106, 8)["east"] == "true"
    assert props(106, 2)["west"] == "true"


def test_mushroom_blocks():
    assert legacy_block(99, 0) == ("minecraft:brown_mushroom_block", {})
    assert legacy_block(100, 5) == ("minecraft:red_mushroom_block", {})
    assert legacy_block(99, 10) == ("minecraft:mushroom_stem", {})
    assert legacy_block(100, 15) == ("minecraft:mushroom_stem", {})
    assert name(39, 0) == "minecraft:brown_mushroom"
    assert name(40, 0) == "minecraft:red_mushroom"


# --------------------------------------------------------------------------- #
# Redstone and interactive blocks
# --------------------------------------------------------------------------- #


def test_repeater_and_comparator():
    assert legacy_block(93, 0) == (
        "minecraft:repeater", {"facing": "south", "delay": "1", "powered": "false"}
    )
    assert props(93, 13)["delay"] == "4"
    assert props(93, 13)["facing"] == "west"
    assert props(94, 0)["powered"] == "true"
    assert legacy_block(149, 0) == (
        "minecraft:comparator",
        {"facing": "south", "mode": "compare", "powered": "false"},
    )
    assert props(149, 4)["mode"] == "subtract"
    assert props(150, 0)["powered"] == "true"


def test_lever_and_buttons():
    assert legacy_block(69, 1) == (
        "minecraft:lever", {"face": "wall", "facing": "east", "powered": "false"}
    )
    assert props(69, 9)["powered"] == "true"
    assert props(69, 0)["face"] == "ceiling"
    assert props(69, 5)["face"] == "floor"
    assert props(69, 6) == {"face": "floor", "facing": "west", "powered": "false"}
    assert legacy_block(77, 1) == (
        "minecraft:stone_button", {"face": "wall", "facing": "east"}
    )
    assert legacy_block(143, 5) == (
        "minecraft:oak_button", {"face": "floor", "facing": "north"}
    )
    assert props(77, 0)["face"] == "ceiling"


def test_pressure_plates_and_lamps():
    assert legacy_block(70, 0) == ("minecraft:stone_pressure_plate", {})
    assert legacy_block(72, 1) == ("minecraft:oak_pressure_plate", {})
    assert name(147, 0) == "minecraft:light_weighted_pressure_plate"
    assert name(148, 0) == "minecraft:heavy_weighted_pressure_plate"
    assert legacy_block(123, 0) == ("minecraft:redstone_lamp", {"lit": "false"})
    assert legacy_block(124, 0) == ("minecraft:redstone_lamp", {"lit": "true"})
    assert legacy_block(73, 0) == ("minecraft:redstone_ore", {"lit": "false"})
    assert legacy_block(74, 0) == ("minecraft:redstone_ore", {"lit": "true"})


def test_tripwire_and_daylight_detector():
    assert legacy_block(131, 2) == (
        "minecraft:tripwire_hook",
        {"facing": "north", "attached": "false", "powered": "false"},
    )
    assert props(131, 4)["attached"] == "true"
    assert props(132, 4)["attached"] == "true"
    assert props(151, 0)["inverted"] == "false"
    assert props(178, 0)["inverted"] == "true"
    assert name(178, 0) == "minecraft:daylight_detector"


# --------------------------------------------------------------------------- #
# Block-entity-backed and other one-off blocks
# --------------------------------------------------------------------------- #


def test_bed_is_red_and_splits_into_head_and_foot():
    assert legacy_block(26, 0) == ("minecraft:red_bed", {"facing": "south", "part": "foot"})
    assert legacy_block(26, 1) == ("minecraft:red_bed", {"facing": "west", "part": "foot"})
    assert legacy_block(26, 2) == ("minecraft:red_bed", {"facing": "north", "part": "foot"})
    assert legacy_block(26, 3) == ("minecraft:red_bed", {"facing": "east", "part": "foot"})
    assert props(26, 8)["part"] == "head"
    assert props(26, 11)["facing"] == "east"


def test_signs_banners_and_skulls():
    assert legacy_block(63, 0) == ("minecraft:oak_sign", {"rotation": "0"})
    assert legacy_block(63, 9) == ("minecraft:oak_sign", {"rotation": "9"})
    assert legacy_block(68, 2) == ("minecraft:oak_wall_sign", {"facing": "north"})
    assert legacy_block(176, 12) == ("minecraft:white_banner", {"rotation": "12"})
    assert legacy_block(177, 5) == ("minecraft:white_wall_banner", {"facing": "east"})
    assert legacy_block(144, 1) == ("minecraft:skeleton_skull", {})
    assert legacy_block(144, 4) == ("minecraft:skeleton_wall_skull", {"facing": "west"})
    assert props(144, 2)["facing"] == "north"


def test_anvil_damage_and_facing():
    assert legacy_block(145, 0) == ("minecraft:anvil", {"facing": "south"})
    assert legacy_block(145, 5) == ("minecraft:chipped_anvil", {"facing": "west"})
    assert legacy_block(145, 9) == ("minecraft:damaged_anvil", {"facing": "west"})


def test_cauldron_split_into_cauldron_and_water_cauldron():
    assert legacy_block(118, 0) == ("minecraft:cauldron", {})
    assert legacy_block(118, 1) == ("minecraft:water_cauldron", {"level": "1"})
    assert legacy_block(118, 2) == ("minecraft:water_cauldron", {"level": "2"})
    assert legacy_block(118, 3) == ("minecraft:water_cauldron", {"level": "3"})


def test_snow_layers_cake_and_brewing_stand():
    assert legacy_block(78, 0) == ("minecraft:snow", {"layers": "1"})
    assert legacy_block(78, 3) == ("minecraft:snow", {"layers": "4"})
    assert legacy_block(78, 7) == ("minecraft:snow", {"layers": "8"})
    assert legacy_block(92, 4) == ("minecraft:cake", {"bites": "4"})
    assert legacy_block(117, 5) == (
        "minecraft:brewing_stand",
        {"has_bottle_0": "true", "has_bottle_1": "false", "has_bottle_2": "true"},
    )


def test_portals_end_frame_and_walls():
    assert legacy_block(90, 1) == ("minecraft:nether_portal", {"axis": "x"})
    assert legacy_block(90, 2) == ("minecraft:nether_portal", {"axis": "z"})
    assert legacy_block(120, 6) == (
        "minecraft:end_portal_frame", {"facing": "north", "eye": "true"}
    )
    assert props(120, 0)["eye"] == "false"
    assert legacy_block(139, 0) == ("minecraft:cobblestone_wall", {})
    assert legacy_block(139, 1) == ("minecraft:mossy_cobblestone_wall", {})
    assert legacy_block(140, 0) == ("minecraft:flower_pot", {})
    assert name(119, 0) == "minecraft:end_portal"
    assert name(209, 0) == "minecraft:end_gateway"


def test_fluids_and_structure_block():
    assert legacy_block(8, 0) == ("minecraft:water", {"level": "0"})
    assert legacy_block(9, 3) == ("minecraft:water", {"level": "3"})
    assert legacy_block(10, 0) == ("minecraft:lava", {"level": "0"})
    assert legacy_block(11, 5) == ("minecraft:lava", {"level": "5"})
    assert name(255, 0) == "minecraft:structure_block"
    assert props(255, 0)["mode"] == "save"
    assert props(255, 2)["mode"] == "corner"
    assert legacy_block(212, 2) == ("minecraft:frosted_ice", {"age": "2"})
    assert name(217, 0) == "minecraft:structure_void"


def test_prismarine_and_misc_full_blocks():
    assert name(168, 0) == "minecraft:prismarine"
    assert name(168, 1) == "minecraft:prismarine_bricks"
    assert name(168, 2) == "minecraft:dark_prismarine"
    assert legacy_block(0, 0) == ("minecraft:air", {})
    assert name(7, 0) == "minecraft:bedrock"
    assert name(2, 0) == "minecraft:grass_block"
    assert name(45, 0) == "minecraft:bricks"
    assert name(25, 0) == "minecraft:note_block"
    assert name(166, 0) == "minecraft:barrier"
    assert name(169, 0) == "minecraft:sea_lantern"
    assert name(174, 0) == "minecraft:packed_ice"
    assert name(214, 0) == "minecraft:nether_wart_block"


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #


def test_unknown_ids_get_a_placeholder():
    assert legacy_block(999, 0) == ("minecraft:unknown_legacy_999", {})
    assert legacy_block(4095, 7) == ("minecraft:unknown_legacy_4095", {})
    # 253 and 254 never existed in vanilla 1.12.2.
    assert legacy_block(253, 0) == ("minecraft:unknown_legacy_253", {})
    assert legacy_block(254, 0) == ("minecraft:unknown_legacy_254", {})


@pytest.mark.parametrize(
    "block_id, meta",
    [(None, 0), (1, None), ("nope", 0), (1, "nope"), (-1, 0), (1, -1), (1, 4096)],
)
def test_never_raises_on_garbage(block_id, meta):
    result = legacy_block(block_id, meta)
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], str) and result[0].count(":") == 1
    assert isinstance(result[1], dict)


def test_every_vanilla_id_is_mapped_and_well_formed():
    unmapped = []
    for block_id in range(256):
        for meta in range(16):
            block_name, block_props = legacy_block(block_id, meta)
            assert block_name.startswith("minecraft:"), (block_id, meta)
            assert all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in block_props.items()
            ), (block_id, meta)
            if block_name.startswith("minecraft:unknown_legacy_"):
                unmapped.append(block_id)
    assert sorted(set(unmapped)) == [253, 254]


def test_returned_property_dicts_are_not_shared():
    first = props(53, 0)
    first["facing"] = "mutated"
    assert props(53, 0)["facing"] == "east"


# --------------------------------------------------------------------------- #
# Name-keyed lookups
# --------------------------------------------------------------------------- #


def test_legacy_name_to_id_table():
    assert LEGACY_NAME_TO_ID["minecraft:stone"] == 1
    assert LEGACY_NAME_TO_ID["minecraft:planks"] == 5
    assert LEGACY_NAME_TO_ID["minecraft:log"] == 17
    assert LEGACY_NAME_TO_ID["minecraft:log2"] == 162
    assert LEGACY_NAME_TO_ID["minecraft:tallgrass"] == 31
    assert LEGACY_NAME_TO_ID["minecraft:stained_hardened_clay"] == 159
    assert LEGACY_NAME_TO_ID["minecraft:concrete"] == 251
    assert LEGACY_NAME_TO_ID["minecraft:structure_block"] == 255
    # 1.12 registry names, not the modern ones.
    assert LEGACY_NAME_TO_ID["minecraft:silver_shulker_box"] == 227
    assert LEGACY_NAME_TO_ID["minecraft:grass"] == 2
    assert LEGACY_NAME_TO_ID["minecraft:web"] == 30
    assert LEGACY_NAME_TO_ID["minecraft:double_stone_slab"] == 43
    assert all(k.startswith("minecraft:") for k in LEGACY_NAME_TO_ID)
    # Every vanilla id except the two that never existed.
    assert len(LEGACY_NAME_TO_ID) == 254
    assert len(set(LEGACY_NAME_TO_ID.values())) == 254


def test_legacy_block_from_name():
    assert legacy_block_from_name("minecraft:log", 1) == (
        "minecraft:spruce_log", {"axis": "y"}
    )
    # The namespace is optional and lookups are case-insensitive.
    assert legacy_block_from_name("log", 1) == ("minecraft:spruce_log", {"axis": "y"})
    assert legacy_block_from_name("LOG", 5) == ("minecraft:spruce_log", {"axis": "x"})
    assert legacy_block_from_name("minecraft:double_stone_slab", 8) == (
        "minecraft:smooth_stone", {}
    )
    assert legacy_block_from_name("minecraft:wool", 14) == ("minecraft:red_wool", {})


def test_unknown_names_pass_through_with_a_namespace():
    assert legacy_block_from_name("somemod:widget", 0) == ("somemod:widget", {})
    assert legacy_block_from_name("somemod:widget", 7) == ("somemod:widget", {})
    assert legacy_block_from_name("not_a_block", 0) == ("minecraft:not_a_block", {})
    assert legacy_block_from_name(None, 0)[1] == {}


def test_name_lookup_agrees_with_id_lookup_everywhere():
    for legacy_name, block_id in LEGACY_NAME_TO_ID.items():
        for meta in (0, 1, 5, 8, 15):
            assert legacy_block_from_name(legacy_name, meta) == legacy_block(
                block_id, meta
            ), (legacy_name, meta)
