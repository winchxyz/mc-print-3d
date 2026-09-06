"""Mob models, rasterization, schematic entities and kit figures."""
import math

import numpy as np
import pytest

from mcprint.assets import AssetStack, ModelResolver, TextureLoader, all_vanilla_jars
from mcprint.mobs import MOB_IDS, MobPlacement, MobVoxelizer, mob_model, normalize_mob_id, place_mobs
from mcprint.mobs.raster import _box_world_matrix, _part_transform
from mcprint.schematics import BlockState, PaletteBuilder, Schematic, load_schematic
from mcprint.schematics.entities import Entity
from mcprint.schematics.litematica import save_litematic
from mcprint.schematics.sponge import save_sponge
from mcprint.schematics.structure import save_structure
from mcprint.voxel import BlockVoxelizer, ColorIndex, VoxelModel, VoxelSettings

JARS = all_vanilla_jars()
JAR = JARS.get("1.21.11") or (JARS[sorted(JARS)[-1]] if JARS else None)
needs_jar = pytest.mark.skipif(JAR is None, reason="no vanilla jar on this machine")


# ---- registry & ids -------------------------------------------------------------------------
def test_registry_builds_every_mob():
    for mid in MOB_IDS:
        m = mob_model(mid)
        assert m is not None and m.parts and m.texture.startswith("minecraft:")
        for part in m.parts:
            if part.parent:
                m.part(part.parent)     # parents resolve


def test_id_normalization_and_props():
    assert normalize_mob_id("minecraft:creeper") == "creeper"
    assert normalize_mob_id("VillagerGolem") == "iron_golem"
    assert normalize_mob_id("Ozelot") == "cat"
    assert mob_model("minecraft:ender_dragon") is None       # animated at runtime, not printable from the rest pose
    red = mob_model("sheep", {"color": 14})
    assert any(b.tint == (0xB0, 0x2E, 0x26) for p in red.parts for b in p.boxes)
    sheared = mob_model("sheep", {"sheared": True})
    assert not any(b.texture for p in sheared.parts for b in p.boxes)
    assert mob_model("slime", {"size": 3}).scale == 4.0
    assert mob_model("pig", {"variant": "minecraft:cold"}).texture.endswith("cold_pig")
    assert mob_model("player", {"slim": True}).id == "player_slim"


# ---- transforms -----------------------------------------------------------------------------
def test_quadruped_body_rotation_matches_game():
    """The pig body box (10x16x8 rotated 90 deg about X) must span z -8..8 and y 10..18 in model space."""
    m = mob_model("pig")
    body = m.part("body")
    R, t = _part_transform(m, body)
    box = body.boxes[0]
    lo = np.asarray(box.origin); hi = lo + np.asarray(box.size)
    pts = np.array([R @ np.array([x, y, z]) + t for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    assert np.allclose(pts[:, 2].min(), -8) and np.allclose(pts[:, 2].max(), 8)
    assert np.allclose(pts[:, 1].min(), 10) and np.allclose(pts[:, 1].max(), 18)


def test_yaw_turns_the_face():
    """A creeper's face (model -Z) points +Z (south) at yaw 0 and -X (west) at yaw 90."""
    m = mob_model("creeper")
    head = m.part("head")
    for yaw, expect in ((0.0, (0, 0, 1)), (90.0, (-1, 0, 0)), (180.0, (0, 0, -1)), (-90.0, (1, 0, 0))):
        A, b = _box_world_matrix(m, head, MobPlacement(m, 0, 0, 0, yaw), 0.0)
        face_dir = A @ np.array([0.0, 0.0, -1.0])
        face_dir /= np.linalg.norm(face_dir)
        assert np.allclose(face_dir, expect, atol=1e-6), (yaw, face_dir)


# ---- rasterization with the real textures ---------------------------------------------------
@pytest.fixture(scope="module")
def vox():
    if JAR is None:
        pytest.skip("no jar")
    stack = AssetStack()
    stack.add(JAR)
    return BlockVoxelizer(ModelResolver(stack), TextureLoader(stack), VoxelSettings(resolution=8, min_thickness=1.0), ColorIndex())


@needs_jar
def test_creeper_raster_shape_and_colors(vox):
    mv = MobVoxelizer(vox, min_units=1.0)
    r = mv.rasterize(MobPlacement(mob_model("creeper"), 0.5, 0.0, 0.5, 0.0))
    assert set(r.cells) == {(0, 0, 0), (0, 1, 0)}          # 1.7 blocks tall, inside one column
    feet = r.cells[(0, 0, 0)].colors
    assert feet[0].any() and not feet[:, 0, 0].any()        # stands on the ground, legs do not fill the corner
    pal = vox.colors.palette()
    idx = np.unique(np.concatenate([p.colors[p.colors != 0] for p in r.cells.values()]))
    rgb = pal[idx].astype(float)
    assert (rgb[:, 1] > rgb[:, 0]).mean() > 0.8              # creepers are green
    assert not mv.missing


@needs_jar
def test_every_mob_rasterizes(vox):
    mv = MobVoxelizer(vox, min_units=1.0)
    for mid in MOB_IDS:
        r = mv.rasterize(MobPlacement(mob_model(mid), 0.5, 0.0, 0.5, 45.0))
        assert r.cells, mid
        assert min(c[1] for c in r.cells) == 0, mid          # feet on the ground
    assert not mv.missing


@needs_jar
def test_slime_outer_cube_is_translucent(vox):
    mv = MobVoxelizer(vox, min_units=1.0)
    r = mv.rasterize(MobPlacement(mob_model("slime", {"size": 1}), 0.5, 0.0, 0.5, 0.0))
    idx = np.unique(np.concatenate([p.colors[p.colors != 0] for p in r.cells.values()]))
    trans = vox.colors.translucent_indices()
    assert any(int(i) in trans for i in idx) and any(int(i) not in trans for i in idx)


@needs_jar
def test_place_mobs_grows_grid_and_merges(vox):
    grass = BlockState.make("minecraft:grass_block", {"snowy": "false"})
    pats = [vox.empty_pattern(), vox.voxelize(grass)]
    blocks = np.zeros((1, 2, 2), dtype=np.int32)
    blocks[0] = 1
    model = VoxelModel(blocks=blocks, palette=[BlockState.make("minecraft:air"), grass], patterns=pats, resolution=8, colors=vox.colors)
    placed, warns = place_mobs(model, [MobPlacement(mob_model("enderman"), 1.0, 1.0, 1.0, 0.0),
                                       MobPlacement(mob_model("chicken"), 0.5, 0.5, 0.5, 0.0)], vox)
    assert placed == 2 and not warns
    assert model.blocks.shape[0] >= 4                           # enderman needs 3 more layers
    mob_cells = [i for i, st in enumerate(model.palette) if st.name == "mcprint:mob"]
    assert mob_cells
    over = [model.patterns[i] for i in mob_cells if "over" in model.patterns[i].note]
    assert over, "chicken standing inside the grass block must merge with it"
    model.build_flat_patterns()
    for i in mob_cells:
        assert model.flat_patterns[i] is model.patterns[i]      # figures keep texel colors in 'block' color mode


# ---- schematic entities ----------------------------------------------------------------------
def _schem_with_entities() -> Schematic:
    pb = PaletteBuilder()
    g = np.zeros((2, 3, 3), dtype=np.int32)
    g[0] = pb.add(BlockState.make("minecraft:stone"))
    s = Schematic(3, 2, 3, pb.states, g, name="mobs")
    s.entities = [Entity("minecraft:creeper", 1.5, 1.0, 1.5, 90.0), Entity("minecraft:sheep", 0.5, 1.0, 2.5, 0.0, {"color": 14}),
                  Entity("minecraft:item_frame", 1.0, 1.0, 1.0, 0.0)]
    return s


def test_entities_round_trip(tmp_path):
    s = _schem_with_entities()
    save_litematic(s, str(tmp_path / "a.litematic"))
    save_sponge(s, str(tmp_path / "b.schem"), 2)
    save_sponge(s, str(tmp_path / "c.schem"), 3)
    save_structure(s, str(tmp_path / "d.nbt"))
    for name in ("a.litematic", "b.schem", "c.schem", "d.nbt"):
        loaded = load_schematic(tmp_path / name)
        got = [(e.id, e.x, e.y, e.z, e.yaw) for e in loaded.entities]
        assert got == [("minecraft:creeper", 1.5, 1.0, 1.5, 90.0), ("minecraft:sheep", 0.5, 1.0, 2.5, 0.0), ("minecraft:item_frame", 1.0, 1.0, 1.0, 0.0)], name
        assert loaded.entities[1].props.get("color") == 14


def test_cropped_shifts_entities():
    s = _schem_with_entities()
    s.blocks[0, 0, :] = 0
    c = s.cropped()
    assert c.length == 2 and c.entities[0].z == 0.5


@needs_jar
def test_pipeline_prints_entities_and_extra_mobs(tmp_path):
    from mcprint.pipeline import Converter
    from mcprint.settings import ConversionSettings
    s = _schem_with_entities()
    save_litematic(s, str(tmp_path / "m.litematic"))
    cfg = ConversionSettings(block_mm=8.0, resolution=8, color_mode="block", max_colors=4, remove_islands=False, fill_cavities=False,
                             extra_mobs=[{"id": "pig", "x": 2.5, "y": 1.0, "z": 0.5, "yaw": 180.0}])
    conv = Converter(cfg)
    res = conv.run(tmp_path / "m.litematic", tmp_path / "m.3mf")
    assert res.stats.get("mobs") == 3                              # creeper, sheep, pig (item frame skipped)
    assert any("item_frame" in w for w in res.warnings)
    assert any(st.name == "mcprint:mob" for st in res.model.palette)
    assert (tmp_path / "m.3mf").exists()
    cfg.include_mobs = False
    cfg.extra_mobs = []
    res2 = Converter(cfg).run(tmp_path / "m.litematic", tmp_path / "n.3mf")
    assert not res2.stats.get("mobs")


@needs_jar
def test_kit_figure_piece(tmp_path):
    from mcprint.kit import KitSettings, build_kit, write_kit
    from mcprint.pipeline import Converter
    from mcprint.settings import ConversionSettings
    s = _schem_with_entities()
    s.entities = s.entities[:1]
    save_litematic(s, str(tmp_path / "k.litematic"))
    cfg = ConversionSettings(block_mm=10.0, resolution=8, color_mode="block", max_colors=4, remove_islands=False, fill_cavities=False, build_type="kit")
    conv = Converter(cfg)
    schem = conv.load(tmp_path / "k.litematic")
    conv.build_assets(schematic=schem)
    model, _ = conv.build_model(schem, 10.0)
    plan = conv.plan_colors(model)
    kit, plates = build_kit(model, plan, KitSettings(unit_mm=10.0, bed_mm=(200, 200, 200)))
    figs = [p for p in kit.pieces if p.axis == "g"]
    assert len(figs) == 1
    f = figs[0]
    assert f.units_y == 2 and f.units_xz == (1, 1) and f.has_socket and not f.has_stud
    assert f.mesh is not None and f.mesh.meshes
    lo, hi = f.mesh.bounds()
    assert hi[2] - lo[2] > 15.0                                     # taller than one unit: the whole creeper
    files = write_kit(kit, plates, tmp_path / "kit", "k")
    assert any("creeper_figure" in p.name for p in files)
    guide = (tmp_path / "kit" / "assembly_guide.html").read_text(encoding="utf-8")
    assert "figure" in guide
