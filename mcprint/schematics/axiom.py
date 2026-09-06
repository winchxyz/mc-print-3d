"""Axiom blueprint (.bp) loader – best effort.

File layout (Axiom mod, 2023+)::

    int32 magic (0x0AE5BB36)
    int32 header_length ; uncompressed NBT compound (Name, Author, Tags, BlockCount, ...)
    int32 thumbnail_length ; PNG bytes
    int32 blockdata_length ; gzip NBT: { BlockRegion: [ {X, Y, Z, BlockStates: {palette: [...], data: long[]}} ... ] }

Each block region is a 16x16x16 section stored like a chunk section palette container
(padded 64-bit packing, bits = max(4, ceil(log2(palette size))), index = (y*16+z)*16+x).
"""
from __future__ import annotations

import logging
import math
import struct

import numpy as np

from .. import nbt
from .base import AIR, BlockState, PaletteBuilder, Schematic

log = logging.getLogger(__name__)

AXIOM_MAGIC = 0x0AE5BB36


def is_axiom_bytes(data: bytes) -> bool:
    return len(data) >= 4 and struct.unpack(">i", data[:4])[0] == AXIOM_MAGIC


def _modernize(name: str, props: dict, data_version):
    try:
        from .renames import modernize_block
        return modernize_block(name, props, data_version)
    except Exception:
        return name, props


def load_axiom_bytes(data: bytes, source: str = "") -> Schematic:
    if not is_axiom_bytes(data):
        raise ValueError("not an Axiom blueprint (bad magic)")
    pos = 4

    def read_block() -> bytes:
        nonlocal pos
        if pos + 4 > len(data):
            raise ValueError("truncated Axiom blueprint")
        (n,) = struct.unpack(">i", data[pos:pos + 4])
        pos += 4
        if n < 0 or pos + n > len(data):
            raise ValueError("truncated Axiom blueprint")
        chunk = data[pos:pos + n]
        pos += n
        return chunk

    header_raw = read_block()
    _thumb = read_block()
    block_raw = read_block()
    try:
        _, header = nbt.loads(header_raw, little_endian=False)
    except Exception:
        header = nbt.Compound()
    _, body = nbt.loads(block_raw, little_endian=False)
    data_version = header.get_int("DataVersion", 0) or body.get_int("DataVersion", 0) or None

    regions = body.get_list("BlockRegion")
    if not regions:
        raise ValueError("Axiom blueprint has no BlockRegion data")
    sections = []
    for reg in regions:
        if not isinstance(reg, nbt.Compound):
            continue
        cx, cy, cz = reg.get_int("X"), reg.get_int("Y"), reg.get_int("Z")
        bs = reg.get_compound("BlockStates")
        pal = bs.get_list("palette")
        states = []
        for entry in pal:
            if not isinstance(entry, nbt.Compound):
                states.append(AIR)
                continue
            name = entry.get_str("Name", "minecraft:air")
            props = {str(k): str(v) for k, v in entry.get_compound("Properties").items()}
            name, props = _modernize(name, props, data_version)
            states.append(BlockState.make(name, props))
        if not states:
            states = [AIR]
        longs = bs.get_array("data")
        if longs is None or len(states) == 1:
            idx = np.zeros(4096, dtype=np.int64)
        else:
            bits = max(4, math.ceil(math.log2(len(states))))
            idx = nbt.unpack_bits(longs, bits, 4096, padded=True)
        idx = np.clip(idx, 0, len(states) - 1)
        sections.append((cx, cy, cz, states, idx.reshape(16, 16, 16)))  # [y, z, x]
    if not sections:
        raise ValueError("Axiom blueprint has no readable sections")

    minx = min(s[0] for s in sections) * 16
    miny = min(s[1] for s in sections) * 16
    minz = min(s[2] for s in sections) * 16
    maxx = max(s[0] for s in sections) * 16 + 16
    maxy = max(s[1] for s in sections) * 16 + 16
    maxz = max(s[2] for s in sections) * 16 + 16
    width, height, length = maxx - minx, maxy - miny, maxz - minz
    pb = PaletteBuilder()
    grid = np.zeros((height, length, width), dtype=np.int32)
    for cx, cy, cz, states, idx in sections:
        remap = np.asarray([pb.add(s) for s in states], dtype=np.int32)
        ox, oy, oz = cx * 16 - minx, cy * 16 - miny, cz * 16 - minz
        grid[oy:oy + 16, oz:oz + 16, ox:ox + 16] = remap[idx]

    s = Schematic(width=width, height=height, length=length, palette=pb.states, blocks=grid,
                  format="axiom", source=source, data_version=data_version,
                  name=header.get_str("Name", ""), author=header.get_str("Author", ""))
    s.metadata = {k: str(v) for k, v in header.items() if not isinstance(v, (nbt.Compound, list, np.ndarray))}
    return s.cropped()
