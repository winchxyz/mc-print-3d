import numpy as np

from mcprint.voxel.mesher import MeshSink, _merge_runs, check_watertight, mesh_chunk


def _mesh_from_grid(grid):
    """grid: [Y, Z, X] materials (unpadded)."""
    Y, Z, X = grid.shape
    padded = np.zeros((Y + 2, Z + 2, X + 2), dtype=np.uint16)
    padded[1:-1, 1:-1, 1:-1] = grid
    sink = MeshSink(Z)
    mesh_chunk(padded, (0, 0, 0), sink)
    return sink.build()


def test_merge_runs_rectangles():
    s = np.array([[1, 1, 0, 2],
                  [1, 1, 0, 2],
                  [0, 0, 0, 2]])
    q = _merge_runs(s)
    rows = {tuple(r) for r in q.tolist()}
    assert (0, 2, 0, 2, 1) in rows      # v0,v1,u0,u1,mat
    assert (0, 3, 3, 4, 2) in rows
    assert len(rows) == 2


def test_single_cube():
    g = np.zeros((1, 1, 1), dtype=np.uint16)
    g[0, 0, 0] = 5
    ms = _mesh_from_grid(g)
    assert len(ms.meshes) == 1
    m = ms.meshes[0]
    assert m.material == 5
    assert m.triangle_count == 12
    assert len(m.vertices) == 8
    ok, bad = check_watertight(m)
    assert ok, bad
    assert abs(m.signed_volume() - 1.0) < 1e-9   # outward normals -> positive volume


def test_greedy_merging_and_orientation():
    g = np.zeros((2, 3, 4), dtype=np.uint16)
    g[:] = 1
    ms = _mesh_from_grid(g)
    m = ms.meshes[0]
    assert m.triangle_count == 12          # one box regardless of voxel count
    assert abs(m.signed_volume() - 24.0) < 1e-9
    assert check_watertight(m)[0]
    lo, hi = m.bounds()
    # print space: X = x (4), Y = Lz - z (3), Z = y (2)
    assert np.allclose(lo, [0, 0, 0]) and np.allclose(hi, [4, 3, 2])


def test_two_materials_closed_separately():
    g = np.zeros((1, 1, 2), dtype=np.uint16)
    g[0, 0, 0] = 1
    g[0, 0, 1] = 2
    ms = _mesh_from_grid(g)
    assert len(ms.meshes) == 2
    for m in ms.meshes:
        assert m.triangle_count == 12
        assert check_watertight(m)[0]
        assert abs(m.signed_volume() - 1.0) < 1e-9


def test_hollow_shape_is_watertight():
    g = np.ones((5, 5, 5), dtype=np.uint16)
    g[1:-1, 1:-1, 1:-1] = 0   # hollow cube -> inner and outer shells
    ms = _mesh_from_grid(g)
    m = ms.meshes[0]
    assert check_watertight(m)[0]
    assert abs(m.signed_volume() - (125 - 27)) < 1e-9


def test_random_watertight():
    rng = np.random.default_rng(3)
    g = (rng.random((6, 7, 8)) < 0.5).astype(np.uint16) * rng.integers(1, 4, size=(6, 7, 8)).astype(np.uint16)
    ms = _mesh_from_grid(g)
    total = 0.0
    for m in ms.meshes:
        assert check_watertight(m)[0]
        total += m.signed_volume()
    assert abs(total - float((g != 0).sum())) < 1e-6
