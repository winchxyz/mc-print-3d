"""The voxel model: schematic block grid + per-block-state patterns, materialised chunk by chunk.

Memory stays bounded: the dense sub-voxel grid is never built in full.  Block-level operations
(island removal, cavity filling, base plate, cropping) work on the small block grid.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

import numpy as np

from ..schematics.base import AIR, BlockState, Schematic
from .colorindex import ColorIndex
from .mesher import MeshSet, MeshSink, mesh_chunk
from .voxelizer import BlockPattern

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]


class Cancelled(Exception):
    pass


@dataclass
class VoxelModel:
    blocks: np.ndarray                    # int32 [y, z, x] palette indices (0 = air)
    palette: list[BlockState]
    patterns: list[BlockPattern]          # aligned with palette
    resolution: int
    colors: ColorIndex
    flat_patterns: Optional[list[BlockPattern]] = None   # per-block single-color variants (built on demand)
    warnings: list[str] = field(default_factory=list)
    relief_steps: int = 0                 # voxels of texture relief to carve below exposed faces (0 = off)

    # ---- sizes -----------------------------------------------------------------------
    @property
    def block_size(self) -> tuple[int, int, int]:
        """(X, Y, Z) in blocks."""
        y, z, x = self.blocks.shape
        return int(x), int(y), int(z)

    @property
    def voxel_size(self) -> tuple[int, int, int]:
        x, y, z = self.block_size
        n = self.resolution
        return x * n, y * n, z * n

    def occupancy(self) -> np.ndarray:
        """Boolean block grid: block has any solid sub-voxel."""
        nonempty = np.array([not p.is_empty for p in self.patterns], dtype=bool)
        return nonempty[self.blocks]

    def solid_blocks(self) -> np.ndarray:
        """Boolean block grid: block is a completely full cube."""
        full = np.array([p.is_full for p in self.patterns], dtype=bool)
        return full[self.blocks]

    # ---- pattern arrays --------------------------------------------------------------
    def _pattern_array(self, flat: bool) -> np.ndarray:
        pats = self.flat_patterns if (flat and self.flat_patterns) else self.patterns
        n = self.resolution
        arr = np.zeros((len(pats), n, n, n), dtype=np.uint16)
        for i, p in enumerate(pats):
            arr[i] = p.colors
        return arr

    def _relief_array(self, flat: bool) -> np.ndarray:
        pats = self.flat_patterns if (flat and self.flat_patterns) else self.patterns
        n = self.resolution
        arr = np.zeros((len(pats), n, n, n), dtype=np.uint8)
        for i, p in enumerate(pats):
            if p.relief is not None:
                arr[i] = p.relief
        return arr

    def build_flat_patterns(self) -> None:
        """Flat-color version of every pattern for 'per block' color mode.

        Each voxel takes the average color of the block face it is exposed on (top voxels of a grass
        block become green, its sides dirt-brown); interior voxels take the overall average.
        """
        pal = self.colors.palette().astype(np.float64)
        trans_flags = self.colors.is_translucent()
        out = []
        for p in self.patterns:
            if p.is_empty or p.avg_rgb is None or p.kind == "mob":
                # mobs are figures: their look *is* the texture, keep texel colors in every color mode
                out.append(p)
                continue
            col = p.colors
            occ = col != 0
            n = col.shape[0]
            # exposure masks per direction (within the pattern; block boundary counts as exposed)
            masks = {}
            up = np.zeros_like(occ); up[-1] = occ[-1]; up[:-1] |= occ[:-1] & ~occ[1:]
            down = np.zeros_like(occ); down[0] = occ[0]; down[1:] |= occ[1:] & ~occ[:-1]
            south = np.zeros_like(occ); south[:, -1] = occ[:, -1]; south[:, :-1] |= occ[:, :-1] & ~occ[:, 1:]
            north = np.zeros_like(occ); north[:, 0] = occ[:, 0]; north[:, 1:] |= occ[:, 1:] & ~occ[:, :-1]
            east = np.zeros_like(occ); east[:, :, -1] = occ[:, :, -1]; east[:, :, :-1] |= occ[:, :, :-1] & ~occ[:, :, 1:]
            west = np.zeros_like(occ); west[:, :, 0] = occ[:, :, 0]; west[:, :, 1:] |= occ[:, :, 1:] & ~occ[:, :, :-1]
            masks = [up, north, south, east, west, down]
            flat = np.zeros_like(col)
            assigned = np.zeros_like(occ)
            counts: dict[int, int] = {}
            for m in masks:
                sel = m & ~assigned
                if not sel.any():
                    continue
                vals = col[m]
                avg = pal[vals].mean(axis=0)
                translucent = bool(trans_flags[vals].mean() > 0.5) if len(vals) else False
                idx = self.colors.index_of_color(tuple(int(round(v)) for v in avg), translucent=translucent)
                flat[sel] = idx
                assigned |= sel
                counts[idx] = counts.get(idx, 0) + int(sel.sum())
            rest = occ & ~assigned
            if rest.any():
                all_t = bool(trans_flags[col[occ]].mean() > 0.5)
                idx = self.colors.index_of_color(p.avg_rgb, translucent=all_t)
                flat[rest] = idx
            q = BlockPattern(flat, kind=p.kind, note=p.note, avg_rgb=p.avg_rgb,
                             dominant=max(counts, key=counts.get) if counts else 0, exposed_counts=counts, relief=p.relief)
            out.append(q)
        self.flat_patterns = out

    # ---- chunked materialisation -------------------------------------------------------
    def iter_chunks(self, chunk_blocks: int = 16, flat: bool = False, mat_lut: Optional[np.ndarray] = None
                    ) -> Iterator[tuple[tuple[int, int, int], np.ndarray]]:
        """Yield ((x0, y0, z0) in sub-voxels, material grid padded by exactly one voxel [Y+2, Z+2, X+2]) per chunk.

        Texture relief (``relief_steps`` > 0) is carved here, with enough extra context around the
        chunk that carving near chunk borders is identical to carving the whole model at once.
        """
        pats = self._pattern_array(flat)
        steps = int(self.relief_steps or 0)
        rel_pats = self._relief_array(flat) if steps else None
        if mat_lut is not None:
            pats = mat_lut[pats].astype(np.uint16 if mat_lut.max() < 65535 else np.uint32)
        n = self.resolution
        Y, Z, X = self.blocks.shape
        c = chunk_blocks
        pad = steps + 1 if steps else 1
        pad_blocks = (pad + n - 1) // n
        for by in range(0, Y, c):
            for bz in range(0, Z, c):
                for bx in range(0, X, c):
                    y1, z1, x1 = min(by + c, Y), min(bz + c, Z), min(bx + c, X)
                    wy0, wz0, wx0 = max(by - pad_blocks, 0), max(bz - pad_blocks, 0), max(bx - pad_blocks, 0)
                    wy1, wz1, wx1 = min(y1 + pad_blocks, Y), min(z1 + pad_blocks, Z), min(x1 + pad_blocks, X)
                    win = self.blocks[wy0:wy1, wz0:wz1, wx0:wx1]
                    dense = _materialize(pats, win, n)
                    py0, pz0, px0 = (by - wy0) * n - pad, (bz - wz0) * n - pad, (bx - wx0) * n - pad
                    H, D, W = (y1 - by) * n + 2 * pad, (z1 - bz) * n + 2 * pad, (x1 - bx) * n + 2 * pad
                    out = _window(dense, py0, pz0, px0, H, D, W)
                    if steps:
                        rel = _window(_materialize(rel_pats, win, n), py0, pz0, px0, H, D, W)
                        carve_relief(out, rel, steps)
                        out = out[pad - 1:H - pad + 1, pad - 1:D - pad + 1, pad - 1:W - pad + 1]
                    yield (bx * n, by * n, bz * n), out

    def dense(self, flat: bool = False, mat_lut: Optional[np.ndarray] = None) -> np.ndarray:
        """Full dense sub-voxel grid [Y, Z, X] – only for small models / tests."""
        pats = self._pattern_array(flat)
        if mat_lut is not None:
            pats = mat_lut[pats]
        n = self.resolution
        grid = _materialize(pats, self.blocks, n)
        if self.relief_steps:
            steps = int(self.relief_steps)
            pad = steps + 1
            padded = np.pad(grid, pad)
            rel = np.pad(_materialize(self._relief_array(flat), self.blocks, n), pad)
            carve_relief(padded, rel, steps)
            grid = padded[pad:-pad, pad:-pad, pad:-pad]
        return grid

    # ---- meshing ---------------------------------------------------------------------
    def mesh(self, mat_lut: Optional[np.ndarray] = None, material_info: Optional[dict] = None, flat: bool = False,
             progress: Optional[ProgressFn] = None, cancel: Optional[Callable[[], bool]] = None,
             chunk_blocks: int = 16) -> MeshSet:
        """Greedy-mesh the whole model.  ``mat_lut`` maps color index -> material id (default identity)."""
        _, _, Lz = self.voxel_size
        sink = MeshSink(Lz)
        Y, Z, X = self.blocks.shape
        c = chunk_blocks
        total = ((Y + c - 1) // c) * ((Z + c - 1) // c) * ((X + c - 1) // c)
        for i, (origin, grid) in enumerate(self.iter_chunks(chunk_blocks=c, flat=flat, mat_lut=mat_lut)):
            if cancel and cancel():
                raise Cancelled()
            if grid.any():
                mesh_chunk(grid, origin, sink)
            if progress and (i % 4 == 0 or i == total - 1):
                progress((i + 1) / max(total, 1), f"Meshing chunk {i + 1}/{total}")
        if material_info is None:
            pal = self.colors.palette()
            material_info = {int(m): {"name": f"color_{m}", "color": tuple(int(v) for v in pal[m])} for m in range(len(pal))} if mat_lut is None else {}
        return sink.build(material_info)

    # ---- color statistics ------------------------------------------------------------
    def color_histogram(self, flat: bool = False) -> dict[int, int]:
        """color index -> exposed voxel count over the whole model (weighted by block usage)."""
        idx, counts = np.unique(self.blocks, return_counts=True)
        pats = self.flat_patterns if (flat and self.flat_patterns) else self.patterns
        hist: dict[int, int] = {}
        for i, c in zip(idx.tolist(), counts.tolist()):
            if i == 0:
                continue
            for ci, n in pats[i].exposed_counts.items():
                hist[ci] = hist.get(ci, 0) + n * c
        return hist

    def block_color_table(self, flat: bool = False) -> list[tuple[BlockState, int, Optional[tuple[int, int, int]]]]:
        """(state, block count, average color) per used palette entry."""
        idx, counts = np.unique(self.blocks, return_counts=True)
        out = []
        for i, c in zip(idx.tolist(), counts.tolist()):
            if i == 0 or self.patterns[i].is_empty:
                continue
            out.append((self.palette[i], c, self.patterns[i].avg_rgb))
        return out


def _materialize(pats: np.ndarray, win: np.ndarray, n: int) -> np.ndarray:
    """Stamp per-block patterns (P, n, n, n) over a block index window [Y, Z, X] -> [Y*n, Z*n, X*n]."""
    d = pats[win]
    return d.transpose(0, 3, 1, 4, 2, 5).reshape(win.shape[0] * n, win.shape[1] * n, win.shape[2] * n)


def _window(dense: np.ndarray, y0: int, z0: int, x0: int, H: int, D: int, W: int) -> np.ndarray:
    """Copy a window (may extend past the array; outside = 0) of the given size."""
    out = np.zeros((H, D, W), dtype=dense.dtype)
    sy0, sz0, sx0 = max(y0, 0), max(z0, 0), max(x0, 0)
    sy1, sz1, sx1 = min(y0 + H, dense.shape[0]), min(z0 + D, dense.shape[1]), min(x0 + W, dense.shape[2])
    if sy1 > sy0 and sz1 > sz0 and sx1 > sx0:
        out[sy0 - y0:sy1 - y0, sz0 - z0:sz1 - z0, sx0 - x0:sx1 - x0] = dense[sy0:sy1, sz0:sz1, sx0:sx1]
    return out


def carve_relief(colors: np.ndarray, relief: np.ndarray, steps: int) -> int:
    """Remove ``relief[v]`` voxels below every exposed surface (in place).  Returns voxels removed.

    Only voxels within ``steps`` of empty space are candidates, so faces shared by two blocks stay
    solid and no hidden channels appear inside walls.
    """
    if steps <= 0 or not relief.any():
        return 0
    solid = colors != 0
    dist = np.full(colors.shape, 255, dtype=np.uint8)
    frontier = ~solid
    for d in range(steps):
        layer = solid & _dilate6(frontier) & (dist == 255)
        if not layer.any():
            break
        dist[layer] = d
        frontier = layer
    remove = solid & (relief > dist)
    colors[remove] = 0
    return int(remove.sum())


# --------------------------------------------------------------------------------------
# Block-level operations
# --------------------------------------------------------------------------------------
def _dilate6(a: np.ndarray) -> np.ndarray:
    out = a.copy()
    out[1:] |= a[:-1]; out[:-1] |= a[1:]
    out[:, 1:] |= a[:, :-1]; out[:, :-1] |= a[:, 1:]
    out[:, :, 1:] |= a[:, :, :-1]; out[:, :, :-1] |= a[:, :, 1:]
    return out


def label_components(mask: np.ndarray, max_iter: int = 100000) -> tuple[np.ndarray, int]:
    """6-connected component labelling by iterative minimum propagation (no scipy needed)."""
    if not mask.any():
        return np.zeros(mask.shape, dtype=np.int64), 0
    labels = np.where(mask, np.arange(1, mask.size + 1, dtype=np.int64).reshape(mask.shape), 0)
    big = np.iinfo(np.int64).max
    for _ in range(max_iter):
        cur = np.where(mask, labels, big)
        m = cur.copy()
        m[1:] = np.minimum(m[1:], cur[:-1]); m[:-1] = np.minimum(m[:-1], cur[1:])
        m[:, 1:] = np.minimum(m[:, 1:], cur[:, :-1]); m[:, :-1] = np.minimum(m[:, :-1], cur[:, 1:])
        m[:, :, 1:] = np.minimum(m[:, :, 1:], cur[:, :, :-1]); m[:, :, :-1] = np.minimum(m[:, :, :-1], cur[:, :, 1:])
        m = np.where(mask, m, 0)
        if np.array_equal(m, labels):
            break
        labels = m
    u = np.unique(labels[mask])
    remap = np.zeros(int(labels.max()) + 1, dtype=np.int64)
    remap[u] = np.arange(1, len(u) + 1)
    return remap[labels], len(u)


def remove_islands(model: VoxelModel, min_blocks: int = 2, keep_ground_only: bool = False) -> int:
    """Drop disconnected groups of blocks smaller than ``min_blocks`` (or all that do not touch y=0).

    Returns the number of removed blocks.
    """
    occ = model.occupancy()
    labels, n = label_components(occ)
    if n <= 1:
        return 0
    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    remove = np.zeros(n + 1, dtype=bool)
    if keep_ground_only:
        grounded = np.unique(labels[0][occ[0]])
        remove[:] = True
        remove[grounded] = False
        remove[0] = False
    else:
        remove[1:] = sizes[1:] < min_blocks
    # never remove the largest component
    remove[int(np.argmax(sizes[1:])) + 1] = False
    kill = remove[labels] & occ
    count = int(kill.sum())
    if count:
        model.blocks[kill] = 0
    return count


def fill_cavities(model: VoxelModel, voxelizer_full_pattern: Callable[[tuple[int, int, int]], BlockPattern]) -> int:
    """Fill fully enclosed air pockets with solid blocks colored like their nearest neighbour.

    Enclosure is evaluated on the block grid using full-cube blocks as walls.  Returns blocks added.
    """
    air = ~model.occupancy()
    # outside = air reachable from the boundary through empty cells; any block with geometry
    # (walls, panes, doors, stairs) counts as a barrier so rooms with windows stay enclosed
    passable = air
    reach = np.zeros_like(passable)
    reach[0] = passable[0]; reach[-1] = passable[-1]
    reach[:, 0] |= passable[:, 0]; reach[:, -1] |= passable[:, -1]
    reach[:, :, 0] |= passable[:, :, 0]; reach[:, :, -1] |= passable[:, :, -1]
    while True:
        grown = _dilate6(reach) & passable
        if np.array_equal(grown, reach):
            break
        reach = grown
    enclosed = air & ~reach
    if not enclosed.any():
        return 0
    # propagate palette indices from neighbours into enclosed cells
    src = model.blocks.copy()
    src[~model.occupancy()] = 0
    filled = model.blocks.copy()
    todo = enclosed.copy()
    guard = 0
    while todo.any() and guard < 10000:
        guard += 1
        best = np.zeros_like(filled)
        for shift_fn in (
            lambda a: np.concatenate([a[1:], np.zeros_like(a[:1])], 0),
            lambda a: np.concatenate([np.zeros_like(a[:1]), a[:-1]], 0),
            lambda a: np.concatenate([a[:, 1:], np.zeros_like(a[:, :1])], 1),
            lambda a: np.concatenate([np.zeros_like(a[:, :1]), a[:, :-1]], 1),
            lambda a: np.concatenate([a[:, :, 1:], np.zeros_like(a[:, :, :1])], 2),
            lambda a: np.concatenate([np.zeros_like(a[:, :, :1]), a[:, :, :-1]], 2),
        ):
            nb = shift_fn(src)
            best = np.where((best == 0) & (nb != 0), nb, best)
        take = todo & (best != 0)
        if not take.any():
            break
        filled[take] = best[take]
        src[take] = best[take]
        todo &= ~take
    # replace filler cells with full-cube patterns of the neighbour's dominant color
    filler_index: dict[int, int] = {}
    changed = enclosed & (filled != model.blocks)
    trans_flags = model.colors.is_translucent()
    for pal_idx in np.unique(filled[changed]).tolist():
        pat = model.patterns[pal_idx]
        rgb = pat.avg_rgb or (128, 128, 128)
        if pal_idx not in filler_index:
            vals = pat.colors[pat.colors != 0]
            is_trans = bool(len(vals) and trans_flags[vals].mean() > 0.5)
            try:
                fp = voxelizer_full_pattern(rgb, translucent=is_trans)
            except TypeError:
                fp = voxelizer_full_pattern(rgb)
            fp.kind = "filler"
            model.palette.append(BlockState.make("mcprint:filler", {"of": str(pal_idx)}))
            model.patterns.append(fp)
            if model.flat_patterns is not None:
                model.flat_patterns.append(fp)
            filler_index[pal_idx] = len(model.palette) - 1
    for pal_idx, new_idx in filler_index.items():
        sel = changed & (filled == pal_idx)
        model.blocks[sel] = new_idx
    return int(changed.sum())


def add_base_plate(model: VoxelModel, layers_blocks: int, rgb: tuple[int, int, int],
                   voxelizer_full_pattern: Callable[[tuple[int, int, int]], BlockPattern], margin_blocks: int = 0) -> None:
    """Insert ``layers_blocks`` full-cube rows under the model (extending X/Z by margin)."""
    if layers_blocks <= 0:
        return
    Y, Z, X = model.blocks.shape
    m = margin_blocks
    new = np.zeros((Y + layers_blocks, Z + 2 * m, X + 2 * m), dtype=np.int32)
    new[layers_blocks:, m:m + Z, m:m + X] = model.blocks
    fp = voxelizer_full_pattern(rgb)
    fp.kind = "base"
    model.palette.append(BlockState.make("mcprint:base_plate"))
    model.patterns.append(fp)
    if model.flat_patterns is not None:
        model.flat_patterns.append(fp)
    new[:layers_blocks] = len(model.palette) - 1
    model.blocks = new


def crop_model(model: VoxelModel) -> None:
    occ = model.occupancy()
    if not occ.any():
        return
    ys, zs, xs = np.nonzero(occ)
    model.blocks = model.blocks[ys.min():ys.max() + 1, zs.min():zs.max() + 1, xs.min():xs.max() + 1].copy()
