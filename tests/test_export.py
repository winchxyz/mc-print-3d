import zipfile

import numpy as np

from mcprint.export import fits_bed, plan_tiles, read_3mf_summary, read_stl, write_3mf, write_obj, write_stl_set
from mcprint.voxel.mesher import MeshSink, mesh_chunk


def _two_material_meshset():
    g = np.zeros((1, 1, 2), dtype=np.uint16)
    g[0, 0, 0] = 1
    g[0, 0, 1] = 2
    padded = np.zeros((3, 3, 4), dtype=np.uint16)
    padded[1:-1, 1:-1, 1:-1] = g
    sink = MeshSink(1)
    mesh_chunk(padded, (0, 0, 0), sink)
    ms = sink.build({1: {"name": "Red PLA", "color": (255, 0, 0), "slot": 1}, 2: {"name": "Blue PLA", "color": (0, 0, 255), "slot": 2}})
    ms.meshes = [m.transformed(scale=2.5) for m in ms.meshes]   # 2.5 mm voxels
    return ms


def test_stl_roundtrip(tmp_path):
    ms = _two_material_meshset()
    files = write_stl_set(tmp_path / "model.stl", ms)
    assert len(files) == 2
    assert files[0].name.startswith("model_slot1_")
    back = read_stl(files[0])
    assert back.triangle_count == 12
    lo, hi = back.bounds()
    assert np.allclose(lo, [0, 0, 0]) and np.allclose(hi, [2.5, 2.5, 2.5])
    merged = write_stl_set(tmp_path / "merged.stl", ms, merged=True)
    assert read_stl(merged[0]).triangle_count == 24


def test_obj(tmp_path):
    ms = _two_material_meshset()
    files = write_obj(tmp_path / "model.obj", ms)
    txt = files[0].read_text()
    assert txt.count("usemtl") == 2 and txt.count("\nf ") == 24
    mtl = files[1].read_text()
    assert "newmtl Red_PLA" in mtl and "Kd 1.0000 0.0000 0.0000" in mtl


def test_3mf(tmp_path):
    ms = _two_material_meshset()
    p = write_3mf(tmp_path / "model.3mf", ms, title="test")
    s = read_3mf_summary(p)
    assert s["objects"] == 3           # 2 meshes + assembly
    assert s["triangles"] == 24
    assert s["materials"] == ["#FF0000FF", "#0000FFFF"]
    assert s["build_items"] == 1
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
    assert "[Content_Types].xml" in names and "_rels/.rels" in names
    pb = write_3mf(tmp_path / "bambu.3mf", ms, flavor="bambu")
    with zipfile.ZipFile(pb) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
        proj = z.read("Metadata/project_settings.config").decode()
    assert 'key="extruder" value="2"' in cfg
    assert "#0000FF" in proj


def test_tiles():
    tiles = plan_tiles((100, 20, 40), block_mm=5.0, bed_mm=(256, 256, 256), margin_mm=6)
    assert len(tiles) == 2
    assert tiles[0].x0 == 0 and tiles[-1].x1 == 100
    assert all(t.y0 == 0 and t.y1 == 20 for t in tiles)
    assert fits_bed((100, 100, 100), (256, 256, 256))
    assert not fits_bed((300, 100, 100), (256, 256, 256))
    tall = plan_tiles((10, 200, 10), block_mm=2.0, bed_mm=(200, 200, 250))
    assert len(tall) == 2 and tall[0].y1 == 100
