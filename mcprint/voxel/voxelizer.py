"""Texture-aware voxelization of a single block state into an N x N x N pattern.

For every model element we test which sub-voxel centres fall inside the (rotated) cuboid, then
look up the texture pixel of the nearest face at that point.  Transparent pixels (alpha below the
threshold) produce NO volume – this is what turns a flat "cross" flower texture, a glass pane's
cut-out, or a door's window into real printable geometry instead of a solid slab.

Thin elements (planes, carpets, pressure plates) are expanded to a minimum thickness so they are
printable.  Blocks without JSON geometry get fallback shapes (see ``assets.fallbacks``).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..assets.fallbacks import fallback_for, is_air_like
from ..assets.models import Element, ModelInstance, ModelResolver, ResolvedState, blockstate_rotation, rotation_matrix
from ..assets.textures import MISSING_TEXTURE, TextureLoader, hashed_color
from ..assets.tints import apply_tint, tint_for
from ..schematics.base import BlockState
from .colorindex import ColorIndex

log = logging.getLogger(__name__)

FLUIDS = {"water", "lava", "flowing_water", "flowing_lava", "bubble_column"}


@dataclass
class VoxelSettings:
    resolution: int = 8                  # sub-voxels per block edge
    min_thickness: float = 1.0           # model units (1/16 block); thin elements are padded to this
    alpha_threshold: int = 96            # texture alpha below this = empty
    cutout_dilation: int = 1             # grow opaque texture areas by this many texels (thin stems/wires stay printable)
    solid_textures: tuple[str, ...] = ("glass", "tinted_glass", "ice", "frosted_ice", "slime", "honey", "water", "lava", "leaves")
    translucent_textures: tuple[str, ...] = ("glass", "ice", "honey_block", "slime_block", "water", "nether_portal")   # kept as their own 'clear filament' color
    translucent_as_solid: bool = True    # textures with many semi-transparent pixels are treated as opaque (stained glass, ice)
    include_fluids: bool = False
    unknown_policy: str = "cube"         # 'cube' | 'skip'
    tint_overrides: dict = field(default_factory=dict)
    obj_interior_fill: bool = True
    relief_depth: float = 0.0            # model units; texture-derived surface relief depth (0 = off)
    relief_mode: str = "auto"            # auto | heightmap | pattern | dark | light
    relief_min_element: float = 4.0      # only emboss element faces whose element is at least this thick (units)

    @property
    def h(self) -> float:
        return 16.0 / self.resolution


@dataclass
class BlockPattern:
    colors: np.ndarray                   # uint16 [y, z, x] (N,N,N), 0 = empty
    kind: str = "ok"                     # ok | fallback | empty | skipped | fluid
    note: str = ""
    avg_rgb: Optional[tuple[int, int, int]] = None      # average color of exposed voxels
    dominant: int = 0                    # most common color index
    exposed_hist: Optional[np.ndarray] = None            # (K,) counts of exposed voxels per color index (sparse via dict below)
    exposed_counts: dict = field(default_factory=dict)   # color index -> exposed voxel count
    relief: Optional[np.ndarray] = None  # uint8 [y, z, x]: voxels to carve below an exposed face (0 = flat)

    @property
    def count(self) -> int:
        return int(np.count_nonzero(self.colors))

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    @property
    def is_full(self) -> bool:
        return self.count == self.colors.size


# --------------------------------------------------------------------------------------
class BlockVoxelizer:
    def __init__(self, resolver: ModelResolver, textures: TextureLoader, settings: VoxelSettings, colors: Optional[ColorIndex] = None):
        self.resolver = resolver
        self.textures = textures
        self.settings = settings
        self.colors = colors or ColorIndex()
        self._cache: dict[BlockState, BlockPattern] = {}
        self._opaque_cache: dict[str, bool] = {}
        self._translucent_cache: dict[str, bool] = {}
        n = settings.resolution
        h = settings.h
        c = (np.arange(n) + 0.5) * h
        # sample centres in [y, z, x] order -> points (x, y, z)
        yy, zz, xx = np.meshgrid(c, c, c, indexing="ij")
        self._points = np.stack([xx.ravel(), yy.ravel(), zz.ravel()], axis=1)  # (n^3, 3)
        self.stats = {"ok": 0, "fallback": 0, "empty": 0, "skipped": 0, "fluid": 0}
        self.fallback_states: dict[str, str] = {}
        self.relief_steps = int(round(settings.relief_depth / h)) if settings.relief_depth > 0 else 0
        if settings.relief_depth > 0:
            self.relief_steps = max(1, min(self.relief_steps, max(1, n // 4)))

    # ---- public --------------------------------------------------------------------
    def voxelize(self, state: BlockState) -> BlockPattern:
        p = self._cache.get(state)
        if p is None:
            try:
                p = self._voxelize(state)
            except Exception as exc:  # never let one weird block kill the conversion
                log.warning("voxelize %s failed: %s", state, exc)
                p = self._fallback(state, None, f"error: {exc}")
            self._finish(p)
            self._cache[state] = p
            self.stats[p.kind] = self.stats.get(p.kind, 0) + 1
        return p

    def empty_pattern(self) -> BlockPattern:
        n = self.settings.resolution
        return BlockPattern(np.zeros((n, n, n), dtype=np.uint16), kind="empty")

    def full_pattern(self, rgb: tuple[int, int, int], kind: str = "ok", translucent: bool = False) -> BlockPattern:
        n = self.settings.resolution
        idx = self.colors.index_of_color(rgb, translucent=translucent)
        p = BlockPattern(np.full((n, n, n), idx, dtype=np.uint16), kind=kind)
        self._finish(p)
        return p

    # ---- core ----------------------------------------------------------------------
    def _voxelize(self, state: BlockState) -> BlockPattern:
        path = state.path
        if is_air_like(path):
            return self.empty_pattern()
        if path in FLUIDS or (state.namespace != "minecraft" and path.endswith(("_water", "_lava", "_fluid", "_fluid_block"))):
            if not self.settings.include_fluids:
                return BlockPattern(self.empty_pattern().colors, kind="skipped", note="fluid")
            return self._fluid(state)
        rs = self.resolver.resolve(state)
        if rs.kind == "empty":
            return self.empty_pattern()
        if rs.kind in ("missing_blockstate", "missing_model", "custom_loader", "builtin_entity"):
            if rs.kind != "builtin_entity" and self.settings.unknown_policy == "skip":
                return BlockPattern(self.empty_pattern().colors, kind="skipped", note=rs.kind)
            has_obj = any(i.model.obj is not None for i in rs.instances)
            if not has_obj:
                return self._fallback(state, rs.particle, rs.kind)
        n = self.settings.resolution
        out = np.zeros(n * n * n, dtype=np.uint16)
        rel = np.zeros(n * n * n, dtype=np.uint8) if self.relief_steps else None
        for inst in rs.instances:
            self._stamp_instance(state, inst, out, rel=rel)
        self._stamp_extra_boxes(state, out)
        pat = BlockPattern(out.reshape(n, n, n), kind="ok", relief=rel.reshape(n, n, n) if rel is not None else None)
        if pat.is_empty and rs.kind == "ok":
            # geometry exists but every sample was transparent (e.g. tiny elements below resolution)
            pat = self._retry_coarse(state, rs)
        return pat

    def _retry_coarse(self, state: BlockState, rs: ResolvedState) -> BlockPattern:
        """Elements thinner than the sample spacing in every axis: mark their centre voxel."""
        n = self.settings.resolution
        out = np.zeros(n * n * n, dtype=np.uint16)
        for inst in rs.instances:
            self._stamp_instance(state, inst, out, extra_pad=self.settings.h)
        pat = BlockPattern(out.reshape(n, n, n), kind="ok")
        if pat.is_empty:
            pat.kind = "empty"
            pat.note = "geometry below resolution"
        return pat

    def _stamp_instance(self, state: BlockState, inst: ModelInstance, out: np.ndarray, extra_pad: float = 0.0,
                        rel: Optional[np.ndarray] = None) -> None:
        model = inst.model
        P = self._points
        R = blockstate_rotation(inst.x, inst.y)
        centre = np.array([8.0, 8.0, 8.0])
        Q = (P - centre) @ R + centre  # model space
        if model.obj is not None and not model.elements:
            self._stamp_obj(state, model, R, out)
            return
        for el in model.elements:
            self._stamp_element(state, el, Q, out, extra_pad, rel)
        self._recolor_nearest_face(state, model, Q, out, rel)

    def _recolor_nearest_face(self, state: BlockState, model, Q: np.ndarray, out: np.ndarray, rel: Optional[np.ndarray]) -> None:
        """Color every solid voxel of this instance from the nearest textured face of *any* element.

        Vanilla builds some blocks (cactus, many mod blocks) from overlapping elements that each carry
        only some faces; a voxel on the west side of such a block must take the west texture even when
        the element that created it only had north/south faces.
        """
        faces = []   # (axis, plane, lo_a, hi_a, lo_b, hi_b, element, fname, face)
        for el in model.elements:
            if el.rot_axis and el.rot_angle:
                continue
            lo, hi = el.frm, el.to
            for fname, face in el.faces.items():
                if face.texture == MISSING_TEXTURE:
                    continue
                if fname in ("down", "up"):
                    axis, plane = 1, (lo[1] if fname == "down" else hi[1])
                    a, b = 0, 2
                elif fname in ("north", "south"):
                    axis, plane = 2, (lo[2] if fname == "north" else hi[2])
                    a, b = 0, 1
                else:
                    axis, plane = 0, (lo[0] if fname == "west" else hi[0])
                    a, b = 1, 2
                if hi[a] - lo[a] < 1e-6 or hi[b] - lo[b] < 1e-6:
                    continue
                faces.append((axis, float(plane), float(lo[a]), float(hi[a]), float(lo[b]), float(hi[b]), a, b, el, fname, face))
        if len(faces) < 2:
            return
        idx = np.nonzero(out)[0]
        if len(idx) == 0:
            return
        q = Q[idx]
        tol = self.settings.h * 0.5 + 1e-6
        dist = np.full((len(idx), len(faces)), np.inf)
        for fi, (axis, plane, la, ha, lb, hb, a, b, el, fname, face) in enumerate(faces):
            inside = (q[:, a] >= la - tol) & (q[:, a] <= ha + tol) & (q[:, b] >= lb - tol) & (q[:, b] <= hb + tol)
            d = np.abs(q[:, axis] - plane)
            dist[:, fi] = np.where(inside, d, np.inf)
        best = np.argmin(dist, axis=1)
        has = np.isfinite(dist[np.arange(len(idx)), best])
        if not has.any():
            return
        for fi, (axis, plane, la, ha, lb, hb, a, b, el, fname, face) in enumerate(faces):
            sel = has & (best == fi)
            if not sel.any():
                continue
            pts = q[sel]
            lo, hi = el.frm, el.to
            w = max(hi[0] - lo[0], 1e-6)
            h = max(hi[1] - lo[1], 1e-6)
            d = max(hi[2] - lo[2], 1e-6)
            if fname == "down":
                sx, sy = (pts[:, 0] - lo[0]) / w, (hi[2] - pts[:, 2]) / d
            elif fname == "up":
                sx, sy = (pts[:, 0] - lo[0]) / w, (pts[:, 2] - lo[2]) / d
            elif fname == "north":
                sx, sy = (hi[0] - pts[:, 0]) / w, (hi[1] - pts[:, 1]) / h
            elif fname == "south":
                sx, sy = (pts[:, 0] - lo[0]) / w, (hi[1] - pts[:, 1]) / h
            elif fname == "west":
                sx, sy = (pts[:, 2] - lo[2]) / d, (hi[1] - pts[:, 1]) / h
            else:
                sx, sy = (hi[2] - pts[:, 2]) / d, (hi[1] - pts[:, 1]) / h
            sx = np.clip(sx, 0.0, 0.999999)
            sy = np.clip(sy, 0.0, 0.999999)
            rot = face.rotation % 360
            if rot == 90:
                sx, sy = 1.0 - sy, sx
            elif rot == 180:
                sx, sy = 1.0 - sx, 1.0 - sy
            elif rot == 270:
                sx, sy = sy, 1.0 - sx
            u1, v1, u2, v2 = face.uv if face.uv is not None else _default_uv(fname, lo, hi)
            u = u1 + sx * (u2 - u1)
            v = v1 + sy * (v2 - v1)
            if face.color is not None:
                rgb = np.tile(np.asarray(face.color, dtype=np.uint8), (len(pts), 1))
                keep = np.ones(len(pts), dtype=bool)
            else:
                opaque = self._is_forced_opaque(face.texture)
                rgb, keep = self._sample_texture(face.texture, u, v, opaque)
            if rgb is None:
                continue
            if face.tintindex >= 0:
                tint = tint_for(state, face.tintindex, self.settings.tint_overrides)
                if tint is not None:
                    rgb = apply_tint(rgb, tint)
            target = idx[sel]
            # only recolor where the texel is opaque; a transparent texel of the nearest face means the
            # voxel really belongs to another element (keep its color)
            ok = keep if keep is not None else np.ones(len(target), dtype=bool)
            if ok.any():
                out[target[ok]] = self.colors.index_of(rgb[ok], translucent=self._is_translucent(face.texture))
                if rel is not None and self.relief_steps:
                    size = hi - lo
                    if size[axis] >= self.settings.relief_min_element:
                        depth = self._sample_relief(face.texture, u[ok], v[ok])
                        if depth is not None:
                            rel[target[ok]] = np.clip(np.round(depth * self.relief_steps), 0, 255).astype(np.uint8)

    def _stamp_element(self, state: BlockState, el: Element, Q: np.ndarray, out: np.ndarray, extra_pad: float,
                       rel: Optional[np.ndarray] = None) -> None:
        s = self.settings
        lo = el.frm.copy()
        hi = el.to.copy()
        q = Q
        if el.rot_axis and el.rot_angle:
            Rel = rotation_matrix(el.rot_axis, el.rot_angle)
            origin = el.rot_origin if el.rot_origin is not None else np.array([8.0, 8.0, 8.0])
            q = (Q - origin) @ Rel + origin  # inverse rotation (Rel^T applied to column vectors)
            if el.rescale and abs(math.cos(math.radians(el.rot_angle))) > 1e-6:
                scale = 1.0 / abs(math.cos(math.radians(el.rot_angle)))
                sv = np.ones(3)
                axis_i = {"x": 0, "y": 1, "z": 2}[el.rot_axis]
                for i in range(3):
                    if i != axis_i:
                        sv[i] = scale
                q = (q - origin) / sv + origin
        size = hi - lo
        tmin = max(s.min_thickness, s.h) + extra_pad   # never thinner than one sample spacing
        pad = np.where(size < tmin, (tmin - size) / 2.0, 0.0)
        elo = lo - pad
        ehi = hi + pad
        inside = np.all((q >= elo) & (q < ehi), axis=1)
        if not inside.any():
            return
        idx = np.nonzero(inside)[0]
        qi = q[idx]
        # nearest face with a texture
        dist = np.full((len(idx), 6), np.inf)
        names = ("down", "up", "north", "south", "west", "east")
        if "down" in el.faces:
            dist[:, 0] = np.abs(qi[:, 1] - lo[1])
        if "up" in el.faces:
            dist[:, 1] = np.abs(hi[1] - qi[:, 1])
        if "north" in el.faces:
            dist[:, 2] = np.abs(qi[:, 2] - lo[2])
        if "south" in el.faces:
            dist[:, 3] = np.abs(hi[2] - qi[:, 2])
        if "west" in el.faces:
            dist[:, 4] = np.abs(qi[:, 0] - lo[0])
        if "east" in el.faces:
            dist[:, 5] = np.abs(hi[0] - qi[:, 0])
        face_i = np.argmin(dist, axis=1)
        colors = np.zeros((len(idx), 3), dtype=np.uint8)
        solid = np.ones(len(idx), dtype=bool)
        trans = np.zeros(len(idx), dtype=bool)
        carve = np.zeros(len(idx), dtype=np.uint8) if rel is not None else None
        normal_axis = {"down": 1, "up": 1, "north": 2, "south": 2, "west": 0, "east": 0}
        w = max(hi[0] - lo[0], 1e-6)
        h = max(hi[1] - lo[1], 1e-6)
        d = max(hi[2] - lo[2], 1e-6)
        for fi, fname in enumerate(names):
            sel = face_i == fi
            if not sel.any() or fname not in el.faces:
                continue
            face = el.faces[fname]
            pts = qi[sel]
            if fname == "down":
                sx, sy = (pts[:, 0] - lo[0]) / w, (hi[2] - pts[:, 2]) / d
            elif fname == "up":
                sx, sy = (pts[:, 0] - lo[0]) / w, (pts[:, 2] - lo[2]) / d
            elif fname == "north":
                sx, sy = (hi[0] - pts[:, 0]) / w, (hi[1] - pts[:, 1]) / h
            elif fname == "south":
                sx, sy = (pts[:, 0] - lo[0]) / w, (hi[1] - pts[:, 1]) / h
            elif fname == "west":
                sx, sy = (pts[:, 2] - lo[2]) / d, (hi[1] - pts[:, 1]) / h
            else:  # east
                sx, sy = (hi[2] - pts[:, 2]) / d, (hi[1] - pts[:, 1]) / h
            sx = np.clip(sx, 0.0, 0.999999)
            sy = np.clip(sy, 0.0, 0.999999)
            rot = face.rotation % 360
            if rot == 90:
                sx, sy = 1.0 - sy, sx
            elif rot == 180:
                sx, sy = 1.0 - sx, 1.0 - sy
            elif rot == 270:
                sx, sy = sy, 1.0 - sx
            if face.uv is not None:
                u1, v1, u2, v2 = face.uv
            else:
                u1, v1, u2, v2 = _default_uv(fname, lo, hi)
            u = u1 + sx * (u2 - u1)
            v = v1 + sy * (v2 - v1)
            if face.color is not None:
                rgb = np.tile(np.asarray(face.color, dtype=np.uint8), (len(pts), 1))
                keep = np.ones(len(pts), dtype=bool)
            else:
                opaque = self._is_forced_opaque(face.texture)
                rgb, keep = self._sample_texture(face.texture, u, v, opaque)
            if rgb is None:
                rgb = np.tile(np.asarray(hashed_color(face.texture if face.texture != MISSING_TEXTURE else state.name), dtype=np.uint8), (len(pts), 1))
                keep = np.ones(len(pts), dtype=bool)
            if face.tintindex >= 0:
                tint = tint_for(state, face.tintindex, self.settings.tint_overrides)
                if tint is not None:
                    rgb = apply_tint(rgb, tint)
            colors[sel] = rgb
            solid[sel] = keep
            trans[sel] = self._is_translucent(face.texture)
            if carve is not None and size[normal_axis[fname]] >= self.settings.relief_min_element:
                depth = self._sample_relief(face.texture, u, v)
                if depth is not None:
                    carve[sel] = np.clip(np.round(depth * self.relief_steps), 0, 255).astype(np.uint8)
        keep_idx = idx[solid]
        if len(keep_idx):
            tk = trans[solid]
            ck = colors[solid]
            if (~tk).any():
                out[keep_idx[~tk]] = self.colors.index_of(ck[~tk])
            if tk.any():
                out[keep_idx[tk]] = self.colors.index_of(ck[tk], translucent=True)
            if carve is not None:
                rel[keep_idx] = carve[solid]

    def _sample_texture(self, resource: str, u: np.ndarray, v: np.ndarray, opaque: bool):
        """Returns (rgb (M,3) uint8, keep (M,) bool) or (None, None) when the texture is missing."""
        if resource == MISSING_TEXTURE:
            return None, None
        tex = self.textures.get(resource)
        if tex is None:
            return None, None
        H, W = tex.shape[:2]
        px = np.clip((u / 16.0 * W).astype(np.int64), 0, W - 1)
        py = np.clip((v / 16.0 * H).astype(np.int64), 0, H - 1)
        if opaque:
            filled = self.textures.filled(resource)
            rgb = filled if filled is not None else tex[..., :3]
            return rgb[py, px], np.ones(len(px), dtype=bool)
        radius = int(round(self.settings.cutout_dilation * W / 16.0)) if self.settings.cutout_dilation > 0 else 0
        if radius > 0:
            d = self.textures.dilated(resource, self.settings.alpha_threshold, radius)
            if d is not None:
                rgb, mask = d
                return rgb[py, px], mask[py, px]
        pix = tex[py, px]
        return pix[:, :3], pix[:, 3] >= self.settings.alpha_threshold

    def _sample_relief(self, resource: str, u: np.ndarray, v: np.ndarray) -> Optional[np.ndarray]:
        """Relief depth fraction (0..1) of the texel under each (u, v), or None when the texture is flat."""
        if resource == MISSING_TEXTURE or not self.relief_steps:
            return None
        depth_map = self.textures.relief_map(resource, self.settings.relief_mode)
        if depth_map is None:
            return None
        H, W = depth_map.shape
        px = np.clip((u / 16.0 * W).astype(np.int64), 0, W - 1)
        py = np.clip((v / 16.0 * H).astype(np.int64), 0, H - 1)
        return depth_map[py, px]

    def _is_translucent(self, resource: str) -> bool:
        """Glass-like textures: printed solid but kept as a separate color for clear filament."""
        v = self._translucent_cache.get(resource)
        if v is None:
            name = resource.split("/")[-1].split(":")[-1]
            v = any(key == name or name.startswith(key + "_") or name.endswith("_" + key) or key in name
                    for key in self.settings.translucent_textures)
            self._translucent_cache[resource] = v
        return v

    def _is_forced_opaque(self, resource: str) -> bool:
        v = self._opaque_cache.get(resource)
        if v is None:
            name = resource.split("/")[-1].split(":")[-1]
            v = False
            for key in self.settings.solid_textures:
                if key == name or name.startswith(key + "_") or name.endswith("_" + key) or (key == "glass" and "glass" in name):
                    v = True
                    break
            if not v and self.settings.translucent_as_solid and resource != MISSING_TEXTURE:
                _fully, semi = self.textures.alpha_stats(resource)
                if semi > 0.15:
                    v = True
            self._opaque_cache[resource] = v
        return v

    # ---- OBJ models -----------------------------------------------------------------
    def _stamp_obj(self, state: BlockState, model, R: np.ndarray, out: np.ndarray) -> None:
        obj = model.obj
        n = self.settings.resolution
        h = self.settings.h
        grid = out.reshape(n, n, n)
        tris = obj.triangles.astype(np.float64)   # (T,3,3) model space
        centre = np.array([8.0, 8.0, 8.0])
        # model -> world: w = R @ (m - c) + c  => rows: (m - c) @ R.T + c
        tris_w = (tris - centre) @ R.T + centre
        step = h * 0.5
        colors_cache: dict[int, np.ndarray] = {}
        for t in range(len(tris_w)):
            a, b, c = tris_w[t]
            e1, e2 = b - a, c - a
            la, lb = np.linalg.norm(e1), np.linalg.norm(e2)
            k = max(2, int(math.ceil(max(la, lb) / step)) + 1)
            us = np.linspace(0, 1, k)
            uu, vv = np.meshgrid(us, us, indexing="ij")
            m = (uu + vv) <= 1.0
            uu, vv = uu[m], vv[m]
            pts = a + uu[:, None] * e1 + vv[:, None] * e2
            ix = np.floor(pts[:, 0] / h).astype(np.int64)
            iy = np.floor(pts[:, 1] / h).astype(np.int64)
            iz = np.floor(pts[:, 2] / h).astype(np.int64)
            ok = (ix >= 0) & (ix < n) & (iy >= 0) & (iy < n) & (iz >= 0) & (iz < n)
            if not ok.any():
                continue
            texres = obj.textures[int(obj.tri_texture[t])]
            tex = self.textures.get(texres) if texres != MISSING_TEXTURE else None
            if tex is not None and obj.uvs is not None:
                uv = obj.uvs[t]
                tu = uv[0][0] + uu * (uv[1][0] - uv[0][0]) + vv * (uv[2][0] - uv[0][0])
                tv = uv[0][1] + uu * (uv[1][1] - uv[0][1]) + vv * (uv[2][1] - uv[0][1])
                Hh, Ww = tex.shape[:2]
                px = np.clip((tu * Ww).astype(np.int64) % Ww, 0, Ww - 1)
                py = np.clip((tv * Hh).astype(np.int64) % Hh, 0, Hh - 1)
                pix = tex[py, px]
                rgb = pix[:, :3]
                alpha = pix[:, 3]
            else:
                avg = self.textures.average_color(texres) if texres != MISSING_TEXTURE else None
                col = np.asarray(avg if avg else hashed_color(state.name), dtype=np.uint8)
                rgb = np.tile(col, (len(pts), 1))
                alpha = np.full(len(pts), 255, dtype=np.uint8)
            keep = ok & (alpha >= self.settings.alpha_threshold)
            if keep.any():
                grid[iy[keep], iz[keep], ix[keep]] = self.colors.index_of(rgb[keep])
        if self.settings.obj_interior_fill:
            _fill_interior(grid)

    def _stamp_extra_boxes(self, state: BlockState, out: np.ndarray) -> None:
        """Add block-entity geometry the JSON model leaves out (bell body, lectern book...)."""
        from ..assets.fallbacks import extra_boxes_for
        boxes = extra_boxes_for(state)
        if not boxes:
            return
        facing = state.properties.get("facing", "north")
        yrot = {"north": 0, "east": 90, "south": 180, "west": 270}.get(facing, 0)
        R = rotation_matrix("y", -yrot)
        centre = np.array([8.0, 8.0, 8.0])
        Q = (self._points - centre) @ R + centre
        for (lo, hi), tex, col in boxes:
            lo_a = np.asarray(lo, dtype=np.float64)
            hi_a = np.asarray(hi, dtype=np.float64)
            inside = np.all((Q >= lo_a) & (Q < hi_a), axis=1)
            if not inside.any():
                continue
            rgb = self.textures.average_color(tex) if tex else None
            if rgb is None:
                rgb = col if col is not None else hashed_color(state.name)
            out[inside] = self.colors.index_of_color(rgb)

    # ---- fallbacks & fluids -------------------------------------------------------------
    def _entity_block(self, state: BlockState) -> Optional[BlockPattern]:
        """Chests, beds, shulker boxes and heads stamped from the game's own ModelPart geometry."""
        try:
            from ..mobs.entity_blocks import entity_block
            from ..mobs.raster import MobPlacement, MobVoxelizer
        except ImportError:  # pragma: no cover
            return None
        spec = entity_block(state)
        if spec is None:
            return None
        mv = MobVoxelizer(self, min_units=self.settings.min_thickness, alpha_threshold=self.settings.alpha_threshold)
        raster = mv.rasterize(MobPlacement(spec.model, 0.0, 0.0, 0.0, 0.0, transform=(spec.A, spec.b)))
        pat = raster.cells.get((0, 0, 0))
        if pat is None or mv.missing:
            return None
        pat.kind = "fallback"
        pat.note = spec.note
        self.fallback_states[str(state)] = spec.note
        return pat

    def _fallback(self, state: BlockState, particle: Optional[str], reason: str) -> BlockPattern:
        eb = self._entity_block(state)
        if eb is not None:
            return eb
        shape = fallback_for(state, particle, reason)
        n = self.settings.resolution
        out = np.zeros(n * n * n, dtype=np.uint16)
        P = self._points
        R = rotation_matrix("y", -shape.y_rotation)
        centre = np.array([8.0, 8.0, 8.0])
        Q = (P - centre) @ R + centre
        if shape.elements and all(self.textures.get(f.texture) is not None
                                  for el in shape.elements for f in el.faces.values() if f.color is None):
            for el in shape.elements:
                self._stamp_element(state, el, Q, out, 0.0, None)
            self.fallback_states[str(state)] = shape.reason or reason
            return BlockPattern(out.reshape(n, n, n), kind="fallback", note=shape.reason or reason)
        for (lo, hi), tex, col in shape.boxes:
            lo_a = np.asarray(lo, dtype=np.float64)
            hi_a = np.asarray(hi, dtype=np.float64)
            size = hi_a - lo_a
            pad = np.where(size < self.settings.min_thickness, (self.settings.min_thickness - size) / 2, 0)
            inside = np.all((Q >= lo_a - pad) & (Q < hi_a + pad), axis=1)
            if not inside.any():
                continue
            rgb = None
            if tex and tex != MISSING_TEXTURE:
                rgb = self.textures.average_color(tex)
            if rgb is None:
                rgb = col if col is not None else hashed_color(state.name)
            out[inside] = self.colors.index_of_color(rgb)
        self.fallback_states[str(state)] = shape.reason or reason
        return BlockPattern(out.reshape(n, n, n), kind="fallback", note=shape.reason or reason)

    def _fluid(self, state: BlockState) -> BlockPattern:
        path = state.path
        texname = "minecraft:block/water_still" if "water" in path or "bubble" in path else "minecraft:block/lava_still"
        rgb = self.textures.average_color(texname)
        if rgb is None:
            rgb = (63, 118, 228) if "water" in path else (207, 88, 15)
        elif "water" in path or "bubble" in path:
            rgb = tuple(int(v) for v in apply_tint(np.asarray([rgb], dtype=np.uint8), tint_for(state, 0, self.settings.tint_overrides))[0])
        n = self.settings.resolution
        # source blocks are ~14/16 high, flowing levels lower
        try:
            level = int(state.properties.get("level", "0"))
        except ValueError:
            level = 0
        height_units = 14.0 if level == 0 or level >= 8 else 14.0 * (8 - level) / 8.0
        rows = max(1, int(round(height_units / self.settings.h)))
        pat = np.zeros((n, n, n), dtype=np.uint16)
        pat[:rows] = self.colors.index_of_color(rgb, translucent=("water" in path or "bubble" in path))
        return BlockPattern(pat, kind="fluid")

    # ---- stats -----------------------------------------------------------------------
    def _finish(self, p: BlockPattern) -> None:
        if p.is_empty:
            return
        col = p.colors
        occ = col != 0
        n = col.shape[0]
        # exposed = has an empty 6-neighbour inside the pattern or touches the block boundary
        exposed = np.zeros_like(occ)
        exposed[0] |= occ[0]; exposed[-1] |= occ[-1]
        exposed[:, 0] |= occ[:, 0]; exposed[:, -1] |= occ[:, -1]
        exposed[:, :, 0] |= occ[:, :, 0]; exposed[:, :, -1] |= occ[:, :, -1]
        exposed[1:] |= occ[1:] & ~occ[:-1]
        exposed[:-1] |= occ[:-1] & ~occ[1:]
        exposed[:, 1:] |= occ[:, 1:] & ~occ[:, :-1]
        exposed[:, :-1] |= occ[:, :-1] & ~occ[:, 1:]
        exposed[:, :, 1:] |= occ[:, :, 1:] & ~occ[:, :, :-1]
        exposed[:, :, :-1] |= occ[:, :, :-1] & ~occ[:, :, 1:]
        vals = col[exposed]
        if len(vals) == 0:
            vals = col[occ]
        u, c = np.unique(vals, return_counts=True)
        p.exposed_counts = {int(k): int(v) for k, v in zip(u, c)}
        p.dominant = int(u[np.argmax(c)])
        pal = self.colors.palette()
        rgb = pal[u].astype(np.float64)
        wsum = c.sum()
        avg = (rgb * c[:, None]).sum(0) / max(wsum, 1)
        p.avg_rgb = tuple(int(round(v)) for v in avg)


def _default_uv(fname: str, lo: np.ndarray, hi: np.ndarray) -> tuple[float, float, float, float]:
    if fname == "down":
        return (lo[0], 16 - hi[2], hi[0], 16 - lo[2])
    if fname == "up":
        return (lo[0], lo[2], hi[0], hi[2])
    if fname == "north":
        return (16 - hi[0], 16 - hi[1], 16 - lo[0], 16 - lo[1])
    if fname == "south":
        return (lo[0], 16 - hi[1], hi[0], 16 - lo[1])
    if fname == "west":
        return (lo[2], 16 - hi[1], hi[2], 16 - lo[1])
    return (16 - hi[2], 16 - hi[1], 16 - lo[2], 16 - lo[1])


def _fill_interior(grid: np.ndarray) -> None:
    """Fill voxels that cannot be reached from the block boundary through empty voxels."""
    occ = grid != 0
    if not occ.any():
        return
    empty = ~occ
    reach = np.zeros_like(empty)
    reach[0] = empty[0]; reach[-1] = empty[-1]
    reach[:, 0] |= empty[:, 0]; reach[:, -1] |= empty[:, -1]
    reach[:, :, 0] |= empty[:, :, 0]; reach[:, :, -1] |= empty[:, :, -1]
    while True:
        grown = reach.copy()
        grown[1:] |= reach[:-1]; grown[:-1] |= reach[1:]
        grown[:, 1:] |= reach[:, :-1]; grown[:, :-1] |= reach[:, 1:]
        grown[:, :, 1:] |= reach[:, :, :-1]; grown[:, :, :-1] |= reach[:, :, 1:]
        grown &= empty
        if np.array_equal(grown, reach):
            break
        reach = grown
    interior = empty & ~reach
    if interior.any():
        vals, counts = np.unique(grid[occ], return_counts=True)
        grid[interior] = vals[np.argmax(counts)]
