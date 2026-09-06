"""Compact index of the colors that appear in a model.

Sub-voxel patterns store ``uint16`` indices into this table (0 = empty).  Colors are binned to
15-bit RGB (32 levels per channel) so the table stays small while staying visually faithful; the
representative color of a bin is the first exact color seen.

Translucent colors (glass, stained glass, ice, water...) live in a second bin table so that a
white glass texel never shares an index with white wool: they stay distinguishable all the way to
color planning, where they become their own "clear filament" material.
"""
from __future__ import annotations

import numpy as np

MAX_BINS = 32768
MAX_COLORS = 65535


class ColorIndex:
    def __init__(self):
        self._table = np.zeros(MAX_BINS, dtype=np.uint16)     # opaque bin key -> index (0 = unassigned)
        self._table_t = np.zeros(MAX_BINS, dtype=np.uint16)   # translucent bin key -> index
        self._rows: list[tuple[int, int, int]] = [(0, 0, 0)]  # index -> representative rgb; row 0 = empty
        self._trans: list[bool] = [False]
        self.rgb = np.zeros((1, 3), dtype=np.uint8)

    def __len__(self) -> int:
        return len(self._rows)

    @staticmethod
    def keys_of(rgb: np.ndarray) -> np.ndarray:
        rgb = np.asarray(rgb, dtype=np.uint16)
        return ((rgb[..., 0] >> 3) << 10) | ((rgb[..., 1] >> 3) << 5) | (rgb[..., 2] >> 3)

    def index_of(self, rgb: np.ndarray, translucent: bool = False) -> np.ndarray:
        """Map an (..., 3) uint8 array to (...) uint16 indices, allocating new entries as needed."""
        rgb = np.asarray(rgb, dtype=np.uint8)
        flat = rgb.reshape(-1, 3)
        if len(flat) == 0:
            return np.zeros(rgb.shape[:-1], dtype=np.uint16)
        table = self._table_t if translucent else self._table
        keys = self.keys_of(flat)
        idx = table[keys]
        missing = idx == 0
        if missing.any():
            mk, first = np.unique(keys[missing], return_index=True)
            src = flat[missing][first]
            for k, c in zip(mk.tolist(), src.tolist()):
                if table[k] == 0:
                    if len(self._rows) >= MAX_COLORS:
                        table[k] = 1
                    else:
                        table[k] = len(self._rows)
                        self._rows.append((int(c[0]), int(c[1]), int(c[2])))
                        self._trans.append(bool(translucent))
            self.rgb = np.asarray(self._rows, dtype=np.uint8)
            idx = table[keys]
        return idx.reshape(rgb.shape[:-1])

    def index_of_color(self, rgb, translucent: bool = False) -> int:
        return int(self.index_of(np.asarray([rgb], dtype=np.uint8), translucent)[0])

    def palette(self) -> np.ndarray:
        """(len, 3) uint8 array of representative colors (row 0 is the empty marker)."""
        return np.asarray(self._rows, dtype=np.uint8)

    def is_translucent(self) -> np.ndarray:
        """(len,) bool array: which color indices are translucent."""
        return np.asarray(self._trans, dtype=bool)

    def translucent_indices(self) -> set[int]:
        return {i for i, t in enumerate(self._trans) if t}
