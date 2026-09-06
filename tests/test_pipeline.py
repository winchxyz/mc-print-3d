"""End-to-end tests against the real vanilla jar (skipped when no Minecraft install is present)."""
import numpy as np
import pytest

from mcprint.assets import AssetStack, ModelResolver, all_vanilla_jars
from mcprint.export import read_3mf_summary, read_stl
from mcprint.pipeline import Converter
from mcprint.schematics import BlockState, PaletteBuilder, Schematic
from mcprint.schematics.litematica import save_litematic
from mcprint.settings import ConversionSettings
from mcprint.voxel.connections import infer_connections
from mcprint.voxel.mesher import check_watertight

JARS = all_vanilla_jars()
JAR = JARS.get("1.21.11") or (JARS[sorted(JARS)[-1]] if JARS else None)
pytestmark = pytest.mark.skipif(JAR is None, reason="no vanilla jar on this machine")


def make_house() -> Schematic:
    pb = PaletteBuilder()
    B = lambda s: pb.add(BlockState.parse(s))  # noqa: E731
    W, H, L = 9, 6, 7
    g = np.zeros((H, L, W), dtype=np.int32)
    grass = B("minecraft:grass_block[snowy=false]")
    planks = B("minecraft:oak_planks")
    stairs_n = B("minecraft:oak_stairs[facing=north,half=bottom,shape=straight]")
    stairs_s = B("minecraft:oak_stairs[facing=south,half=bottom,shape=straight]")
    pane = B("minecraft:glass_pane[north=false,south=false,east=true,west=true]")
    fence = B("minecraft:oak_fence")           # no connection props -> inferred
    poppy = B("minecraft:poppy")
    torch = B("minecraft:torch")               # floating torch -> island
    g[0] = grass
    for y in (1, 2, 3):
        for x in range(1, 6):
            for z in range(1, 5):
                if x in (1, 5) or z in (1, 4):
                    g[y, z, x] = planks
    g[2, 4, 3] = pane
    for x in range(0, 7):
        g[4, 1, x] = stairs_s
        g[4, 4, x] = stairs_n
        g[4, 2, x] = planks
        g[4, 3, x] = planks
    for x in range(1, 6):
        g[1, 6, x] = fence
    g[1, 5, 7] = poppy
    g[5, 6, 8] = torch   # floats in the air
    return Schematic(width=W, height=H, length=L, palette=pb.states, blocks=g, name="house")


@pytest.fixture(scope="module")
def house_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("schem") / "house.litematic"
    save_litematic(make_house(), str(p))
    return p


def _converter(**overrides) -> Converter:
    s = ConversionSettings(**{"block_mm": 4.0, "max_colors": 4, **overrides})
    conv = Converter(s)
    stack = AssetStack()
    stack.add(JAR, label="vanilla")
    conv.use_assets(stack)
    return conv


def test_connection_inference():
    s = make_house()
    stack = AssetStack()
    stack.add(JAR)
    changed = infer_connections(s, ModelResolver(stack))
    assert changed == 5
    st = s.get(3, 1, 6)
    assert st.name == "minecraft:oak_fence"
    assert st.properties["east"] == "true" and st.properties["west"] == "true"
    assert st.properties["north"] == "false"
    end = s.get(1, 1, 6)
    assert end.properties["west"] == "false" and end.properties["east"] == "true"


def test_full_conversion_3mf(house_path, tmp_path):
    conv = _converter()
    res = conv.run(house_path, tmp_path / "house.3mf")
    assert res.files and res.files[0].suffix == ".3mf"
    x, y, z = res.size_mm
    # 9 x 7 footprint at 4 mm -> print X 36, Y 28 ; height 6 blocks minus removed floating torch row -> 5 blocks = 20
    assert abs(x - 36.0) < 1e-3 and abs(y - 28.0) < 1e-3
    assert abs(z - 20.0) < 1e-3
    assert res.stats["islands_removed_blocks"] == 1          # the floating torch
    assert res.stats["connections_inferred"] == 5
    assert res.stats["cavity_blocks_filled"] > 0             # house interior
    assert res.stats["states"]["fallback"] == 0
    for m in res.meshes.meshes:
        ok, bad = check_watertight(m)
        assert ok, f"material {m.material}: {bad} unbalanced edges"
        assert m.signed_volume() > 0
    summary = read_3mf_summary(res.files[0])
    assert summary["objects"] == len(res.meshes.meshes) + 1
    assert len(summary["materials"]) == len(res.meshes.meshes) <= 4


def test_texel_mode_and_stl(house_path, tmp_path):
    conv = _converter(color_mode="texel", max_colors=3, fill_cavities=False, remove_islands=False)
    res = conv.run(house_path, tmp_path / "house.stl", fmt="stl")
    assert len(res.files) == len(res.meshes.meshes) <= 3
    back = read_stl(res.files[0])
    assert back.triangle_count == res.meshes.meshes[0].triangle_count
    assert res.stats.get("islands_removed_blocks", 0) == 0
    assert 22.0 <= res.size_mm[2] <= 24.0      # torch row kept (a torch is 10/16 block tall)


def test_filament_assignment(house_path, tmp_path):
    from mcprint.color.filament import Filament
    conv = _converter()
    conv.settings.filaments = [Filament("#8B5A2B", "brown").to_dict(), Filament("#3C8D2F", "green").to_dict(),
                               Filament("#FFFFFF", "white").to_dict(), Filament("#000000", "black").to_dict()]
    res = conv.run(house_path)
    used = res.plan.used_materials()
    names = {m.filament.name for m in used}
    assert "brown" in names and "green" in names
    assert all(m.filament is not None for m in used)


def test_tiles_and_rotation(house_path, tmp_path):
    conv = _converter(split_to_bed=True, bed_mm=[24, 40, 100], bed_margin_mm=2, rotate_deg=90)
    res = conv.run(house_path, tmp_path / "house.3mf")
    assert len(res.files) >= 2
    assert all(f.exists() for f in res.files)


def test_single_color_and_base(house_path, tmp_path):
    conv = _converter(color_mode="single", base_plate_mm=4.0)
    res = conv.run(house_path, tmp_path / "house.obj", fmt="obj")
    assert len(res.meshes.meshes) == 1
    assert abs(res.size_mm[2] - 24.0) < 1e-3   # 20 + 4 mm base
    assert res.files[0].suffix == ".obj" and res.files[1].suffix == ".mtl"
