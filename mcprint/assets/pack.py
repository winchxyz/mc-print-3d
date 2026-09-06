"""Reading resources from jars, zips and folders, layered like Minecraft's resource pack stack.

Priority: resource packs (highest) > mod jars > vanilla jar.  Nested "jar-in-jar" mods
(Forge ``META-INF/jarjar``, Fabric ``META-INF/jars``) are unpacked in memory when they contain assets.
"""
from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


class AssetSource:
    """One jar/zip/folder.  Only ``assets/...`` and ``pack.mcmeta`` entries are indexed."""

    def __init__(self, path: Path, label: str = "", data: Optional[bytes] = None):
        self.path = Path(path)
        self.label = label or self.path.name
        self._zip: Optional[zipfile.ZipFile] = None
        self._names: set[str] = set()
        self._dir_root: Optional[Path] = None
        self.namespaces: set[str] = set()
        self.nested: list["AssetSource"] = []
        self.ok = False
        self.error = ""
        try:
            if data is not None:
                self._zip = zipfile.ZipFile(io.BytesIO(data))
                self._index_zip()
            elif self.path.is_dir():
                self._dir_root = self.path
                self._index_dir()
            else:
                self._zip = zipfile.ZipFile(self.path)
                self._index_zip()
            self.ok = True
        except Exception as exc:
            self.error = str(exc)
            log.warning("cannot open asset source %s: %s", path, exc)

    # -- indexing ----------------------------------------------------------------------
    def _index_zip(self) -> None:
        assert self._zip is not None
        nested_jars = []
        for info in self._zip.infolist():
            n = info.filename
            if n.startswith("assets/") and not n.endswith("/"):
                self._names.add(n)
                parts = n.split("/", 2)
                if len(parts) >= 2:
                    self.namespaces.add(parts[1])
            elif n == "pack.mcmeta":
                self._names.add(n)
            elif (n.startswith("META-INF/jarjar/") or n.startswith("META-INF/jars/")) and n.endswith(".jar"):
                nested_jars.append(n)
        for nj in nested_jars:
            try:
                data = self._zip.read(nj)
                sub = AssetSource(self.path / nj, label=f"{self.label}!{Path(nj).name}", data=data)
                if sub.ok and sub._names:
                    self.nested.append(sub)
            except Exception as exc:
                log.debug("nested jar %s unreadable: %s", nj, exc)

    def _index_dir(self) -> None:
        assert self._dir_root is not None
        root = self._dir_root
        adir = root / "assets"
        if adir.is_dir():
            for dirpath, _dirs, files in os.walk(adir):
                rel_dir = Path(dirpath).relative_to(root).as_posix()
                for f in files:
                    n = f"{rel_dir}/{f}"
                    self._names.add(n)
                    parts = n.split("/", 2)
                    if len(parts) >= 2:
                        self.namespaces.add(parts[1])
        if (root / "pack.mcmeta").is_file():
            self._names.add("pack.mcmeta")

    # -- access ------------------------------------------------------------------------
    def has(self, name: str) -> bool:
        if name in self._names:
            return True
        return any(s.has(name) for s in self.nested)

    def read(self, name: str) -> Optional[bytes]:
        if name in self._names:
            try:
                if self._zip is not None:
                    return self._zip.read(name)
                assert self._dir_root is not None
                return (self._dir_root / name).read_bytes()
            except Exception as exc:
                log.debug("read %s from %s failed: %s", name, self.label, exc)
                return None
        for s in self.nested:
            d = s.read(name)
            if d is not None:
                return d
        return None

    def names(self) -> Iterable[str]:
        yield from self._names
        for s in self.nested:
            yield from s.names()

    @property
    def count(self) -> int:
        return len(self._names) + sum(s.count for s in self.nested)

    def close(self) -> None:
        if self._zip is not None:
            try:
                self._zip.close()
            except Exception:
                pass
        for s in self.nested:
            s.close()


class AssetStack:
    """Ordered list of sources; ``read`` returns the first hit (highest priority first)."""

    def __init__(self):
        self.sources: list[AssetSource] = []
        self._json_cache: dict[str, Optional[dict]] = {}
        self._exists_cache: dict[str, bool] = {}

    # -- building ----------------------------------------------------------------------
    def add(self, path: Path | str, label: str = "", front: bool = False) -> Optional[AssetSource]:
        src = AssetSource(Path(path), label)
        if not src.ok:
            return None
        if front:
            self.sources.insert(0, src)
        else:
            self.sources.append(src)
        self._json_cache.clear()
        self._exists_cache.clear()
        return src

    @classmethod
    def from_instance(cls, instance, progress: Optional[ProgressFn] = None, include_mods: bool = True,
                      resource_packs: Optional[Iterable[Path]] = None, use_enabled_packs: bool = True) -> "AssetStack":
        """Vanilla jar (lowest priority) + mod jars + resource packs (highest priority)."""
        stack = cls()
        if instance.jar_path:
            if progress:
                progress(f"Loading vanilla assets: {instance.jar_path.name}")
            stack.add(instance.jar_path, label=f"vanilla {instance.game_version or instance.jar_path.stem}")
        if include_mods:
            jars = instance.mod_jars
            for i, jar in enumerate(jars):
                if progress and (i % 25 == 0 or i == len(jars) - 1):
                    progress(f"Indexing mods {i + 1}/{len(jars)}: {jar.name}")
                src = stack.add(jar, label=jar.name)
                if src is not None and src.count == 0 and not src.nested:
                    stack.sources.remove(src)
        packs = list(resource_packs) if resource_packs is not None else (instance.enabled_resource_packs() if use_enabled_packs else [])
        for p in packs:
            if progress:
                progress(f"Loading resource pack: {Path(p).name}")
            stack.add(p, label=f"pack {Path(p).name}", front=True)
        for extra in getattr(instance, "extra_jars", []) or []:
            stack.add(extra, label=Path(extra).name, front=True)
        return stack

    # -- lookup ------------------------------------------------------------------------
    def exists(self, name: str) -> bool:
        v = self._exists_cache.get(name)
        if v is None:
            v = any(s.has(name) for s in self.sources)
            self._exists_cache[name] = v
        return v

    def read(self, name: str) -> Optional[bytes]:
        for s in self.sources:
            if s.has(name):
                d = s.read(name)
                if d is not None:
                    return d
        return None

    def read_json(self, name: str) -> Optional[dict]:
        if name in self._json_cache:
            return self._json_cache[name]
        raw = self.read(name)
        val: Optional[dict] = None
        if raw is not None:
            try:
                txt = raw.decode("utf-8-sig", errors="replace")
                val = json.loads(txt)
                if not isinstance(val, dict):
                    val = None
            except Exception as exc:
                log.debug("bad json %s: %s", name, exc)
                val = _lenient_json(raw)
        self._json_cache[name] = val
        return val

    def namespaces(self) -> set[str]:
        ns: set[str] = set()
        for s in self.sources:
            ns |= s.namespaces
            for n in s.nested:
                ns |= n.namespaces
        return ns

    def list_prefix(self, prefix: str, suffix: str = "") -> list[str]:
        out = set()
        for s in self.sources:
            for n in s.names():
                if n.startswith(prefix) and n.endswith(suffix):
                    out.add(n)
        return sorted(out)

    def describe(self) -> str:
        return "\n".join(f"{i:3d}. {s.label} ({s.count} assets)" for i, s in enumerate(self.sources))

    def close(self) -> None:
        for s in self.sources:
            s.close()

    def __len__(self) -> int:
        return len(self.sources)


def _lenient_json(raw: bytes) -> Optional[dict]:
    """Some mod JSONs contain comments or trailing commas; strip them and retry."""
    import re
    txt = raw.decode("utf-8-sig", errors="replace")
    txt = re.sub(r"//[^\n]*", "", txt)
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    txt = re.sub(r",\s*([}\]])", r"\1", txt)
    try:
        v = json.loads(txt)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


# --------------------------------------------------------------------------------------
# Resource location helpers
# --------------------------------------------------------------------------------------
def split_resource(res: str, default_ns: str = "minecraft") -> tuple[str, str]:
    res = res.strip()
    if ":" in res:
        ns, path = res.split(":", 1)
        return (ns or default_ns), path
    return default_ns, res


def blockstate_path(block_name: str) -> str:
    ns, path = split_resource(block_name)
    return f"assets/{ns}/blockstates/{path}.json"


def model_path(model_res: str) -> str:
    ns, path = split_resource(model_res)
    return f"assets/{ns}/models/{path}.json"


def texture_path(tex_res: str) -> str:
    ns, path = split_resource(tex_res)
    if path.endswith(".png"):
        path = path[:-4]
    return f"assets/{ns}/textures/{path}.png"
