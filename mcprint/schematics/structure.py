"""Vanilla structure block / template (.nbt) loader – also used by Create/KubeJS ponder scenes, datapacks..."""
from __future__ import annotations

import logging

import numpy as np

from .. import nbt
from .entities import entity_to_compound, resolve_entities
from .base import AIR, BlockState, PaletteBuilder, Schematic

log = logging.getLogger(__name__)


def is_structure(root: nbt.Compound) -> bool:
    return isinstance(root.get("size"), list) and ("palette" in root or "palettes" in root) and isinstance(root.get("blocks"), list)


def _modernize(name: str, props: dict, data_version):
    try:
        from .renames import modernize_block
        return modernize_block(name, props, data_version)
    except Exception:
        return name, props


def load_structure(root: nbt.Compound, source: str = "") -> Schematic:
    size = [int(v) for v in root.get_list("size")]
    if len(size) != 3:
        raise ValueError("structure file has no size")
    width, height, length = size
    data_version = root.get_int("DataVersion", 0) or None
    palette_tag = root.get("palette")
    if palette_tag is None:
        pals = root.get_list("palettes")
        palette_tag = pals[0] if pals else nbt.List()
    pb = PaletteBuilder()
    remap = []
    for entry in palette_tag:
        if not isinstance(entry, nbt.Compound):
            remap.append(0)
            continue
        name = entry.get_str("Name", "minecraft:air")
        props = {str(k): str(v) for k, v in entry.get_compound("Properties").items()}
        name, props = _modernize(name, props, data_version)
        remap.append(pb.add(BlockState.make(name, props)))
    remap_arr = np.asarray(remap or [0], dtype=np.int32)

    grid = np.zeros((height, length, width), dtype=np.int32)
    blocks = root.get_list("blocks")
    n = len(blocks)
    if n:
        pos = np.zeros((n, 3), dtype=np.int64)
        st = np.zeros(n, dtype=np.int64)
        block_entities = []
        for i, b in enumerate(blocks):
            if not isinstance(b, nbt.Compound):
                continue
            p = b.get_list("pos")
            if len(p) == 3:
                pos[i] = (int(p[0]), int(p[1]), int(p[2]))
            st[i] = b.get_int("state", 0)
            if "nbt" in b:
                block_entities.append(b)
        valid = ((pos[:, 0] >= 0) & (pos[:, 0] < width) & (pos[:, 1] >= 0) & (pos[:, 1] < height)
                 & (pos[:, 2] >= 0) & (pos[:, 2] < length) & (st >= 0) & (st < len(remap_arr)))
        pos, st = pos[valid], st[valid]
        grid[pos[:, 1], pos[:, 2], pos[:, 0]] = remap_arr[st]
    else:
        block_entities = []

    s = Schematic(width=width, height=height, length=length, palette=pb.states, blocks=grid,
                  format="structure", source=source, data_version=data_version)
    s.block_entities = block_entities
    ents = root.get_list("entities")
    if ents:
        def _pos(tag):
            p = tag.get_list("pos")
            return [float(v) for v in p] if len(p) == 3 else None
        tagged = []
        for t in ents:
            if not isinstance(t, nbt.Compound):
                continue
            c = nbt.Compound(t.get_compound("nbt"))
            c["__pos"] = t.get_list("pos")
            tagged.append(c)
        s.entities = resolve_entities(tagged, (width, height, length), [(0.0, 0.0, 0.0)], s.warnings,
                                      pos_key=lambda t: [float(v) for v in t.get_list("__pos")] if len(t.get_list("__pos")) == 3 else None)
    s.metadata = {"author": root.get_str("author", "")}
    s.author = root.get_str("author", "")
    return s


def save_structure(schem: Schematic, path: str) -> None:
    """Write a vanilla structure .nbt file (used for tests; vanilla limits sizes to 48 but we do not)."""
    pal = nbt.List([], nbt.TAG_COMPOUND)
    for st in schem.palette:
        c = nbt.Compound({"Name": nbt.String(st.name)})
        if st.props:
            c["Properties"] = nbt.Compound({k: nbt.String(v) for k, v in st.props})
        pal.append(c)
    blocks = nbt.List([], nbt.TAG_COMPOUND)
    ys, zs, xs = np.nonzero(schem.blocks)
    for y, z, x in zip(ys.tolist(), zs.tolist(), xs.tolist()):
        blocks.append(nbt.Compound({
            "pos": nbt.List([nbt.Int(x), nbt.Int(y), nbt.Int(z)], nbt.TAG_INT),
            "state": nbt.Int(int(schem.blocks[y, z, x])),
        }))
    root = nbt.Compound({
        "size": nbt.List([nbt.Int(schem.width), nbt.Int(schem.height), nbt.Int(schem.length)], nbt.TAG_INT),
        "entities": nbt.List([entity_to_compound(e, "structure") for e in schem.entities], nbt.TAG_COMPOUND),
        "blocks": blocks,
        "palette": pal,
        "DataVersion": nbt.Int(schem.data_version or 3953),
    })
    nbt.dump(root, path, name="")
