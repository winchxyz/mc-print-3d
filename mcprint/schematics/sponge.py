"""Sponge schematic (.schem) loader – versions 1, 2 and 3 (WorldEdit 7+, FAWE, Axiom, Litematica export...)."""
from __future__ import annotations

import logging

import numpy as np

from .. import nbt
from .base import AIR, BlockState, PaletteBuilder, Schematic
from .entities import entity_to_compound, resolve_entities

log = logging.getLogger(__name__)


def is_sponge(root: nbt.Compound) -> bool:
    if "Version" in root and ("Palette" in root or "BlockData" in root):
        return True
    inner = root.get("Schematic")
    return isinstance(inner, nbt.Compound) and "Version" in inner and ("Blocks" in inner or "Palette" in inner)


def _modernize(name: str, props: dict, data_version):
    try:
        from .renames import modernize_block
        return modernize_block(name, props, data_version)
    except Exception:
        return name, props


def load_sponge(root: nbt.Compound, source: str = "") -> Schematic:
    inner = root
    if isinstance(root.get("Schematic"), nbt.Compound) and "Version" in root["Schematic"]:
        inner = root["Schematic"]
    version = inner.get_int("Version", 1)
    data_version = inner.get_int("DataVersion", 0) or None
    width = inner.get_int("Width") & 0xFFFF
    height = inner.get_int("Height") & 0xFFFF
    length = inner.get_int("Length") & 0xFFFF
    n = width * height * length

    if version >= 3 and isinstance(inner.get("Blocks"), nbt.Compound):
        bc = inner["Blocks"]
        palette_tag = bc.get_compound("Palette")
        data = bc.get_array("Data")
        block_entities = bc.get_list("BlockEntities")
    else:
        palette_tag = inner.get_compound("Palette")
        data = inner.get_array("BlockData")
        block_entities = inner.get_list("BlockEntities") or inner.get_list("TileEntities")
    if data is None:
        raise ValueError("Sponge schematic has no block data")

    indices = nbt.read_varint_array(data.view(np.uint8), count=n)

    # Palette: state string -> index
    max_index = int(indices.max()) if len(indices) else 0
    src_states: list = [None] * (max(max_index + 1, len(palette_tag)))
    for state_str, idx in palette_tag.items():
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if i >= len(src_states):
            src_states.extend([None] * (i + 1 - len(src_states)))
        try:
            bs = BlockState.parse(str(state_str))
        except ValueError:
            log.warning("bad palette entry %r", state_str)
            bs = AIR
        name, props = _modernize(bs.name, bs.properties, data_version)
        src_states[i] = BlockState.make(name, props)

    pb = PaletteBuilder()
    remap = np.zeros(len(src_states), dtype=np.int32)
    missing = 0
    for i, st in enumerate(src_states):
        if st is None:
            missing += 1
            remap[i] = 0
        else:
            remap[i] = pb.add(st)
    grid = remap[indices].reshape(height, length, width).astype(np.int32)

    offset = inner.get_array("Offset")
    off = tuple(int(v) for v in offset[:3]) if offset is not None and len(offset) >= 3 else (0, 0, 0)
    meta = inner.get_compound("Metadata")
    s = Schematic(width=width, height=height, length=length, palette=pb.states, blocks=grid,
                  format=f"sponge{version}", source=source, data_version=data_version, offset=off)
    s.name = meta.get_str("Name", "")
    s.author = meta.get_str("Author", "")
    s.metadata = {k: (str(v) if not isinstance(v, (nbt.Compound, list, np.ndarray)) else "…") for k, v in meta.items()}
    s.block_entities = list(block_entities) if block_entities else []
    ents = inner.get_list("Entities")
    if ents:
        s.entities = resolve_entities(ents, (width, height, length), [(0.0, 0.0, 0.0), tuple(float(v) for v in off)], s.warnings)
    if missing:
        s.warnings.append(f"{missing} palette indices without a palette entry were treated as air")
    return s


def save_sponge(schem: Schematic, path: str, version: int = 2) -> None:
    """Write a Sponge v2 or v3 schematic (used for tests and for re-exporting)."""
    palette = nbt.Compound()
    for i, st in enumerate(schem.palette):
        palette[str(st)] = nbt.Int(i)
    flat = schem.blocks.astype(np.int64).reshape(-1)
    # varint encode
    out = bytearray()
    for v in flat.tolist():
        v = int(v)
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
    data = np.frombuffer(bytes(out), dtype=np.int8).copy().view(nbt.ByteArray)
    common = {
        "Version": nbt.Int(version),
        "DataVersion": nbt.Int(schem.data_version or 3953),
        "Width": nbt.Short(schem.width if schem.width < 32768 else schem.width - 65536),
        "Height": nbt.Short(schem.height if schem.height < 32768 else schem.height - 65536),
        "Length": nbt.Short(schem.length if schem.length < 32768 else schem.length - 65536),
        "Offset": np.asarray(schem.offset, dtype=np.int32).view(nbt.IntArray),
        "Metadata": nbt.Compound({"Name": nbt.String(schem.name or "mc-print-3d")}),
    }
    if version >= 3:
        root = nbt.Compound({"Schematic": nbt.Compound({**common, "Blocks": nbt.Compound({
            "Palette": palette, "Data": data, "BlockEntities": nbt.List([], nbt.TAG_COMPOUND)}),
            "Entities": nbt.List([entity_to_compound(e, "sponge3") for e in schem.entities], nbt.TAG_COMPOUND)})})
        nbt.dump(root, path, name="")
    else:
        root = nbt.Compound({**common, "PaletteMax": nbt.Int(len(schem.palette)), "Palette": palette,
                             "BlockData": data, "BlockEntities": nbt.List([], nbt.TAG_COMPOUND),
                             "Entities": nbt.List([entity_to_compound(e) for e in schem.entities], nbt.TAG_COMPOUND)})
        nbt.dump(root, path, name="Schematic")
