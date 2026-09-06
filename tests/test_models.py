import os
from pathlib import Path

import numpy as np
import pytest

from mcprint.assets import AssetStack, ModelResolver, TextureLoader, all_vanilla_jars, find_instances
from mcprint.assets.models import _eval_condition, _pick_variant, blockstate_rotation, rotation_matrix
from mcprint.schematics import BlockState

JARS = all_vanilla_jars()
JAR = JARS.get("1.21.11") or (JARS[sorted(JARS)[-1]] if JARS else None)


@pytest.fixture(scope="module")
def stack():
    if JAR is None:
        pytest.skip("no vanilla jar on this machine")
    s = AssetStack()
    s.add(JAR, label="vanilla")
    return s


def test_variant_matching():
    variants = {"facing=east,half=bottom": 1, "facing=east,half=top": 2, "facing=south,half=bottom": 3}
    assert _pick_variant(variants, {"facing": "south", "half": "bottom"}) == "facing=south,half=bottom"
    # missing property -> best partial match, first wins on ties
    assert _pick_variant(variants, {"facing": "east"}) == "facing=east,half=bottom"
    assert _pick_variant({"": 1}, {"facing": "east"}) == ""
    assert _pick_variant({"normal": 1}, {}) == "normal"


def test_condition_eval():
    assert _eval_condition({"north": "true"}, {"north": "true"})
    assert not _eval_condition({"north": "true"}, {"north": "false"})
    assert not _eval_condition({"north": "true"}, {})
    assert _eval_condition({"OR": [{"a": "1"}, {"b": "2"}]}, {"b": "2"})
    assert _eval_condition({"AND": [{"a": "1"}, {"b": "2|3"}]}, {"a": "1", "b": "3"})
    assert not _eval_condition({"AND": [{"a": "1"}, {"b": "2"}]}, {"a": "1", "b": "3"})


def test_rotation_conventions():
    # blockstate y=90 turns something facing east (+x) to face south (+z)
    R = blockstate_rotation(0, 90)
    v = R @ np.array([1.0, 0, 0])
    assert np.allclose(v, [0, 0, 1], atol=1e-9)
    # x=90 turns north (-z) to down (-y)
    R = blockstate_rotation(90, 0)
    v = R @ np.array([0, 0, -1.0])
    assert np.allclose(v, [0, -1, 0], atol=1e-9)
    assert np.allclose(rotation_matrix("y", 90) @ np.array([1.0, 0, 0]), [0, 0, -1], atol=1e-9)


def test_resolve_vanilla_blocks(stack):
    r = ModelResolver(stack)
    stone = r.resolve(BlockState.parse("minecraft:stone"))
    assert stone.kind == "ok" and stone.is_full_cube
    tex = stone.instances[0].model.elements[0].faces["up"].texture
    assert tex == "minecraft:block/stone"

    stairs = r.resolve(BlockState.parse("minecraft:oak_stairs[facing=south,half=bottom,shape=straight]"))
    assert stairs.kind == "ok" and not stairs.is_full_cube
    assert stairs.instances[0].y == 90
    assert len(stairs.instances[0].model.elements) == 2

    fence = r.resolve(BlockState.parse("minecraft:oak_fence[north=true,south=true,east=false,west=false]"))
    assert fence.kind == "ok"
    assert len(fence.instances) == 3  # post + 2 sides

    flower = r.resolve(BlockState.parse("minecraft:poppy"))
    assert flower.kind == "ok"
    els = flower.instances[0].model.elements
    assert len(els) == 2 and els[0].rot_axis == "y" and els[0].rot_angle == 45 and els[0].rescale
    assert els[0].faces["north"].texture == "minecraft:block/poppy"

    grass = r.resolve(BlockState.parse("minecraft:grass_block[snowy=false]"))
    assert grass.kind == "ok"
    up = grass.instances[0].model.elements[0].faces["up"]
    assert up.tintindex == 0

    chest = r.resolve(BlockState.parse("minecraft:chest[facing=north]"))
    assert chest.kind == "builtin_entity"

    air = r.resolve(BlockState.parse("minecraft:air"))
    assert air.kind == "empty"

    unknown = r.resolve(BlockState.parse("somemod:does_not_exist"))
    assert unknown.kind == "missing_blockstate"

    glass_pane = r.resolve(BlockState.parse("minecraft:glass_pane[north=true,south=false,east=false,west=false]"))
    assert glass_pane.kind == "ok" and len(glass_pane.instances) >= 2

    # legacy-converted state without connection props still resolves (post only)
    fence2 = r.resolve(BlockState.parse("minecraft:oak_fence"))
    assert fence2.kind == "ok" and len(fence2.instances) == 1


def test_bare_names_get_game_defaults(stack):
    """Blocks named without properties (legacy files, catalogs) resolve like freshly placed blocks."""
    r = ModelResolver(stack)
    wall = r.resolve(BlockState.parse("minecraft:cobblestone_wall"))
    assert wall.kind == "ok" and len(wall.instances) == 1          # post only
    assert "wall_post" in wall.instances[0].model.name
    mushroom = r.resolve(BlockState.parse("minecraft:brown_mushroom_block"))
    assert mushroom.kind == "ok" and len(mushroom.instances) == 6  # every face present
    vine = r.resolve(BlockState.parse("minecraft:vine"))
    assert vine.kind == "ok" and len(vine.instances) == 1          # one face, not a ceiling plate
    carpet = r.resolve(BlockState.parse("minecraft:pale_moss_carpet"))
    assert carpet.kind == "ok" and len(carpet.instances) == 1      # bottom only, no side walls
    rod = r.resolve(BlockState.parse("minecraft:end_rod"))
    assert rod.instances[0].x == 0 and rod.instances[0].y == 0     # facing=up is the unrotated model
    rail = r.resolve(BlockState.parse("minecraft:activator_rail"))
    assert "raised" not in rail.instances[0].model.name            # flat, not ascending
    chain = r.resolve(BlockState.parse("minecraft:iron_chain"))
    assert chain.instances[0].x == 0                               # axis=y


def test_layered_elements_take_nearest_face_texture(stack):
    """Cactus is built from overlapping elements with partial faces: side voxels must use cactus_side."""
    from mcprint.voxel import BlockVoxelizer, ColorIndex, VoxelSettings
    t = TextureLoader(stack)
    vx = BlockVoxelizer(ModelResolver(stack), t, VoxelSettings(resolution=16, cutout_dilation=0), ColorIndex())
    p = vx.voxelize(BlockState.parse("minecraft:cactus"))
    pal = vx.colors.palette().astype(int)
    side = t.get("minecraft:block/cactus_side")[..., :3].astype(int)
    # west face voxels (x index 1, the element is inset by one texel) row by row vs. texture column 0.. (u = z)
    col = p.colors[:, :, 1]                        # [y, z]
    ok = 0
    for yi in range(16):
        for zi in range(2, 14):
            c = pal[col[15 - yi, zi]]
            expect = side[yi, zi]
            if np.abs(c - expect).max() <= 24:      # 15-bit color binning tolerance
                ok += 1
    assert ok > 0.8 * 16 * 12


def test_textures(stack):
    t = TextureLoader(stack)
    arr = t.get("minecraft:block/stone")
    assert arr is not None and arr.shape == (16, 16, 4)
    water = t.get("minecraft:block/water_still")  # animated strip -> first frame
    assert water is not None and water.shape[0] == water.shape[1]
    avg = t.average_color("minecraft:block/stone")
    assert avg is not None and 100 < avg[0] < 140
    assert t.get("minecraft:block/nope") is None
    ft, semi = t.alpha_stats("minecraft:block/poppy")
    assert ft > 0.5


@pytest.mark.skipif(not find_instances(), reason="no instances")
def test_find_instances():
    inst = find_instances()
    assert any(i.jar_path for i in inst)
    names = [i.name for i in inst]
    assert names
