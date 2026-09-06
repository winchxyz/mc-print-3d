"""Litematica (.litematic) loader – multi-region, tightly packed block states."""
from __future__ import annotations

import logging
import math

import numpy as np

from .. import nbt
from .entities import entity_to_compound, resolve_entities
from .base import AIR, BlockState, PaletteBuilder, Schematic

log = logging.getLogger(__name__)


def is_litematic(root: nbt.Compound) -> bool:
    return isinstance(root.get("Regions"), nbt.Compound)


def _modernize(name: str, props: dict, data_version):
    try:
        from .renames import modernize_block
        return modernize_block(name, props, data_version)
    except Exception:
        return name, props


def _vec(c: nbt.Compound) -> tuple[int, int, int]:
    return c.get_int("x"), c.get_int("y"), c.get_int("z")


def load_litematic(root: nbt.Compound, source: str = "") -> Schematic:
    regions = root.get_compound("Regions")
    if not regions:
        raise ValueError("Litematica file has no regions")
    data_version = root.get_int("MinecraftDataVersion", 0) or None
    meta = root.get_compound("Metadata")

    # Compute global bounds over all regions
    boxes = []
    for rname, reg in regions.items():
        if not isinstance(reg, nbt.Compound):
            continue
        px, py, pz = _vec(reg.get_compound("Position"))
        sx, sy, sz = _vec(reg.get_compound("Size"))
        minx = px if sx >= 0 else px + sx + 1
        miny = py if sy >= 0 else py + sy + 1
        minz = pz if sz >= 0 else pz + sz + 1
        boxes.append((rname, reg, (minx, miny, minz), (abs(sx), abs(sy), abs(sz))))
    if not boxes:
        raise ValueError("Litematica file has no readable regions")
    gx0 = min(b[2][0] for b in boxes)
    gy0 = min(b[2][1] for b in boxes)
    gz0 = min(b[2][2] for b in boxes)
    gx1 = max(b[2][0] + b[3][0] for b in boxes)
    gy1 = max(b[2][1] + b[3][1] for b in boxes)
    gz1 = max(b[2][2] + b[3][2] for b in boxes)
    width, height, length = gx1 - gx0, gy1 - gy0, gz1 - gz0

    pb = PaletteBuilder()
    grid = np.zeros((height, length, width), dtype=np.int32)
    block_entities = []
    warnings = []
    entities = []
    for rname, reg, (minx, miny, minz), (sx, sy, sz) in boxes:
        ents = reg.get_list("Entities")
        if ents:
            px, py, pz = _vec(reg.get_compound("Position"))
            # Litematica stores positions relative to the region's Position corner (not its min corner)
            entities.extend(resolve_entities(ents, (width, height, length), [(float(gx0 - px), float(gy0 - py), float(gz0 - pz))], warnings))
        pal_list = reg.get_list("BlockStatePalette")
        states = []
        for entry in pal_list:
            if not isinstance(entry, nbt.Compound):
                states.append(AIR)
                continue
            name = entry.get_str("Name", "minecraft:air")
            props = {str(k): str(v) for k, v in entry.get_compound("Properties").items()}
            name, props = _modernize(name, props, data_version)
            states.append(BlockState.make(name, props))
        if not states:
            states = [AIR]
        remap = np.array([pb.add(s) for s in states], dtype=np.int32)
        n = sx * sy * sz
        longs = reg.get_array("BlockStates")
        if longs is None or n == 0:
            continue
        bits = max(2, math.ceil(math.log2(len(states)))) if len(states) > 1 else 2
        try:
            idx = nbt.unpack_bits(longs, bits, n, padded=False)
        except nbt.NBTError as exc:
            warnings.append(f"region {rname}: {exc}")
            continue
        idx = np.clip(idx, 0, len(states) - 1)
        sub = remap[idx].reshape(sy, sz, sx)
        ox, oy, oz = minx - gx0, miny - gy0, minz - gz0
        target = grid[oy:oy + sy, oz:oz + sz, ox:ox + sx]
        # later regions override earlier ones only where non-air
        mask = sub != 0
        target[mask] = sub[mask]
        tes = reg.get_list("TileEntities")
        if tes:
            block_entities.extend(list(tes))

    s = Schematic(width=width, height=height, length=length, palette=pb.states, blocks=grid,
                  format="litematica", source=source, data_version=data_version,
                  name=meta.get_str("Name", ""), author=meta.get_str("Author", ""), offset=(gx0, gy0, gz0))
    s.metadata = {
        "description": meta.get_str("Description", ""),
        "region_count": len(boxes),
        "regions": [b[0] for b in boxes],
        "total_blocks": meta.get_int("TotalBlocks", 0),
        "version": root.get_int("Version", 0),
    }
    s.block_entities = block_entities
    s.warnings = warnings
    s.entities = entities
    return s


def save_litematic(schem: Schematic, path: str) -> None:
    """Write a single-region .litematic (used for tests/fixtures)."""
    pal = nbt.List([], nbt.TAG_COMPOUND)
    for st in schem.palette:
        c = nbt.Compound({"Name": nbt.String(st.name)})
        if st.props:
            c["Properties"] = nbt.Compound({k: nbt.String(v) for k, v in st.props})
        pal.append(c)
    bits = max(2, math.ceil(math.log2(len(schem.palette)))) if len(schem.palette) > 1 else 2
    flat = schem.blocks.reshape(-1)
    longs = nbt.pack_bits(flat, bits, padded=False).view(nbt.LongArray)
    region = nbt.Compound({
        "Position": nbt.Compound({"x": nbt.Int(0), "y": nbt.Int(0), "z": nbt.Int(0)}),
        "Size": nbt.Compound({"x": nbt.Int(schem.width), "y": nbt.Int(schem.height), "z": nbt.Int(schem.length)}),
        "BlockStatePalette": pal,
        "BlockStates": longs,
        "TileEntities": nbt.List([], nbt.TAG_COMPOUND),
        "Entities": nbt.List([entity_to_compound(e) for e in schem.entities], nbt.TAG_COMPOUND),
        "PendingBlockTicks": nbt.List([], nbt.TAG_COMPOUND),
        "PendingFluidTicks": nbt.List([], nbt.TAG_COMPOUND),
    })
    root = nbt.Compound({
        "MinecraftDataVersion": nbt.Int(schem.data_version or 3953),
        "Version": nbt.Int(6),
        "SubVersion": nbt.Int(1),
        "Metadata": nbt.Compound({
            "Name": nbt.String(schem.name or "mc-print-3d"),
            "Author": nbt.String(schem.author or "mc-print-3d"),
            "Description": nbt.String(""),
            "EnclosingSize": nbt.Compound({"x": nbt.Int(schem.width), "y": nbt.Int(schem.height), "z": nbt.Int(schem.length)}),
            "TotalBlocks": nbt.Int(schem.block_count),
            "TotalVolume": nbt.Int(schem.volume),
            "RegionCount": nbt.Int(1),
            "TimeCreated": nbt.Long(0),
            "TimeModified": nbt.Long(0),
        }),
        "Regions": nbt.Compound({"main": region}),
    })
    nbt.dump(root, path, name="")
