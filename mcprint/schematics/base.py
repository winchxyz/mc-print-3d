"""Common in-memory representation of a loaded schematic."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

_STATE_RE = re.compile(r"^([a-z0-9_.\-:/]+)(?:\[(.*)\])?$", re.I)


@dataclass(frozen=True)
class BlockState:
    """A namespaced block id plus its properties, e.g. minecraft:oak_stairs[facing=east,half=bottom]."""

    name: str                                  # always namespaced: 'minecraft:stone'
    props: tuple[tuple[str, str], ...] = ()    # sorted (key, value) pairs

    @staticmethod
    def make(name: str, props: Optional[dict] = None) -> "BlockState":
        name = normalize_name(name)
        items = tuple(sorted((str(k), _norm_value(v)) for k, v in (props or {}).items()))
        return BlockState(name, items)

    @staticmethod
    def parse(text: str) -> "BlockState":
        """Parse 'minecraft:oak_stairs[facing=east,half=bottom]' (namespace optional)."""
        text = text.strip()
        m = _STATE_RE.match(text)
        if not m:
            raise ValueError(f"invalid block state string: {text!r}")
        name = m.group(1)
        props: dict[str, str] = {}
        if m.group(2):
            for pair in m.group(2).split(","):
                if not pair.strip():
                    continue
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    props[k.strip()] = v.strip()
                else:
                    props[pair.strip()] = "true"
        return BlockState.make(name, props)

    @property
    def properties(self) -> dict[str, str]:
        return dict(self.props)

    @property
    def namespace(self) -> str:
        return self.name.split(":", 1)[0]

    @property
    def path(self) -> str:
        return self.name.split(":", 1)[1] if ":" in self.name else self.name

    def with_props(self, **changes: str) -> "BlockState":
        p = self.properties
        p.update({k: str(v) for k, v in changes.items()})
        return BlockState.make(self.name, p)

    def __str__(self) -> str:
        if not self.props:
            return self.name
        return self.name + "[" + ",".join(f"{k}={v}" for k, v in self.props) + "]"


def _norm_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    s = str(v)
    return s


def normalize_name(name: str) -> str:
    name = str(name).strip().lower()
    if not name:
        return "minecraft:air"
    if ":" not in name:
        return "minecraft:" + name
    return name


AIR = BlockState.make("minecraft:air")


@dataclass
class Schematic:
    """A dense block grid.

    ``blocks`` is an int32 array indexed ``[y, z, x]`` (Minecraft: Y up, X east, Z south) holding
    indices into ``palette``.  Index 0 is always air (``palette[0] == AIR``).
    """

    width: int      # X size
    height: int     # Y size
    length: int     # Z size
    palette: list[BlockState]
    blocks: np.ndarray                      # int32 [y, z, x]
    format: str = ""                        # 'mcedit' | 'sponge' | 'litematica' | 'structure' | 'mcstructure' | 'axiom'
    source: str = ""                        # file path
    name: str = ""
    author: str = ""
    data_version: Optional[int] = None
    offset: tuple[int, int, int] = (0, 0, 0)
    metadata: dict = field(default_factory=dict)
    block_entities: list = field(default_factory=list)   # raw compounds (kept for reference / future use)
    warnings: list[str] = field(default_factory=list)
    entities: list = field(default_factory=list)         # mcprint.schematics.entities.Entity (mobs, positions relative to origin)

    # -- convenience --------------------------------------------------------------------
    @property
    def size(self) -> tuple[int, int, int]:
        """(X, Y, Z) size in blocks."""
        return self.width, self.height, self.length

    @property
    def block_count(self) -> int:
        return int(np.count_nonzero(self.blocks))

    @property
    def volume(self) -> int:
        return int(self.width * self.height * self.length)

    def unique_states(self) -> list[BlockState]:
        used = np.unique(self.blocks)
        return [self.palette[int(i)] for i in used if i != 0]

    def state_counts(self) -> dict[BlockState, int]:
        idx, counts = np.unique(self.blocks, return_counts=True)
        return {self.palette[int(i)]: int(c) for i, c in zip(idx, counts) if i != 0}

    def get(self, x: int, y: int, z: int) -> BlockState:
        return self.palette[int(self.blocks[y, z, x])]

    def cropped(self) -> "Schematic":
        """Return a copy trimmed to the bounding box of non-air blocks."""
        nz = np.nonzero(self.blocks)
        if len(nz[0]) == 0:
            return self
        y0, y1 = int(nz[0].min()), int(nz[0].max()) + 1
        z0, z1 = int(nz[1].min()), int(nz[1].max()) + 1
        x0, x1 = int(nz[2].min()), int(nz[2].max()) + 1
        sub = self.blocks[y0:y1, z0:z1, x0:x1].copy()
        s = Schematic(width=x1 - x0, height=y1 - y0, length=z1 - z0, palette=list(self.palette), blocks=sub,
                      format=self.format, source=self.source, name=self.name, author=self.author,
                      data_version=self.data_version, offset=(self.offset[0] + x0, self.offset[1] + y0, self.offset[2] + z0),
                      metadata=dict(self.metadata), block_entities=list(self.block_entities), warnings=list(self.warnings))
        from .entities import Entity
        s.entities = [Entity(e.id, e.x - x0, e.y - y0, e.z - z0, e.yaw, dict(e.props)) for e in self.entities]
        return s

    def compact_palette(self) -> "Schematic":
        """Drop unused palette entries (keeps index 0 = air)."""
        used = np.unique(self.blocks)
        if 0 not in used:
            used = np.concatenate([[0], used])
        remap = np.zeros(len(self.palette), dtype=np.int32)
        new_pal = [AIR]
        for i in used:
            i = int(i)
            if i == 0:
                continue
            remap[i] = len(new_pal)
            new_pal.append(self.palette[i])
        self.blocks = remap[self.blocks].astype(np.int32)
        self.palette = new_pal
        return self


class PaletteBuilder:
    """Helper to build a palette incrementally with de-duplication."""

    def __init__(self):
        self.states: list[BlockState] = [AIR]
        self.index: dict[BlockState, int] = {AIR: 0}

    def add(self, state: BlockState) -> int:
        i = self.index.get(state)
        if i is None:
            i = len(self.states)
            self.states.append(state)
            self.index[state] = i
        return i

    def add_many(self, states: Iterable[BlockState]) -> list[int]:
        return [self.add(s) for s in states]


def is_air(state: BlockState) -> bool:
    return state.name in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")
