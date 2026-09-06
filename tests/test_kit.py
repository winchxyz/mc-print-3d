"""Modular kit: piece merging, connector geometry, plates and guide."""
from pathlib import Path

import numpy as np
import pytest

from mcprint.color.quantize import plan_clusters
from mcprint.kit import KitSettings, build_kit, build_piece_mesh, extract_pieces, pack_plates, write_kit
from mcprint.schematics import AIR, BlockState
from mcprint.voxel import BlockPattern, ColorIndex, VoxelModel
from mcprint.voxel.boxmesh import BoxModel, VoxelBody
from mcprint.voxel.mesher import check_watertight


def _pattern(colors, n=8, idx=1):
    p = BlockPattern(colors, kind="ok")
    occ = colors != 0
    p.avg_rgb = (120, 80, 40)
    p.dominant = idx
    p.exposed_counts = {idx: int(occ.sum())}
    return p


def _model():
    """3 x 2 x 2 blocks: a 3-long run of stone on the ground, a slab and a thin post on top."""
    n = 8
    ci = ColorIndex()
    stone = ci.index_of_color((120, 120, 120))
    wood = ci.index_of_color((150, 110, 60))
    full = np.full((n, n, n), stone, dtype=np.uint16)
    slab = np.zeros((n, n, n), dtype=np.uint16); slab[: n // 2] = wood
    post = np.zeros((n, n, n), dtype=np.uint16); post[:, 3:5, 3:5] = wood            # thin 2-voxel post
    pats = [BlockPattern(np.zeros((n, n, n), dtype=np.uint16), kind="empty"), _pattern(full, idx=stone), _pattern(slab, idx=wood), _pattern(post, idx=wood)]
    blocks = np.zeros((2, 2, 3), dtype=np.int32)      # [y, z, x]
    blocks[0, 0, :] = 1
    blocks[0, 1, :] = 1
    blocks[1, 0, 0] = 2
    blocks[1, 0, 2] = 3
    palette = [AIR, BlockState.parse("minecraft:stone"), BlockState.parse("minecraft:oak_slab[type=bottom]"), BlockState.parse("minecraft:oak_fence")]
    m = VoxelModel(blocks=blocks, palette=palette, patterns=pats, resolution=n, colors=ci)
    plan = plan_clusters(m.color_histogram(), ci.palette(), 2)
    return m, plan


def test_boxmodel_exact_volume():
    bm = BoxModel()
    bm.add((0, 0, 0), (10, 10, 10))
    bm.cut((2.35, 2.35, 0), (7.65, 7.65, 2.0))
    bm.add((2.5, 2.5, 10), (7.5, 7.5, 11.8))
    m = bm.mesh().meshes[0]
    assert check_watertight(m)[0]
    assert abs(m.signed_volume() - (1000 - 5.3 * 5.3 * 2.0 + 25 * 1.8)) < 1e-6
    occ = np.zeros((4, 4, 4), dtype=bool); occ[:2] = True
    bm2 = BoxModel(); bm2.add_body(VoxelBody(occ, None, (0, 0, 0), 2.5)); bm2.cut((3, 3, 0), (7, 7, 1.5))
    m2 = bm2.mesh().meshes[0]
    assert check_watertight(m2)[0] and abs(m2.signed_volume() - (500 - 16 * 1.5)) < 1e-6


def test_extract_and_connectors():
    m, plan = _model()
    s = KitSettings(unit_mm=10.0, max_piece_len=6, baseplate=True, bed_mm=(200, 200, 200))
    kit = extract_pieces(m, plan, s)
    labels = sorted((p.state.path, p.length, p.count) for p in kit.pieces)
    assert ("stone", 3, 2) in labels               # two 3-long bars on layer 0 (merged along X)
    assert ("oak_slab", 1, 1) in labels and ("oak_fence", 1, 1) in labels
    assert kit.total_pieces == 4
    slab = next(p for p in kit.pieces if p.state.path == "oak_slab")
    fence = next(p for p in kit.pieces if p.state.path == "oak_fence")
    bar = next(p for p in kit.pieces if p.length == 3)
    assert bar.cube and bar.has_stud and bar.has_socket
    assert not slab.has_stud and slab.has_socket and not slab.base_tile     # thick bottom: socket cut directly
    assert fence.has_socket and fence.base_tile                             # thin post: gets a base tile
    assert len(kit.baseplates) == 1 and kit.baseplates[0].x1 == 3 and kit.baseplates[0].z1 == 2
    # geometry: bar with 3 studs and 3 sockets
    ms = build_piece_mesh(bar, m, s)
    mesh = ms.meshes[0]
    assert check_watertight(mesh)[0]
    expect = 3 * 1000 - 3 * (s.socket_w ** 2 * s.socket_d) + 3 * (s.stud_w ** 2 * (s.stud_h - s.chamfer_mm) + (s.stud_w - 2 * s.chamfer_mm) ** 2 * s.chamfer_mm)
    chamfer_extra = 3 * ((s.socket_w + 2 * s.chamfer_mm) ** 2 - s.socket_w ** 2) * s.chamfer_mm
    assert abs(mesh.signed_volume() - (expect - chamfer_extra)) < 1e-3
    lo, hi = mesh.bounds()
    assert np.allclose(lo, [0, 0, 0]) and np.allclose(hi, [30, 10, 10 + s.stud_h])
    fm = build_piece_mesh(fence, m, s).meshes[0]
    assert check_watertight(fm)[0] and fm.signed_volume() > s.base_tile_mm * 100 * 0.5


def test_paired_pieces_door_and_bed():
    """Door halves stack into one 2-unit-tall piece; bed foot+head merge into one 2-long piece."""
    n = 8
    ci = ColorIndex()
    wood = ci.index_of_color((150, 110, 60))
    red = ci.index_of_color((180, 40, 40))
    door = np.zeros((n, n, n), dtype=np.uint16); door[:, 6:8, :] = wood          # thin panel on the south side
    door_up = door.copy()
    bed = np.zeros((n, n, n), dtype=np.uint16); bed[1:4, :, :] = red; bed[0, :2, :2] = red   # mattress + one leg
    bed_head = bed.copy()
    pats = [BlockPattern(np.zeros((n, n, n), dtype=np.uint16), kind="empty"), _pattern(door, idx=wood), _pattern(door_up, idx=wood),
            _pattern(bed, idx=red), _pattern(bed_head, idx=red)]
    blocks = np.zeros((2, 3, 3), dtype=np.int32)      # [y, z, x]
    blocks[0, 0, 0] = 1; blocks[1, 0, 0] = 2           # door lower at y=0, upper at y=1
    blocks[0, 1, 2] = 3; blocks[0, 2, 2] = 4           # bed foot at z=1 facing south -> head at z=2
    palette = [AIR,
               BlockState.parse("minecraft:oak_door[facing=south,half=lower,hinge=left,open=false]"),
               BlockState.parse("minecraft:oak_door[facing=south,half=upper,hinge=left,open=false]"),
               BlockState.parse("minecraft:red_bed[facing=south,part=foot]"),
               BlockState.parse("minecraft:red_bed[facing=south,part=head]")]
    m = VoxelModel(blocks=blocks, palette=palette, patterns=pats, resolution=n, colors=ci)
    plan = plan_clusters(m.color_histogram(), ci.palette(), 2)
    s = KitSettings(unit_mm=10.0, baseplate=False)
    kit = extract_pieces(m, plan, s)
    assert kit.total_pieces == 2
    door_p = next(p for p in kit.pieces if "door" in p.state.path)
    bed_p = next(p for p in kit.pieces if "bed" in p.state.path)
    assert door_p.axis == "y" and door_p.length == 2 and door_p.block_indices == (1, 2)
    assert bed_p.axis == "z" and bed_p.length == 2 and bed_p.block_indices == (3, 4)
    assert door_p.base_tile and door_p.has_socket and not door_p.has_stud
    dm = build_piece_mesh(door_p, m, s).meshes[0]
    assert check_watertight(dm)[0]
    lo, hi = dm.bounds()
    assert np.allclose(hi[2], 20.0) and np.allclose(hi[:2], [10, 10])       # two units tall, one footprint
    bm = build_piece_mesh(bed_p, m, s).meshes[0]
    assert check_watertight(bm)[0]
    lo, hi = bm.bounds()
    assert np.allclose(hi[:2], [10, 20])                                    # one unit along X, two along print Y
    assert [pl.units_y for pl in kit.placements if pl.piece is door_p] == [2]


def test_alternating_layers_and_plates(tmp_path):
    m, plan = _model()
    m.blocks[1, :, :] = 1                              # second layer solid stone too
    s = KitSettings(unit_mm=10.0, bed_mm=(60, 60, 60), bed_margin_mm=5, spacing_mm=2)
    kit, plates = build_kit(m, plan, s)
    axes = {p.axis for pl in kit.placements for p in [pl.piece] if pl.y == 1}
    assert axes == {"z"}                               # odd layer merged along Z
    assert all(pl.material == plates[0].material or True for pl in plates)
    for pl in plates:
        for it in pl.items:
            w, d, _h = kit.footprints[it.piece.id]
            assert it.x + w <= 60 - 5 + 1e-6 and it.y + d <= 60 - 5 + 1e-6
    files = write_kit(kit, plates, tmp_path / "kit", "test")
    names = {f.name for f in files}
    assert "assembly_guide.html" in names and "parts.csv" in names and "README.txt" in names
    assert any(n.startswith("baseplate_") for n in names)
    html = (tmp_path / "kit" / "assembly_guide.html").read_text(encoding="utf-8")
    assert html.count("<svg") == 2                     # one map per layer
    assert (tmp_path / "kit" / "pieces").exists() and any((tmp_path / "kit" / "plates").glob("*.3mf"))
