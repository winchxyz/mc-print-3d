"""Entity records stored in schematics (mobs, players, item frames...).

Every loader converts its own layout into :class:`Entity` objects with positions relative to the
schematic's block origin (0, 0, 0 = min corner), a yaw in Minecraft's convention and a few
render-relevant NBT values (sheep color, slime size, variants...).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .. import nbt


@dataclass
class Entity:
    id: str                       # namespaced id, e.g. 'minecraft:creeper'
    x: float                      # blocks, relative to the schematic origin (feet centre)
    y: float
    z: float
    yaw: float = 0.0              # degrees (0 = south)
    props: dict = field(default_factory=dict)

    @property
    def path(self) -> str:
        return self.id.split(":", 1)[1] if ":" in self.id else self.id


def _floats(v, n: int = 3) -> Optional[list[float]]:
    if v is None:
        return None
    try:
        out = [float(x) for x in list(v)]
    except (TypeError, ValueError):
        return None
    return out if len(out) >= n else None


def _normalize_id(raw: str) -> str:
    s = str(raw).strip()
    if not s:
        return ""
    if ":" not in s:
        # legacy CamelCase ids (Creeper, PigZombie, VillagerGolem...)
        s = "".join(("_" + c.lower()) if c.isupper() and i else c.lower() for i, c in enumerate(s))
        s = "minecraft:" + s
    return s.lower()


def entity_props(tag: nbt.Compound) -> dict:
    """Render-relevant values from an entity compound."""
    props: dict = {}
    if "Color" in tag:
        props["color"] = tag.get_int("Color", 0)
    if "Sheared" in tag:
        props["sheared"] = bool(tag.get_int("Sheared", 0))
    if "Size" in tag:
        props["size"] = tag.get_int("Size", 1)
    v = tag.find("variant", "Variant")
    if isinstance(v, str):
        props["variant"] = v
    if tag.get_int("IsBaby", 0) or tag.get_int("Age", 0) < 0:
        props["baby"] = True
    if "CustomName" in tag:
        props["name"] = str(tag.get("CustomName"))[:40]
    return props


def entity_from_compound(tag: nbt.Compound, pos: Optional[Iterable[float]] = None, id_keys=("id", "Id", "identifier"),
                         origin: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Optional[Entity]:
    """Build an :class:`Entity` from an entity NBT compound.

    ``pos`` overrides the compound's own ``Pos``; ``origin`` is subtracted from absolute positions.
    """
    raw_id = tag.find(*id_keys, default="")
    if not raw_id and isinstance(tag.get("Data"), nbt.Compound):
        raw_id = tag["Data"].find(*id_keys, default="")
    eid = _normalize_id(raw_id)
    if not eid:
        return None
    p = _floats(pos) if pos is not None else None
    if p is None:
        p = _floats(tag.find("Pos", "pos"))
    if p is None:
        return None
    data = tag["Data"] if isinstance(tag.get("Data"), nbt.Compound) else tag
    rot = _floats(data.find("Rotation", "rotation"), 1)
    yaw = float(rot[0]) if rot else 0.0
    props = entity_props(data)
    if data is not tag:
        props.update(entity_props(tag))
    return Entity(eid, p[0] - origin[0], p[1] - origin[1], p[2] - origin[2], yaw, props)


def inside(e: Entity, size: tuple[int, int, int], margin: float = 2.0) -> bool:
    return (-margin <= e.x <= size[0] + margin and -margin <= e.y <= size[1] + margin
            and -margin <= e.z <= size[2] + margin)


def resolve_entities(tags: Iterable[nbt.Compound], size: tuple[int, int, int], origins: Iterable[tuple[float, float, float]] = ((0.0, 0.0, 0.0),),
                     warnings: Optional[list] = None, pos_key=None) -> list[Entity]:
    """Parse entity compounds trying each candidate origin until the entity lands inside the schematic."""
    out: list[Entity] = []
    dropped = 0
    origins = list(origins)
    for tag in tags:
        if not isinstance(tag, nbt.Compound):
            continue
        pos = pos_key(tag) if pos_key else None
        found = None
        for o in origins:
            e = entity_from_compound(tag, pos=pos, origin=o)
            if e is None:
                break
            if inside(e, size):
                found = e
                break
        if found is not None:
            out.append(found)
        elif e is not None:
            dropped += 1
    if dropped and warnings is not None:
        warnings.append(f"{dropped} entities outside the schematic bounds were ignored")
    return out


def entity_to_compound(e: Entity, layout: str = "flat", origin: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> nbt.Compound:
    """Entity -> NBT compound. ``layout``: 'flat' (Litematica / Sponge v2 / MCEdit: id + Pos + data at top level),
    'sponge3' (Id + Pos + Data) or 'structure' (pos + blockPos + nbt)."""
    pos = nbt.List([nbt.Double(e.x + origin[0]), nbt.Double(e.y + origin[1]), nbt.Double(e.z + origin[2])], nbt.TAG_DOUBLE)
    data = nbt.Compound({"Rotation": nbt.List([nbt.Float(e.yaw), nbt.Float(0.0)], nbt.TAG_FLOAT)})
    if "color" in e.props:
        data["Color"] = nbt.Byte(int(e.props["color"]) if not isinstance(e.props["color"], str) or str(e.props["color"]).isdigit() else 0)
    if "size" in e.props:
        data["Size"] = nbt.Int(int(e.props["size"]))
    if e.props.get("sheared"):
        data["Sheared"] = nbt.Byte(1)
    if e.props.get("variant"):
        data["variant"] = nbt.String(str(e.props["variant"]))
    if layout == "sponge3":
        return nbt.Compound({"Id": nbt.String(e.id), "Pos": pos, "Data": data})
    if layout == "structure":
        data["id"] = nbt.String(e.id)
        return nbt.Compound({"pos": pos, "blockPos": nbt.List([nbt.Int(int(e.x)), nbt.Int(int(e.y)), nbt.Int(int(e.z))], nbt.TAG_INT), "nbt": data})
    c = nbt.Compound(data)
    c["id"] = nbt.String(e.id)
    c["Pos"] = pos
    return c


__all__ = ["Entity", "entity_from_compound", "entity_props", "entity_to_compound", "inside", "resolve_entities"]
