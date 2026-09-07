"""Chests, beds, shulker boxes and heads stamped from the game's own ModelPart layers."""
import numpy as np
import pytest

from mcprint.assets import AssetStack, ModelResolver, TextureLoader, all_vanilla_jars
from mcprint.mobs.entity_blocks import entity_block
from mcprint.schematics import BlockState
from mcprint.voxel import BlockVoxelizer, ColorIndex, VoxelSettings

JARS = all_vanilla_jars()
JAR = JARS.get("1.21.11") or (JARS[sorted(JARS)[-1]] if JARS else None)
needs_jar = pytest.mark.skipif(JAR is None, reason="no vanilla jar on this machine")


def test_entity_block_specs_exist():
    for s in ("minecraft:chest[facing=north,type=single]", "minecraft:chest[facing=south,type=left]", "minecraft:red_bed[facing=east,part=head]",
              "minecraft:shulker_box[facing=up]", "minecraft:skeleton_skull[rotation=0]", "minecraft:creeper_wall_head[facing=south]", "minecraft:piglin_head[rotation=3]"):
        eb = entity_block(BlockState.parse(s))
        assert eb is not None and eb.model.parts, s
        assert eb.A.shape == (3, 3) and eb.b.shape == (3,)
    assert entity_block(BlockState.parse("minecraft:stone")) is None
    assert entity_block(BlockState.parse("minecraft:oak_planks")) is None


@pytest.fixture(scope="module")
def vox():
    if JAR is None:
        pytest.skip("no jar")
    stack = AssetStack()
    stack.add(JAR)
    return BlockVoxelizer(ModelResolver(stack), TextureLoader(stack), VoxelSettings(resolution=16, min_thickness=1.0), ColorIndex())


def _extent(p):
    ys, zs, xs = np.nonzero(p.colors != 0)
    return (xs.min(), xs.max(), ys.min(), ys.max(), zs.min(), zs.max())


@needs_jar
def test_single_chest_geometry_and_lock(vox):
    p = vox.voxelize(BlockState.parse("minecraft:chest[facing=north,type=single]"))
    assert "game model" in p.note
    x0, x1, y0, y1, z0, z1 = _extent(p)
    assert (x0, x1) == (1, 14) and (y0, y1) == (0, 13)          # 14 wide, 10 base + 5 lid (lid top at 14)
    assert z0 == 0 and z1 == 14                                 # the lock sticks out of the north face
    lock = p.colors[7:11, 0, :]                                 # z = 0 row holds only the lock
    assert 0 < np.count_nonzero(lock) <= 4 * 3


@needs_jar
def test_double_chest_halves_join_with_one_lock(vox):
    right = vox.voxelize(BlockState.parse("minecraft:chest[facing=south,type=right]"))
    left = vox.voxelize(BlockState.parse("minecraft:chest[facing=south,type=left]"))
    assert "double" in right.note and "double" in left.note
    rx = _extent(right)
    lx = _extent(left)
    assert rx[0] == 1 and rx[1] == 15, rx                       # right half: open seam at x = 16, wall at x = 1
    assert lx[0] == 0 and lx[1] == 14, lx                       # left half: open seam at x = 0
    # each half carries half of the lock on the south face (z = 15), next to the seam
    r_lock = np.count_nonzero(right.colors[7:11, 15, 14:16])
    l_lock = np.count_nonzero(left.colors[7:11, 15, 0:2])
    assert r_lock > 0 and l_lock > 0
    assert np.count_nonzero(right.colors[7:11, 15, 4:10]) == 0    # no lock in the middle of the half


@needs_jar
def test_bed_head_has_the_pillow_at_the_far_end(vox):
    foot = vox.voxelize(BlockState.parse("minecraft:red_bed[facing=east,part=foot]"))
    head = vox.voxelize(BlockState.parse("minecraft:red_bed[facing=east,part=head]"))
    for p in (foot, head):
        x0, x1, y0, y1, z0, z1 = _extent(p)
        assert (x0, x1, z0, z1) == (0, 15, 0, 15) and y0 == 0 and y1 == 8      # legs to the ground, mattress top at 9/16
    pal = vox.colors.palette()
    top = head.colors[8]                                        # mattress top layer [z, x]
    far = pal[top[:, 12:16][top[:, 12:16] != 0]].astype(float)    # east end of the head block
    near = pal[top[:, 0:4][top[:, 0:4] != 0]].astype(float)
    assert far.min(axis=1).mean() > near.min(axis=1).mean() + 60     # pillow (white) at the far end, blanket (red) toward the foot


@needs_jar
def test_skull_rotation_and_wall_head(vox):
    south = vox.voxelize(BlockState.parse("minecraft:skeleton_skull[rotation=8]"))
    north = vox.voxelize(BlockState.parse("minecraft:skeleton_skull[rotation=0]"))
    assert _extent(south) == (4, 11, 0, 7, 4, 11) and _extent(north) == (4, 11, 0, 7, 4, 11)
    assert not np.array_equal(south.colors, north.colors)      # turned by 180 degrees
    flipped = north.colors[:, ::-1, ::-1]
    assert (south.colors == flipped).mean() > 0.97               # same skull turned around (sampling ties aside)
    wall = vox.voxelize(BlockState.parse("minecraft:creeper_wall_head[facing=south]"))
    assert _extent(wall) == (4, 11, 4, 11, 0, 7)                # hangs on the north wall, 4 units up


@needs_jar
def test_shulker_box_fills_the_block(vox):
    p = vox.voxelize(BlockState.parse("minecraft:lime_shulker_box[facing=up]"))
    assert _extent(p) == (0, 15, 0, 15, 0, 15)
    assert np.count_nonzero(p.colors) > 3000
