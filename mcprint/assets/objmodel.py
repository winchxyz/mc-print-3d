"""Wavefront OBJ loader for Forge/NeoForge ``obj`` block models (Immersive Engineering & co).

Forge OBJ models live in 0..1 block space; we scale to 0..16 model units.  Materials are mapped
to textures through the model JSON ``textures`` map (material name -> texture) or the MTL
``map_Kd`` value.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np

from .pack import AssetStack, split_resource
from .textures import MISSING_TEXTURE

log = logging.getLogger(__name__)


def _read_text(stack: AssetStack, resource: str, kind: str) -> Optional[str]:
    ns, path = split_resource(resource)
    cands = [f"assets/{ns}/{path}"]
    if not path.startswith("models/"):
        cands.append(f"assets/{ns}/models/{path}")
    for c in cands:
        raw = stack.read(c)
        if raw is not None:
            return raw.decode("utf-8", errors="replace")
    return None


def _parse_mtl(text: str) -> dict[str, str]:
    mats: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("newmtl "):
            cur = line[7:].strip()
            mats.setdefault(cur, "")
        elif line.startswith("map_Kd ") and cur:
            mats[cur] = line[7:].strip().split()[-1]
    return mats


def load_obj(stack: AssetStack, resource: str, textures: dict[str, str], flip_v: bool = False):
    from .models import ObjMesh
    text = _read_text(stack, resource, "obj")
    if text is None:
        raise FileNotFoundError(resource)
    ns, path = split_resource(resource)
    base_dir = path.rsplit("/", 1)[0] if "/" in path else ""
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    tris: list[tuple[tuple[int, int], tuple[int, int], tuple[int, int]]] = []
    tri_mat: list[str] = []
    mtl: dict[str, str] = {}
    cur_mat = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v" and len(parts) >= 4:
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif tag == "vt" and len(parts) >= 3:
            uvs.append((float(parts[1]), float(parts[2])))
        elif tag == "f" and len(parts) >= 4:
            idx = []
            for p in parts[1:]:
                bits = p.split("/")
                vi = int(bits[0])
                ti = int(bits[1]) if len(bits) > 1 and bits[1] else 0
                vi = vi - 1 if vi > 0 else len(verts) + vi
                ti = ti - 1 if ti > 0 else (len(uvs) + ti if ti < 0 else -1)
                idx.append((vi, ti))
            for i in range(1, len(idx) - 1):
                tris.append((idx[0], idx[i], idx[i + 1]))
                tri_mat.append(cur_mat)
        elif tag == "usemtl":
            cur_mat = " ".join(parts[1:])
        elif tag == "mtllib":
            mtlname = " ".join(parts[1:])
            mres = f"{ns}:{base_dir}/{mtlname}" if base_dir else f"{ns}:{mtlname}"
            mtext = _read_text(stack, mres, "mtl")
            if mtext:
                mtl.update(_parse_mtl(mtext))
    if not tris:
        raise ValueError("obj has no faces")
    V = np.asarray(verts, dtype=np.float32) * 16.0
    T = np.zeros((len(tris), 3, 3), dtype=np.float32)
    U = np.zeros((len(tris), 3, 2), dtype=np.float32)
    has_uv = False
    for i, tri in enumerate(tris):
        for j, (vi, ti) in enumerate(tri):
            T[i, j] = V[vi] if 0 <= vi < len(V) else 0
            if 0 <= ti < len(uvs):
                u, v = uvs[ti]
                U[i, j] = (u, (1 - v) if not flip_v else v)
                has_uv = True
    # material -> texture resource
    tex_list: list[str] = []
    tex_index: dict[str, int] = {}

    def tex_for(mat: str) -> str:
        if mat in textures and not textures[mat].startswith("#"):
            t = textures[mat]
        elif mat in mtl and mtl[mat]:
            t = mtl[mat]
            t = re.sub(r"\.png$", "", t)
            if t.startswith("#") and t[1:] in textures:
                t = textures[t[1:]]
        else:
            t = next((v for k, v in textures.items() if k != "particle" and not v.startswith("#")), None) or textures.get("particle", MISSING_TEXTURE)
        if t.startswith("#"):
            t = MISSING_TEXTURE
        ns2, p2 = split_resource(t)
        return f"{ns2}:{p2}"

    tri_tex = np.zeros(len(tris), dtype=np.int32)
    for i, mat in enumerate(tri_mat):
        t = tex_for(mat)
        if t not in tex_index:
            tex_index[t] = len(tex_list)
            tex_list.append(t)
        tri_tex[i] = tex_index[t]
    return ObjMesh(triangles=T, uvs=U if has_uv else None, textures=tex_list, tri_texture=tri_tex)
