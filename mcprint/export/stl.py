"""Binary STL writer (one solid per file; multi-material -> one file per material)."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from ..voxel.mesher import Mesh, MeshSet


def _facet_array(mesh: Mesh) -> np.ndarray:
    v = mesh.vertices.astype(np.float32)
    t = mesh.triangles
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a).astype(np.float32)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]
    dtype = np.dtype([("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3), ("c", "<f4", 3), ("attr", "<u2")])
    arr = np.empty(len(t), dtype=dtype)
    arr["n"] = n
    arr["a"] = a
    arr["b"] = b
    arr["c"] = c
    arr["attr"] = 0
    return arr


def write_stl(path: str | Path, mesh: Mesh, header: str = "mc-print-3d") -> Path:
    path = Path(path)
    arr = _facet_array(mesh)
    with open(path, "wb") as fh:
        h = header.encode("ascii", "replace")[:80]
        fh.write(h + b" " * (80 - len(h)))
        fh.write(struct.pack("<I", len(arr)))
        fh.write(arr.tobytes())
    return path


def write_stl_set(base_path: str | Path, meshes: MeshSet, merged: bool = False) -> list[Path]:
    """Write ``<base>.stl`` (merged) or ``<base>_<slot>_<material>.stl`` per material."""
    base = Path(base_path)
    if base.suffix.lower() == ".stl":
        base = base.with_suffix("")
    out: list[Path] = []
    if merged or len(meshes.meshes) == 1:
        p = base.with_suffix(".stl")
        write_stl(p, meshes.merged() if merged else meshes.meshes[0])
        out.append(p)
        return out
    for i, m in enumerate(meshes.meshes, start=1):
        info = meshes.materials.get(m.material, {})
        slot = info.get("slot") or i
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (m.name or f"material_{m.material}"))[:40]
        p = base.parent / f"{base.name}_slot{slot}_{safe}.stl"
        write_stl(p, m)
        out.append(p)
    return out


def read_stl(path: str | Path) -> Mesh:
    """Read a binary STL (for tests / verification)."""
    data = Path(path).read_bytes()
    n = struct.unpack("<I", data[80:84])[0]
    dtype = np.dtype([("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3), ("c", "<f4", 3), ("attr", "<u2")])
    arr = np.frombuffer(data[84:84 + n * dtype.itemsize], dtype=dtype)
    verts = np.concatenate([arr["a"], arr["b"], arr["c"]], axis=0).reshape(n, 3, 3).reshape(-1, 3)
    tris = np.arange(n * 3, dtype=np.int32).reshape(n, 3)
    return Mesh(0, verts.astype(np.float32), tris)
