"""Color utilities: hex parsing, sRGB <-> CIELAB, CIEDE2000 distance (vectorized with numpy)."""
from __future__ import annotations

import re
from typing import Iterable, Sequence

import numpy as np

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})$")


def parse_hex(value: str) -> tuple[int, int, int]:
    """Parse '#RRGGBB', 'RRGGBB', '#RRGGBBAA' (alpha ignored) or '#RGB' into an (r, g, b) tuple.

    Raises ValueError for anything else.
    """
    if not isinstance(value, str):
        raise ValueError(f"not a color string: {value!r}")
    m = _HEX_RE.match(value.strip())
    if not m:
        raise ValueError(f"invalid hex color: {value!r}")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def to_hex(rgb: Sequence[float]) -> str:
    r, g, b = (int(max(0, min(255, round(float(c))))) for c in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.asarray(c, dtype=np.float64), 0.0, 1.0)
    s = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return s * 255.0


_M_RGB2XYZ = np.array(
    [[0.4124564, 0.3575761, 0.1804375],
     [0.2126729, 0.7151522, 0.0721750],
     [0.0193339, 0.1191920, 0.9503041]]
)
_WHITE = np.array([0.95047, 1.0, 1.08883])  # D65


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert an (..., 3) array of 0-255 sRGB values to CIELAB (D65)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    lin = srgb_to_linear(rgb)
    xyz = lin @ _M_RGB2XYZ.T
    xyz = xyz / _WHITE
    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float64)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    eps = 216 / 24389
    kappa = 24389 / 27

    def finv(t):
        t3 = t ** 3
        return np.where(t3 > eps, t3, (116 * t - 16) / kappa)

    xyz = np.stack([finv(fx), finv(fy), finv(fz)], axis=-1) * _WHITE
    lin = xyz @ np.linalg.inv(_M_RGB2XYZ).T
    return linear_to_srgb(lin)


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 color difference, broadcasting over leading dimensions.

    lab1: (..., 3), lab2: (..., 3) -> (...)
    """
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    kL = kC = kH = 1.0
    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where((C1p * C2p) == 0, 0.0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    hsum = h1p + h2p
    hdiff = np.abs(h1p - h2p)
    hbp = np.where(
        (C1p * C2p) == 0, hsum,
        np.where(hdiff <= 180, hsum / 2,
                 np.where(hsum < 360, (hsum + 360) / 2, (hsum - 360) / 2)),
    )
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dtheta = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    RC = 2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    SL = 1 + (0.015 * (Lbp - 50) ** 2) / np.sqrt(20 + (Lbp - 50) ** 2)
    SC = 1 + 0.045 * Cbp
    SH = 1 + 0.015 * Cbp * T
    RT = -np.sin(np.radians(2 * dtheta)) * RC
    return np.sqrt((dLp / (kL * SL)) ** 2 + (dCp / (kC * SC)) ** 2 + (dHp / (kH * SH)) ** 2
                   + RT * (dCp / (kC * SC)) * (dHp / (kH * SH)))


def nearest_palette_index(colors_rgb: np.ndarray, palette_rgb: np.ndarray, metric: str = "ciede2000") -> np.ndarray:
    """For each color (N,3) return the index of the nearest palette entry (M,3).

    metric: 'ciede2000' (perceptual, default), 'lab' (euclidean in Lab) or 'rgb'.
    """
    colors_rgb = np.asarray(colors_rgb, dtype=np.float64).reshape(-1, 3)
    palette_rgb = np.asarray(palette_rgb, dtype=np.float64).reshape(-1, 3)
    if len(palette_rgb) == 0:
        raise ValueError("empty palette")
    if len(colors_rgb) == 0:
        return np.zeros(0, dtype=np.int64)
    if metric == "rgb":
        d = ((colors_rgb[:, None, :] - palette_rgb[None, :, :]) ** 2).sum(-1)
        return np.argmin(d, axis=1)
    lab_c = rgb_to_lab(colors_rgb)
    lab_p = rgb_to_lab(palette_rgb)
    out = np.empty(len(colors_rgb), dtype=np.int64)
    step = 4096  # bound memory for large inputs
    for i in range(0, len(colors_rgb), step):
        chunk = lab_c[i:i + step]
        if metric == "lab":
            d = ((chunk[:, None, :] - lab_p[None, :, :]) ** 2).sum(-1)
        else:
            d = ciede2000(chunk[:, None, :], lab_p[None, :, :])
        out[i:i + step] = np.argmin(d, axis=1)
    return out


def luminance(rgb: Iterable[float]) -> float:
    r, g, b = (float(c) for c in list(rgb)[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_text_color(rgb: Iterable[float]) -> str:
    """Return '#000000' or '#FFFFFF' for text drawn on top of the given color."""
    return "#000000" if luminance(rgb) > 140 else "#FFFFFF"
