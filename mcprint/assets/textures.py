"""Texture loading (PNG -> RGBA numpy), animation handling, average colors."""
from __future__ import annotations

import io
import json
import logging
from typing import Optional

import numpy as np

from .pack import AssetStack, texture_path

log = logging.getLogger(__name__)

MISSING_TEXTURE = "__missing__"


def _decode_png(raw: bytes) -> Optional[np.ndarray]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required to decode textures") from exc
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGBA")
            arr = np.asarray(im, dtype=np.uint8).copy()
        return arr
    except Exception as exc:
        log.debug("png decode failed: %s", exc)
        return None


class TextureLoader:
    """Loads textures from an :class:`AssetStack` with caching; returns the first animation frame."""

    def __init__(self, stack: AssetStack):
        self.stack = stack
        self._cache: dict[str, Optional[np.ndarray]] = {}
        self._avg_cache: dict[str, Optional[tuple[int, int, int]]] = {}
        self._dil_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._relief_cache: dict[str, Optional[np.ndarray]] = {}
        self.missing: set[str] = set()

    def get(self, resource: str) -> Optional[np.ndarray]:
        """RGBA uint8 array (H, W, 4) or None if the texture does not exist."""
        if resource in self._cache:
            return self._cache[resource]
        arr = self._load(resource)
        self._cache[resource] = arr
        if arr is None:
            self.missing.add(resource)
        return arr

    def _load(self, resource: str) -> Optional[np.ndarray]:
        path = texture_path(resource)
        raw = self.stack.read(path)
        if raw is None:
            # legacy packs: 'blocks/stone' vs 'block/stone'
            alt = None
            if "/blocks/" in path:
                alt = path.replace("/blocks/", "/block/")
            elif "/block/" in path:
                alt = path.replace("/block/", "/blocks/")
            if alt:
                raw = self.stack.read(alt)
                if raw is not None:
                    path = alt
        if raw is None:
            return None
        arr = _decode_png(raw)
        if arr is None:
            return None
        h, w = arr.shape[:2]
        frame_h = None
        meta_raw = self.stack.read(path + ".mcmeta")
        if meta_raw is not None:
            try:
                meta = json.loads(meta_raw.decode("utf-8-sig", errors="replace"))
                anim = meta.get("animation")
                if isinstance(anim, dict):
                    fw = int(anim.get("width", w))
                    fh = int(anim.get("height", fw if fw else w))
                    if fh > 0 and fh < h:
                        frame_h = fh
            except Exception:
                pass
        if frame_h is None and h > w and w > 0 and h % w == 0:
            frame_h = w  # animation strip without (readable) mcmeta
        if frame_h:
            arr = arr[:frame_h]
        return arr

    def average_color(self, resource: str) -> Optional[tuple[int, int, int]]:
        if resource in self._avg_cache:
            return self._avg_cache[resource]
        arr = self.get(resource)
        val: Optional[tuple[int, int, int]] = None
        if arr is not None:
            a = arr[..., 3].astype(np.float64) / 255.0
            wsum = a.sum()
            if wsum > 0:
                rgb = (arr[..., :3].astype(np.float64) * a[..., None]).sum(axis=(0, 1)) / wsum
                val = tuple(int(round(c)) for c in rgb)  # type: ignore[assignment]
        self._avg_cache[resource] = val
        return val

    def dilated(self, resource: str, threshold: int, radius: int) -> Optional[tuple[np.ndarray, np.ndarray]]:
        """(rgb, opaque_mask) with the opaque area grown by ``radius`` texels (4-neighbourhood steps).

        Grown pixels take the color of the neighbour they were grown from, so thin stems and
        wires keep their color while becoming printable.
        """
        key = f"{resource}|{threshold}|{radius}"
        cached = self._dil_cache.get(key)
        if cached is not None:
            return cached
        arr = self.get(resource)
        if arr is None:
            return None
        rgb = arr[..., :3].copy()
        mask = arr[..., 3] >= threshold
        for _ in range(max(0, radius)):
            grown = mask.copy()
            new_rgb = rgb.copy()
            for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
                m2 = np.roll(mask, shift, axis=axis)
                c2 = np.roll(rgb, shift, axis=axis)
                if axis == 0:
                    if shift == 1:
                        m2[0] = False
                    else:
                        m2[-1] = False
                else:
                    if shift == 1:
                        m2[:, 0] = False
                    else:
                        m2[:, -1] = False
                take = m2 & ~grown
                new_rgb[take] = c2[take]
                grown |= take
            mask, rgb = grown, new_rgb
        out = (rgb, mask)
        self._dil_cache[key] = out
        return out

    # ---- surface relief --------------------------------------------------------------
    def relief_map(self, resource: str, mode: str = "auto") -> Optional[np.ndarray]:
        """Per-texel recess depth as a float32 (H, W) array in 0..1 (1 = full relief depth), or None.

        Sources, in order:
        1. Real height maps shipped by PBR resource packs: ``<texture>_h.png`` (grey = height) or the
           alpha channel of ``<texture>_n.png`` (LabPBR convention, 255 = top surface).
        2. Pattern analysis of the color texture: the luminance histogram is split with Otsu's
           method and the *minority* class is treated as recessed.  This is what makes brick mortar
           (light, ~25 % of the texels), stone-brick cracks and plank seams (dark) all sink in while
           noisy textures such as plain stone or sand stay flat.

        ``mode``: 'auto' (height map, else pattern), 'heightmap' (only real height maps),
        'pattern' (minority recessed), 'dark' (darker texels recessed), 'light' (lighter recessed).
        """
        key = f"{resource}|{mode}"
        if key in self._relief_cache:
            return self._relief_cache[key]
        result: Optional[np.ndarray] = None
        try:
            if mode in ("auto", "heightmap"):
                result = self._heightmap_relief(resource)
            if result is None and mode in ("auto", "pattern", "dark", "light"):
                result = self._pattern_relief(resource, mode)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("relief map %s failed: %s", resource, exc)
            result = None
        self._relief_cache[key] = result
        return result

    def _heightmap_relief(self, resource: str) -> Optional[np.ndarray]:
        base = self.get(resource)
        if base is None:
            return None
        H, W = base.shape[:2]
        for suffix, channel in (("_h", "lum"), ("_n", "alpha")):
            arr = self.get(resource + suffix)
            if arr is None:
                continue
            if arr.shape[0] != H or arr.shape[1] != W:
                continue
            if channel == "alpha":
                h = arr[..., 3].astype(np.float32) / 255.0
                if h.min() > 0.99:  # no height information stored
                    continue
            else:
                h = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]).astype(np.float32) / 255.0
            depth = 1.0 - h
            depth -= depth.min()
            if depth.max() < 0.05:
                continue
            depth /= depth.max()
            depth[base[..., 3] == 0] = 0.0
            return depth.astype(np.float32)
        return None

    def _pattern_relief(self, resource: str, mode: str) -> Optional[np.ndarray]:
        """Find groove-like structures (mortar, seams, cracks) in a color texture.

        Each texel is compared with the mean of its neighbourhood (wrap-around, ~5 texels for a
        16 px texture).  Otsu's threshold on the magnitude of that difference separates outliers
        from the shaded body of the material; outliers of the dominant sign (dark by preference)
        are the candidate grooves.  They are accepted only if they cover 4-45 % of the texture,
        have real contrast and form connected, line-like structures - so brick mortar, plank seams
        and stone-brick cracks sink in while the speckle of plain stone, dirt or wool stays flat.
        """
        arr = self.get(resource)
        if arr is None:
            return None
        rgb = arr[..., :3].astype(np.float32)
        alpha = arr[..., 3]
        lum = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0
        valid = alpha > 0
        if valid.sum() < 16 or lum.shape[0] < 4 or lum.shape[1] < 4:
            return None
        H, W = lum.shape
        k = max(3, int(round(W / 16 * 5)) | 1)
        d = lum - _wrap_box_mean(lum, k)
        mag = np.abs(d)
        peak = float(mag.max())
        if peak < 1e-4:
            return None
        t = _otsu_threshold((mag / peak)[valid])
        if t is None:
            return None
        outlier = (mag >= t * peak) & valid
        if not outlier.any():
            return None
        n_dark = int((outlier & (d < 0)).sum())
        n_light = int((outlier & (d > 0)).sum())
        if mode == "dark":
            recessed = outlier & (d < 0)
        elif mode == "light":
            recessed = outlier & (d > 0)
        else:
            recessed = outlier & ((d < 0) if n_dark >= 0.8 * n_light else (d > 0))
        if not recessed.any():
            return None
        frac = float(recessed.sum()) / float(valid.sum())
        strength = float(mag[outlier].mean())
        if mode in ("auto", "pattern"):
            if not (0.04 <= frac <= 0.45) or strength < 0.065:
                return None
            if _connectivity(recessed) < 0.6 or _line_likeness(recessed) < 0.6:
                return None
        else:
            if not (0.02 <= frac <= 0.6) or strength < 0.04:
                return None
        depth = np.zeros(lum.shape, dtype=np.float32)
        depth[recessed] = 1.0
        return depth

    def alpha_stats(self, resource: str) -> tuple[float, float]:
        """(fraction fully transparent, fraction semi-transparent) – used to classify cutout vs translucent."""
        arr = self.get(resource)
        if arr is None:
            return 0.0, 0.0
        a = arr[..., 3]
        n = a.size or 1
        return float((a == 0).sum()) / n, float(((a > 0) & (a < 255)).sum()) / n


def _wrap_box_mean(a: np.ndarray, k: int) -> np.ndarray:
    """Mean over a k x k window with wrap-around edges (textures tile)."""
    out = np.zeros_like(a, dtype=np.float32)
    r = k // 2
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out += np.roll(np.roll(a, dy, axis=0), dx, axis=1)
    return out / float(k * k)


def _connectivity(mask: np.ndarray) -> float:
    """Fraction of set pixels having at least two set 8-neighbours (wrap-around)."""
    nb = np.zeros(mask.shape, dtype=np.int32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                nb += np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return float((nb[mask] >= 2).mean()) if mask.any() else 0.0


def _line_likeness(mask: np.ndarray) -> float:
    """Fraction of set pixels lying in a straight run of >= 3 (horizontal, vertical or diagonal)."""
    inrun = np.zeros_like(mask)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
        b = np.roll(np.roll(mask, -dy, axis=0), -dx, axis=1)
        a2 = np.roll(np.roll(a, dy, axis=0), dx, axis=1)
        b2 = np.roll(np.roll(b, -dy, axis=0), -dx, axis=1)
        inrun |= mask & ((a & b) | (a & a2) | (b & b2))
    return float(inrun[mask].mean()) if mask.any() else 0.0


def _otsu_threshold(values: np.ndarray) -> Optional[float]:
    """Otsu's threshold on values in 0..1 (None when the data has no spread)."""
    v = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
    if v.size == 0 or float(v.max() - v.min()) < 1e-3:
        return None
    hist, edges = np.histogram(v, bins=64, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return None
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = total - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1e-12)
    m1 = (np.sum(hist * centers) - np.cumsum(hist * centers)) / np.maximum(w1, 1e-12)
    between = w0 * w1 * (m0 - m1) ** 2
    between[(w0 == 0) | (w1 == 0)] = -1
    best = float(between.max())
    if best <= 0:
        return None
    plateau = np.nonzero(between >= best - 1e-9 * max(best, 1.0))[0]
    i = int(plateau[len(plateau) // 2])   # middle of a flat maximum (well separated modes)
    return float(edges[i + 1])


def hashed_color(name: str) -> tuple[int, int, int]:
    """Deterministic, reasonably bright color for unknown blocks (so they stay visible)."""
    import hashlib
    h = hashlib.md5(name.encode("utf-8")).digest()
    r, g, b = h[0], h[1], h[2]
    # keep it mid-bright
    return (80 + r * 150 // 255, 80 + g * 150 // 255, 80 + b * 150 // 255)
