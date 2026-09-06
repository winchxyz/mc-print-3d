"""Locate Minecraft installations, game jars, mod folders and resource packs on this machine.

Supported launchers: vanilla/.minecraft (also TLauncher, official launcher), CurseForge, Prism/MultiMC/PolyMC,
Modrinth App, ATLauncher, GDLauncher, Technic, FTB App, plus manual paths.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass
class Instance:
    """A playable Minecraft instance: a game version jar plus (optionally) mods and resource packs."""

    name: str
    launcher: str                       # 'vanilla', 'curseforge', 'prism', 'modrinth', 'atlauncher', 'manual', ...
    game_version: str = ""              # '1.21.1'
    loader: str = ""                    # 'forge', 'neoforge', 'fabric', 'quilt', '' (vanilla)
    game_dir: Optional[Path] = None      # folder with mods/, resourcepacks/, options.txt
    jar_path: Optional[Path] = None      # vanilla client jar
    mods_dir: Optional[Path] = None
    resourcepacks_dir: Optional[Path] = None
    extra_jars: list[Path] = field(default_factory=list)

    @property
    def mod_jars(self) -> list[Path]:
        if not self.mods_dir or not self.mods_dir.is_dir():
            return []
        out = []
        for p in sorted(self.mods_dir.iterdir()):
            if p.suffix.lower() in (".jar", ".zip") and p.is_file():
                out.append(p)
        return out

    @property
    def resource_packs(self) -> list[Path]:
        if not self.resourcepacks_dir or not self.resourcepacks_dir.is_dir():
            return []
        out = []
        for p in sorted(self.resourcepacks_dir.iterdir()):
            if p.is_dir() and (p / "pack.mcmeta").exists():
                out.append(p)
            elif p.suffix.lower() == ".zip" and p.is_file():
                out.append(p)
        return out

    def enabled_resource_packs(self) -> list[Path]:
        """Resource packs enabled in options.txt (in priority order, highest first), if known."""
        if not self.game_dir:
            return []
        opts = self.game_dir / "options.txt"
        names: list[str] = []
        try:
            for line in opts.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("resourcePacks:"):
                    names = json.loads(line.split(":", 1)[1].strip())
                    break
        except Exception:
            return []
        out = []
        for n in reversed(names):  # options.txt lists lowest priority first
            n = n.replace("file/", "")
            if n in ("vanilla", "mod_resources", "fabric", "programmer_art", "high_contrast") or n.startswith("fabric/"):
                continue
            if not self.resourcepacks_dir:
                continue
            p = self.resourcepacks_dir / n
            if p.exists():
                out.append(p)
        return out

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.game_version:
            bits.append(self.game_version)
        if self.loader:
            bits.append(self.loader)
        return " · ".join(bits) + f"  [{self.launcher}]"

    def describe(self) -> str:
        return (f"{self.label}\n  game dir: {self.game_dir}\n  jar: {self.jar_path}\n  mods: {len(self.mod_jars)}"
                f"\n  resource packs: {len(self.resource_packs)}")


# --------------------------------------------------------------------------------------
# Version jar helpers
# --------------------------------------------------------------------------------------
def version_key(v: str) -> tuple:
    m = _VERSION_RE.match(v or "")
    if not m:
        return (0, 0, 0, v)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), v)


def find_version_jars(versions_dir: Path) -> dict[str, Path]:
    """Map version id -> client jar for a launcher 'versions' folder (vanilla layout)."""
    out: dict[str, Path] = {}
    if not versions_dir.is_dir():
        return out
    for vdir in versions_dir.iterdir():
        if not vdir.is_dir():
            continue
        jar = vdir / f"{vdir.name}.jar"
        if jar.is_file() and jar.stat().st_size > 1_000_000:
            out[vdir.name] = jar
        else:
            # modded profiles (forge-47.x-1.20.1) often have no jar; they inherit from a vanilla one
            pass
    return out


def base_version_of(version_id: str, versions_dir: Optional[Path] = None) -> str:
    """'forge-47.4.13' + version json inheritsFrom -> '1.20.1'; 'fabric-loader-0.18.4-1.20.1' -> '1.20.1'."""
    if versions_dir:
        vjson = versions_dir / version_id / f"{version_id}.json"
        try:
            d = json.loads(vjson.read_text(encoding="utf-8"))
            if d.get("inheritsFrom"):
                return str(d["inheritsFrom"])
        except Exception:
            pass
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", version_id)
    return m.group(1) if m else version_id


def pick_vanilla_jar(game_version: str, candidates: dict[str, Path]) -> Optional[Path]:
    """Best vanilla jar for a game version: exact, then same major.minor, then newest."""
    if not candidates:
        return None
    if game_version in candidates:
        return candidates[game_version]
    k = version_key(game_version)
    same_minor = [v for v in candidates if version_key(v)[:2] == k[:2] and re.fullmatch(r"\d+\.\d+(\.\d+)?", v)]
    if same_minor:
        return candidates[sorted(same_minor, key=version_key)[-1]]
    vanilla = [v for v in candidates if re.fullmatch(r"\d+\.\d+(\.\d+)?", v)]
    if vanilla:
        return candidates[sorted(vanilla, key=version_key)[-1]]
    return candidates[sorted(candidates, key=version_key)[-1]]


# --------------------------------------------------------------------------------------
# Launcher discovery
# --------------------------------------------------------------------------------------
def _home() -> Path:
    return Path.home()


def default_minecraft_dir() -> Optional[Path]:
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        cands = [Path(appdata) / ".minecraft"] if appdata else []
    elif sys.platform == "darwin":
        cands = [_home() / "Library" / "Application Support" / "minecraft"]
    else:
        cands = [_home() / ".minecraft"]
    for c in cands:
        if c.is_dir():
            return c
    return None


def _loader_from_version_id(vid: str) -> str:
    v = vid.lower()
    for name in ("neoforge", "forge", "fabric", "quilt", "optifine"):
        if name in v:
            return name
    return ""


def _vanilla_instances() -> list[Instance]:
    mc = default_minecraft_dir()
    if not mc:
        return []
    versions = find_version_jars(mc / "versions")
    out: list[Instance] = []
    # one instance per installed vanilla version (mods/ & resourcepacks/ are shared)
    profiles = {}
    try:
        lp = json.loads((mc / "launcher_profiles.json").read_text(encoding="utf-8"))
        profiles = lp.get("profiles", {})
    except Exception:
        pass
    seen = set()
    for pid, prof in profiles.items():
        vid = str(prof.get("lastVersionId", ""))
        if vid in ("latest-release", "latest-snapshot", ""):
            continue
        gdir = Path(prof["gameDir"]) if prof.get("gameDir") else mc
        base = base_version_of(vid, mc / "versions")
        jar = pick_vanilla_jar(base, versions)
        if not jar:
            continue
        key = (str(gdir), base, vid)
        if key in seen:
            continue
        seen.add(key)
        out.append(Instance(name=prof.get("name") or vid, launcher="vanilla", game_version=base,
                            loader=_loader_from_version_id(vid), game_dir=gdir, jar_path=jar,
                            mods_dir=gdir / "mods", resourcepacks_dir=gdir / "resourcepacks"))
    for vid, jar in sorted(versions.items(), key=lambda kv: version_key(kv[0]), reverse=True):
        if not re.fullmatch(r"\d+\.\d+(\.\d+)?", vid):
            continue
        key = (str(mc), vid, vid)
        if key in seen:
            continue
        seen.add(key)
        out.append(Instance(name=f"Minecraft {vid}", launcher="vanilla", game_version=vid, loader="",
                            game_dir=mc, jar_path=jar, mods_dir=mc / "mods", resourcepacks_dir=mc / "resourcepacks"))
    return out


def _curseforge_instances() -> list[Instance]:
    roots = []
    if sys.platform.startswith("win"):
        roots.append(_home() / "curseforge" / "minecraft")
        roots.append(Path(os.environ.get("USERPROFILE", str(_home()))) / "curseforge" / "minecraft")
        docs = _home() / "Documents" / "Curse" / "Minecraft"
        roots.append(docs)
    elif sys.platform == "darwin":
        roots.append(_home() / "Documents" / "curseforge" / "minecraft")
    else:
        roots.append(_home() / "Documents" / "curseforge" / "minecraft")
    out = []
    seen = set()
    for root in roots:
        inst_dir = root / "Instances"
        if not inst_dir.is_dir() or str(inst_dir) in seen:
            continue
        seen.add(str(inst_dir))
        versions = find_version_jars(root / "Install" / "versions")
        versions.update({k: v for k, v in find_version_jars(default_minecraft_dir() / "versions").items()} if default_minecraft_dir() else {})
        for idir in sorted(inst_dir.iterdir()):
            if not idir.is_dir():
                continue
            gv, loader, name = "", "", idir.name
            mi = idir / "minecraftinstance.json"
            try:
                d = json.loads(mi.read_text(encoding="utf-8", errors="replace"))
                name = d.get("name") or name
                base = d.get("baseModLoader") or {}
                gv = str(base.get("minecraftVersion") or d.get("gameVersion") or "")
                loader_name = str(base.get("name") or "")
                loader = _loader_from_version_id(loader_name)
            except Exception:
                man = idir / "manifest.json"
                try:
                    d = json.loads(man.read_text(encoding="utf-8", errors="replace"))
                    gv = str(d.get("minecraft", {}).get("version", ""))
                    ml = d.get("minecraft", {}).get("modLoaders", [{}])
                    loader = _loader_from_version_id(str(ml[0].get("id", ""))) if ml else ""
                except Exception:
                    pass
            jar = pick_vanilla_jar(gv, versions) if gv else None
            out.append(Instance(name=name, launcher="curseforge", game_version=gv, loader=loader, game_dir=idir,
                                jar_path=jar, mods_dir=idir / "mods", resourcepacks_dir=idir / "resourcepacks"))
    return out


def _prism_like_instances() -> list[Instance]:
    """Prism Launcher / MultiMC / PolyMC layout: <root>/instances/<name>/.minecraft or minecraft."""
    roots: list[Path] = []
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots += [Path(appdata) / "PrismLauncher", Path(appdata) / "PolyMC", Path(appdata) / "MultiMC"]
        roots += [_home() / "MultiMC", Path("C:/MultiMC")]
    elif sys.platform == "darwin":
        roots += [_home() / "Library" / "Application Support" / "PrismLauncher",
                  _home() / "Library" / "Application Support" / "PolyMC"]
    else:
        roots += [_home() / ".local" / "share" / "PrismLauncher", _home() / ".local" / "share" / "PolyMC",
                  _home() / ".local" / "share" / "multimc", _home() / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher"]
    out = []
    for root in roots:
        inst_dir = root / "instances"
        if not inst_dir.is_dir():
            continue
        launcher = root.name.lower().replace("launcher", "")
        # vanilla jars: libraries/com/mojang/minecraft/<ver>/minecraft-<ver>-client.jar
        versions: dict[str, Path] = {}
        lib = root / "libraries" / "com" / "mojang" / "minecraft"
        if lib.is_dir():
            for vdir in lib.iterdir():
                jar = vdir / f"minecraft-{vdir.name}-client.jar"
                if jar.is_file():
                    versions[vdir.name] = jar
        for idir in sorted(inst_dir.iterdir()):
            if not idir.is_dir():
                continue
            gdir = idir / ".minecraft"
            if not gdir.is_dir():
                gdir = idir / "minecraft"
            if not gdir.is_dir():
                continue
            gv, loader, name = "", "", idir.name
            try:
                cfg = (idir / "instance.cfg").read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^name=(.*)$", cfg, re.M)
                if m:
                    name = m.group(1).strip()
            except Exception:
                pass
            try:
                pack = json.loads((idir / "mmc-pack.json").read_text(encoding="utf-8"))
                for comp in pack.get("components", []):
                    uid = comp.get("uid", "")
                    if uid == "net.minecraft":
                        gv = str(comp.get("version", ""))
                    elif uid in ("net.minecraftforge", "net.neoforged", "net.fabricmc.fabric-loader", "org.quiltmc.quilt-loader"):
                        loader = {"net.minecraftforge": "forge", "net.neoforged": "neoforge",
                                  "net.fabricmc.fabric-loader": "fabric", "org.quiltmc.quilt-loader": "quilt"}[uid]
            except Exception:
                pass
            jar = pick_vanilla_jar(gv, versions) if gv else None
            if not jar and default_minecraft_dir():
                jar = pick_vanilla_jar(gv, find_version_jars(default_minecraft_dir() / "versions"))
            out.append(Instance(name=name, launcher=launcher or "prism", game_version=gv, loader=loader, game_dir=gdir,
                                jar_path=jar, mods_dir=gdir / "mods", resourcepacks_dir=gdir / "resourcepacks"))
    return out


def _modrinth_instances() -> list[Instance]:
    roots = []
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots += [Path(appdata) / "ModrinthApp", Path(appdata) / "com.modrinth.theseus"]
    elif sys.platform == "darwin":
        roots += [_home() / "Library" / "Application Support" / "ModrinthApp",
                  _home() / "Library" / "Application Support" / "com.modrinth.theseus"]
    else:
        roots += [_home() / ".local" / "share" / "ModrinthApp", _home() / ".local" / "share" / "com.modrinth.theseus"]
    out = []
    for root in roots:
        prof_dir = root / "profiles"
        if not prof_dir.is_dir():
            continue
        versions = find_version_jars(root / "meta" / "versions")
        for idir in sorted(prof_dir.iterdir()):
            if not idir.is_dir():
                continue
            gv, loader = "", ""
            try:
                d = json.loads((idir / "profile.json").read_text(encoding="utf-8"))
                meta = d.get("metadata", d)
                gv = str(meta.get("game_version", ""))
                loader = str(meta.get("loader", "") or "")
                if loader == "vanilla":
                    loader = ""
            except Exception:
                pass
            jar = pick_vanilla_jar(gv, versions) if gv else None
            if not jar and default_minecraft_dir():
                jar = pick_vanilla_jar(gv, find_version_jars(default_minecraft_dir() / "versions"))
            out.append(Instance(name=idir.name, launcher="modrinth", game_version=gv, loader=loader, game_dir=idir,
                                jar_path=jar, mods_dir=idir / "mods", resourcepacks_dir=idir / "resourcepacks"))
    return out


def _atlauncher_instances() -> list[Instance]:
    roots = []
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "Programs" / "ATLauncher")
        roots.append(Path("C:/ATLauncher"))
    roots.append(_home() / "ATLauncher")
    out = []
    for root in roots:
        inst_dir = root / "instances"
        if not inst_dir.is_dir():
            continue
        versions = find_version_jars(root / "versions") if (root / "versions").is_dir() else {}
        for idir in sorted(inst_dir.iterdir()):
            if not idir.is_dir():
                continue
            gv, loader, name = "", "", idir.name
            try:
                d = json.loads((idir / "instance.json").read_text(encoding="utf-8"))
                name = d.get("launcher", {}).get("name") or name
                gv = str(d.get("id", ""))
                lv = d.get("launcher", {}).get("loaderVersion", {}) or {}
                loader = str(lv.get("type", "")).lower()
            except Exception:
                pass
            jar = pick_vanilla_jar(gv, versions) if gv else None
            if not jar and default_minecraft_dir():
                jar = pick_vanilla_jar(gv, find_version_jars(default_minecraft_dir() / "versions"))
            out.append(Instance(name=name, launcher="atlauncher", game_version=gv, loader=loader, game_dir=idir,
                                jar_path=jar, mods_dir=idir / "mods", resourcepacks_dir=idir / "resourcepacks"))
    return out


def find_instances() -> list[Instance]:
    """All instances found on this machine, vanilla first, then modded launchers."""
    out: list[Instance] = []
    for fn in (_vanilla_instances, _curseforge_instances, _prism_like_instances, _modrinth_instances, _atlauncher_instances):
        try:
            out.extend(fn())
        except Exception as exc:  # never let one launcher break discovery
            log.warning("instance discovery failed in %s: %s", fn.__name__, exc)
    return out


def all_vanilla_jars() -> dict[str, Path]:
    """Every vanilla client jar we can find, keyed by version id."""
    jars: dict[str, Path] = {}
    mc = default_minecraft_dir()
    if mc:
        jars.update(find_version_jars(mc / "versions"))
    for root in (_home() / "curseforge" / "minecraft" / "Install",):
        jars.update(find_version_jars(root / "versions"))
    for inst in _prism_like_instances() + _modrinth_instances():
        if inst.jar_path and inst.game_version:
            jars.setdefault(inst.game_version, inst.jar_path)
    return {k: v for k, v in jars.items() if re.fullmatch(r"\d+\.\d+(\.\d+)?", k)}


def manual_instance(jar: Optional[Path] = None, mods_dir: Optional[Path] = None, game_dir: Optional[Path] = None,
                    resourcepacks: Iterable[Path] = ()) -> Instance:
    inst = Instance(name="Custom", launcher="manual", game_dir=game_dir, jar_path=jar, mods_dir=mods_dir,
                    resourcepacks_dir=(game_dir / "resourcepacks") if game_dir else None)
    inst.extra_jars = list(resourcepacks)
    return inst
