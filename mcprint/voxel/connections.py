"""Infer fence / wall / pane / iron-bar connections for block states that lack them.

Pre-1.13 schematics (numeric ids) and Bedrock structures store no connection properties – the
game computes them from neighbours at render time.  We do the same on the block grid so legacy
builds get proper fences instead of lonely posts.
"""
from __future__ import annotations

import logging

import numpy as np

from ..assets.models import ModelResolver
from ..assets.pack import blockstate_path
from ..schematics.base import AIR, BlockState, Schematic

log = logging.getLogger(__name__)

FAMILY_NONE, FAMILY_FENCE, FAMILY_GATE, FAMILY_WALL, FAMILY_PANE = 0, 1, 2, 3, 4


def family_of(state: BlockState) -> int:
    p = state.path
    if p.endswith("_fence_gate") or p == "fence_gate":
        return FAMILY_GATE
    if p.endswith("_fence") or p == "fence":
        return FAMILY_FENCE
    if p.endswith("_wall") and not p.endswith("_wall_sign") and "banner" not in p and "torch" not in p and "head" not in p and "skull" not in p:
        return FAMILY_WALL
    if p.endswith("_pane") or p == "iron_bars" or p.endswith("_bars"):
        return FAMILY_PANE
    return FAMILY_NONE


def _needs_inference(state: BlockState, resolver: ModelResolver) -> bool:
    fam = family_of(state)
    if fam in (FAMILY_NONE, FAMILY_GATE):
        return False
    if "north" in state.properties:
        return False
    bs = resolver.stack.read_json(blockstate_path(state.name))
    if not bs or not isinstance(bs.get("multipart"), list):
        return False
    for part in bs["multipart"]:
        when = part.get("when") if isinstance(part, dict) else None
        if isinstance(when, dict):
            keys = set(when.keys())
            for sub in when.get("OR", []) + when.get("AND", []) if isinstance(when.get("OR", when.get("AND")), list) else []:
                if isinstance(sub, dict):
                    keys |= set(sub.keys())
            if keys & {"north", "south", "east", "west"}:
                return True
    return False


def infer_connections(schem: Schematic, resolver: ModelResolver) -> int:
    """Rewrite the schematic in place, adding connection properties.  Returns changed block count."""
    pal = schem.palette
    fam = np.zeros(len(pal), dtype=np.int8)
    need = np.zeros(len(pal), dtype=bool)
    solid = np.zeros(len(pal), dtype=bool)
    for i, st in enumerate(pal):
        if st == AIR:
            continue
        fam[i] = family_of(st)
        need[i] = _needs_inference(st, resolver)
        if fam[i] == FAMILY_NONE:
            try:
                solid[i] = resolver.resolve(st).is_full_cube
            except Exception:
                solid[i] = False
    if not need.any():
        return 0
    blocks = schem.blocks
    cell_fam = fam[blocks]
    cell_solid = solid[blocks]
    cell_need = need[blocks]
    Y, Z, X = blocks.shape

    def shifted(arr, dz, dx):
        out = np.zeros_like(arr)
        zs = slice(max(dz, 0), Z + min(dz, 0))
        zd = slice(max(-dz, 0), Z + min(-dz, 0))
        xs = slice(max(dx, 0), X + min(dx, 0))
        xd = slice(max(-dx, 0), X + min(-dx, 0))
        out[:, zd, xd] = arr[:, zs, xs]
        return out

    # neighbour in direction d: north = z-1, south = z+1, west = x-1, east = x+1
    dirs = {"north": (-1, 0), "south": (1, 0), "west": (0, -1), "east": (0, 1)}
    conn = {}
    for name, (dz, dx) in dirs.items():
        nf = shifted(cell_fam, dz, dx)
        ns = shifted(cell_solid, dz, dx)
        same = np.zeros_like(cell_need)
        # fences connect to fences and gates; walls to walls; panes to panes
        same |= (cell_fam == FAMILY_FENCE) & ((nf == FAMILY_FENCE) | (nf == FAMILY_GATE))
        same |= (cell_fam == FAMILY_WALL) & (nf == FAMILY_WALL)
        same |= (cell_fam == FAMILY_PANE) & (nf == FAMILY_PANE)
        conn[name] = cell_need & (same | ns)
    above_wall = np.zeros_like(cell_need)
    above_wall[:-1] = cell_fam[1:] == FAMILY_WALL
    above_solid = np.zeros_like(cell_need)
    above_solid[:-1] = cell_solid[1:] | (cell_fam[1:] != FAMILY_NONE)

    bits = (conn["north"].astype(np.int32) | (conn["south"].astype(np.int32) << 1)
            | (conn["west"].astype(np.int32) << 2) | (conn["east"].astype(np.int32) << 3)
            | (above_wall.astype(np.int32) << 4) | (above_solid.astype(np.int32) << 5))
    key = np.where(cell_need, blocks.astype(np.int64) * 64 + bits, -1)
    uniq = np.unique(key[key >= 0])
    new_index: dict[int, int] = {}
    new_states: list[BlockState] = []
    for k in uniq.tolist():
        idx, b = k // 64, k % 64
        st = pal[idx]
        f = fam[idx]
        n, s, w, e = bool(b & 1), bool(b & 2), bool(b & 4), bool(b & 8)
        props = st.properties
        if f == FAMILY_WALL:
            tall = bool(b & 16)
            val = "tall" if tall else "low"
            props.update({"north": val if n else "none", "south": val if s else "none",
                          "west": val if w else "none", "east": val if e else "none"})
            straight = (n and s and not w and not e) or (w and e and not n and not s)
            props["up"] = "false" if (straight and not bool(b & 32)) else "true"
        else:
            props.update({"north": str(n).lower(), "south": str(s).lower(), "west": str(w).lower(), "east": str(e).lower()})
        ns = BlockState.make(st.name, props)
        new_index[k] = len(pal) + len(new_states)
        new_states.append(ns)
    if not new_states:
        return 0
    pal.extend(new_states)
    remap = np.vectorize(lambda k: new_index.get(int(k), -1))(uniq)
    lut = dict(zip(uniq.tolist(), remap.tolist()))
    changed = key >= 0
    flat = key[changed]
    blocks[changed] = np.asarray([lut[int(k)] for k in flat.tolist()], dtype=np.int32)
    return int(changed.sum())
