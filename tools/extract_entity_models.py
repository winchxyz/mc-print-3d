"""Extract vanilla entity model geometry straight from a Minecraft client jar.

Entity models are not data files: they are Java code (``CreeperModel.createBodyLayer()`` and friends)
building ``LayerDefinition`` objects from ``CubeListBuilder`` / ``PartPose`` calls.  This tool runs that
code with a tiny JVM bytecode interpreter that understands exactly the geometry API, using Mojang's
official mappings to find the classes, and writes every layer to JSON::

    python tools/extract_entity_models.py <client.jar> <client_mappings.txt> mcprint/mobs/vanilla_models.json

Mappings for a version come from the launcher manifest (``downloads.client_mappings.url`` in the
version json at https://piston-meta.mojang.com/mc/game/version_manifest_v2.json).

Only the *rest pose* is extracted (what ``LayerDefinition`` describes).  Animation poses applied at
runtime (``setupAnim``: zombie arms, spider legs, snow golem arms...) live in :mod:`mcprint.mobs.models`.
"""
from __future__ import annotations

import json
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional

# ======================================================================================
# mappings
# ======================================================================================

class Mappings:
    def __init__(self, text: str):
        self.class_to_obf: dict[str, str] = {}
        self.obf_to_class: dict[str, str] = {}
        self.methods: dict[tuple[str, str, str], str] = {}     # (obf class, obf name, obf descriptor) -> real name
        self.fields: dict[tuple[str, str], str] = {}           # (obf class, obf field) -> real name
        self._members: dict[str, list[str]] = {}
        cur = None
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            if not line.startswith(" "):
                m = re.match(r"^(\S+) -> (\S+):$", line)
                if not m:
                    continue
                cur = m.group(2)
                self.class_to_obf[m.group(1)] = cur
                self.obf_to_class[cur] = m.group(1)
                self._members[cur] = []
            elif cur is not None:
                self._members[cur].append(line.strip())
        for obf_cls, members in self._members.items():
            for mem in members:
                mem = re.sub(r"^\d+:\d+:", "", mem)
                m = re.match(r"^(\S+) (\S+)\((.*)\) -> (\S+)$", mem)
                if m:
                    ret, name, args, obf = m.groups()
                    desc = self._descriptor(args, ret)
                    self.methods[(obf_cls, obf, desc)] = name
                    continue
                m = re.match(r"^(\S+) (\S+) -> (\S+)$", mem)
                if m:
                    self.fields[(obf_cls, m.group(3))] = m.group(2)

    _PRIM = {"int": "I", "float": "F", "long": "J", "double": "D", "boolean": "Z", "byte": "B", "char": "C", "short": "S", "void": "V"}

    def _type(self, t: str) -> str:
        dims = t.count("[]")
        t = t.replace("[]", "")
        if t in self._PRIM:
            core = self._PRIM[t]
        else:
            core = "L" + self.class_to_obf.get(t, t).replace(".", "/") + ";"
        return "[" * dims + core

    def _descriptor(self, args: str, ret: str) -> str:
        parts = [a for a in args.split(",") if a]
        return "(" + "".join(self._type(a) for a in parts) + ")" + self._type(ret)

    def real_class(self, obf: str) -> str:
        return self.obf_to_class.get(obf.replace("/", "."), obf)


# ======================================================================================
# class file parsing
# ======================================================================================

@dataclass
class Method:
    name: str
    desc: str
    access: int
    code: bytes = b""
    max_locals: int = 0


@dataclass
class ClassFile:
    name: str
    cp: list
    methods: dict[tuple[str, str], Method]
    fields: dict[str, str]
    super_name: str = ""

    def const(self, i: int):
        return self.cp[i]

    def utf8(self, i: int) -> str:
        return self.cp[i][1]

    def class_name(self, i: int) -> str:
        return self.utf8(self.cp[i][1])

    def member_ref(self, i: int) -> tuple[str, str, str]:
        tag, cls_i, nat_i = self.cp[i]
        nat = self.cp[nat_i]
        return self.class_name(cls_i), self.utf8(nat[1]), self.utf8(nat[2])


def parse_class(data: bytes) -> ClassFile:
    pos = 10
    n = struct.unpack(">H", data[8:10])[0]
    cp: list = [None]
    i = 1
    while i < n:
        tag = data[pos]
        pos += 1
        if tag == 1:
            ln = struct.unpack(">H", data[pos:pos + 2])[0]
            raw = data[pos + 2:pos + 2 + ln]
            pos += 2 + ln
            try:
                s = raw.decode("utf-8")
            except UnicodeDecodeError:
                s = raw.decode("latin-1")
            cp.append((1, s))
        elif tag == 3:
            cp.append((3, struct.unpack(">i", data[pos:pos + 4])[0])); pos += 4
        elif tag == 4:
            cp.append((4, struct.unpack(">f", data[pos:pos + 4])[0])); pos += 4
        elif tag == 5:
            cp.append((5, struct.unpack(">q", data[pos:pos + 8])[0])); pos += 8; cp.append(None); i += 1
        elif tag == 6:
            cp.append((6, struct.unpack(">d", data[pos:pos + 8])[0])); pos += 8; cp.append(None); i += 1
        elif tag in (7, 8, 16, 19, 20):
            cp.append((tag, struct.unpack(">H", data[pos:pos + 2])[0])); pos += 2
        elif tag in (9, 10, 11, 12, 17, 18):
            a, b = struct.unpack(">HH", data[pos:pos + 4]); cp.append((tag, a, b)); pos += 4
        elif tag == 15:
            cp.append((15, data[pos], struct.unpack(">H", data[pos + 1:pos + 3])[0])); pos += 3
        else:
            raise ValueError(f"unknown constant tag {tag}")
        i += 1
    access, this_i, super_i = struct.unpack(">HHH", data[pos:pos + 6]); pos += 6
    name = cp[cp[this_i][1]][1]
    super_name = cp[cp[super_i][1]][1] if super_i else ""
    ifn = struct.unpack(">H", data[pos:pos + 2])[0]; pos += 2 + 2 * ifn
    fields: dict[str, str] = {}
    fn = struct.unpack(">H", data[pos:pos + 2])[0]; pos += 2
    for _ in range(fn):
        acc, ni, di, an = struct.unpack(">HHHH", data[pos:pos + 8]); pos += 8
        fields[cp[ni][1]] = cp[di][1]
        for _a in range(an):
            ln = struct.unpack(">I", data[pos + 2:pos + 6])[0]; pos += 6 + ln
    methods: dict[tuple[str, str], Method] = {}
    mn = struct.unpack(">H", data[pos:pos + 2])[0]; pos += 2
    for _ in range(mn):
        acc, ni, di, an = struct.unpack(">HHHH", data[pos:pos + 8]); pos += 8
        m = Method(cp[ni][1], cp[di][1], acc)
        for _a in range(an):
            ani, ln = struct.unpack(">HI", data[pos:pos + 6]); pos += 6
            if cp[ani][1] == "Code":
                max_stack, max_locals, code_len = struct.unpack(">HHI", data[pos:pos + 8])
                m.code = data[pos + 8:pos + 8 + code_len]
                m.max_locals = max_locals
            pos += ln
        methods[(m.name, m.desc)] = m
    return ClassFile(name, cp, methods, fields, super_name)


# ======================================================================================
# model objects
# ======================================================================================

@dataclass
class Deformation:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Pose:
    x: float = 0.0; y: float = 0.0; z: float = 0.0
    rx: float = 0.0; ry: float = 0.0; rz: float = 0.0
    sx: float = 1.0; sy: float = 1.0; sz: float = 1.0


@dataclass
class Cube:
    origin: tuple
    size: tuple
    uv: tuple
    mirror: bool
    inflate: tuple
    name: Optional[str] = None


@dataclass
class Builder:
    cubes: list = field(default_factory=list)
    uv: tuple = (0, 0)
    mirror: bool = False


@dataclass
class Part:
    name: str
    pose: Pose
    cubes: list
    children: dict = field(default_factory=dict)


@dataclass
class Mesh:
    root: Part


@dataclass
class Layer:
    mesh: Mesh
    w: int
    h: int
    root_scale: float = 1.0        # MeshTransformer.scaling(): cave spider 0.7, husk 1.0625, babies...


class Scaling:
    def __init__(self, s: float):
        self.s = float(s)


class Unknown:
    """Any value we do not model (sets of Directions, lambdas...)."""
    def __init__(self, what: str = ""):
        self.what = what

    def __repr__(self):
        return f"Unknown({self.what})"


class LayerKey:
    def __init__(self, name: str):
        self.name = name


class MapBuilder:
    def __init__(self):
        self.entries: dict = {}


class Wide:
    """long / double value (two stack slots in the JVM)."""
    def __init__(self, v):
        self.v = v


class JavaRandom:
    def __init__(self, seed: int):
        self.seed = (seed ^ 0x5DEECE66D) & ((1 << 48) - 1)

    def next(self, bits: int) -> int:
        self.seed = (self.seed * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
        v = self.seed >> (48 - bits)
        if v >= 1 << (bits - 1):
            v -= 1 << bits
        return v

    def next_int(self, bound: int) -> int:
        if bound & (bound - 1) == 0:
            return (bound * self.next(31)) >> 31
        while True:
            bits = self.next(31)
            val = bits % bound
            if bits - val + (bound - 1) < (1 << 31):
                return val

    def next_float(self) -> float:
        return self.next(24) / float(1 << 24)


class Unsupported(Exception):
    pass


# ======================================================================================
# interpreter
# ======================================================================================

def _desc_args(desc: str) -> list[str]:
    """Argument type list of a method descriptor."""
    out = []
    i = 1
    while desc[i] != ")":
        j = i
        while desc[j] == "[":
            j += 1
        if desc[j] == "L":
            j = desc.index(";", j)
        out.append(desc[i:j + 1])
        i = j + 1
    return out


def _desc_ret(desc: str) -> str:
    return desc[desc.index(")") + 1:]


class Interpreter:
    def __init__(self, jar: zipfile.ZipFile, maps: Mappings):
        self.jar = jar
        self.maps = maps
        self._classes: dict[str, ClassFile] = {}
        self.api = {
            "CubeListBuilder": maps.class_to_obf["net.minecraft.client.model.geom.builders.CubeListBuilder"],
            "PartPose": maps.class_to_obf["net.minecraft.client.model.geom.PartPose"],
            "PartDefinition": maps.class_to_obf["net.minecraft.client.model.geom.builders.PartDefinition"],
            "MeshDefinition": maps.class_to_obf["net.minecraft.client.model.geom.builders.MeshDefinition"],
            "LayerDefinition": maps.class_to_obf["net.minecraft.client.model.geom.builders.LayerDefinition"],
            "CubeDeformation": maps.class_to_obf["net.minecraft.client.model.geom.builders.CubeDeformation"],
            "ModelLayers": maps.class_to_obf["net.minecraft.client.model.geom.ModelLayers"],
            "RandomSource": maps.class_to_obf.get("net.minecraft.util.RandomSource", "?"),
            "MeshTransformer": maps.class_to_obf.get("net.minecraft.client.model.geom.builders.MeshTransformer", "?"),
        }
        self.by_obf = {v: k for k, v in self.api.items()}
        self.depth = 0
        self.last_map: Optional[MapBuilder] = None
        self.lenient = False          # createRoots: a failing sub-call yields Unknown instead of aborting everything
        self.statics: dict[tuple[str, str], Any] = {}
        self._clinit_done: set[str] = set()

    # ---- class loading -------------------------------------------------------------
    def load(self, obf: str) -> Optional[ClassFile]:
        obf = obf.replace(".", "/")
        if obf in self._classes:
            return self._classes[obf]
        try:
            data = self.jar.read(obf + ".class")
        except KeyError:
            self._classes[obf] = None
            return None
        cf = parse_class(data)
        self._classes[obf] = cf
        return cf

    def real_method(self, cls: str, name: str, desc: str) -> str:
        return self.maps.methods.get((cls.replace("/", "."), name, desc), name)

    def real_field(self, cls: str, name: str) -> str:
        return self.maps.fields.get((cls.replace("/", "."), name), name)

    # ---- entry points --------------------------------------------------------------
    def run_static(self, cls: str, name: str, desc: str, args: list) -> Any:
        cf = self.load(cls)
        if cf is None:
            raise Unsupported(f"class {cls} not in jar")
        m = cf.methods.get((name, desc))
        hops = 0
        while m is None and cf is not None and cf.super_name and hops < 8:     # JVM resolves static calls up the superclass chain
            cf = self.load(cf.super_name)
            m = cf.methods.get((name, desc)) if cf is not None else None
            hops += 1
        if m is None:
            raise Unsupported(f"method {cls}.{name}{desc} missing")
        if not m.code:
            raise Unsupported(f"no code for {cls}.{name}")
        self.depth += 1
        if self.depth > 24:
            raise Unsupported("recursion too deep")
        try:
            return self.execute(cf, m, args)
        finally:
            self.depth -= 1

    # ---- API emulation -------------------------------------------------------------
    def call_api(self, kind: str, cls: str, name: str, desc: str, args: list, this: Any = None) -> Any:
        """Emulate a geometry-API call. Returns the result (or Unknown)."""
        api = self.by_obf.get(cls.replace("/", "."))
        rname = self.real_method(cls, name, desc)
        argt = _desc_args(desc)
        if isinstance(this, Unknown) and name != "<init>":
            return Unknown(f"{api or cls}.{rname} on unknown")
        if api == "CubeListBuilder":
            if rname == "create":
                return Builder()
            b = this
            if not isinstance(b, Builder):
                return Unknown("builder")
            if rname == "texOffs":
                b.uv = (int(args[0]), int(args[1])); return b
            if rname == "mirror":
                b.mirror = bool(args[0]) if args else True; return b
            if rname == "addBox":
                vals = list(args)
                cname = None
                if argt and argt[0] == "Ljava/lang/String;":
                    cname = vals.pop(0); argt = argt[1:]
                x, y, z, w, h, d = (float(v) for v in vals[:6])
                rest = vals[6:]
                restt = argt[6:]
                inflate = (0.0, 0.0, 0.0)
                mirror = b.mirror
                uv = b.uv
                ints = [float(v) for t, v in zip(restt, rest) if t == "I"]
                if len(ints) >= 2:                       # addBox(name, x, y, z, w, h, d, [deformation,] u, v)
                    uv = (int(ints[-2]), int(ints[-1]))
                for t, v in zip(restt, rest):
                    if t == "L" + self.api["CubeDeformation"] + ";":
                        if isinstance(v, Deformation):
                            inflate = (v.x, v.y, v.z)
                    elif t == "Z":
                        mirror = bool(v)
                b.cubes.append(Cube((x, y, z), (w, h, d), uv, mirror, inflate, cname))
                return b
            if rname == "getCubes":
                return b.cubes
            return Unknown("builder." + rname)
        if api == "CubeDeformation":
            if name == "<init>":
                if len(args) == 1:
                    this.x = this.y = this.z = float(args[0])
                else:
                    this.x, this.y, this.z = (float(v) for v in args[:3])
                return None
            if rname == "extend":
                d = this
                if len(args) == 1:
                    return Deformation(d.x + float(args[0]), d.y + float(args[0]), d.z + float(args[0]))
                return Deformation(d.x + float(args[0]), d.y + float(args[1]), d.z + float(args[2]))
            return Unknown("deformation." + rname)
        if api == "PartPose":
            if rname == "offset":
                return Pose(float(args[0]), float(args[1]), float(args[2]))
            if rname == "rotation":
                return Pose(0, 0, 0, float(args[0]), float(args[1]), float(args[2]))
            if rname == "offsetAndRotation":
                return Pose(*(float(v) for v in args[:6]))
            if rname == "translated":
                p = this; return Pose(p.x + float(args[0]), p.y + float(args[1]), p.z + float(args[2]), p.rx, p.ry, p.rz, p.sx, p.sy, p.sz)
            if rname == "withScale":
                p = this; s = float(args[0]); return Pose(p.x, p.y, p.z, p.rx, p.ry, p.rz, s, s, s)
            if rname == "scaled":
                p = this
                if len(args) == 1:
                    s = float(args[0]); return Pose(p.x * s, p.y * s, p.z * s, p.rx, p.ry, p.rz, p.sx * s, p.sy * s, p.sz * s)
                sx, sy, sz = (float(v) for v in args[:3])
                return Pose(p.x * sx, p.y * sy, p.z * sz, p.rx, p.ry, p.rz, p.sx * sx, p.sy * sy, p.sz * sz)
            if rname in ("x", "y", "z", "xRot", "yRot", "zRot", "xScale", "yScale", "zScale"):
                return getattr(this, {"xRot": "rx", "yRot": "ry", "zRot": "rz", "xScale": "sx", "yScale": "sy", "zScale": "sz"}.get(rname, rname))
            return Unknown("pose." + rname)
        if api == "MeshDefinition":
            if name == "<init>":
                this.root = Part("root", Pose(), [], {}) if not args else (args[0] if isinstance(args[0], Part) else Part("root", Pose(), [], {}))
                return None
            if rname == "getRoot":
                return this.root
            if rname == "apply" and isinstance(args[0], Scaling):
                return Unknown("mesh.apply(scaling)")      # scale is only representable on a LayerDefinition here
            return Unknown("mesh." + rname)
        if api == "PartDefinition":
            p = this
            if not isinstance(p, Part):
                return Unknown("part")
            if rname == "addOrReplaceChild":
                cname = args[0] if isinstance(args[0], str) else f"part{len(p.children)}"
                if isinstance(args[1], Part):
                    child = Part(cname, args[1].pose, list(args[1].cubes), dict(args[1].children))
                else:
                    b = args[1]
                    pose = args[2] if isinstance(args[2], Pose) else Pose()
                    child = Part(cname, pose, list(b.cubes) if isinstance(b, Builder) else [], {})
                p.children[cname] = child
                return child
            if rname == "getChild":
                return p.children.get(args[0]) or Unknown("child")
            if rname == "clearChild":
                p.children.pop(args[0], None); return p
            if rname == "getChildren":
                return Unknown("children")
            return Unknown("part." + rname)
        if api == "LayerDefinition":
            if rname == "create":
                mesh = args[0]
                if not isinstance(mesh, Mesh):
                    return Unknown("layer(mesh?)")
                return Layer(mesh, int(args[1]), int(args[2]))
            if rname == "apply":
                if isinstance(this, Layer) and isinstance(args[0], Scaling):
                    return Layer(this.mesh, this.w, this.h, this.root_scale * args[0].s)
                return Unknown("layer.apply")
            return Unknown("layer." + rname)
        if api == "MeshTransformer":
            if rname == "scaling":
                return Scaling(args[0])
            return Unknown("transformer." + rname)
        if api == "RandomSource":
            if rname == "create":
                seed = args[0].v if isinstance(args[0], Wide) else int(args[0])
                return JavaRandom(seed)
            if isinstance(this, JavaRandom):
                if rname == "nextInt":
                    return this.next_int(int(args[0]))
                if rname == "nextFloat":
                    return this.next_float()
            return Unknown("random." + rname)
        # java.util.Random too
        if cls == "java/util/Random":
            if name == "<init>":
                seed = args[0].v if isinstance(args[0], Wide) else int(args[0])
                this.__dict__["rng"] = JavaRandom(seed); return None
            if name == "nextInt" and isinstance(this, dict) or hasattr(this, "rng"):
                return this.rng.next_int(int(args[0]))
        if cls.endswith("ImmutableMap$Builder") or cls.endswith("ImmutableMap"):
            if name == "builder":
                self.last_map = MapBuilder()
                return self.last_map
            if isinstance(this, MapBuilder):
                if name == "put":
                    this.entries[args[0]] = args[1]; return this
                if name in ("build", "buildOrThrow", "buildKeepingLast"):
                    return this.entries
            return Unknown("map." + name)
        return Unknown(f"{cls}.{name}")

    # ---- bytecode execution --------------------------------------------------------
    def execute(self, cf: ClassFile, m: Method, args: list) -> Any:
        code = m.code
        locals_: list = [None] * max(m.max_locals, len(args) + 4)
        i = 0
        for a in args:
            locals_[i] = a
            i += 2 if isinstance(a, Wide) else 1
        stack: list = []
        pc = 0
        steps = 0
        u16 = lambda p: struct.unpack(">H", code[p:p + 2])[0]  # noqa: E731
        s16 = lambda p: struct.unpack(">h", code[p:p + 2])[0]  # noqa: E731
        s32 = lambda p: struct.unpack(">i", code[p:p + 4])[0]  # noqa: E731

        def num(v):
            if isinstance(v, Wide):
                return v.v
            if v is None or isinstance(v, Unknown):
                raise Unsupported(f"arithmetic on {v!r}")
            return v

        while pc < len(code):
            steps += 1
            if steps > 200000:
                raise Unsupported("too many steps")
            op = code[pc]
            npc = pc + 1
            # ---- constants
            if op == 0x00:
                pass
            elif op == 0x01:
                stack.append(None)
            elif 0x02 <= op <= 0x08:
                stack.append(op - 0x03)
            elif op in (0x09, 0x0a):
                stack.append(Wide(op - 0x09))
            elif 0x0b <= op <= 0x0d:
                stack.append(float(op - 0x0b))
            elif op in (0x0e, 0x0f):
                stack.append(Wide(float(op - 0x0e)))
            elif op == 0x10:
                stack.append(struct.unpack(">b", code[pc + 1:pc + 2])[0]); npc = pc + 2
            elif op == 0x11:
                stack.append(s16(pc + 1)); npc = pc + 3
            elif op in (0x12, 0x13, 0x14):
                idx = code[pc + 1] if op == 0x12 else u16(pc + 1)
                npc = pc + 2 if op == 0x12 else pc + 3
                c = cf.cp[idx]
                if c[0] in (3, 4):
                    stack.append(c[1])
                elif c[0] in (5, 6):
                    stack.append(Wide(c[1]))
                elif c[0] == 8:
                    stack.append(cf.utf8(c[1]))
                elif c[0] == 7:
                    stack.append(Unknown("class " + cf.class_name(idx)))
                else:
                    stack.append(Unknown("ldc"))
            # ---- locals
            elif 0x15 <= op <= 0x19:
                stack.append(locals_[code[pc + 1]]); npc = pc + 2
            elif 0x1a <= op <= 0x2d:
                stack.append(locals_[(op - 0x1a) % 4])
            elif 0x2e <= op <= 0x35:
                idx = stack.pop(); arr = stack.pop()
                stack.append(arr[int(idx)] if isinstance(arr, list) else Unknown("aload"))
            elif 0x36 <= op <= 0x3a:
                locals_[code[pc + 1]] = stack.pop(); npc = pc + 2
            elif 0x3b <= op <= 0x4e:
                locals_[(op - 0x3b) % 4] = stack.pop()
            elif 0x4f <= op <= 0x56:
                v = stack.pop(); idx = stack.pop(); arr = stack.pop()
                if isinstance(arr, list):
                    arr[int(idx)] = v
            # ---- stack ops
            elif op == 0x57:
                stack.pop()
            elif op == 0x58:
                v = stack.pop()
                if not isinstance(v, Wide):
                    stack.pop()
            elif op == 0x59:
                stack.append(stack[-1])
            elif op == 0x5a:
                v1 = stack.pop(); v2 = stack.pop(); stack.extend([v1, v2, v1])
            elif op == 0x5b:
                v1 = stack.pop(); v2 = stack.pop()
                if isinstance(v2, Wide):
                    stack.extend([v1, v2, v1])
                else:
                    v3 = stack.pop(); stack.extend([v1, v3, v2, v1])
            elif op == 0x5c:
                v1 = stack.pop()
                if isinstance(v1, Wide):
                    stack.extend([v1, v1])
                else:
                    v2 = stack.pop(); stack.extend([v2, v1, v2, v1])
            elif op == 0x5d:
                v1 = stack.pop()
                if isinstance(v1, Wide):
                    v2 = stack.pop(); stack.extend([v1, v2, v1])
                else:
                    v2 = stack.pop(); v3 = stack.pop(); stack.extend([v2, v1, v3, v2, v1])
            elif op == 0x5e:
                raise Unsupported("dup2_x2")
            elif op == 0x5f:
                v1 = stack.pop(); v2 = stack.pop(); stack.extend([v1, v2])
            # ---- arithmetic
            elif 0x60 <= op <= 0x73:
                b = stack.pop(); a = stack.pop()
                kind = (op - 0x60) % 4      # 0 int 1 long 2 float 3 double
                opn = (op - 0x60) // 4      # 0 add 1 sub 2 mul 3 div 4 rem
                x, y = num(a), num(b)
                if opn == 0:
                    r = x + y
                elif opn == 1:
                    r = x - y
                elif opn == 2:
                    r = x * y
                elif opn == 3:
                    r = (int(x / y) if kind in (0, 1) else x / y) if y != 0 else 0
                else:
                    r = (x - int(x / y) * y) if y != 0 else 0
                if kind in (0, 1):
                    r = int(r)
                stack.append(Wide(r) if kind in (1, 3) else r)
            elif 0x74 <= op <= 0x77:
                a = stack.pop(); r = -num(a)
                stack.append(Wide(r) if isinstance(a, Wide) else r)
            elif 0x78 <= op <= 0x83:
                b = stack.pop(); a = stack.pop(); x, y = int(num(a)), int(num(b))
                r = {0x78: x << (y & 31), 0x79: x << (y & 63), 0x7a: x >> (y & 31), 0x7b: x >> (y & 63),
                     0x7c: (x % (1 << 32)) >> (y & 31), 0x7d: (x % (1 << 64)) >> (y & 63),
                     0x7e: x & y, 0x7f: x & y, 0x80: x | y, 0x81: x | y, 0x82: x ^ y, 0x83: x ^ y}[op]
                stack.append(Wide(r) if isinstance(a, Wide) else r)
            elif op == 0x84:
                idx = code[pc + 1]; c = struct.unpack(">b", code[pc + 2:pc + 3])[0]
                locals_[idx] = int(locals_[idx]) + c; npc = pc + 3
            elif 0x85 <= op <= 0x93:
                a = num(stack.pop())
                to_wide = op in (0x85, 0x87, 0x8a, 0x8c, 0x8d, 0x8f)      # i2l i2d f2l f2d l2d? (0x8a l2d) d2l(0x8f)
                to_float = op in (0x86, 0x89, 0x8d, 0x90)                 # i2f l2f l2d(no) d2f
                if op in (0x86, 0x87, 0x89, 0x8a, 0x8d, 0x90):
                    r = float(a)
                else:
                    r = int(a)
                    if op == 0x91:
                        r = ((r + 128) % 256) - 128
                    elif op == 0x92:
                        r = r % 65536
                    elif op == 0x93:
                        r = ((r + 32768) % 65536) - 32768
                stack.append(Wide(r) if op in (0x85, 0x87, 0x8a, 0x8c, 0x8e, 0x8f) else r)
            elif 0x94 <= op <= 0x98:
                b = num(stack.pop()); a = num(stack.pop())
                stack.append(-1 if a < b else (1 if a > b else 0))
            # ---- branches
            elif 0x99 <= op <= 0x9e:
                v = num(stack.pop()); off = s16(pc + 1); npc = pc + 3
                cond = {0x99: v == 0, 0x9a: v != 0, 0x9b: v < 0, 0x9c: v >= 0, 0x9d: v > 0, 0x9e: v <= 0}[op]
                if cond:
                    npc = pc + off
            elif 0x9f <= op <= 0xa4:
                b = num(stack.pop()); a = num(stack.pop()); off = s16(pc + 1); npc = pc + 3
                cond = {0x9f: a == b, 0xa0: a != b, 0xa1: a < b, 0xa2: a >= b, 0xa3: a > b, 0xa4: a <= b}[op]
                if cond:
                    npc = pc + off
            elif op in (0xa5, 0xa6):
                b = stack.pop(); a = stack.pop(); off = s16(pc + 1); npc = pc + 3
                if (a is b) == (op == 0xa5):
                    npc = pc + off
            elif op == 0xa7:
                npc = pc + s16(pc + 1)
            elif op == 0xc8:
                npc = pc + s32(pc + 1)
            elif op == 0xaa:
                v = int(num(stack.pop())); p = (pc + 4) & ~3
                default = s32(p); lo = s32(p + 4); hi = s32(p + 8)
                npc = pc + (s32(p + 12 + 4 * (v - lo)) if lo <= v <= hi else default)
            elif op == 0xab:
                v = int(num(stack.pop())); p = (pc + 4) & ~3
                default = s32(p); n = s32(p + 4)
                npc = pc + default
                for k in range(n):
                    if s32(p + 8 + 8 * k) == v:
                        npc = pc + s32(p + 12 + 8 * k); break
            elif op in (0xc6, 0xc7):
                v = stack.pop(); off = s16(pc + 1); npc = pc + 3
                if (v is None) == (op == 0xc6):
                    npc = pc + off
            elif 0xac <= op <= 0xb0:
                return stack.pop()
            elif op == 0xb1:
                return None
            # ---- fields
            elif op == 0xb2:
                cls, fname, fdesc = cf.member_ref(u16(pc + 1)); npc = pc + 3
                api = self.by_obf.get(cls.replace("/", "."))
                real = self.real_field(cls, fname)
                if api == "ModelLayers":
                    stack.append(LayerKey(real.lower()))
                elif api == "PartPose" and real == "ZERO":
                    stack.append(Pose())
                elif api == "CubeDeformation" and real == "NONE":
                    stack.append(Deformation())
                else:
                    key = (cls, fname)
                    if key not in self.statics and cls not in self._clinit_done and self.maps.real_class(cls).startswith("net.minecraft.client.model"):
                        self._clinit_done.add(cls)
                        ccf = self.load(cls)
                        cm = ccf.methods.get(("<clinit>", "()V")) if ccf else None
                        if cm is not None and cm.code:
                            saved = self.lenient
                            self.lenient = True
                            try:
                                self.execute(ccf, cm, [])
                            except Exception:  # noqa: BLE001
                                pass
                            self.lenient = saved
                    stack.append(self.statics.get(key, Unknown(f"static {cls}.{real}")))
            elif op == 0xb3:
                cls, fname, fdesc = cf.member_ref(u16(pc + 1)); npc = pc + 3
                self.statics[(cls, fname)] = stack.pop()
            elif op == 0xb4:
                obj = stack.pop(); cls, fname, fdesc = cf.member_ref(u16(pc + 1)); npc = pc + 3
                real = self.real_field(cls, fname)
                stack.append(getattr(obj, real, Unknown("field " + real)) if not isinstance(obj, Unknown) else Unknown("field"))
            elif op == 0xb5:
                stack.pop(); stack.pop(); npc = pc + 3
            # ---- invocations
            elif op in (0xb6, 0xb7, 0xb8, 0xb9):
                cls, mname, mdesc = cf.member_ref(u16(pc + 1)); npc = pc + (5 if op == 0xb9 else 3)
                nargs = len(_desc_args(mdesc))
                args_ = [stack.pop() for _ in range(nargs)][::-1]
                this = None if op == 0xb8 else stack.pop()
                ret = _desc_ret(mdesc)
                result = self.invoke(op, cls, mname, mdesc, args_, this)
                if mname == "<init>":
                    pass
                elif ret != "V":
                    stack.append(result)
            elif op == 0xba:
                idx = u16(pc + 1); npc = pc + 5
                # invokedynamic: lambdas / string concat -> opaque; pop its arguments
                nat = cf.cp[cf.cp[idx][2]]
                mdesc = cf.utf8(nat[2])
                for _ in range(len(_desc_args(mdesc))):
                    stack.pop()
                if _desc_ret(mdesc) != "V":
                    stack.append(Unknown("lambda"))
            # ---- objects
            elif op == 0xbb:
                cls = cf.class_name(u16(pc + 1)); npc = pc + 3
                api = self.by_obf.get(cls.replace("/", "."))
                if api == "CubeDeformation":
                    stack.append(Deformation())
                elif api == "MeshDefinition":
                    stack.append(Mesh(Part("root", Pose(), [], {})))
                elif api == "CubeListBuilder":
                    stack.append(Builder())
                elif cls == "java/util/Random":
                    stack.append(Unknown("random"))
                else:
                    stack.append(Unknown("new " + self.maps.real_class(cls)))
            elif op == 0xbc:
                n = int(num(stack.pop())); stack.append([0] * n); npc = pc + 2
            elif op == 0xbd:
                n = int(num(stack.pop())); stack.append([None] * n); npc = pc + 3
            elif op == 0xbe:
                arr = stack.pop(); stack.append(len(arr) if isinstance(arr, list) else 0)
            elif op == 0xbf:
                if self.last_map is not None and _desc_ret(m.desc) == "Ljava/util/Map;":
                    return self.last_map.entries      # e.g. the sanity check at the end of createRoots tripping on an Unknown
                raise Unsupported("athrow")
            elif op == 0xc0:
                npc = pc + 3
            elif op == 0xc1:
                stack.pop(); stack.append(0); npc = pc + 3
            elif op in (0xc2, 0xc3):
                stack.pop()
            elif op == 0xc4:
                wop = code[pc + 1]; idx = u16(pc + 2)
                if wop == 0x84:
                    locals_[idx] = int(locals_[idx]) + s16(pc + 4); npc = pc + 6
                elif 0x15 <= wop <= 0x19:
                    stack.append(locals_[idx]); npc = pc + 4
                elif 0x36 <= wop <= 0x3a:
                    locals_[idx] = stack.pop(); npc = pc + 4
                else:
                    raise Unsupported("wide")
            elif op == 0xc5:
                raise Unsupported("multianewarray")
            elif op in (0xa8, 0xa9, 0xc9):
                raise Unsupported("jsr")
            else:
                raise Unsupported(f"opcode {op:#x}")
            pc = npc
        return None

    def invoke(self, op: int, cls: str, name: str, desc: str, args: list, this: Any) -> Any:
        api = self.by_obf.get(cls.replace("/", "."))
        if api is not None or cls == "java/util/Random" or "ImmutableMap" in cls:
            return self.call_api("api", cls, name, desc, args, this)
        # java.lang.Math / Mth helpers that models occasionally use
        if cls == "java/lang/Math":
            import math
            fn = {"sin": math.sin, "cos": math.cos, "abs": abs, "toRadians": math.radians, "sqrt": math.sqrt, "max": max, "min": min}.get(name)
            if fn is not None:
                vals = [a.v if isinstance(a, Wide) else a for a in args]
                r = fn(*vals)
                return Wide(r) if _desc_ret(desc) in ("D", "J") else r
        real_cls = self.maps.real_class(cls)
        if real_cls.startswith("net.minecraft.util.Mth"):
            import math
            rname = self.real_method(cls, name, desc)
            fn = {"sin": math.sin, "cos": math.cos, "abs": abs, "sqrt": math.sqrt}.get(rname)
            if fn is not None:
                return fn(args[0])
        if op == 0xb8:
            # static call into game code (model helpers, ModelLayers.register...): interpret it
            if real_cls.startswith("net.minecraft.client.model") or real_cls.startswith("net.minecraft.client.renderer"):
                try:
                    return self.run_static(cls, name, desc, args)
                except Unsupported:
                    if self.lenient:
                        return Unknown(f"failed {real_cls}.{name}")
                    raise
                except (TypeError, ValueError, IndexError, AttributeError) as exc:
                    if self.lenient:
                        return Unknown(f"failed {real_cls}.{name}: {exc}")
                    raise Unsupported(f"{real_cls}.{name}: {type(exc).__name__}: {exc}")
            return Unknown(f"static {real_cls}.{name}")
        if name == "<init>":
            return None
        return Unknown(f"{real_cls}.{name}")


# ======================================================================================
# conversion to JSON
# ======================================================================================

def _f(v) -> float:
    if isinstance(v, Wide):
        v = v.v
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"not a number: {v!r}")
    return float(v)


def part_to_json(part: Part, path: str, parent: Optional[str], out: list) -> None:
    p = part.pose if isinstance(part.pose, Pose) else Pose()
    cubes = []
    for c in part.cubes:
        try:
            cube = {"origin": [_f(v) for v in c.origin], "size": [_f(v) for v in c.size], "uv": [_f(v) for v in c.uv],
                    "mirror": bool(c.mirror), "inflate": [_f(v) for v in c.inflate]}
        except ValueError:
            continue
        if isinstance(c.name, str):
            cube["name"] = c.name
        cubes.append(cube)
    out.append({
        "path": path, "name": str(part.name), "parent": parent,
        "offset": [_f(p.x), _f(p.y), _f(p.z)], "rotation": [_f(p.rx), _f(p.ry), _f(p.rz)], "scale": [_f(p.sx), _f(p.sy), _f(p.sz)],
        "cubes": cubes,
    })
    for name, child in part.children.items():
        if isinstance(child, Part):
            part_to_json(child, f"{path}/{name}", path, out)


def layer_to_json(layer: Layer) -> dict:
    parts: list = []
    for name, child in layer.mesh.root.children.items():
        if isinstance(child, Part):
            part_to_json(child, str(name), None, parts)
    return {"texture_size": [int(_f(layer.w)), int(_f(layer.h))], "root_scale": float(layer.root_scale), "parts": parts}


def extract(jar_path: str, mappings_path: str) -> dict:
    jar = zipfile.ZipFile(jar_path)
    maps = Mappings(open(mappings_path, encoding="utf-8").read())
    it = Interpreter(jar, maps)
    ld = maps.class_to_obf["net.minecraft.client.model.geom.LayerDefinitions"]
    layers: dict = {}
    failed: list = []
    errors: dict = {}
    # 1. LayerDefinitions.createRoots(): the authoritative layer name -> definition table
    obf_name = None
    for (c, n, d), real in maps.methods.items():
        if c == ld and real == "createRoots":
            obf_name, obf_desc = n, d
    if obf_name is not None:
        it.lenient = True
        try:
            result = it.run_static(ld, obf_name, obf_desc, [])
        except Exception as exc:  # noqa: BLE001
            errors["createRoots"] = f"{type(exc).__name__}: {exc}"
            result = it.last_map.entries if it.last_map is not None else None
        it.lenient = False
        if isinstance(result, dict):
            for k, v in result.items():
                if isinstance(k, LayerKey) and isinstance(v, Layer):
                    try:
                        layers[k.name] = layer_to_json(v)
                    except ValueError as exc:
                        errors[k.name] = str(exc)
                elif isinstance(k, LayerKey):
                    failed.append(k.name)
    # 2. every model class with a static LayerDefinition factory, keyed by class name (fallback / extra)
    layer_desc = "L" + maps.class_to_obf["net.minecraft.client.model.geom.builders.LayerDefinition"] + ";"
    deform = "L" + maps.class_to_obf["net.minecraft.client.model.geom.builders.CubeDeformation"] + ";"
    classes: dict = {}
    for real, obf in maps.class_to_obf.items():
        if not real.startswith("net.minecraft.client.model") or "$" in real:
            continue
        cf = it.load(obf)
        if cf is None:
            continue
        for (n, d), m in cf.methods.items():
            if not d.endswith(")" + layer_desc) or not (m.access & 0x0008):
                continue
            argt = _desc_args(d)
            args: list = []
            ok = True
            for t in argt:
                if t == deform:
                    args.append(Deformation())
                elif t == "Z":
                    args.append(0)
                elif t == "F":
                    args.append(0.0)
                elif t == "I":
                    args.append(0)
                else:
                    ok = False
            if not ok:
                continue
            rname = maps.methods.get((obf, n, d), n)
            key = real.split(".")[-1] + "." + rname + ("" if not argt else "(" + ",".join(argt) + ")")
            try:
                lay = it.run_static(obf, n, d, args)
            except Unsupported as exc:
                errors[key] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{type(exc).__name__}: {exc}"
                continue
            if isinstance(lay, Layer):
                try:
                    classes[key] = layer_to_json(lay)
                except ValueError as exc:
                    errors[key] = str(exc)
    return {"layers": layers, "classes": classes, "failed": sorted(failed), "errors": errors, "layer_count": len(layers)}


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    data = extract(argv[1], argv[2])
    with open(argv[3], "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    print(f"{data['layer_count']} named layers + {len(data['classes'])} class factories written to {argv[3]}; "
          f"{len(data['failed'])} layers could not be evaluated: {data['failed'][:20]}; errors: {len(data['errors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
