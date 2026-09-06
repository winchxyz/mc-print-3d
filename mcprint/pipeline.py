"""End-to-end conversion driver shared by the CLI and the GUI.

Stages (each reports progress and can be cancelled):
  1. load schematic            -> Schematic
  2. build assets              -> AssetStack (vanilla + mods + resource packs)
  3. voxelize                  -> VoxelModel (per-state texture-aware patterns, block ops)
  4. plan colors               -> ColorPlan (clusters -> materials / filaments)
  5. mesh + scale              -> MeshSet in millimetres, print axes (X right, Y back, Z up)
  6. export                    -> files
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .assets import AssetStack, Instance, ModelResolver, TextureLoader, find_instances
from .color.filament import Filament, PrintPalette
from .color.quantize import ColorPlan, assign_filaments, plan_clusters, plan_full, plan_single
from .export import plan_tiles, write_3mf, write_obj, write_stl_set
from .schematics import Schematic, load_schematic
from .settings import ConversionSettings
from .util.color import parse_hex
from .voxel import (BlockVoxelizer, Cancelled, ColorIndex, MeshSet, VoxelModel, VoxelSettings, add_base_plate,
                    crop_model, fill_cavities, remove_islands)
from .voxel.connections import infer_connections

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


@dataclass
class ConversionResult:
    schematic: Optional[Schematic] = None
    model: Optional[VoxelModel] = None
    plan: Optional[ColorPlan] = None
    meshes: Optional[MeshSet] = None            # in mm, print axes
    files: list[Path] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    block_mm: float = 0.0
    resolution: int = 0
    timings: dict = field(default_factory=dict)
    kit: object = None                          # mcprint.kit.Kit when build_type == 'kit'
    plates: list = field(default_factory=list)

    @property
    def size_mm(self) -> tuple[float, float, float]:
        if self.meshes is None:
            return (0.0, 0.0, 0.0)
        lo, hi = self.meshes.bounds()
        return tuple(float(v) for v in (hi - lo))

    def size_text(self) -> str:
        x, y, z = self.size_mm
        return f"X {x:.1f} mm x Y {y:.1f} mm x Z {z:.1f} mm"


class Converter:
    def __init__(self, settings: ConversionSettings, progress: Optional[ProgressFn] = None, cancel: Optional[CancelFn] = None):
        self.settings = settings
        self._progress = progress
        self._cancel = cancel
        self.assets: Optional[AssetStack] = None
        self.resolver: Optional[ModelResolver] = None
        self.textures: Optional[TextureLoader] = None
        self.instance: Optional[Instance] = None

    # ---- helpers -------------------------------------------------------------------------
    def progress(self, frac: float, msg: str) -> None:
        log.info("%3d%% %s", int(frac * 100), msg)
        if self._progress:
            self._progress(frac, msg)

    def check_cancel(self) -> None:
        if self._cancel and self._cancel():
            raise Cancelled()

    # ---- stage 1: schematic --------------------------------------------------------------
    def load(self, path: str | Path) -> Schematic:
        self.progress(0.0, f"Loading {Path(path).name}")
        s = load_schematic(path)
        if self.settings.crop:
            s = s.cropped()
        s.compact_palette()
        x, y, z = s.size
        self.progress(0.05, f"Loaded {s.format}: X {x} x Y {y} x Z {z} blocks, {s.block_count:,} blocks, {len(s.unique_states()):,} states")
        return s

    # ---- stage 2: assets -----------------------------------------------------------------
    def use_assets(self, stack: AssetStack, instance: Optional[Instance] = None) -> None:
        self.assets = stack
        self.instance = instance
        self.resolver = ModelResolver(stack)
        self.textures = TextureLoader(stack)

    def build_assets(self, instance: Optional[Instance] = None, schematic: Optional[Schematic] = None) -> AssetStack:
        inst = instance or self.pick_instance(schematic)
        if inst is None:
            raise RuntimeError("No Minecraft installation found. Install Minecraft or point the app at a client jar.")
        stack = AssetStack.from_instance(inst, progress=lambda m: self.progress(0.08, m), include_mods=self.settings.include_mods,
                                         use_enabled_packs=self.settings.use_resource_packs)
        for extra in self.settings.extra_asset_paths:
            stack.add(Path(extra), label=Path(extra).name, front=True)
        self.use_assets(stack, inst)
        self.progress(0.1, f"Assets ready: {len(stack)} sources, {len(stack.namespaces())} namespaces")
        return stack

    def pick_instance(self, schematic: Optional[Schematic] = None) -> Optional[Instance]:
        instances = find_instances()
        if not instances:
            return None
        if self.settings.instance_name:
            for i in instances:
                if i.label == self.settings.instance_name or i.name == self.settings.instance_name:
                    return i
        # prefer an instance whose mods cover the schematic's namespaces
        if schematic is not None:
            ns_needed = {st.namespace for st in schematic.unique_states()} - {"minecraft"}
            if ns_needed:
                best, best_score = None, -1
                for i in instances:
                    jars = i.mod_jars
                    if not jars:
                        continue
                    names = " ".join(j.name.lower() for j in jars)
                    score = sum(1 for ns in ns_needed if ns.replace("_", "") in names.replace("_", "").replace("-", ""))
                    if score > best_score:
                        best, best_score = i, score
                if best is not None and best_score > 0 and best.jar_path:
                    return best
        # newest vanilla with a jar
        with_jar = [i for i in instances if i.jar_path]
        if not with_jar:
            return None
        vanilla = [i for i in with_jar if i.launcher == "vanilla" and not i.loader]
        pool = vanilla or with_jar
        from .assets.locator import version_key
        return sorted(pool, key=lambda i: version_key(i.game_version), reverse=True)[0]

    # ---- stage 3: voxel model ------------------------------------------------------------
    def compute_block_mm(self, schem: Schematic) -> float:
        s = self.settings
        x, y, z = schem.size
        if s.fit_to_bed and s.bed_mm:
            bx, by, bz = s.bed_mm
            m = s.bed_margin_mm
            horiz = (x, z) if s.rotate_deg in (0, 180) else (z, x)
            return max(0.2, min((bx - m) / max(horiz[0], 1), (by - m) / max(horiz[1], 1), bz / max(y, 1)))
        if s.target_size_mm:
            longest = max(x, z, 1)
            return max(0.2, s.target_size_mm / longest)
        return s.block_mm

    def voxel_settings(self, block_mm: float) -> VoxelSettings:
        s = self.settings
        n = s.effective_resolution(block_mm)
        return VoxelSettings(resolution=n, min_thickness=s.min_thickness_units(block_mm), alpha_threshold=s.alpha_threshold,
                             cutout_dilation=s.cutout_dilation, solid_textures=tuple(s.solid_textures),
                             translucent_as_solid=s.translucent_as_solid, include_fluids=s.include_fluids,
                             unknown_policy=s.unknown_policy, relief_depth=s.relief_units(block_mm), relief_mode=s.relief_mode)

    def kit_settings(self, block_mm: float):
        from .kit import KitSettings
        s = self.settings
        bed = tuple(float(v) for v in (s.bed_mm or [256.0, 256.0, 256.0]))
        return KitSettings(unit_mm=block_mm, fit_tolerance_mm=s.kit_fit_mm, max_piece_len=max(1, int(s.kit_max_len)),
                           alternate_layers=s.kit_alternate, textured=bool(s.kit_textured and s.style == "textured"),
                           detailed=s.kit_detailed, skip_non_cube=False, baseplate=s.kit_baseplate,
                           bed_mm=bed, bed_margin_mm=s.bed_margin_mm)

    def build_model(self, schem: Schematic, block_mm: Optional[float] = None) -> tuple[VoxelModel, dict]:
        if self.resolver is None or self.textures is None:
            self.build_assets(schematic=schem)
        assert self.resolver and self.textures
        block_mm = block_mm or self.compute_block_mm(schem)
        vs = self.voxel_settings(block_mm)
        stats: dict = {"block_mm": block_mm, "resolution": vs.resolution, "voxel_mm": block_mm / vs.resolution}
        t0 = time.time()
        if self.settings.infer_connections:
            try:
                changed = infer_connections(schem, self.resolver)
                if changed:
                    stats["connections_inferred"] = changed
            except Exception as exc:
                log.warning("connection inference failed: %s", exc)
        self.check_cancel()
        colors = ColorIndex()
        vox = BlockVoxelizer(self.resolver, self.textures, vs, colors)
        patterns = []
        total = len(schem.palette)
        for i, st in enumerate(schem.palette):
            if i % 50 == 0:
                self.check_cancel()
                self.progress(0.12 + 0.4 * i / max(total, 1), f"Voxelizing block types {i + 1}/{total}: {st.path}")
            patterns.append(vox.voxelize(st))
        if self.settings.style == "cubes" or (self.settings.build_type == "kit" and not self.settings.kit_detailed):
            for i, p in enumerate(patterns):
                if i and not p.is_empty and not p.is_full:
                    patterns[i] = vox.full_pattern(p.avg_rgb or (128, 128, 128))
        model = VoxelModel(blocks=schem.blocks.copy(), palette=list(schem.palette), patterns=patterns, resolution=vs.resolution, colors=colors,
                           relief_steps=vox.relief_steps)
        stats["voxelize_s"] = round(time.time() - t0, 2)
        stats["relief_steps"] = vox.relief_steps
        stats["states"] = dict(vox.stats)
        stats["fallback_states"] = dict(vox.fallback_states)
        stats["missing_blockstates"] = sorted(self.resolver.missing_blockstates)
        stats["missing_models"] = sorted(self.resolver.missing_models)
        stats["missing_textures"] = sorted(self.textures.missing)
        # ---- block-level operations
        self.progress(0.55, "Cleaning up: islands / cavities / base")
        full = lambda rgb: vox.full_pattern(rgb)
        if self.settings.remove_islands:
            removed = remove_islands(model, self.settings.island_min_blocks, self.settings.keep_ground_only)
            stats["islands_removed_blocks"] = removed
        if self.settings.fill_cavities and self.settings.build_type != "kit":
            filled = fill_cavities(model, full)
            stats["cavity_blocks_filled"] = filled
        if self.settings.base_plate_mm > 0:
            layers = max(1, int(math.ceil(self.settings.base_plate_mm / block_mm)))
            rgb = parse_hex(self.settings.single_color_hex) if self.settings.color_mode == "single" else (110, 110, 110)
            add_base_plate(model, layers, rgb, full, self.settings.base_margin_blocks)
            stats["base_layers"] = layers
        if self.settings.crop:
            crop_model(model)
        if self.settings.color_mode == "block":
            model.build_flat_patterns()
        x, y, z = model.block_size
        stats["blocks"] = (x, y, z)
        stats["voxels"] = model.voxel_size
        stats["solid_voxels"] = int(sum(p.count for p in model.patterns) and np.sum([model.patterns[i].count for i in model.blocks.ravel()]))
        self.progress(0.6, f"Voxel model: X {x} x Y {y} x Z {z} blocks at {vs.resolution}³ per block")
        return model, stats

    # ---- stage 4: colors -----------------------------------------------------------------
    def palette(self) -> PrintPalette:
        return PrintPalette([Filament.from_dict(d) for d in self.settings.filaments])

    def plan_colors(self, model: VoxelModel, filaments: Optional[list[Filament]] = None, max_colors: Optional[int] = None) -> ColorPlan:
        s = self.settings
        flat = s.color_mode == "block"
        hist = model.color_histogram(flat=flat)
        pal = model.colors.palette()
        if s.color_mode == "single":
            return plan_single(hist, pal, rgb=parse_hex(s.single_color_hex))
        if s.color_mode == "full":
            return plan_full(hist, pal)
        fils = filaments if filaments is not None else self.palette().filaments
        k = max_colors or s.max_colors
        if fils and s.auto_assign:
            k = max(k, len(fils)) if s.color_mode == "texel" else max(k, min(len(fils) * 2, 32))
        plan = plan_clusters(hist, pal, k)
        if fils and s.auto_assign:
            assign_filaments(plan, fils, max_slots=max_colors or s.max_colors)
        return plan

    # ---- stage 5: mesh -------------------------------------------------------------------
    def mesh(self, model: VoxelModel, plan: ColorPlan, block_mm: float, full_color: bool = False) -> MeshSet:
        flat = self.settings.color_mode == "block" and not full_color
        lut = None if full_color else plan.lut
        info = None if full_color else plan.material_info()
        ms = model.mesh(mat_lut=lut, material_info=info, flat=flat,
                        progress=lambda f, m: self.progress(0.62 + 0.3 * f, m), cancel=self._cancel)
        return self.finish_mesh(ms, model, block_mm)

    def finish_mesh(self, ms: MeshSet, model: VoxelModel, block_mm: float) -> MeshSet:
        scale = block_mm / model.resolution
        rot = self.settings.rotate_deg % 360
        out = MeshSet(materials=ms.materials, unit=1.0)
        for m in ms.meshes:
            v = m.vertices.astype(np.float64) * scale
            if rot:
                a = math.radians(rot)
                c, s_ = math.cos(a), math.sin(a)
                x, y = v[:, 0].copy(), v[:, 1].copy()
                v[:, 0] = c * x - s_ * y
                v[:, 1] = s_ * x + c * y
            out.meshes.append(type(m)(m.material, v.astype(np.float32), m.triangles, m.name, m.color))
        if rot and out.meshes:
            lo, _ = out.bounds()
            for m in out.meshes:
                m.vertices[:, 0] -= np.float32(lo[0])
                m.vertices[:, 1] -= np.float32(lo[1])
        return out

    # ---- stage 6: export -----------------------------------------------------------------
    def export(self, ms: MeshSet, out_path: str | Path, fmt: Optional[str] = None, title: str = "mc-print-3d") -> list[Path]:
        fmt = fmt or self.settings.export_format
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "3mf":
            return [write_3mf(out_path.with_suffix(".3mf"), ms, title=title)]
        if fmt == "3mf-bambu":
            return [write_3mf(out_path.with_suffix(".3mf"), ms, title=title, flavor="bambu")]
        if fmt == "stl":
            return write_stl_set(out_path.with_suffix(".stl"), ms)
        if fmt == "stl-merged":
            return write_stl_set(out_path.with_suffix(".stl"), ms, merged=True)
        if fmt == "obj":
            return write_obj(out_path.with_suffix(".obj"), ms, object_name=title)
        raise ValueError(f"unknown export format {fmt}")

    # ---- everything ----------------------------------------------------------------------
    def run(self, path: str | Path, out_path: Optional[str | Path] = None, instance: Optional[Instance] = None,
            fmt: Optional[str] = None) -> ConversionResult:
        t0 = time.time()
        res = ConversionResult()
        schem = self.load(path)
        res.schematic = schem
        res.warnings.extend(schem.warnings)
        if self.assets is None:
            self.build_assets(instance=instance, schematic=schem)
        block_mm = self.compute_block_mm(schem)
        model, stats = self.build_model(schem, block_mm)
        res.model, res.stats, res.block_mm, res.resolution = model, stats, block_mm, model.resolution
        res.timings["voxelize"] = time.time() - t0
        self.progress(0.6, "Planning colors")
        plan = self.plan_colors(model)
        res.plan = plan
        t1 = time.time()
        if self.settings.build_type == "kit":
            res.meshes = self.mesh(model, plan, block_mm)
            from .kit import build_kit, write_kit
            kit, plates = build_kit(model, plan, self.kit_settings(block_mm), progress=self.progress)
            res.kit, res.plates = kit, plates
            res.stats["kit"] = {"pieces": kit.total_pieces, "types": len(kit.pieces), "plates": len(plates), "baseplates": len(kit.baseplates)}
            if out_path:
                kit_dir = Path(out_path).parent / (Path(out_path).stem + "_kit")
                self.progress(0.95, f"Writing kit to {kit_dir}")
                res.files = write_kit(kit, plates, kit_dir, Path(path).stem, progress=self.progress)
        elif self.settings.split_to_bed and self.settings.bed_mm:
            res.meshes, res.files = self._export_tiles(model, plan, block_mm, out_path, fmt, Path(path).stem)
        else:
            ms = self.mesh(model, plan, block_mm)
            res.meshes = ms
            if out_path:
                self.progress(0.95, "Writing files")
                res.files = self.export(ms, out_path, fmt, title=Path(path).stem)
        res.timings["mesh_export"] = time.time() - t1
        res.timings["total"] = time.time() - t0
        res.stats["triangles"] = res.meshes.triangle_count if res.meshes else 0
        self.progress(1.0, f"Done: {res.size_text()}, {res.stats['triangles']:,} triangles")
        return res

    def _export_tiles(self, model: VoxelModel, plan: ColorPlan, block_mm: float, out_path, fmt, title):
        s = self.settings
        bed = tuple(float(v) for v in s.bed_mm)  # type: ignore[arg-type]
        x, y, z = model.block_size
        if s.rotate_deg in (90, 270):
            bed = (bed[1], bed[0], bed[2])
        tiles = plan_tiles((x, y, z), block_mm, bed, s.bed_margin_mm)
        files: list[Path] = []
        combined = MeshSet(materials=plan.material_info())
        for i, t in enumerate(tiles):
            self.check_cancel()
            self.progress(0.62 + 0.3 * i / len(tiles), f"Meshing tile {i + 1}/{len(tiles)}")
            sub = VoxelModel(blocks=model.blocks[t.y0:t.y1, t.z0:t.z1, t.x0:t.x1].copy(), palette=model.palette,
                             patterns=model.patterns, resolution=model.resolution, colors=model.colors,
                             flat_patterns=model.flat_patterns, relief_steps=model.relief_steps)
            ms = sub.mesh(mat_lut=plan.lut, material_info=plan.material_info(), flat=(s.color_mode == "block"))
            ms = self.finish_mesh(ms, sub, block_mm)
            if out_path:
                p = Path(out_path)
                tile_path = p.parent / f"{p.stem}_{t.label}{p.suffix}"
                files.extend(self.export(ms, tile_path, fmt, title=f"{title} {t.label}"))
            # offset tiles for a combined preview (place side by side)
            off = np.array([t.x0 * block_mm, (z - t.z1) * block_mm, t.y0 * block_mm], dtype=np.float32)
            for m in ms.meshes:
                combined.meshes.append(type(m)(m.material, m.vertices + off, m.triangles, m.name, m.color))
        return combined, files
