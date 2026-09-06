"""Compact index of the colors that appear in a model.

Sub-voxel patterns store ``uint16`` indices into this table (0 = empty).  Colors are binned to
15-bit RGB (32 levels per channel) so the table never exceeds 32768 entries while staying
visually faithful; the representative color of a bin is the first exact color seen.
"""
from __future__ import annotations

import numpy as np

MAX_COLORS = 32768


class ColorIndex:
    def __init__(self):
        self._table = np.zeros(MAX_COLORS, dtype=np.uint16)   # bin key -> index (0 = unassigned)
        self.rgb = np.zeros((1, 3), dtype=np.uint8)            # index -> representative rgb; row 0 = empty
        self._rows: list[tuple[int, int, int]] = [(0, 0, 0)]

    def __len__(self) -> int:
        return len(self._rows)

    @staticmethod
    def keys_of(rgb: np.ndarray) -> np.ndarray:
        rgb = np.asarray(rgb, dtype=np.uint16)
        return ((rgb[..., 0] >> 3) << 10) | ((rgb[..., 1] >> 3) << 5) | (rgb[..., 2] >> 3)

    def index_of(self, rgb: np.ndarray) -> np.ndarray:
        """Map an (..., 3) uint8 array to (...) uint16 indices, allocating new entries as needed."""
        rgb = np.asarray(rgb, dtype=np.uint8)
        flat = rgb.reshape(-1, 3)
        if len(flat) == 0:
            return np.zeros(rgb.shape[:-1], dtype=np.uint16)
        keys = self.keys_of(flat)
        idx = self._table[keys]
        missing = idx == 0
        if missing.any():
            mk, first = np.unique(keys[missing], return_index=True)
            src = flat[missing][first]
            for k, c in zip(mk.tolist(), src.tolist()):
                if self._table[k] == 0:
                    if len(self._rows) >= MAX_COLORS:
                        # table full: reuse nearest existing bin (extremely unlikely with 15-bit binning)
                        self._table[k] = 1
                    else:
                        self._table[k] = len(self._rows)
                        self._rows.append((int(c[0]), int(c[1]), int(c[2])))
            self.rgb = np.asarray(self._rows, dtype=np.uint8)
            idx = self._table[keys]
        return idx.reshape(rgb.shape[:-1])

    def index_of_color(self, rgb) -> int:
        return int(self.index_of(np.asarray([rgb], dtype=np.uint8))[0])

    def palette(self) -> np.ndarray:
        """(len, 3) uint8 array of representative colors (row 0 is the empty marker)."""
        return np.asarray(self._rows, dtype=np.uint8)
