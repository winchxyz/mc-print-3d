"""Exact meshing of unions/differences of axis-aligned boxes (and voxel patterns) on a non-uniform grid.

All box edges (in millimetres, print axes X right / Y back / Z up) become grid planes; every cell
of the resulting non-uniform grid is either solid or empty, and the greedy voxel mesher turns the
cell grid into a watertight mesh whose vertices are mapped back to the exact coordinates.  This is
how kit pieces get millimetre-precise studs and sockets while still carrying voxel-textured bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .mesher import Mesh, MeshSet, MeshSink, mesh_chunk

Vec = tuple[float, float, float]


@dataclass
class Box:
    lo: Vec
    hi: Vec
    material: int = 1
    subtract: bool = False


@dataclass
class VoxelBody:
    """A voxel pattern placed in print space: cell (ix, iy, iz) covers [origin + i*pitch, ... + pitch)."""

    occupancy: np.ndarray            # bool [Z, Y, X] in print axes
    materials: Optional[np.ndarray]  # uint16 [Z, Y, X] material ids (0 = empty) or None -> body_material
    origin: Vec
    pitch: float
    body_material: int = 1


@dataclass
class BoxModel:
    boxes: list[Box] = field(default_factory=list)
    bodies: list[VoxelBody] = field(default_factory=list)

    def add(self, lo: Vec, hi: Vec, material: int = 1) -> None:
        self.boxes.append(Box(tuple(float(v) for v in lo), tuple(float(v) for v in hi), material, False))

    def cut(self, lo: Vec, hi: Vec) -> None:
        self.boxes.append(Box(tuple(float(v) for v in lo), tuple(float(v) for v in hi), 0, True))

    def add_body(self, body: VoxelBody) -> None:
        self.bodies.append(body)

    # ---------------------------------------------------------------------------------
    def _planes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        xs, ys, zs = [], [], []
        for b in self.boxes:
            xs += [b.lo[0], b.hi[0]]; ys += [b.lo[1], b.hi[1]]; zs += [b.lo[2], b.hi[2]]
        for body in self.bodies:
            nz, ny, nx = body.occupancy.shape
            ox, oy, oz = body.origin
            xs += (ox + np.arange(nx + 1) * body.pitch).tolist()
            ys += (oy + np.arange(ny + 1) * body.pitch).tolist()
            zs += (oz + np.arange(nz + 1) * body.pitch).tolist()
        def uniq(v):
            a = np.unique(np.round(np.asarray(v, dtype=np.float64), 6))
            return a
        return uniq(xs), uniq(ys), uniq(zs)

    def rasterize(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Returns (materials [Z, Y, X] uint16 cell grid, xs, ys, zs plane coordinates)."""
        xs, ys, zs = self._planes()
        if len(xs) < 2 or len(ys) < 2 or len(zs) < 2:
            return np.zeros((0, 0, 0), dtype=np.uint16), xs, ys, zs
        cx = (xs[:-1] + xs[1:]) / 2
        cy = (ys[:-1] + ys[1:]) / 2
        cz = (zs[:-1] + zs[1:]) / 2
        grid = np.zeros((len(cz), len(cy), len(cx)), dtype=np.uint16)
        CZ, CY, CX = np.meshgrid(cz, cy, cx, indexing="ij")
        for body in self.bodies:
            nz, ny, nx = body.occupancy.shape
            ox, oy, oz = body.origin
            ix = np.floor((CX - ox) / body.pitch).astype(np.int64)
            iy = np.floor((CY - oy) / body.pitch).astype(np.int64)
            iz = np.floor((CZ - oz) / body.pitch).astype(np.int64)
            inside = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny) & (iz >= 0) & (iz < nz)
            ixc, iyc, izc = np.clip(ix, 0, nx - 1), np.clip(iy, 0, ny - 1), np.clip(iz, 0, nz - 1)
            occ = inside & body.occupancy[izc, iyc, ixc]
            if body.materials is not None:
                mats = body.materials[izc, iyc, ixc]
                grid = np.where(occ, np.maximum(mats, 1).astype(np.uint16), grid)
            else:
                grid = np.where(occ, np.uint16(body.body_material), grid)
        for b in self.boxes:
            inside = ((CX > b.lo[0]) & (CX < b.hi[0]) & (CY > b.lo[1]) & (CY < b.hi[1]) & (CZ > b.lo[2]) & (CZ < b.hi[2]))
            if b.subtract:
                grid = np.where(inside, np.uint16(0), grid)
            else:
                grid = np.where(inside, np.uint16(b.material), grid)
        return grid, xs, ys, zs

    def mesh(self, material_info: Optional[dict] = None, single_material: Optional[int] = None) -> MeshSet:
        grid, xs, ys, zs = self.rasterize()
        if grid.size == 0 or not grid.any():
            return MeshSet()
        if single_material is not None:
            grid = np.where(grid != 0, np.uint16(single_material), np.uint16(0))
        # the voxel mesher works on [y_mc, z_mc, x_mc] with print Y = Lz - z_mc; feed
        # [Z_print, Y_print reversed, X_print] so its output lands in print axes directly.
        g = grid[:, ::-1, :]
        padded = np.zeros(tuple(d + 2 for d in g.shape), dtype=np.uint16)
        padded[1:-1, 1:-1, 1:-1] = g
        sink = MeshSink(g.shape[1])
        mesh_chunk(padded, (0, 0, 0), sink)
        ms = sink.build(material_info or {})
        for m in ms.meshes:
            v = m.vertices.astype(np.float64)
            v[:, 0] = np.interp(v[:, 0], np.arange(len(xs)), xs)
            v[:, 1] = np.interp(v[:, 1], np.arange(len(ys)), ys)
            v[:, 2] = np.interp(v[:, 2], np.arange(len(zs)), zs)
            m.vertices = v.astype(np.float32)
        return ms
