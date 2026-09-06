"""Greedy surface meshing of material grids into per-material closed triangle meshes.

Each material's mesh is watertight on its own (faces are emitted wherever a voxel of material m
touches anything that is not m), which is what multi-material slicers expect for separate parts.

Grid axis order is ``[y, z, x]`` (Minecraft: y up, z south, x east).  Output coordinates are in
sub-voxel units in *print space*: X = x, Y = (Lz - z), Z = y (right-handed, Z up).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Mesh:
    material: int
    vertices: np.ndarray            # (V, 3) float32
    triangles: np.ndarray           # (T, 3) int32, CCW seen from outside
    name: str = ""
    color: tuple[int, int, int] = (200, 200, 200)

    @property
    def triangle_count(self) -> int:
        return int(len(self.triangles))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if len(self.vertices) == 0:
            return np.zeros(3), np.zeros(3)
        return self.vertices.min(0), self.vertices.max(0)

    def transformed(self, scale: float = 1.0, offset=(0.0, 0.0, 0.0)) -> "Mesh":
        v = self.vertices.astype(np.float64) * scale + np.asarray(offset, dtype=np.float64)
        return Mesh(self.material, v.astype(np.float32), self.triangles, self.name, self.color)

    def signed_volume(self) -> float:
        v = self.vertices.astype(np.float64)
        t = self.triangles
        a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


@dataclass
class MeshSet:
    meshes: list[Mesh] = field(default_factory=list)
    unit: float = 1.0                       # mm per coordinate unit after transform
    materials: dict[int, dict] = field(default_factory=dict)   # material id -> {'name','color'}

    @property
    def triangle_count(self) -> int:
        return sum(m.triangle_count for m in self.meshes)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        mins = [m.vertices.min(0) for m in self.meshes if len(m.vertices)]
        maxs = [m.vertices.max(0) for m in self.meshes if len(m.vertices)]
        if not mins:
            return np.zeros(3), np.zeros(3)
        return np.min(mins, axis=0), np.max(maxs, axis=0)

    def merged(self) -> Mesh:
        if not self.meshes:
            return Mesh(0, np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32))
        vs, ts, off = [], [], 0
        for m in self.meshes:
            vs.append(m.vertices)
            ts.append(m.triangles + off)
            off += len(m.vertices)
        return Mesh(0, np.concatenate(vs), np.concatenate(ts), name="merged")


# --------------------------------------------------------------------------------------
# Greedy quad extraction (vectorised two-pass run merging)
# --------------------------------------------------------------------------------------
def _merge_runs(slice_: np.ndarray) -> np.ndarray:
    """slice_: (V, U) ints, 0 = no face.  Returns (Q, 5) int64 rows: v0, v1, u0, u1, mat."""
    V, U = slice_.shape
    if V == 0 or U == 0 or not slice_.any():
        return np.zeros((0, 5), dtype=np.int64)
    s = slice_.astype(np.int64)
    left = np.zeros_like(s)
    left[:, 1:] = s[:, :-1]
    right = np.zeros_like(s)
    right[:, :-1] = s[:, 1:]
    start = (s != 0) & (s != left)
    end = (s != 0) & (s != right)
    sv, su = np.nonzero(start)
    ev, eu = np.nonzero(end)
    # starts and ends are matched in raster order per row
    runs = np.stack([sv, su, eu + 1, s[sv, su]], axis=1)  # v, u0, u1, mat
    if len(runs) == 0:
        return np.zeros((0, 5), dtype=np.int64)
    order = np.lexsort((runs[:, 0], runs[:, 3], runs[:, 2], runs[:, 1]))  # by u0, u1, mat, v
    r = runs[order]
    same = np.zeros(len(r), dtype=bool)
    same[1:] = (r[1:, 1] == r[:-1, 1]) & (r[1:, 2] == r[:-1, 2]) & (r[1:, 3] == r[:-1, 3]) & (r[1:, 0] == r[:-1, 0] + 1)
    group = np.cumsum(~same) - 1
    ngroups = group[-1] + 1
    v0 = np.full(ngroups, np.iinfo(np.int64).max, dtype=np.int64)
    v1 = np.full(ngroups, -1, dtype=np.int64)
    np.minimum.at(v0, group, r[:, 0])
    np.maximum.at(v1, group, r[:, 0])
    first = np.zeros(ngroups, dtype=np.int64)
    first[group[::-1]] = np.arange(len(r))[::-1]  # first index of each group
    u0 = r[first, 1]
    u1 = r[first, 2]
    mat = r[first, 3]
    return np.stack([v0, v1 + 1, u0, u1, mat], axis=1)


def mesh_chunk(grid: np.ndarray, origin: tuple[int, int, int], sink: "MeshSink") -> None:
    """Emit quads for a padded material grid (1 voxel of context on every side).

    grid: uint16/int [Y+2, Z+2, X+2]; origin = (x0, y0, z0) of the unpadded chunk in global sub-voxel coords.
    """
    g = grid
    core = g[1:-1, 1:-1, 1:-1]
    x0, y0, z0 = origin
    # ---- faces normal to +/-X (slices at constant x) ---------------------------------
    # +X face of voxel x exists where core != neighbour at x+1
    for axis, plus in ((2, True), (2, False), (1, True), (1, False), (0, True), (0, False)):
        if axis == 2:      # x
            nb = g[1:-1, 1:-1, 2:] if plus else g[1:-1, 1:-1, :-2]
        elif axis == 1:    # z
            nb = g[1:-1, 2:, 1:-1] if plus else g[1:-1, :-2, 1:-1]
        else:              # y
            nb = g[2:, 1:-1, 1:-1] if plus else g[:-2, 1:-1, 1:-1]
        mask = (core != 0) & (core != nb)
        if not mask.any():
            continue
        faces = np.where(mask, core, 0)
        if axis == 2:
            # iterate over x slices: slice_[y, z]
            for xi in range(faces.shape[2]):
                sl = faces[:, :, xi]
                if not sl.any():
                    continue
                q = _merge_runs(sl)      # v = y, u = z
                if len(q):
                    sink.add_x(q, x0 + xi + (1 if plus else 0), y0, z0, plus)
        elif axis == 1:
            for zi in range(faces.shape[1]):
                sl = faces[:, zi, :]     # v = y, u = x
                if not sl.any():
                    continue
                q = _merge_runs(sl)
                if len(q):
                    sink.add_z(q, z0 + zi + (1 if plus else 0), y0, x0, plus)
        else:
            for yi in range(faces.shape[0]):
                sl = faces[yi, :, :]     # v = z, u = x
                if not sl.any():
                    continue
                q = _merge_runs(sl)
                if len(q):
                    sink.add_y(q, y0 + yi + (1 if plus else 0), z0, x0, plus)


class MeshSink:
    """Collects quads per material and converts them to triangle meshes in print space."""

    def __init__(self, length_z: int):
        self.Lz = int(length_z)
        self._quads: dict[int, list[np.ndarray]] = {}   # mat -> list of (Q, 4, 3) corner arrays (minecraft coords)

    def _push(self, mats: np.ndarray, corners: np.ndarray) -> None:
        for m in np.unique(mats):
            sel = mats == m
            self._quads.setdefault(int(m), []).append(corners[sel])

    def add_x(self, q: np.ndarray, x: int, y0: int, z0: int, plus: bool) -> None:
        # q rows: v0,v1 (y), u0,u1 (z), mat
        y_lo, y_hi = q[:, 0] + y0, q[:, 1] + y0
        z_lo, z_hi = q[:, 2] + z0, q[:, 3] + z0
        X = np.full(len(q), x)
        # corners in minecraft coords (x, y, z); CCW when viewed from +x for plus faces
        c = np.stack([
            np.stack([X, y_lo, z_lo], 1), np.stack([X, y_lo, z_hi], 1),
            np.stack([X, y_hi, z_hi], 1), np.stack([X, y_hi, z_lo], 1)], axis=1).astype(np.float64)
        # normal of (c1-c0)x(c2-c0): (0,0,dz)x(0,dy,dz) = (-dy*dz, 0, 0) -> points -x; flip for plus
        if plus:
            c = c[:, ::-1]
        self._push(q[:, 4], c)

    def add_z(self, q: np.ndarray, z: int, y0: int, x0: int, plus: bool) -> None:
        y_lo, y_hi = q[:, 0] + y0, q[:, 1] + y0
        x_lo, x_hi = q[:, 2] + x0, q[:, 3] + x0
        Z = np.full(len(q), z)
        c = np.stack([
            np.stack([x_lo, y_lo, Z], 1), np.stack([x_hi, y_lo, Z], 1),
            np.stack([x_hi, y_hi, Z], 1), np.stack([x_lo, y_hi, Z], 1)], axis=1).astype(np.float64)
        # (dx,0,0)x(dx,dy,0) = (0,0,dx*dy) -> +z; flip for minus
        if not plus:
            c = c[:, ::-1]
        self._push(q[:, 4], c)

    def add_y(self, q: np.ndarray, y: int, z0: int, x0: int, plus: bool) -> None:
        z_lo, z_hi = q[:, 0] + z0, q[:, 1] + z0
        x_lo, x_hi = q[:, 2] + x0, q[:, 3] + x0
        Y = np.full(len(q), y)
        c = np.stack([
            np.stack([x_lo, Y, z_lo], 1), np.stack([x_lo, Y, z_hi], 1),
            np.stack([x_hi, Y, z_hi], 1), np.stack([x_hi, Y, z_lo], 1)], axis=1).astype(np.float64)
        # (0,0,dz)x(dx,0,dz) = (0*dz-dz*0, dz*dx-0, 0-0) = (0, dx*dz, 0) -> +y ; flip for minus
        if not plus:
            c = c[:, ::-1]
        self._push(q[:, 4], c)

    def build(self, material_info: Optional[dict] = None, weld: bool = True, fix_tjunctions: bool = True) -> MeshSet:
        ms = MeshSet()
        for m in sorted(self._quads):
            quads = np.concatenate(self._quads[m], axis=0)  # (Q, 4, 3) minecraft coords
            # to print space: X = x, Y = Lz - z, Z = y
            P = np.empty_like(quads)
            P[..., 0] = quads[..., 0]
            P[..., 1] = self.Lz - quads[..., 2]
            P[..., 2] = quads[..., 1]
            if fix_tjunctions:
                verts, tris = _triangulate_manifold(P.astype(np.int64))
            else:
                verts = P.reshape(-1, 3)
                n = len(quads)
                base = np.arange(n) * 4
                tris = np.stack([
                    np.stack([base, base + 1, base + 2], 1),
                    np.stack([base, base + 2, base + 3], 1)], axis=1).reshape(-1, 3)
                if weld:
                    verts, inv = np.unique(verts, axis=0, return_inverse=True)
                    tris = inv.reshape(-1)[tris]
            info = (material_info or {}).get(m, {})
            ms.meshes.append(Mesh(m, verts.astype(np.float32), tris.astype(np.int32),
                                  name=info.get("name", f"material_{m}"), color=tuple(info.get("color", (200, 200, 200)))))
        ms.materials = dict(material_info or {})
        return ms


def _triangulate_manifold(quads: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Triangulate integer-coordinate quads so that shared edges match exactly (no T-junctions).

    Any mesh vertex lying strictly inside a quad edge is inserted into that edge; quads that
    gained vertices are fan-triangulated around their centre.  Returns (vertices float64, tris int64).
    """
    Q = len(quads)
    corners = quads.reshape(-1, 3)
    verts, inv = np.unique(corners, axis=0, return_inverse=True)
    inv = inv.reshape(Q, 4)
    # --- find vertices lying on quad edges -----------------------------------------------
    # edges: (Q, 4) from corner i to corner (i+1)%4
    e0 = quads                                  # (Q,4,3)
    e1 = np.roll(quads, -1, axis=1)
    diff = e1 - e0
    axis = np.argmax(np.abs(diff), axis=2)      # (Q,4) axis along which the edge runs
    lo = np.minimum(e0, e1)
    hi = np.maximum(e0, e1)
    extra: dict[tuple[int, int], np.ndarray] = {}
    for a in range(3):
        b, c = [i for i in range(3) if i != a]
        # vertices grouped by their (b,c) line, sorted by a
        key_v = verts[:, b] * (verts[:, c].max() + 2) + verts[:, c]
        order = np.lexsort((verts[:, a], key_v))
        kv_sorted = key_v[order]
        av_sorted = verts[order, a]
        sel = np.nonzero(axis == a)
        if len(sel[0]) == 0:
            continue
        qi, ei = sel
        kb = lo[qi, ei, b] * (verts[:, c].max() + 2) + lo[qi, ei, c]
        a0 = lo[qi, ei, a]
        a1 = hi[qi, ei, a]
        BIG = np.int64(av_sorted.max() + 2)
        comb = kv_sorted.astype(np.int64) * BIG + av_sorted.astype(np.int64)
        L = np.searchsorted(comb, kb.astype(np.int64) * BIG + a0, side="right")
        R = np.searchsorted(comb, kb.astype(np.int64) * BIG + a1, side="left")
        has = R > L
        for q, e, l, r in zip(qi[has].tolist(), ei[has].tolist(), L[has].tolist(), R[has].tolist()):
            ids = order[l:r]
            # order along the edge direction
            if diff[q, e, a] < 0:
                ids = ids[::-1]
            extra[(q, e)] = ids
    # --- triangulate ------------------------------------------------------------------------
    plain = np.ones(Q, dtype=bool)
    for (q, _e) in extra:
        plain[q] = False
    tris_list = []
    pq = np.nonzero(plain)[0]
    if len(pq):
        c = inv[pq]
        tris_list.append(np.stack([c[:, 0], c[:, 1], c[:, 2]], 1))
        tris_list.append(np.stack([c[:, 0], c[:, 2], c[:, 3]], 1))
    new_verts = [verts.astype(np.float64)]
    next_id = len(verts)
    fan = []
    for q in np.nonzero(~plain)[0].tolist():
        ring = []
        for e in range(4):
            ring.append(int(inv[q, e]))
            ex = extra.get((q, e))
            if ex is not None:
                ring.extend(int(x) for x in ex)
        centre = quads[q].mean(axis=0)
        cid = next_id
        next_id += 1
        new_verts.append(centre[None, :].astype(np.float64))
        n = len(ring)
        for i in range(n):
            fan.append((cid, ring[i], ring[(i + 1) % n]))
    if fan:
        tris_list.append(np.asarray(fan, dtype=np.int64))
    tris = np.concatenate(tris_list) if tris_list else np.zeros((0, 3), dtype=np.int64)
    return np.concatenate(new_verts), tris


def check_watertight(mesh: Mesh) -> tuple[bool, int]:
    """Closed-surface check: every directed edge must be balanced by the same number of reversed edges.

    (Voxels of one material that touch only along an edge legitimately use that edge twice in
    each direction; slicers handle that.)  Returns (ok, unbalanced_edge_count).
    """
    t = mesh.triangles.astype(np.int64)
    if len(t) == 0:
        return True, 0
    n = len(mesh.vertices) + 1
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    key = e[:, 0] * n + e[:, 1]
    rkey = e[:, 1] * n + e[:, 0]
    k, kc = np.unique(key, return_counts=True)
    rk, rkc = np.unique(rkey, return_counts=True)
    if len(k) != len(rk) or not np.array_equal(k, rk):
        return False, int(len(np.setxor1d(k, rk)))
    bad = int((kc != rkc).sum())
    return bad == 0, bad
