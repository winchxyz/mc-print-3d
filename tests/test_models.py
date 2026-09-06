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
