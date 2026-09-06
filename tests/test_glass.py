"""Glass-like blocks keep their own translucent color so they can be printed with clear filament."""
import numpy as np
import pytest

from mcprint.assets import AssetStack, ModelResolver, TextureLoader, all_vanilla_jars
from mcprint.color.catalog import catalog_filaments
from mcprint.color.filament import Filament
from mcprint.color.quantize import assign_filaments, is_clear_filament, plan_clusters
from mcprint.schematics import BlockState
from mcprint.voxel import BlockVoxelizer, ColorIndex, VoxelSettings

JARS = all_vanilla_jars()
JAR = JARS.get("1.21.11") or (JARS[sorted(JARS)[-1]] if JARS else None)


def test_colorindex_keeps_translucent_apart():
    ci = ColorIndex()
    a = ci.index_of_color((250, 250, 250))
    b = ci.index_of_color((250, 250, 250), translucent=True)
    assert a != b
    assert ci.translucent_indices() == {b}
    assert ci.index_of_color((250, 250, 250)) == a           # stable
    assert ci.is_translucent()[b] and not ci.is_translucent()[a]


def test_plan_clusters_reserves_a_glass_cluster():
    pal = np.array([[0, 0, 0], [200, 20, 20], [20, 200, 20], [230, 240, 245], [235, 242, 248]], dtype=np.uint8)
    hist = {1: 100, 2: 80, 3: 30, 4: 20}
    plan = plan_clusters(hist, pal, 3, translucent={3, 4})
    glass = [c for c in plan.clusters if c.translucent]
    assert len(glass) == 1 and set(glass[0].members.tolist()) == {3, 4}
    assert len(plan.clusters) == 3                             # 2 opaque + 1 glass
    m = plan.materials[glass[0].material]
    assert m.translucent and "clear" in m.name.lower()
    assert plan.lut[3] == plan.lut[4] == glass[0].material and plan.lut[1] != plan.lut[3]
    # filament assignment prefers a clear filament for the glass cluster
    fils = [Filament("#FF0000", "red"), Filament("#00FF00", "green"), Filament("#F0F0F0", "white"),
            Filament("#EAF2F6", "Transparent", material="PETG", tags=["translucent"])]
    assign_filaments(plan, fils)
    g = next(c for c in plan.clusters if c.translucent)
    assert plan.materials[g.material].filament.name == "Transparent"
    assert is_clear_filament(fils[3]) and not is_clear_filament(fils[2])


def test_catalog_has_clear_filaments():
    clear = [f for f in catalog_filaments() if "translucent" in f.tags]
    assert len(clear) >= 10
    assert any(f.vendor == "Bambu Lab" and "Transparent" in f.name for f in clear)


@pytest.mark.skipif(JAR is None, reason="no vanilla jar")
def test_glass_blocks_are_translucent_colors():
    stack = AssetStack()
    stack.add(JAR)
    vx = BlockVoxelizer(ModelResolver(stack), TextureLoader(stack), VoxelSettings(resolution=8), ColorIndex())
    glass = vx.voxelize(BlockState.parse("minecraft:glass"))
    pane = vx.voxelize(BlockState.parse("minecraft:blue_stained_glass_pane[north=true,south=true,east=false,west=false]"))
    wool = vx.voxelize(BlockState.parse("minecraft:white_wool"))
    ice = vx.voxelize(BlockState.parse("minecraft:ice"))
    t = vx.colors.is_translucent()
    assert glass.is_full and t[glass.colors[glass.colors != 0]].all()
    assert t[pane.colors[pane.colors != 0]].all()
    assert t[ice.colors[ice.colors != 0]].all()
    assert not t[wool.colors[wool.colors != 0]].any()
