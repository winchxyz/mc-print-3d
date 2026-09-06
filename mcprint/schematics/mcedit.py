"""Legacy MCEdit / Schematica / old WorldEdit ``.schematic`` loader (numeric block ids)."""
from __future__ import annotations

import logging

import numpy as np

from .. import nbt
from .base import AIR, BlockState, PaletteBuilder, Schematic

log = logging.getLogger(__name__)


def _legacy_lookup():
    try:
        from .legacy_ids import LEGACY_NAME_TO_ID, legacy_block, legacy_block_from_name
        return legacy_block, legacy_block_from_name, LEGACY_NAME_TO_ID
    except Exception as exc:  # pragma: no cover - only while the table is missing
        log.warning("legacy id table unavailable: %s", exc)

        def legacy_block(bid, meta):
            return (f"minecraft:unknown_legacy_{bid}", {})

        def legacy_block_from_name(name, meta):
            return (name if ":" in name else "minecraft:" + name, {})

        return legacy_block, legacy_block_from_name, {}


def is_mcedit(root: nbt.Compound) -> bool:
    return isinstance(root.get("Blocks"), np.ndarray) and "Width" in root and "Height" in root and "Length" in root


def load_mcedit(root: nbt.Compound, source: str = "") -> Schematic:
    legacy_block, legacy_block_from_name, name_to_id = _legacy_lookup()

    width = root.get_int("Width") & 0xFFFF
    height = root.get_int("Height") & 0xFFFF
    length = root.get_int("Length") & 0xFFFF
    blocks = root.get_array("Blocks")
    if blocks is None:
        raise ValueError("MCEdit schematic has no Blocks array")
    blocks = blocks.astype(np.uint16) & 0xFF
    n = width * height * length
    if len(blocks) < n:
        raise ValueError(f"MCEdit schematic Blocks array too short ({len(blocks)} < {n})")
    blocks = blocks[:n]
    data = root.get_array("Data")
    if data is None or len(data) < n:
        data = np.zeros(n, dtype=np.uint8)
    data = (data[:n].astype(np.uint8) & 0x0F)

    add = root.get_array("AddBlocks")
    if add is not None and len(add) > 0:
        add = add.astype(np.uint8)
        need = (n + 1) // 2
        if len(add) < need:
            add = np.concatenate([add, np.zeros(need - len(add), dtype=np.uint8)])
        hi = np.zeros(n, dtype=np.uint16)
        even_count, odd_count = (n + 1) // 2, n // 2
        hi[0::2] = (add[:even_count] & 0x0F).astype(np.uint16)      # even indices: low nibble
        hi[1::2] = ((add[:odd_count] >> 4) & 0x0F).astype(np.uint16)  # odd indices: high nibble
        blocks = blocks | (hi << 8)

    # Mod id mappings (Schematica: name -> id ; MCEdit-Unified/others: id -> name)
    id_to_name: dict[int, str] = {}
    mapping = root.get("SchematicaMapping")
    if isinstance(mapping, nbt.Compound):
        for name, bid in mapping.items():
            try:
                id_to_name[int(bid) & 0xFFF] = str(name)
            except (TypeError, ValueError):
                continue
    for key in ("BlockIDs", "blockIDs", "BlockIds"):
        m2 = root.get(key)
        if isinstance(m2, nbt.Compound):
            for k, v in m2.items():
                try:
                    id_to_name[int(k)] = str(v)
                except (TypeError, ValueError):
                    try:
                        id_to_name[int(v)] = str(k)
                    except (TypeError, ValueError):
                        continue

    combined = (blocks.astype(np.int32) << 4) | data.astype(np.int32)
    uniq, inverse = np.unique(combined, return_inverse=True)
    pb = PaletteBuilder()
    remap = np.zeros(len(uniq), dtype=np.int32)
    unknown = set()
    for i, key in enumerate(uniq):
        bid = int(key) >> 4
        meta = int(key) & 0xF
        if bid == 0:
            remap[i] = 0
            continue
        if bid in id_to_name:
            mapped = id_to_name[bid]
            if mapped in name_to_id or ("minecraft:" + mapped) in name_to_id or mapped.startswith("minecraft:"):
                name, props = legacy_block_from_name(mapped, meta)
            else:
                name, props = legacy_block_from_name(mapped, meta)
        else:
            name, props = legacy_block(bid, meta)
            if name.startswith("minecraft:unknown_legacy_"):
                unknown.add(bid)
        remap[i] = pb.add(BlockState.make(name, props))
    grid = remap[inverse].reshape(height, length, width).astype(np.int32)

    s = Schematic(width=width, height=height, length=length, palette=pb.states, blocks=grid,
                  format="mcedit", source=source, name=root.get_str("Name", ""), data_version=None)
    s.metadata = {
        "materials": root.get_str("Materials", "Alpha"),
        "we_offset": [root.get_int("WEOffsetX"), root.get_int("WEOffsetY"), root.get_int("WEOffsetZ")],
        "we_origin": [root.get_int("WEOriginX"), root.get_int("WEOriginY"), root.get_int("WEOriginZ")],
    }
    ents = root.get("TileEntities")
    if isinstance(ents, list):
        s.block_entities = list(ents)
    if unknown:
        s.warnings.append(f"{len(unknown)} unknown numeric block ids (no name mapping in file): "
                          + ", ".join(str(u) for u in sorted(unknown)[:20]))
    return s
