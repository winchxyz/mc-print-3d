"""Schematic loading with automatic format detection.

Supported formats
-----------------
- ``.schematic``  MCEdit / Schematica / WorldEdit (pre-1.13, numeric ids)
- ``.schem``      Sponge schematic v1, v2, v3 (WorldEdit 7+, FAWE, Axiom, Litematica export)
- ``.litematic``  Litematica (multi-region)
- ``.nbt``        Vanilla structure block templates (also Create/KubeJS ponder scenes, datapack structures)
- ``.mcstructure`` Bedrock Edition structures (little-endian NBT)
- ``.bp``         Axiom blueprints (best effort)
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Optional

from .. import nbt
from .base import AIR, BlockState, PaletteBuilder, Schematic, is_air, normalize_name

SUPPORTED_EXTENSIONS = (".schematic", ".schem", ".litematic", ".nbt", ".mcstructure", ".bp", ".zip")

FILE_FILTER = ("Minecraft schematics (*.schematic *.schem *.litematic *.nbt *.mcstructure *.bp *.zip);;"
               "MCEdit / Schematica (*.schematic);;Sponge / WorldEdit (*.schem);;Litematica (*.litematic);;"
               "Structure block (*.nbt);;Bedrock structure (*.mcstructure);;Axiom blueprint (*.bp);;All files (*)")


class SchematicFormatError(ValueError):
    pass


def detect_format(data: bytes, filename: str = "") -> str:
    """Return one of 'mcedit', 'sponge', 'litematica', 'structure', 'mcstructure', 'axiom'."""
    from .axiom import is_axiom_bytes
    if is_axiom_bytes(data):
        return "axiom"
    ext = Path(filename).suffix.lower()
    little = ext == ".mcstructure"
    try:
        _, root = nbt.loads(data, little_endian=True if little else None)
    except nbt.NBTError as exc:
        if not little:
            try:
                _, root = nbt.loads(data, little_endian=True)
            except nbt.NBTError:
                raise SchematicFormatError(f"not an NBT file: {exc}") from exc
        else:
            raise SchematicFormatError(f"not an NBT file: {exc}") from exc
    return _detect_root(root, ext)


def _detect_root(root: nbt.Compound, ext: str) -> str:
    from .litematica import is_litematic
    from .mcedit import is_mcedit
    from .mcstructure import is_mcstructure
    from .sponge import is_sponge
    from .structure import is_structure
    if is_litematic(root):
        return "litematica"
    if is_sponge(root):
        return "sponge"
    if is_mcedit(root):
        return "mcedit"
    if is_structure(root):
        return "structure"
    if is_mcstructure(root):
        return "mcstructure"
    raise SchematicFormatError(f"unrecognised schematic layout (keys: {', '.join(list(root.keys())[:12])})")


def load_schematic_bytes(data: bytes, filename: str = "") -> Schematic:
    from .axiom import is_axiom_bytes, load_axiom_bytes
    if is_axiom_bytes(data):
        return load_axiom_bytes(data, filename)
    ext = Path(filename).suffix.lower()
    root: Optional[nbt.Compound] = None
    errors = []
    order = [True, False] if ext == ".mcstructure" else [False, True]
    for little in order:
        try:
            _, root = nbt.loads(data, little_endian=little)
            fmt = _detect_root(root, ext)
            break
        except (nbt.NBTError, SchematicFormatError) as exc:
            errors.append(str(exc))
            root = None
    if root is None:
        raise SchematicFormatError("; ".join(errors) or "unreadable file")
    if fmt == "litematica":
        from .litematica import load_litematic
        return load_litematic(root, filename)
    if fmt == "sponge":
        from .sponge import load_sponge
        return load_sponge(root, filename)
    if fmt == "mcedit":
        from .mcedit import load_mcedit
        return load_mcedit(root, filename)
    if fmt == "structure":
        from .structure import load_structure
        return load_structure(root, filename)
    if fmt == "mcstructure":
        from .mcstructure import load_mcstructure
        return load_mcstructure(root, filename)
    raise SchematicFormatError(f"unsupported format {fmt}")


def load_schematic(path: str | os.PathLike) -> Schematic:
    """Load any supported schematic file (zip archives containing one schematic are unpacked)."""
    path = str(path)
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(SUPPORTED_EXTENSIONS[:-1])]
            if not names:
                raise SchematicFormatError("zip archive contains no schematic files")
            inner = names[0]
            data = z.read(inner)
            s = load_schematic_bytes(data, inner)
            s.source = path
            return s
    s = load_schematic_bytes(data, path)
    s.source = path
    if not s.name:
        s.name = Path(path).stem
    return s


__all__ = ["load_schematic", "load_schematic_bytes", "detect_format", "Schematic", "BlockState", "AIR",
           "PaletteBuilder", "is_air", "normalize_name", "SchematicFormatError", "SUPPORTED_EXTENSIONS", "FILE_FILTER"]
