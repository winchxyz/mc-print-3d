"""Block model resolution: blockstate JSON -> variant/multipart selection -> model JSON with parents.

The output of :meth:`ModelResolver.resolve` is a :class:`ResolvedState`: a list of model instances
(each = element list + blockstate rotation) ready for voxelization, plus diagnostics.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..schematics.base import BlockState
from .pack import AssetStack, blockstate_path, model_path, split_resource
from .textures import MISSING_TEXTURE

log = logging.getLogger(__name__)

FACE_NAMES = ("down", "up", "north", "south", "west", "east")


@dataclass
class Face:
    texture: str                                   # resolved resource ('minecraft:block/stone') or MISSING_TEXTURE
    uv: Optional[tuple[float, float, float, float]] = None
    rotation: int = 0
    tintindex: int = -1
    ref: str = ""                                  # raw reference from the JSON ('#side'), kept for parent chains


@dataclass
class Element:
    frm: np.ndarray                                # (3,) model units
    to: np.ndarray
    faces: dict[str, Face] = field(default_factory=dict)
    rot_origin: Optional[np.ndarray] = None
    rot_axis: str = ""
    rot_angle: float = 0.0
    rescale: bool = False
    shade: bool = True

    @property
    def is_full_cube(self) -> bool:
        return (not self.rot_axis or self.rot_angle == 0) and np.allclose(self.frm, 0) and np.allclose(self.to, 16)


@dataclass
class ObjMesh:
    """Triangle soup from an OBJ model (Forge/NeoForge obj loader)."""
    triangles: np.ndarray            # (N, 3, 3) float32, model units 0..16
    uvs: Optional[np.ndarray]        # (N, 3, 2) or None
    textures: list[str]              # per-triangle texture resource index -> resource
    tri_texture: np.ndarray          # (N,) int index into textures


@dataclass
class Model:
    name: str
    elements: list[Element] = field(default_factory=list)
    textures: dict[str, str] = field(default_factory=dict)
    parents: list[str] = field(default_factory=list)
    builtin: str = ""                 # 'entity' | 'generated' | 'missing' | ''
    loader: str = ""                  # custom loader id if any
    raw: dict = field(default_factory=dict)
    missing: bool = False             # model file not found
    obj: Optional[ObjMesh] = None

    @property
    def particle(self) -> Optional[str]:
        t = self.textures.get("particle")
        if t and not t.startswith("#"):
            return t
        for v in self.textures.values():
            if v and not v.startswith("#"):
                return v
        return None

    @property
    def has_geometry(self) -> bool:
        return bool(self.elements) or self.obj is not None


@dataclass
class ModelInstance:
    model: Model
    x: int = 0
    y: int = 0
    uvlock: bool = False


@dataclass
class ResolvedState:
    state: BlockState
    instances: list[ModelInstance] = field(default_factory=list)
    kind: str = "ok"                  # ok | empty | missing_blockstate | missing_model | builtin_entity | custom_loader
    warnings: list[str] = field(default_factory=list)
    particle: Optional[str] = None

    @property
    def has_geometry(self) -> bool:
        return any(i.model.has_geometry for i in self.instances)

    @property
    def is_full_cube(self) -> bool:
        for inst in self.instances:
            if inst.model.elements and any(e.is_full_cube for e in inst.model.elements):
                return True
        return False


# --------------------------------------------------------------------------------------
# Model loading
# --------------------------------------------------------------------------------------
class ModelResolver:
    def __init__(self, stack: AssetStack):
        self.stack = stack
        self._models: dict[str, Model] = {}
        self._states: dict[BlockState, ResolvedState] = {}
        self.missing_models: set[str] = set()
        self.missing_blockstates: set[str] = set()

    # ---- models ----------------------------------------------------------------------
    def load_model(self, resource: str, _depth: int = 0) -> Model:
        resource = _normalize_model_res(resource)
        m = self._models.get(resource)
        if m is not None:
            return m
        m = self._load_model_uncached(resource, _depth)
        self._models[resource] = m
        return m

    def _read_model_json(self, resource: str) -> Optional[dict]:
        ns, path = split_resource(resource)
        for cand in (path, f"block/{path}" if "/" not in path else None):
            if cand is None:
                continue
            d = self.stack.read_json(model_path(f"{ns}:{cand}"))
            if d is not None:
                return d
        return None

    def _load_model_uncached(self, resource: str, depth: int) -> Model:
        ns, path = split_resource(resource)
        if path.startswith("builtin/"):
            return Model(name=resource, builtin=path.split("/", 1)[1])
        data = self._read_model_json(resource)
        if data is None:
            self.missing_models.add(resource)
            return Model(name=resource, missing=True)
        return self.model_from_json(resource, data, depth)

    def model_from_json(self, resource: str, data: dict, depth: int = 0) -> Model:
        model = Model(name=resource, raw=data)
        parent_res = data.get("parent")
        parent: Optional[Model] = None
        if isinstance(parent_res, str) and depth < 32:
            parent = self.load_model(parent_res, depth + 1)
            model.parents = [parent.name] + parent.parents
            model.builtin = parent.builtin
            model.textures = dict(parent.textures)
            model.elements = list(parent.elements)
            model.loader = parent.loader
            model.obj = parent.obj
        tex = data.get("textures")
        if isinstance(tex, dict):
            for k, v in tex.items():
                if isinstance(v, str):
                    model.textures[str(k)] = v
        loader = data.get("loader")
        if isinstance(loader, str):
            model.loader = loader
        elements = data.get("elements")
        if isinstance(elements, list):
            model.elements = [e for e in (self._parse_element(el) for el in elements) if e is not None]
        # custom loaders with usable content
        if model.loader:
            self._apply_loader(model, data, depth)
        # resolve texture variables for faces on private copies (parents keep their raw '#refs')
        model.elements = [_copy_element(el) for el in model.elements]
        for el in model.elements:
            for f in el.faces.values():
                f.texture = resolve_texture_var(f.ref, model.textures)
        return model

    def _apply_loader(self, model: Model, data: dict, depth: int) -> None:
        loader = model.loader
        kind = loader.split(":", 1)[-1]
        if kind == "composite":
            children = data.get("children") or data.get("parts") or {}
            elems: list[Element] = []
            if isinstance(children, dict):
                for cname, child in children.items():
                    if isinstance(child, dict):
                        cm = self.model_from_json(f"{model.name}#{cname}", {**child, "textures": {**model.textures, **child.get("textures", {})}}, depth + 1)
                        elems.extend(cm.elements)
                        if cm.obj is not None and model.obj is None:
                            model.obj = cm.obj
                    elif isinstance(child, str):
                        cm = self.load_model(child, depth + 1)
                        elems.extend(cm.elements)
            if elems:
                model.elements = elems
        elif kind == "obj":
            objres = data.get("model")
            if isinstance(objres, str):
                try:
                    from .objmodel import load_obj
                    model.obj = load_obj(self.stack, objres, model.textures, flip_v=bool(data.get("flip_v", False)))
                except Exception as exc:
                    log.debug("obj model %s failed: %s", objres, exc)
        elif kind in ("separate_transforms", "separate-perspective"):
            base = data.get("base")
            if isinstance(base, dict):
                cm = self.model_from_json(model.name + "#base", {**base, "textures": {**model.textures, **base.get("textures", {})}}, depth + 1)
                if cm.elements:
                    model.elements = cm.elements
            elif isinstance(base, str):
                cm = self.load_model(base, depth + 1)
                if cm.elements:
                    model.elements = cm.elements
        elif kind in ("elements",):
            pass  # vanilla-style elements already parsed

    @staticmethod
    def _parse_element(el: dict) -> Optional[Element]:
        if not isinstance(el, dict):
            return None
        try:
            frm = np.asarray([float(v) for v in el.get("from", [0, 0, 0])][:3], dtype=np.float64)
            to = np.asarray([float(v) for v in el.get("to", [16, 16, 16])][:3], dtype=np.float64)
        except (TypeError, ValueError):
            return None
        if frm.shape != (3,) or to.shape != (3,):
            return None
        lo = np.minimum(frm, to)
        hi = np.maximum(frm, to)
        e = Element(frm=lo, to=hi, shade=bool(el.get("shade", True)))
        rot = el.get("rotation")
        if isinstance(rot, dict):
            try:
                e.rot_origin = np.asarray([float(v) for v in rot.get("origin", [8, 8, 8])][:3], dtype=np.float64)
                e.rot_axis = str(rot.get("axis", "y")).lower()
                e.rot_angle = float(rot.get("angle", 0.0))
                e.rescale = bool(rot.get("rescale", False))
            except (TypeError, ValueError):
                e.rot_origin = None
        faces = el.get("faces")
        if isinstance(faces, dict):
            for fname, fdata in faces.items():
                fname = str(fname).lower()
                if fname not in FACE_NAMES or not isinstance(fdata, dict):
                    continue
                uv = fdata.get("uv")
                uvt = None
                if isinstance(uv, list) and len(uv) == 4:
                    try:
                        uvt = tuple(float(v) for v in uv)  # type: ignore[assignment]
                    except (TypeError, ValueError):
                        uvt = None
                try:
                    rotation = int(fdata.get("rotation", 0)) % 360
                except (TypeError, ValueError):
                    rotation = 0
                try:
                    tint = int(fdata.get("tintindex", -1))
                except (TypeError, ValueError):
                    tint = -1
                ref = str(fdata.get("texture", ""))
                e.faces[fname] = Face(texture=ref, uv=uvt, rotation=rotation, tintindex=tint, ref=ref)
        if not e.faces:
            # element with no faces still occupies volume (rare); give it all faces with the particle texture
            for fname in FACE_NAMES:
                e.faces[fname] = Face(texture="#particle", ref="#particle")
        return e

    # ---- blockstates -----------------------------------------------------------------
    def resolve(self, state: BlockState) -> ResolvedState:
        r = self._states.get(state)
        if r is None:
            r = self._resolve_uncached(state)
            self._states[state] = r
        return r

    def _resolve_uncached(self, state: BlockState) -> ResolvedState:
        rs = ResolvedState(state=state)
        bs = self.stack.read_json(blockstate_path(state.name))
        if bs is None:
            # try a plain model with the block's name
            m = self.load_model(f"{state.namespace}:block/{state.path}")
            if not m.missing:
                rs.instances = [ModelInstance(m)]
                rs.warnings.append("no blockstate file; used model of the same name")
            else:
                self.missing_blockstates.add(state.name)
                rs.kind = "missing_blockstate"
                return rs
        else:
            try:
                rs.instances = self._instances_from_blockstate(bs, state, rs)
            except Exception as exc:
                rs.warnings.append(f"blockstate parse error: {exc}")
                rs.instances = []
        rs.particle = next((i.model.particle for i in rs.instances if i.model.particle), None)
        if not rs.instances:
            if rs.kind == "ok":
                rs.kind = "missing_model"
            return rs
        if all(i.model.missing for i in rs.instances):
            rs.kind = "missing_model"
        elif not rs.has_geometry:
            from .fallbacks import is_air_like, is_block_entity_block
            if any(i.model.builtin == "entity" for i in rs.instances) or is_block_entity_block(state.path):
                rs.kind = "builtin_entity"
            elif any(i.model.loader for i in rs.instances):
                rs.kind = "custom_loader"
            elif is_air_like(state.path) or state.namespace == "minecraft" or rs.particle is None:
                rs.kind = "empty"
            else:
                # mod block with a particle texture but no JSON geometry: almost always a block-entity renderer
                rs.kind = "builtin_entity"
        return rs

    def _instances_from_blockstate(self, bs: dict, state: BlockState, rs: ResolvedState) -> list[ModelInstance]:
        props = {k: str(v) for k, v in state.properties.items()}
        variants = bs.get("variants")
        if isinstance(variants, dict) and variants:
            if bs.get("forge_marker") == 1 or _looks_like_forge_variants(variants):
                return self._forge_variants(bs, variants, props)
            key = _pick_variant(variants, props)
            if key is None:
                return []
            return self._apply(variants[key])
        multipart = bs.get("multipart")
        if isinstance(multipart, list):
            out: list[ModelInstance] = []
            for part in multipart:
                if not isinstance(part, dict):
                    continue
                cond = part.get("when")
                if cond is None or _eval_condition(cond, props):
                    out.extend(self._apply(part.get("apply")))
            return out
        rs.warnings.append("blockstate has neither variants nor multipart")
        return []

    def _forge_variants(self, bs: dict, variants: dict, props: dict[str, str]) -> list[ModelInstance]:
        defaults = bs.get("defaults") if isinstance(bs.get("defaults"), dict) else {}
        merged: dict = dict(defaults)
        # 'normal' / 'inventory' keys hold whole variants; property keys hold {value: partial}
        if "normal" in variants and isinstance(variants["normal"], (dict, list)):
            base = variants["normal"] if isinstance(variants["normal"], dict) else variants["normal"][0]
            merged.update(base)
        for prop, table in variants.items():
            if prop in ("normal", "inventory") or not isinstance(table, dict):
                continue
            val = props.get(prop)
            if val is not None and val in table and isinstance(table[val], dict):
                merged.update(table[val])
        if "model" not in merged:
            return []
        return self._apply(merged)

    def _apply(self, apply) -> list[ModelInstance]:
        if isinstance(apply, list):
            if not apply:
                return []
            best = max((a for a in apply if isinstance(a, dict)), key=lambda a: float(a.get("weight", 1)), default=None)
            if best is None:
                return []
            apply = best
        if not isinstance(apply, dict):
            return []
        res = apply.get("model")
        if not isinstance(res, str):
            return []
        m = self.load_model(res)
        try:
            x = int(apply.get("x", 0)) % 360
            y = int(apply.get("y", 0)) % 360
        except (TypeError, ValueError):
            x, y = 0, 0
        return [ModelInstance(m, x=x, y=y, uvlock=bool(apply.get("uvlock", False)))]


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _normalize_model_res(res: str) -> str:
    ns, path = split_resource(res)
    return f"{ns}:{path}"


def _copy_element(el: Element) -> Element:
    return Element(frm=el.frm, to=el.to, faces={k: Face(texture=f.texture, uv=f.uv, rotation=f.rotation, tintindex=f.tintindex, ref=f.ref)
                                                for k, f in el.faces.items()},
                   rot_origin=el.rot_origin, rot_axis=el.rot_axis, rot_angle=el.rot_angle, rescale=el.rescale, shade=el.shade)


def resolve_texture_var(value: str, textures: dict[str, str], limit: int = 16) -> str:
    v = value
    for _ in range(limit):
        if not v or v == MISSING_TEXTURE:
            return MISSING_TEXTURE
        if v.startswith("#"):
            nxt = textures.get(v[1:])
            if nxt is None:
                return MISSING_TEXTURE
            v = nxt
        else:
            ns, path = split_resource(v)
            return f"{ns}:{path}"
    return MISSING_TEXTURE


def _parse_variant_key(key: str) -> dict[str, str]:
    key = key.strip()
    if key in ("", "normal", "default"):
        return {}
    out = {}
    for pair in key.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _pick_variant(variants: dict, props: dict[str, str]) -> Optional[str]:
    """Best matching variant key: no contradictions, most matched properties, else fewest contradictions."""
    best_key = None
    best_score = None
    for key in variants:
        want = _parse_variant_key(str(key))
        contradictions = 0
        matched = 0
        for k, v in want.items():
            have = props.get(k)
            if have is None:
                continue
            if have == v:
                matched += 1
            else:
                contradictions += 1
        score = (-contradictions, matched)
        if best_score is None or score > best_score:
            best_score = score
            best_key = key
    return best_key


def _looks_like_forge_variants(variants: dict) -> bool:
    """Forge 1.12 blockstate format: variants = {prop: {value: {...}}} without 'model' keys at depth 1."""
    if "normal" in variants or "inventory" in variants:
        return True
    for k, v in variants.items():
        if isinstance(v, dict) and "model" in v:
            return False
        if isinstance(v, list):
            return False
        if isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()) and "=" not in str(k):
            return True
    return False


def _eval_condition(cond, props: dict[str, str]) -> bool:
    if not isinstance(cond, dict):
        return True
    if "OR" in cond:
        return any(_eval_condition(c, props) for c in cond["OR"]) if isinstance(cond["OR"], list) else True
    if "AND" in cond:
        return all(_eval_condition(c, props) for c in cond["AND"]) if isinstance(cond["AND"], list) else True
    for k, v in cond.items():
        have = props.get(k)
        if have is None:
            return False
        options = {s.strip() for s in str(v).split("|")}
        if have not in options:
            return False
    return True


def rotation_matrix(axis: str, degrees: float) -> np.ndarray:
    """Right-handed rotation about +axis."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    return np.eye(3)


def blockstate_rotation(x: int, y: int) -> np.ndarray:
    """Model->world rotation for blockstate x/y (vanilla: rotate -y about Y, then -x about X... applied as Ry(-y)·Rx(-x))."""
    return rotation_matrix("y", -y) @ rotation_matrix("x", -x)
