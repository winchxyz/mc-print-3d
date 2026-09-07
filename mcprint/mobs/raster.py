"""Turn a :class:`MobModel` into block-sized voxel patterns and place it in a :class:`VoxelModel`.

Every box is rasterized by inverse-transforming the centre of each candidate sub-voxel into the
box's own space (so rotated parts such as spider legs or a wolf tail come out right) and coloring
it from the atlas face it is closest to.  The transform chain is the game's:

    world = R_y(180 - yaw) · scale · S(-1, -1, 1) · part_chain(box point)     (+ ground shift)

with ``part_chain`` = nested ``PartPose`` offset/rotation (rotation Z·Y·X) like ``ModelPart``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..assets.models import rotation_matrix
from ..schematics.base import BlockState
from ..voxel.voxelizer import BlockPattern, BlockVoxelizer
from .models import MobBox, MobModel, MobPart

MOB_BLOCK = "mcprint:mob"

FACES = ("top", "bottom", "west", "north", "east", "south")


@dataclass
class MobPlacement:
    model: MobModel
    x: float                # blocks, entity origin (feet centre) relative to the schematic
    y: float
    z: float
    yaw: float = 0.0        # degrees, Minecraft convention (0 = facing south / +Z, 90 = west)
    scale: float = 1.0      # extra user scale on top of the model's own render scale
    name: str = ""
    transform: Optional[tuple] = None   # (A 3x3, b) model units -> units, replaces the entity renderer chain (block entities)

    @property
    def label(self) -> str:
        return self.name or self.model.label or self.model.id


@dataclass
class MobRaster:
    """Sub-voxel colors of one mob, split into whole block cells: (bx, by, bz) -> pattern."""
    cells: dict = field(default_factory=dict)
    origin: tuple[int, int, int] = (0, 0, 0)
    size: tuple[int, int, int] = (0, 0, 0)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------------------
def _part_transform(model: MobModel, part: MobPart) -> tuple[np.ndarray, np.ndarray]:
    """(R, t) mapping part-local points to entity model space (Minecraft units, y down)."""
    R = np.eye(3)
    t = np.zeros(3)
    p: Optional[MobPart] = part
    depth = 0
    while p is not None and depth < 16:
        rx, ry, rz = p.rotation
        Rp = rotation_matrix("z", math.degrees(rz)) @ rotation_matrix("y", math.degrees(ry)) @ rotation_matrix("x", math.degrees(rx))
        # child transform sits inside the parent's: q = off_parent + R_parent (off_child + R_child p)
        R = Rp @ R
        t = Rp @ t + np.asarray(p.offset, dtype=np.float64)
        p = model.part(p.parent) if p.parent else None
        depth += 1
    return R, t


def _box_bounds(box: MobBox, min_units: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(lo, hi) of the printed box (inflated + padded to min thickness) and the unpadded (lo0, hi0)."""
    lo0 = np.asarray(box.origin, dtype=np.float64)
    hi0 = lo0 + np.asarray(box.size, dtype=np.float64)
    lo = lo0 - box.inflate
    hi = hi0 + box.inflate
    size = hi - lo
    pad = np.where(size < min_units, (min_units - size) / 2.0, 0.0)
    return lo - pad, hi + pad, lo0, hi0


def _box_world_matrix(model: MobModel, part: MobPart, placement: MobPlacement, ground_shift: float
                      ) -> tuple[np.ndarray, np.ndarray]:
    """(A, b): part-local (units) -> world sub-voxel-independent units relative to the entity origin, y up."""
    Rp, tp = _part_transform(model, part)
    if placement.transform is not None:           # block entity renderer: its own pose chain, no mirror, no grounding
        A0, b0 = placement.transform
        return A0 @ Rp, A0 @ tp + np.asarray(b0, dtype=np.float64)
    s = model.scale * placement.scale
    S = np.diag([-s, -s, s])                      # LivingEntityRenderer scale(-1, -1, 1) and the render scale
    Ry = rotation_matrix("y", 180.0 - placement.yaw)
    A = Ry @ S @ Rp
    b = Ry @ (S @ tp + np.array([0.0, ground_shift, 0.0]))
    return A, b


def _corners(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], dtype=np.float64)


# ---------------------------------------------------------------------------------------
# texture sampling
# ---------------------------------------------------------------------------------------
def _face_rects(box: MobBox, uv: tuple[float, float]) -> dict[str, tuple[float, float, float, float]]:
    u, v = uv
    w, h, d = box.size
    return {
        "top": (u + d, v, w, d), "bottom": (u + d + w, v, w, d),
        "west": (u, v + d, d, h), "north": (u + d, v + d, w, h),
        "east": (u + d + w, v + d, d, h), "south": (u + 2 * d + w, v + d, w, h),
    }


def _face_fractions(face: str, p: np.ndarray, lo0: np.ndarray, hi0: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(fu, fv) in [0, 1) across the atlas rectangle of ``face`` for box-local points ``p``."""
    w = max(hi0[0] - lo0[0], 1e-6)
    h = max(hi0[1] - lo0[1], 1e-6)
    d = max(hi0[2] - lo0[2], 1e-6)
    if face == "top" or face == "bottom":
        fu, fv = (p[:, 0] - lo0[0]) / w, (hi0[2] - p[:, 2]) / d
    elif face == "west":
        fu, fv = (hi0[2] - p[:, 2]) / d, (p[:, 1] - lo0[1]) / h
    elif face == "north":
        fu, fv = (p[:, 0] - lo0[0]) / w, (p[:, 1] - lo0[1]) / h
    elif face == "east":
        fu, fv = (p[:, 2] - lo0[2]) / d, (p[:, 1] - lo0[1]) / h
    else:  # south
        fu, fv = (hi0[0] - p[:, 0]) / w, (p[:, 1] - lo0[1]) / h
    return np.clip(fu, 0.0, 0.999999), np.clip(fv, 0.0, 0.999999)


def _sample(tex: np.ndarray, rect: tuple[float, float, float, float], fu: np.ndarray, fv: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Texels under ``rect`` (in the model's declared texture pixels; ``scale`` = actual / declared width for HD textures)."""
    H, W = tex.shape[:2]
    u0, v0, rw, rh = (v * scale for v in rect)
    px = np.clip(np.floor(u0 + fu * rw).astype(np.int64), 0, W - 1)
    py = np.clip(np.floor(v0 + fv * rh).astype(np.int64), 0, H - 1)
    return tex[py, px]


# ---------------------------------------------------------------------------------------
# rasterization
# ---------------------------------------------------------------------------------------
class MobVoxelizer:
    def __init__(self, vox: BlockVoxelizer, min_units: float = 1.0, alpha_threshold: int = 96):
        self.vox = vox
        self.textures = vox.textures
        self.colors = vox.colors
        self.n = vox.settings.resolution
        self.min_units = float(min_units)
        self.alpha = int(alpha_threshold)
        self.missing: set[str] = set()

    # -- helpers ---------------------------------------------------------------------
    def _texture(self, resource: str) -> Optional[np.ndarray]:
        tex = self.textures.get(resource)
        if tex is None:
            self.missing.add(resource)
        return tex

    def _ground_shift(self, placement: MobPlacement) -> float:
        """Lift so the lowest printed point sits on y = 0 (feet on the ground)."""
        if placement.transform is not None:
            return 0.0
        model = placement.model
        s = model.scale * placement.scale
        S = np.diag([-s, -s, s])
        lowest = math.inf
        for part in model.parts:
            Rp, tp = _part_transform(model, part)
            for box in part.boxes:
                lo, hi, _l0, _h0 = _box_bounds(box, self.min_units / max(s, 1e-6))
                pts = (S @ (Rp @ _corners(lo, hi).T + tp[:, None])).T
                lowest = min(lowest, float(pts[:, 1].min()))
        return -lowest if math.isfinite(lowest) else 0.0

    # -- main ------------------------------------------------------------------------
    def rasterize(self, placement: MobPlacement) -> MobRaster:
        model = placement.model
        n = self.n
        s = model.scale * placement.scale
        min_local = self.min_units / max(s, 1e-6)          # thickness padding in box units
        ground = self._ground_shift(placement)
        # world extent (units, relative to the entity origin) -> block cell range
        boxes = []
        lo_all = np.full(3, math.inf)
        hi_all = np.full(3, -math.inf)
        for part in model.parts:
            A, b = _box_world_matrix(model, part, placement, ground)
            for box in part.boxes:
                lo, hi, lo0, hi0 = _box_bounds(box, min_local)
                pts = (A @ _corners(lo, hi).T + b[:, None]).T
                lo_all = np.minimum(lo_all, pts.min(0))
                hi_all = np.maximum(hi_all, pts.max(0))
                boxes.append((box, A, b, lo, hi, lo0, hi0, pts.min(0), pts.max(0)))
        if not boxes:
            return MobRaster()
        origin_blocks = np.array([placement.x, placement.y, placement.z], dtype=np.float64)
        lo_b = np.floor(origin_blocks + lo_all / 16.0 + 1e-6).astype(int)
        hi_b = np.ceil(origin_blocks + hi_all / 16.0 - 1e-6).astype(int)
        size_b = np.maximum(hi_b - lo_b, 1)
        vol = np.zeros((size_b[1] * n, size_b[2] * n, size_b[0] * n), dtype=np.uint16)   # [y, z, x]
        # sub-voxel centre -> units relative to the entity origin
        unit = 16.0 / n
        base = (lo_b - origin_blocks) * 16.0        # units at the raster's lower corner
        tex_default = self._texture(model.texture)
        for box, A, b, lo, hi, lo0, hi0, wlo, whi in boxes:
            i0 = np.floor((wlo - base) / unit).astype(int) - 1
            i1 = np.ceil((whi - base) / unit).astype(int) + 1
            i0 = np.maximum(i0, 0)
            i1 = np.minimum(i1, [size_b[0] * n, size_b[1] * n, size_b[2] * n])
            if np.any(i1 <= i0):
                continue
            ix, iy, iz = np.meshgrid(np.arange(i0[0], i1[0]), np.arange(i0[1], i1[1]), np.arange(i0[2], i1[2]), indexing="ij")
            ix, iy, iz = ix.ravel(), iy.ravel(), iz.ravel()
            centres = np.stack([ix, iy, iz], axis=1).astype(np.float64) + 0.5
            world = base + centres * unit
            local = np.linalg.solve(A, (world - b).T).T            # box-local (Minecraft units, y down)
            if box.mirror:
                local[:, 0] = (lo0[0] + hi0[0]) - local[:, 0]
            inside = np.all((local >= lo) & (local < hi), axis=1)
            if not inside.any():
                continue
            idx = np.nonzero(inside)[0]
            p = local[idx]
            dist = np.stack([p[:, 1] - lo[1], hi[1] - p[:, 1], p[:, 0] - lo[0], p[:, 2] - lo[2], hi[0] - p[:, 0], hi[2] - p[:, 2]], axis=1)
            face_i = np.argmin(dist, axis=1)
            rgb = np.zeros((len(idx), 3), dtype=np.uint8)
            keep = np.ones(len(idx), dtype=bool)
            tex = self._texture(box.texture) if box.texture else tex_default
            if box.translucent and tex is not None:
                # printed solid in clear filament: transparent texels take the nearest painted color, never a hole
                filled = self.textures.filled(box.texture or model.texture)
                if filled is not None:
                    tex = np.concatenate([filled, np.full(filled.shape[:2] + (1,), 255, dtype=np.uint8)], axis=2)
            rects = _face_rects(box, box.uv)
            orects = _face_rects(box, box.overlay_uv) if box.overlay_uv else None
            otex = self._texture(box.overlay_texture) if box.overlay_texture else tex
            for fi, face in enumerate(FACES):
                sel = face_i == fi
                if not sel.any():
                    continue
                fu, fv = _face_fractions(face, p[sel], lo0, hi0)
                if box.face_textures and face in box.face_textures:
                    res, rect = box.face_textures[face]
                    ft = self._texture(res)
                    if ft is None:
                        rgb[sel] = (200, 120, 40)
                        continue
                    r = rect or (0, 0, ft.shape[1], ft.shape[0])
                    px = _sample(ft, (r[0], r[1], r[2] - r[0], r[3] - r[1]), fu, fv)
                    rgb[sel] = px[:, :3]
                    keep[sel] = px[:, 3] >= self.alpha
                    continue
                if tex is None:
                    rgb[sel] = (150, 150, 150)
                    continue
                tsz = box.tex_size or model.tex_size
                px = _sample(tex, rects[face], fu, fv, tex.shape[1] / tsz[0])
                col = px[:, :3].astype(np.uint8)
                # translucent parts (slime) are printed solid in clear filament: their soft alpha is not a cut-out
                ok = px[:, 3] >= self.alpha if not box.translucent else np.ones(len(fu), dtype=bool)
                if orects is not None and otex is not None:
                    op = _sample(otex, orects[face], fu, fv, otex.shape[1] / tsz[0])
                    over = op[:, 3] >= self.alpha
                    col = np.where(over[:, None], op[:, :3], col).astype(np.uint8)
                    ok |= over
                rgb[sel] = col
                keep[sel] = ok
            if box.tint is not None:
                t = np.asarray(box.tint, dtype=np.float64) / 255.0
                rgb = np.clip(rgb.astype(np.float64) * t, 0, 255).astype(np.uint8)
            k = idx[keep]
            if not len(k):
                continue
            ci = self.colors.index_of(rgb[keep], translucent=bool(box.translucent))
            vol[iy[k], iz[k], ix[k]] = ci
        raster = MobRaster(origin=(int(lo_b[0]), int(lo_b[1]), int(lo_b[2])), size=tuple(int(v) for v in size_b))
        for by in range(size_b[1]):
            for bz in range(size_b[2]):
                for bx in range(size_b[0]):
                    sub = vol[by * n:(by + 1) * n, bz * n:(bz + 1) * n, bx * n:(bx + 1) * n]
                    if not sub.any():
                        continue
                    pat = BlockPattern(sub.copy(), kind="mob", note=placement.label)
                    self.vox._finish(pat)
                    raster.cells[(int(lo_b[0]) + bx, int(lo_b[1]) + by, int(lo_b[2]) + bz)] = pat
        return raster


# ---------------------------------------------------------------------------------------
# placement into a VoxelModel
# ---------------------------------------------------------------------------------------
PLATFORM_RGB = (0x8C, 0x8C, 0x8C)


def add_platform(raster: MobRaster, n: int, thickness_voxels: int, margin_voxels: int, color_index: int) -> None:
    """Put a plate under the figure: the footprint of its lowest voxel layer grown by ``margin_voxels``,
    ``thickness_voxels`` thick, in the cell layer below the feet (so the feet stand on it)."""
    if thickness_voxels <= 0 or not raster.cells:
        return
    y0 = min(c[1] for c in raster.cells)
    ground = [(c, p) for c, p in raster.cells.items() if c[1] == y0]
    xs: list[int] = []
    zs: list[int] = []
    for (bx, _by, bz), pat in ground:
        occ = pat.colors != 0
        rows = np.nonzero(occ)[0]
        if not len(rows):
            continue
        low = occ[rows.min()]                     # lowest occupied voxel layer of this cell [z, x]
        zz, xx = np.nonzero(low)
        xs.extend((bx * n + xx).tolist())
        zs.extend((bz * n + zz).tolist())
    if not xs:
        return
    x0, x1 = min(xs) - margin_voxels, max(xs) + margin_voxels + 1
    z0, z1 = min(zs) - margin_voxels, max(zs) + margin_voxels + 1
    t = min(thickness_voxels, n)
    for bx in range(x0 // n - (1 if x0 < 0 and x0 % n else 0), (x1 - 1) // n + 1):
        for bz in range(z0 // n - (1 if z0 < 0 and z0 % n else 0), (z1 - 1) // n + 1):
            cell = (bx, y0 - 1, bz)
            pat = raster.cells.get(cell)
            colors = pat.colors.copy() if pat is not None else np.zeros((n, n, n), dtype=np.uint16)
            lx0, lx1 = max(x0 - bx * n, 0), min(x1 - bx * n, n)
            lz0, lz1 = max(z0 - bz * n, 0), min(z1 - bz * n, n)
            if lx0 >= lx1 or lz0 >= lz1:
                continue
            colors[n - t:n, lz0:lz1, lx0:lx1] = np.where(colors[n - t:n, lz0:lz1, lx0:lx1] == 0, color_index, colors[n - t:n, lz0:lz1, lx0:lx1])
            raster.cells[cell] = BlockPattern(colors, kind="mob", note=(pat.note if pat is not None else "platform"))
    raster.origin = (min(raster.origin[0], min(c[0] for c in raster.cells)), min(raster.origin[1], y0 - 1), min(raster.origin[2], min(c[2] for c in raster.cells)))


def place_mobs(model, placements: list[MobPlacement], vox: BlockVoxelizer, min_units: float = 1.0,
               alpha_threshold: int = 96, platform_units: float = 0.0) -> tuple[int, list[str]]:
    """Rasterize ``placements`` and write them into ``model`` (a :class:`VoxelModel`).

    The block grid grows when a mob sticks out of it.  Mob voxels are merged over existing block
    geometry (a mob standing in tall grass keeps the grass).  ``platform_units`` > 0 adds a plate of
    that thickness (model units) under every figure.  Returns (mobs placed, warnings).
    """
    mv = MobVoxelizer(vox, min_units=min_units, alpha_threshold=alpha_threshold)
    warnings: list[str] = []
    placed = 0
    n = vox.settings.resolution
    plate_color = vox.colors.index_of_color(PLATFORM_RGB) if platform_units > 0 else 0
    for k, pl in enumerate(placements):
        try:
            raster = mv.rasterize(pl)
            if platform_units > 0:
                add_platform(raster, n, max(1, int(round(platform_units * n / 16.0))), max(1, int(round(n / 8))), plate_color)
                for pat in raster.cells.values():
                    if not pat.exposed_counts:
                        vox._finish(pat)
        except Exception as exc:  # pragma: no cover - never let one mob kill the conversion
            warnings.append(f"mob {pl.label}: {exc}")
            continue
        if not raster.cells:
            warnings.append(f"mob {pl.label}: nothing to print (texture missing?)")
            continue
        xs = [c[0] for c in raster.cells]; ys = [c[1] for c in raster.cells]; zs = [c[2] for c in raster.cells]
        Y, Z, X = model.blocks.shape
        pad_lo = [max(0, -min(xs)), max(0, -min(ys)), max(0, -min(zs))]
        pad_hi = [max(0, max(xs) + 1 - X), max(0, max(ys) + 1 - Y), max(0, max(zs) + 1 - Z)]
        if any(pad_lo) or any(pad_hi):
            model.blocks = np.pad(model.blocks, ((pad_lo[1], pad_hi[1]), (pad_lo[2], pad_hi[2]), (pad_lo[0], pad_hi[0])))
            if any(pad_lo):
                # every previously placed mob and block moved: shift the remaining placements too
                for later in placements[k + 1:]:
                    later.x += pad_lo[0]; later.y += pad_lo[1]; later.z += pad_lo[2]
        for (bx, by, bz), pat in raster.cells.items():
            bx += pad_lo[0]; by += pad_lo[1]; bz += pad_lo[2]
            existing = int(model.blocks[by, bz, bx])
            if existing and not model.patterns[existing].is_empty:
                base = model.patterns[existing]
                merged = base.colors.copy()
                m = pat.colors != 0
                merged[m] = pat.colors[m]
                pat = BlockPattern(merged, kind="mob", note=f"{pl.label} over {model.palette[existing].path}")
                vox._finish(pat)
            state = BlockState.make(MOB_BLOCK, {"id": pl.model.id, "n": str(k), "cell": f"{bx}_{by}_{bz}"})
            model.palette.append(state)
            model.patterns.append(pat)
            if model.flat_patterns is not None:
                model.flat_patterns.append(pat)
            model.blocks[by, bz, bx] = len(model.palette) - 1
        placed += 1
    for res in sorted(mv.missing):
        warnings.append(f"mob texture missing: {res}")
    return placed, warnings


def is_mob_state(state: BlockState) -> bool:
    return state.name == MOB_BLOCK


__all__ = ["MobPlacement", "MobRaster", "MobVoxelizer", "place_mobs", "is_mob_state", "MOB_BLOCK"]
