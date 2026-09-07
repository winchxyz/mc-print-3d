"""Evaluate the game's own idle pose for each mob by running ``Model.setupAnim`` from the client jar.

``vanilla_models.json`` holds the rest layers; the game then poses them every frame in ``setupAnim``
(zombie arms forward, an axolotl lying on land, a cat's tail).  This tool builds each model class the
way the game does (``<init>(ModelPart root)`` over a stub ModelPart tree), runs ``setupAnim`` with an
idle render state (every field zero / false, plus a few overrides such as ``onGroundFactor = 1``) and
records how each part moved.  Output::

    python tools/extract_entity_poses.py <client.jar> <client_mappings.txt> mcprint/mobs/vanilla_models.json mcprint/mobs/vanilla_poses.json

Keyframe animations (``KeyframeAnimation.apply``) are treated as no-ops: at age 0 they are at their
first keyframe, which is the rest pose for every idle animation in the game.
"""
from __future__ import annotations

import json
import math
import sys
import zipfile
from typing import Any, Optional

sys.path.insert(0, __import__("os").path.dirname(__file__))
from extract_entity_models import (Interpreter, Mappings, Unknown, Unsupported, Wide, _desc_args, _desc_ret)  # noqa: E402

# layer name -> (model class simple name, {render state field: value}); default = <Layer>Model
MODEL_CLASSES = {
    "player": ("PlayerModel", {}), "player_slim": ("PlayerModel", {}),
    "zombie": ("ZombieModel", {}), "husk": ("ZombieModel", {}), "giant": ("ZombieModel", {}), "drowned": ("DrownedModel", {}),
    "zombie_villager": ("ZombieVillagerModel", {}), "skeleton": ("SkeletonModel", {}), "stray": ("SkeletonModel", {}),
    "wither_skeleton": ("SkeletonModel", {}), "bogged": ("BoggedModel", {}), "parched": ("SkeletonModel", {}),
    "piglin": ("PiglinModel", {}), "piglin_brute": ("PiglinModel", {}), "zombified_piglin": ("PiglinModel", {}),
    "evoker": ("IllagerModel", {}), "vindicator": ("IllagerModel", {}), "pillager": ("IllagerModel", {}), "illusioner": ("IllagerModel", {}),
    "witch": ("WitchModel", {}), "villager": ("VillagerModel", {}), "wandering_trader": ("VillagerModel", {}),
    "cat": ("CatModel", {}), "ocelot": ("OcelotModel", {}), "mooshroom": ("CowModel", {}), "cave_spider": ("SpiderModel", {}),
    "elder_guardian": ("GuardianModel", {}), "glow_squid": ("SquidModel", {}), "zoglin": ("HoglinModel", {}),
    "skeleton_horse": ("HorseModel", {}), "zombie_horse": ("HorseModel", {}), "magma_cube": ("MagmaCubeModel", {}),
    "pufferfish": ("PufferfishBigModel", {}), "pufferfish_big": ("PufferfishBigModel", {}), "happy_ghast": ("HappyGhastModel", {}),
    "axolotl": ("AxolotlModel", {"onGroundFactor": 1.0}),
    "bee": ("BeeModel", {"isOnGround": True, "hasStinger": True}),
    "turtle": ("TurtleModel", {"isOnLand": True}),
    "wolf": ("WolfModel", {"tailAngle": math.pi / 5}),                       # Wolf.getTailAngle() for a wild wolf
    "goat": ("GoatModel", {"hasLeftHorn": True, "hasRightHorn": True}),
    "snow_golem": ("SnowGolemModel", {"hasPumpkin": True}),
    "parrot": ("ParrotModel", {"pose": ("enum", "net.minecraft.client.model.animal.parrot.ParrotModel$Pose", "STANDING")}),
    "salmon": ("SalmonModel", {}), "cod": ("CodModel", {}), "tadpole": ("TadpoleModel", {}),
}
_CROSSED = ("enum", "net.minecraft.world.entity.monster.illager.AbstractIllager$IllagerArmPose", "CROSSED")
MODEL_CLASSES["evoker"] = ("IllagerModel", {"armPose": _CROSSED})
MODEL_CLASSES["vindicator"] = ("IllagerModel", {"armPose": _CROSSED})
MODEL_CLASSES["illusioner"] = ("IllagerModel", {"armPose": _CROSSED})


class Vec3f:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class JList:
    def __init__(self, items):
        self.items = list(items)


class JIter:
    def __init__(self, items):
        self.items = list(items)
        self.i = 0


class ModelPartStub:
    """Just enough of net.minecraft.client.model.geom.ModelPart for setupAnim code."""

    def __init__(self, path: str, part: dict, children: dict):
        self.path = path
        off, rot, sc = part["offset"], part["rotation"], part.get("scale", [1, 1, 1])
        self.initial = (list(off), list(rot), list(sc))
        self.children = children
        self.visible = True
        self.skipDraw = False
        self.resetPose()

    def resetPose(self):
        (self.x, self.y, self.z), (self.xRot, self.yRot, self.zRot), (self.xScale, self.yScale, self.zScale) = \
            (tuple(self.initial[0]), tuple(self.initial[1]), tuple(self.initial[2]))

    def all_parts(self):
        out = [self]
        for c in self.children.values():
            out.extend(c.all_parts())
        return out

    def state(self):
        return {"pos": [self.x, self.y, self.z], "rot": [self.xRot, self.yRot, self.zRot], "scale": [self.xScale, self.yScale, self.zScale],
                "visible": bool(self.visible) and not bool(self.skipDraw)}


class JavaObject:
    """An instance of a game class (model, render state, enum constant...)."""

    def __init__(self, cls: str, defaults=None):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "_defaults", defaults)

    def __getattr__(self, name):
        d = object.__getattribute__(self, "_defaults")
        if d is not None:
            return d(name)
        return Unknown(f"field {name}")


class PoseInterpreter(Interpreter):
    def __init__(self, jar, maps: Mappings):
        super().__init__(jar, maps)
        self.mth = maps.class_to_obf.get("net.minecraft.util.Mth", "?")
        self.model_part = maps.class_to_obf["net.minecraft.client.model.geom.ModelPart"]
        self.model_cls = maps.class_to_obf["net.minecraft.client.model.Model"]
        self.keyframe = maps.class_to_obf.get("net.minecraft.client.animation.KeyframeAnimation", "?")
        self.lenient = True
        self.state_types: dict[str, dict[str, str]] = {}   # obf state class -> {real field: type}
        self.unknown_calls: set[str] = set()

    # ---- leniency -------------------------------------------------------------------
    def unknown_number(self, v):
        return 0.0

    # ---- statics ---------------------------------------------------------------------
    def get_static(self, cls: str, fname: str, real: str):
        if cls == self.mth:
            return {"PI": math.pi, "HALF_PI": math.pi / 2, "TWO_PI": 2 * math.pi, "DEG_TO_RAD": math.pi / 180, "RAD_TO_DEG": math.pi / 180 * 180 / math.pi * (180 / math.pi),
                    "EPSILON": 1e-5, "SQRT_OF_TWO": math.sqrt(2)}.get(real, Unknown("Mth." + real))
        rc = self.maps.real_class(cls)
        cf = self.load(cls)
        is_enum = cf is not None and bool(cf.access & 0x4000)
        if is_enum or rc.startswith("net.minecraft.client.model") or rc.startswith("net.minecraft.client.animation") or "$" in cls:
            key = (cls, fname)
            if key not in self.statics and cls not in self._clinit_done and cf is not None:
                self._clinit_done.add(cls)
                cm = cf.methods.get(("<clinit>", "()V"))
                if cm is not None and cm.code:
                    try:
                        self.execute(cf, cm, [])
                    except Exception:  # noqa: BLE001
                        pass
            return self.statics.get(key, Unknown(f"static {rc}.{real}"))
        return Unknown(f"static {rc}.{real}")

    def enum_constant(self, real_class: str, name: str):
        """The JavaObject for an enum constant (runs the enum's <clinit>)."""
        obf = self.maps.class_to_obf.get(real_class)
        if obf is None:
            return None
        cf = self.load(obf)
        if cf is None:
            return None
        for (c, f), r in self.maps.fields.items():
            if c == obf and r == name:
                return self.get_static(obf, f, r)
        return None

    def new_object(self, cls: str):
        if cls == "org/joml/Vector3f":
            return Vec3f()
        if self.load(cls) is not None:
            return JavaObject(cls)
        return Unknown("new " + self.maps.real_class(cls))

    # ---- calls -----------------------------------------------------------------------
    def invoke(self, op: int, cls: str, name: str, desc: str, args: list, this: Any) -> Any:
        rname = self.real_method(cls, name, desc)
        # ModelPart stubs
        if isinstance(this, ModelPartStub):
            return self.part_call(this, rname, args)
        if isinstance(this, Vec3f):
            return self.vec_call(this, name, desc, args)
        if isinstance(this, JList):
            if name in ("iterator", "listIterator"):
                return JIter(this.items)
            if name == "size":
                return len(this.items)
            if name == "get":
                return this.items[int(args[0])]
            if name == "forEach":
                return None
            return Unknown("list." + name)
        if isinstance(this, JIter):
            if name == "hasNext":
                return 1 if this.i < len(this.items) else 0
            if name == "next":
                v = this.items[this.i]; this.i += 1; return v
            return Unknown("iter." + name)
        if cls == self.mth:
            return self.mth_call(rname, args, desc)
        if cls == "java/lang/Math":
            return super().invoke(op, cls, name, desc, args, this)
        if cls == self.keyframe or self.maps.real_class(cls).startswith("net.minecraft.client.animation"):
            return None if _desc_ret(desc) == "V" else Unknown("animation")
        rc = self.maps.real_class(cls)
        if rc == "net.minecraft.client.model.Model":
            if name == "<init>":
                self.put_field(this, "root", args[0] if args else Unknown("root"))
                return None
            if rname == "resetPose":
                root = getattr(this, "root", None)
                if isinstance(root, ModelPartStub):
                    for p in root.all_parts():
                        p.resetPose()
                return None
            if rname == "root":
                return getattr(this, "root", Unknown("root"))
            if rname == "allParts":
                root = getattr(this, "root", None)
                return JList(root.all_parts()) if isinstance(root, ModelPartStub) else Unknown("allParts")
        if cls == "java/lang/Object" and name == "<init>":
            return None
        if cls == "java/lang/Enum":
            if name == "<init>" and isinstance(this, JavaObject) and len(args) >= 2:
                object.__setattr__(this, "_name", args[0]); object.__setattr__(this, "_ordinal", int(args[1]))
                return None
            if name == "ordinal" and isinstance(this, JavaObject):
                return int(getattr(this, "_ordinal", 0))
            if name == "name" and isinstance(this, JavaObject):
                return getattr(this, "_name", "")
            return Unknown("Enum." + name)
        if isinstance(this, JavaObject) and name in ("ordinal", "name") and hasattr(this, "_ordinal"):
            return int(this._ordinal) if name == "ordinal" else this._name
        if isinstance(this, JavaObject):
            # instance method on a game object: run the real code up the class chain
            target = this._cls if op != 0xb7 else cls          # invokespecial names the class explicitly (super calls, constructors)
            cf, m = self.find_method(target, name, desc)
            if m is not None:
                try:
                    return self.run_method(target, name, desc, this, args)
                except Unsupported as exc:
                    self.unknown_calls.add(f"{rc}.{rname}: {exc}")
                    return None if _desc_ret(desc) == "V" else Unknown(f"failed {rname}")
            if name == "<init>":
                return None
            # render state accessors and the like: typed defaults
            ret = _desc_ret(desc)
            if ret in ("F", "D"):
                return 0.0
            if ret in ("Z", "I", "J", "B", "S", "C"):
                return 0
            self.unknown_calls.add(f"{rc}.{rname}")
            return Unknown(f"{rc}.{rname}")
        if op == 0xb8 and (rc.startswith("net.minecraft.client.model") or rc.startswith("net.minecraft.client.renderer")):
            try:
                return self.run_static(cls, name, desc, args)
            except Unsupported as exc:
                self.unknown_calls.add(f"{rc}.{rname}: {exc}")
                return None if _desc_ret(desc) == "V" else Unknown(f"failed {rname}")
        result = super().invoke(op, cls, name, desc, args, this)
        return result

    def part_call(self, part: ModelPartStub, rname: str, args: list):
        if rname == "getChild":
            child = part.children.get(args[0])
            if child is None:
                raise Unsupported(f"no child {args[0]!r} under {part.path}")
            return child
        if rname == "hasChild":
            return 1 if args[0] in part.children else 0
        if rname == "resetPose":
            part.resetPose(); return None
        if rname == "setPos":
            part.x, part.y, part.z = (float(a) for a in args[:3]); return None
        if rname == "setRotation":
            part.xRot, part.yRot, part.zRot = (float(a) for a in args[:3]); return None
        if rname == "offsetPos":
            v = args[0]
            if isinstance(v, Vec3f):
                part.x += v.x; part.y += v.y; part.z += v.z
            return None
        if rname == "offsetRotation":
            v = args[0]
            if isinstance(v, Vec3f):
                part.xRot += v.x; part.yRot += v.y; part.zRot += v.z
            return None
        if rname == "offsetScale":
            v = args[0]
            if isinstance(v, Vec3f):
                part.xScale += v.x; part.yScale += v.y; part.zScale += v.z
            return None
        if rname == "getAllParts":
            return JList(part.all_parts())
        if rname == "isEmpty":
            return 0
        if rname in ("storePose", "getInitialPose"):
            from extract_entity_models import Pose
            return Pose(part.x, part.y, part.z, part.xRot, part.yRot, part.zRot, part.xScale, part.yScale, part.zScale)
        if rname == "loadPose":
            p = args[0]
            if hasattr(p, "x"):
                part.x, part.y, part.z, part.xRot, part.yRot, part.zRot = p.x, p.y, p.z, p.rx, p.ry, p.rz
            return None
        if rname in ("render", "translateAndRotate", "visit", "setInitialPose", "rotateBy", "addAllChildren"):
            return None
        return Unknown("ModelPart." + rname)

    def vec_call(self, v: Vec3f, name: str, desc: str, args: list):
        if name == "<init>":
            if len(args) >= 3:
                v.x, v.y, v.z = (float(a) for a in args[:3])
            return None
        if name in ("x", "y", "z"):
            return getattr(v, name)
        if name == "set" and len(args) >= 3:
            v.x, v.y, v.z = (float(a) for a in args[:3]); return v
        if name == "mul" and len(args) == 1:
            v.x *= args[0]; v.y *= args[0]; v.z *= args[0]; return v
        if name == "mul" and len(args) >= 3:
            v.x *= args[0]; v.y *= args[1]; v.z *= args[2]; return v
        if name == "add" and isinstance(args[0], Vec3f):
            v.x += args[0].x; v.y += args[0].y; v.z += args[0].z; return v
        return Unknown("Vector3f." + name)

    def mth_call(self, rname: str, args: list, desc: str):
        a = [x.v if isinstance(x, Wide) else (0.0 if isinstance(x, Unknown) else x) for x in args]
        fn = {
            "sin": lambda x: math.sin(x), "cos": lambda x: math.cos(x), "sqrt": lambda x: math.sqrt(max(x, 0)), "abs": lambda x: abs(x),
            "clamp": lambda x, lo, hi: max(lo, min(hi, x)), "lerp": lambda t, x, y: x + t * (y - x),
            "clampedLerp": lambda x, y, t: x + max(0.0, min(1.0, t)) * (y - x),
            "rotLerp": lambda t, x, y: x + t * (((y - x + 180.0) % 360.0) - 180.0),
            "rotLerpRad": lambda t, x, y: x + t * (((y - x + math.pi) % (2 * math.pi)) - math.pi),
            "wrapDegrees": lambda x: ((x + 180.0) % 360.0) - 180.0, "triangleWave": lambda x, p: (abs(x % p - p * 0.5) - p * 0.25) / (p * 0.25),
            "floor": lambda x: math.floor(x), "ceil": lambda x: math.ceil(x), "square": lambda x: x * x, "sign": lambda x: (x > 0) - (x < 0),
            "positiveModulo": lambda x, y: x % y if y else 0.0, "absMax": lambda x, y: max(abs(x), abs(y)),
            "frac": lambda x: x - math.floor(x), "inverseLerp": lambda v, lo, hi: (v - lo) / (hi - lo) if hi != lo else 0.0,
            "smoothstep": lambda x: x * x * (3 - 2 * x), "catmullrom": lambda t, a, b, c, d: b,
            "equal": lambda x, y: 1 if abs(x - y) < 1e-5 else 0, "degreesDifference": lambda x, y: ((y - x + 180.0) % 360.0) - 180.0,
            "degreesDifferenceAbs": lambda x, y: abs(((y - x + 180.0) % 360.0) - 180.0), "fastInvSqrt": lambda x: 1 / math.sqrt(x) if x > 0 else 0.0,
            "lerpInt": lambda t, x, y: int(x + t * (y - x)), "nextInt": lambda r, lo, hi: lo, "nextFloat": lambda r, lo, hi: lo,
            "wrapDegreesFloat": lambda x: ((x + 180.0) % 360.0) - 180.0,
        }.get(rname)
        if fn is None:
            self.unknown_calls.add("Mth." + rname)
            return 0.0 if _desc_ret(desc) in ("F", "D") else (0 if _desc_ret(desc) in ("I", "Z", "J") else Unknown("Mth." + rname))
        try:
            r = fn(*a)
        except TypeError:
            r = 0.0
        ret = _desc_ret(desc)
        if ret in ("I", "J", "Z"):
            r = int(r)
        return Wide(r) if ret in ("D", "J") else r


# ---------------------------------------------------------------------------------------
def build_stub_tree(layer: dict) -> ModelPartStub:
    parts = {p["path"]: p for p in layer["parts"]}
    stubs: dict[str, ModelPartStub] = {}
    for path in sorted(parts, key=lambda s: s.count("/"), reverse=True):     # children first
        p = parts[path]
        children = {c["path"].rsplit("/", 1)[-1]: stubs[c["path"]] for c in layer["parts"] if c["parent"] == path}
        stubs[path] = ModelPartStub(path, p, children)
    root_children = {p["path"]: stubs[p["path"]] for p in layer["parts"] if p["parent"] is None}
    root = ModelPartStub("", {"offset": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, root_children)
    return root


def evaluate(it: PoseInterpreter, maps: Mappings, layer_name: str, layer: dict, overrides: dict) -> tuple[Optional[dict], str]:
    simple, state_over = MODEL_CLASSES.get(layer_name, (None, {}))
    if simple is None:
        simple = "".join(w.capitalize() for w in layer_name.split("_")) + "Model"
    state_over = {**state_over, **overrides}
    cands = [k for k in maps.class_to_obf if k.endswith("." + simple)]
    if not cands:
        return None, f"no class {simple}"
    real = cands[0]
    obf = maps.class_to_obf[real]
    cf = it.load(obf)
    if cf is None:
        return None, f"class {real} not in jar"
    # constructor <init>(ModelPart)
    ctor = None
    for (n, d), m in cf.methods.items():
        if n == "<init>" and d.startswith("(L" + it.model_part + ";)"):
            ctor = d
    if ctor is None:
        return None, "no <init>(ModelPart)"
    # the specific setupAnim(XRenderState)
    setup = None
    state_cls = None
    c = cf
    hops = 0
    while c is not None and setup is None and hops < 6:
        for (n, d), m in c.methods.items():
            if m.access & 0x0040:          # bridge methods forward to the real overload
                continue
            if maps.methods.get((c.name, n, d), n) == "setupAnim" and m.code and _desc_args(d) and _desc_args(d)[0] != "Ljava/lang/Object;":
                setup = (c.name, n, d)
                state_cls = _desc_args(d)[0][1:-1]
        c = it.load(c.super_name) if c.super_name else None
        hops += 1
    if setup is None:
        return None, "no setupAnim(state)"
    root = build_stub_tree(layer)
    model = JavaObject(obf)
    it.unknown_calls = set()
    try:
        it.run_method(obf, "<init>", ctor, model, [root])
    except Unsupported as exc:
        return None, f"<init> failed: {exc}"
    # typed defaults for the render state, walking its superclasses
    types: dict[str, str] = {}
    c = it.load(state_cls)
    hops = 0
    while c is not None and hops < 8:
        for (k_cls, k_field), t in maps.field_types.items():
            if k_cls == c.name:
                types.setdefault(k_field, t)
        c = it.load(c.super_name) if c.super_name else None
        hops += 1

    def default(name):
        if name in state_over:
            v = state_over[name]
            if isinstance(v, tuple) and v and v[0] == "enum":
                return it.enum_constant(v[1], v[2])
            return (1 if v else 0) if isinstance(v, bool) else v
        t = types.get(name)
        if t in ("float", "double"):
            return 0.0
        if t in ("int", "long", "boolean", "byte", "short", "char"):
            return 0
        if t is not None:
            return None                      # object fields are null in the idle state (no carried block, no item)
        return Unknown(f"state.{name}")
    state = JavaObject(state_cls, default)
    try:
        it.run_method(setup[0], setup[1], setup[2], model, [state])
    except Unsupported as exc:
        return None, f"setupAnim failed: {exc}"
    poses: dict = {}
    for stub in root.all_parts():
        if stub is root:
            continue
        st = stub.state()
        init_pos, init_rot, init_sc = stub.initial
        changed = (any(abs(a - b) > 1e-4 for a, b in zip(st["pos"], init_pos)) or any(abs(a - b) > 1e-4 for a, b in zip(st["rot"], init_rot))
                   or not st["visible"] or any(abs(a - b) > 1e-4 for a, b in zip(st["scale"], init_sc)))
        if changed:
            poses[stub.path] = st
    note = "; ".join(sorted(it.unknown_calls))[:300]
    return poses, note


def main(argv):
    if len(argv) < 5:
        print(__doc__)
        return 2
    jar = zipfile.ZipFile(argv[1])
    maps = Mappings(open(argv[2], encoding="utf-8").read())
    layers = json.load(open(argv[3], encoding="utf-8"))["layers"]
    it = PoseInterpreter(jar, maps)
    only = set(argv[5].split(",")) if len(argv) > 5 else None
    out: dict = {}
    for name in sorted(layers):
        if only and name not in only:
            continue
        if any(k in name for k in ("boat", "raft", "minecart", "baby", "banner", "chest", "bed_", "conduit", "decorated", "skull", "head", "shulker_box", "sign", "book", "bell", "armor", "saddle", "spit", "stinger", "trident", "shield", "elytra", "cape", "ears", "spin", "wind", "outer", "undercoat", "eyes", "decor", "ropes", "harness", "collar", "coral", "no_hat", "pattern", "wool", "crystal", "knot", "charge", "bullet", "patch", "fangs", "dragon", "arrow", "stand")):
            continue
        poses, note = evaluate(it, maps, name, layers[name], {})
        if poses is None:
            print(f"{name:22s} SKIP {note}")
            continue
        out[name] = {"parts": poses, "note": note}
        moved = ", ".join(f"{p}: rot {[round(v, 3) for v in st['rot']]}{'' if st['visible'] else ' hidden'}" for p, st in poses.items())
        print(f"{name:22s} {len(poses):2d} parts changed  {moved[:230]}{'  | ' + note if note else ''}")
    with open(argv[4], "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("wrote", argv[4], len(out), "models")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
