"""Texture-derived surface relief: pattern analysis, carving, and end-to-end on real blocks."""
import numpy as np
import pytest

from mcprint.assets import AssetStack, ModelResolver, TextureLoader, all_vanilla_jars
from mcprint.assets.textures import _otsu_threshold
from mcprint.schematics import BlockState
from mcprint.voxel import BlockVoxelizer, VoxelSettings
from mcprint.voxel.grid import carve_relief
from mcprint.voxel.mesher import MeshSink, check_watertight, mesh_chunk

JARS = all_vanilla_jars()
JAR = JARS.get("1.21.11") or (JARS[sorted(JARS)[-1]] if JARS else None)


class _FakeStack:
    """Minimal stand-in for AssetStack serving in-memory PNGs."""

    def __init__(self, images: dict[str, np.ndarray]):
        self.images = images

    def read(self, name: str):
        import io
        from PIL import Image
        for res, arr in self.images.items():
            if name == f"assets/minecraft/textures/{res}.png":
                buf = io.BytesIO()
                Image.fromarray(arr, "RGBA").save(buf, format="PNG")
                return buf.getvalue()
        return None


def _rgba(color, shape=(16, 16)):
    a = np.zeros((*shape, 4), dtype=np.uint8)
    a[..., :3] = color
    a[..., 3] = 255
    return a


def test_otsu():
    v = np.concatenate([np.full(300, 0.2), np.full(100, 0.8)])
    t = _otsu_threshold(v)
    assert 0.3 < t < 0.7
    assert _otsu_threshold(np.full(50, 0.5)) is None


def test_pattern_relief_bricks_and_noise():
    bricks = _rgba((150, 60, 50))
    bricks[3::4, :, :3] = (170, 170, 165)          # light mortar rows (25 %)
    stone_bricks = _rgba((120, 120, 120))
    stone_bricks[::4, :, :3] = (50, 50, 50)          # dark cracks
    rng = np.random.default_rng(0)
    noise = _rgba((128, 128, 128))
    noise[..., :3] = rng.integers(90, 166, size=(16, 16, 3))
    loader = TextureLoader(_FakeStack({"block/bricks": bricks, "block/stone_bricks": stone_bricks, "block/noise": noise}))
    r = loader.relief_map("minecraft:block/bricks")
    assert r is not None and r[3, 0] == 1.0 and r[0, 0] == 0.0          # mortar lines (light) recessed
    r2 = loader.relief_map("minecraft:block/stone_bricks")
    assert r2 is not None and r2[0, 0] == 1.0 and r2[1, 0] == 0.0        # cracks (dark) recessed
    assert loader.relief_map("minecraft:block/noise") is None            # speckle -> flat
    forced = loader.relief_map("minecraft:block/bricks", mode="dark")
    assert forced is None or forced[3, 0] == 0.0                          # forced dark never recesses the light mortar


def test_heightmap_relief_labpbr():
    base = _rgba((100, 100, 100))
    normal = _rgba((128, 128, 255))
    normal[..., 3] = 255
    normal[4:8, :, 3] = 0          # LabPBR: alpha = height, 0 = deepest
    loader = TextureLoader(_FakeStack({"block/x": base, "block/x_n": normal}))
    r = loader.relief_map("minecraft:block/x")
    assert r is not None and r[5, 5] == 1.0 and r[0, 0] == 0.0


def test_carve_relief_only_exposed():
    colors = np.zeros((8, 8, 8), dtype=np.uint16)
    colors[1:-1, 1:-1, 1:-1] = 1          # solid 6^3 block surrounded by empty space (like a padded chunk)
    relief = np.zeros((8, 8, 8), dtype=np.uint8)
    relief[1:-1, 1:-1, 1:-1] = 1
    relief[4] = 2                         # one horizontal layer asks for 2 voxels of depth
    removed = carve_relief(colors, relief, steps=2)
    assert removed > 0
    assert colors[3, 3, 3] == 1           # deep interior stays solid
    assert colors[1, 3, 3] == 0           # outer layer (relief 1, dist 0) removed
    assert colors[2, 3, 3] == 1           # second layer not removed where relief == 1
    assert colors[4, 1, 4] == 0 and colors[4, 2, 4] == 0   # relief 2 removes dist 0 and dist 1
    assert colors[4, 3, 4] == 1


@pytest.mark.skipif(JAR is None, reason="no vanilla jar")
def test_real_blocks_get_relief_and_stay_watertight():
    stack = AssetStack()
    stack.add(JAR)
    r, t = ModelResolver(stack), TextureLoader(stack)
    vx = BlockVoxelizer(r, t, VoxelSettings(resolution=16, min_thickness=1.0, relief_depth=1.0))
    assert vx.relief_steps == 1
    got = {}
    for name in ("minecraft:bricks", "minecraft:stone_bricks", "minecraft:cobblestone", "minecraft:oak_planks", "minecraft:stone", "minecraft:oak_log[axis=y]"):
        p = vx.voxelize(BlockState.parse(name))
        assert p.is_full                                   # relief is applied later, at grid level
        got[name] = int(p.relief.any()) if p.relief is not None else 0
    assert got["minecraft:bricks"] and got["minecraft:stone_bricks"] and got["minecraft:cobblestone"] and got["minecraft:oak_planks"]
    # carve a 2x1x1 wall of bricks: grooves appear on the outside only
    p = vx.voxelize(BlockState.parse("minecraft:bricks"))
    wall = np.concatenate([p.colors, p.colors], axis=2)
    wall_rel = np.concatenate([p.relief, p.relief], axis=2)
    padded = np.zeros(tuple(d + 2 for d in wall.shape), dtype=np.uint16)   # empty space around, like a chunk
    padded[1:-1, 1:-1, 1:-1] = np.where(wall != 0, 1, 0)
    rel = np.zeros(padded.shape, dtype=np.uint8)
    rel[1:-1, 1:-1, 1:-1] = wall_rel
    before = int((padded != 0).sum())
    carve_relief(padded, rel, 1)
    after = int((padded != 0).sum())
    assert 0 < before - after < before * 0.2
    # the face shared by the two blocks stays solid (only its outer rim sees the top/side relief)
    assert (padded[2:-2, 2:-2, 16] != 0).all() and (padded[2:-2, 2:-2, 17] != 0).all()
    sink = MeshSink(wall.shape[1])
    mesh_chunk(padded, (0, 0, 0), sink)
    m = sink.build().meshes[0]
    ok, bad = check_watertight(m)
    assert ok, bad
    assert abs(m.signed_volume() - after) < 1e-6
