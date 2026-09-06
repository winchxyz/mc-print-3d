"""Split a model into tiles that fit a printer bed (block-aligned cuts)."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Tile:
    index: tuple[int, int, int]          # (ix, iy, iz) tile indices
    x0: int; x1: int                     # block ranges (X)
    y0: int; y1: int                     # block ranges (Y, Minecraft up)
    z0: int; z1: int                     # block ranges (Z)

    @property
    def label(self) -> str:
        return f"tile_x{self.index[0] + 1}_y{self.index[2] + 1}_z{self.index[1] + 1}"


def plan_tiles(size_blocks: tuple[int, int, int], block_mm: float, bed_mm: tuple[float, float, float],
               margin_mm: float = 5.0, split_z: bool = True) -> list[Tile]:
    """Return block-aligned tiles so every tile fits (bed_x - margin) x (bed_y - margin) x bed_z.

    ``size_blocks`` = (X, Y, Z) in Minecraft axes; print X = X, print Y = Z, print Z = Y.
    """
    X, Y, Z = size_blocks
    bx, by, bz = bed_mm
    per_x = max(1, int(math.floor((bx - margin_mm) / block_mm)))
    per_y = max(1, int(math.floor((by - margin_mm) / block_mm)))     # print Y <- Minecraft Z
    per_z = max(1, int(math.floor(bz / block_mm))) if split_z else max(Y, 1)   # print Z <- Minecraft Y
    nx = max(1, math.ceil(X / per_x))
    nz = max(1, math.ceil(Z / per_y))
    ny = max(1, math.ceil(Y / per_z))
    # distribute evenly so tiles are similar in size
    def ranges(total: int, n: int) -> list[tuple[int, int]]:
        out = []
        for i in range(n):
            a = round(i * total / n)
            b = round((i + 1) * total / n)
            if b > a:
                out.append((a, b))
        return out
    tiles = []
    for iy, (y0, y1) in enumerate(ranges(Y, ny)):
        for iz, (z0, z1) in enumerate(ranges(Z, nz)):
            for ix, (x0, x1) in enumerate(ranges(X, nx)):
                tiles.append(Tile((ix, iy, iz), x0, x1, y0, y1, z0, z1))
    return tiles


def fits_bed(size_mm: tuple[float, float, float], bed_mm: tuple[float, float, float], margin_mm: float = 0.0) -> bool:
    """size_mm and bed_mm in print axes (X, Y, Z)."""
    return (size_mm[0] <= bed_mm[0] - margin_mm and size_mm[1] <= bed_mm[1] - margin_mm and size_mm[2] <= bed_mm[2])
