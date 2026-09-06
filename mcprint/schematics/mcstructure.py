"""Bedrock Edition structure (.mcstructure) loader – little-endian NBT, Bedrock block names."""
from __future__ import annotations

import logging

import numpy as np

from .. import nbt
from .entities import resolve_entities
from .base import AIR, BlockState, PaletteBuilder, Schematic

log = logging.getLogger(__name__)


def is_mcstructure(root: nbt.Compound) -> bool:
    return isinstance(root.get("structure"), nbt.Compound) and isinstance(root.get("size"), list)


def _bedrock_to_java(name: str, states: dict):
    try:
        from .bedrock_names import bedrock_to_java
        return bedrock_to_java(name, states)
    except Exception:
        return name, {str(k): str(v) for k, v in states.items()}


def load_mcstructure(root: nbt.Compound, source: str = "") -> Schematic:
    size = [int(v) for v in root.get_list("size")]
    if len(size) != 3:
        raise ValueError("mcstructure has no size")
    width, height, length = size
    structure = root.get_compound("structure")
    layers = structure.get_list("block_indices")
    if not layers:
        raise ValueError("mcstructure has no block_indices")
    palette_root = structure.get_compound("palette")
    default = palette_root.get_compound("default") or (next(iter(palette_root.values()), nbt.Compound()) if palette_root else nbt.Compound())
    block_palette = default.get_list("block_palette") if isinstance(default, nbt.Compound) else nbt.List()

    pb = PaletteBuilder()
    remap = []
    for entry in block_palette:
        if not isinstance(entry, nbt.Compound):
            remap.append(0)
            continue
        name = entry.get_str("name", "minecraft:air")
        states = {}
        for k, v in entry.get_compound("states").items():
            if isinstance(v, (nbt.Byte,)):
                states[str(k)] = int(v)
            elif isinstance(v, (int, float)):
                states[str(k)] = int(v) if isinstance(v, int) else float(v)
            else:
                states[str(k)] = str(v)
        jname, jprops = _bedrock_to_java(name, states)
        remap.append(pb.add(BlockState.make(jname, jprops)))
    remap_arr = np.asarray(remap or [0], dtype=np.int32)

    n = width * height * length
    grid = np.zeros((height, length, width), dtype=np.int32)
    # Layer 0 = blocks, layer 1 = secondary (waterlogging).  Order: x major, then y, then z.
    for li, layer in enumerate(layers[:2]):
        idx = np.asarray([int(v) for v in layer], dtype=np.int64)
        if len(idx) < n:
            idx = np.concatenate([idx, -np.ones(n - len(idx), dtype=np.int64)])
        idx = idx[:n].reshape(width, height, length)   # [x, y, z]
        idx = np.transpose(idx, (1, 2, 0))             # -> [y, z, x]
        valid = (idx >= 0) & (idx < len(remap_arr))
        mapped = np.where(valid, remap_arr[np.clip(idx, 0, len(remap_arr) - 1)], 0)
        if li == 0:
            grid = mapped.astype(np.int32)
        else:
            # only fill where the primary layer is air
            fill = (grid == 0) & (mapped != 0)
            grid[fill] = mapped[fill]

    origin = root.get_list("structure_world_origin")
    off = tuple(int(v) for v in origin) if len(origin) == 3 else (0, 0, 0)
    s = Schematic(width=width, height=height, length=length, palette=pb.states, blocks=grid,
                  format="mcstructure", source=source, offset=off)
    s.metadata = {"format_version": root.get_int("format_version", 0)}
    ents = structure.get_list("entities")
    if ents:
        s.entities = resolve_entities(ents, (width, height, length), [tuple(float(v) for v in off), (0.0, 0.0, 0.0)], s.warnings)
    return s
