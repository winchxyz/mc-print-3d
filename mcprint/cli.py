"""Command line interface.

Examples
--------
  mcprint convert castle.litematic -o out/castle.3mf --block-mm 4 --colors 4
  mcprint convert house.schem --target-size 180 --filaments "#FFFFFF,#000000,#C12E1F,#0A2989" --format 3mf-bambu
  mcprint convert base.schematic --instance "All the Mods 10" --fluids --format stl
  mcprint info castle.litematic
  mcprint instances
  mcprint printers --scan
  mcprint filaments --sync bambu:01P00A000000000
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .settings import ConversionSettings


def _add_convert_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", help="schematic file (.schematic .schem .litematic .nbt .mcstructure .bp)")
    p.add_argument("-o", "--output", help="output file (extension chosen by --format); default: <input>.3mf")
    p.add_argument("--format", choices=["3mf", "3mf-bambu", "stl", "stl-merged", "obj"], default=None)
    g = p.add_argument_group("scale")
    g.add_argument("--block-mm", type=float, help="size of one block in mm (default 5)")
    g.add_argument("--target-size", type=float, help="longest horizontal side in mm (overrides --block-mm)")
    g.add_argument("--fit-bed", action="store_true", help="scale to the printer bed given by --bed")
    g.add_argument("--resolution", type=int, help="sub-voxels per block (default: automatic from nozzle)")
    g.add_argument("--nozzle", type=float, help="nozzle diameter in mm (default 0.4)")
    g.add_argument("--thickness", type=float, help="minimum feature thickness in mm (default 0.8)")
    g.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], help="rotate the print around Z")
    g = p.add_argument_group("content")
    g.add_argument("--fluids", action="store_true", help="include water/lava as solid blocks")
    g.add_argument("--skip-unknown", action="store_true", help="skip blocks without models instead of printing cubes")
    g.add_argument("--no-islands", action="store_true", help="keep floating/disconnected block groups")
    g.add_argument("--ground-only", action="store_true", help="keep only block groups touching the ground")
    g.add_argument("--no-fill", action="store_true", help="do not fill enclosed cavities")
    g.add_argument("--base", type=float, help="add a base plate of this thickness in mm")
    g.add_argument("--no-dilate", action="store_true", help="do not thicken thin texture features")
    g.add_argument("--relief", type=float, help="surface relief depth in mm carved from textures (bricks, planks...); 0 = flat faces (default 0.5)")
    g.add_argument("--relief-mode", choices=["auto", "heightmap", "pattern", "dark", "light"], help="how texels map to depth (default auto)")
    g = p.add_argument_group("colors")
    g.add_argument("--color-mode", choices=["block", "texel", "single", "full"], help="block = flat color per block type (default), texel = per texture pixel")
    g.add_argument("--colors", type=int, help="number of colors / filament slots (default 4)")
    g.add_argument("--filaments", help="comma separated hex colors of loaded filaments, or 'printer:<id>' to sync, or 'slicer' to import presets")
    g.add_argument("--single-color", help="hex color for --color-mode single")
    g = p.add_argument_group("assets")
    g.add_argument("--instance", help="Minecraft instance name/label to take assets from (see 'mcprint instances')")
    g.add_argument("--jar", help="vanilla client jar to use instead of an instance")
    g.add_argument("--mods", help="mods folder to add")
    g.add_argument("--pack", action="append", default=[], help="extra resource pack / jar / folder (repeatable, highest priority)")
    g.add_argument("--no-mods", action="store_true")
    g = p.add_argument_group("printer / bed")
    g.add_argument("--bed", help="bed size XxYxZ in mm, e.g. 256x256x256 (used by --fit-bed and --split)")
    g.add_argument("--split", action="store_true", help="split into tiles that fit the bed")
    g.add_argument("--printer", help="saved printer profile id (fills --bed and slot count)")
    p.add_argument("-v", "--verbose", action="store_true")


def _settings_from_args(a: argparse.Namespace) -> ConversionSettings:
    s = ConversionSettings.load()
    if a.block_mm:
        s.block_mm = a.block_mm
    s.target_size_mm = a.target_size if a.target_size else None
    s.fit_to_bed = bool(a.fit_bed)
    if a.resolution:
        s.resolution = a.resolution
    if a.nozzle:
        s.nozzle_mm = a.nozzle
    if a.thickness is not None:
        s.min_thickness_mm = a.thickness
    if a.rotate is not None:
        s.rotate_deg = a.rotate
    s.include_fluids = bool(a.fluids)
    if a.skip_unknown:
        s.unknown_policy = "skip"
    if a.no_islands:
        s.remove_islands = False
    if a.ground_only:
        s.keep_ground_only = True
    if a.no_fill:
        s.fill_cavities = False
    if a.base:
        s.base_plate_mm = a.base
    if a.no_dilate:
        s.cutout_dilation = 0
    if a.relief is not None:
        s.relief_mm = max(0.0, a.relief)
    if a.relief_mode:
        s.relief_mode = a.relief_mode
    if a.color_mode:
        s.color_mode = a.color_mode
    if a.colors:
        s.max_colors = a.colors
    if a.single_color:
        s.single_color_hex = a.single_color
    if a.instance:
        s.instance_name = a.instance
    if a.no_mods:
        s.include_mods = False
    if a.format:
        s.export_format = a.format
    s.extra_asset_paths = list(a.pack or [])
    if a.printer:
        from .printers.manager import PrinterProfiles
        info = PrinterProfiles().get(a.printer)
        if info is None:
            raise SystemExit(f"unknown printer profile {a.printer!r}")
        if info.bed_x and info.bed_y and info.bed_z:
            s.bed_mm = [info.bed_x, info.bed_y, info.bed_z]
        if info.filament_slots and not a.filaments:
            from .color.filament import PrintPalette
            s.filaments = [f.to_dict() for f in PrintPalette.from_slots(info.filament_slots).filaments]
        if not a.colors:
            s.max_colors = info.multi_color_slots
    if a.bed:
        try:
            s.bed_mm = [float(v) for v in a.bed.lower().split("x")]
            assert len(s.bed_mm) == 3
        except Exception:
            raise SystemExit("--bed must look like 256x256x256")
    s.split_to_bed = bool(a.split)
    if a.filaments:
        s.filaments = [f.to_dict() for f in _parse_filaments(a.filaments)]
    return s


def _parse_filaments(spec: str):
    from .color.filament import Filament
    spec = spec.strip()
    if spec == "slicer":
        from .color.slicer_presets import import_slicer_filaments
        return import_slicer_filaments()
    if spec.startswith("printer:"):
        from .printers.manager import PrinterProfiles, sync_filaments
        from .color.filament import PrintPalette
        info = PrinterProfiles().get(spec[8:])
        if info is None:
            raise SystemExit(f"unknown printer profile {spec[8:]!r}")
        return PrintPalette.from_slots(sync_filaments(info, progress=print)).filaments
    if spec.startswith("catalog:"):
        from .color.catalog import catalog_filaments
        vendor = spec[8:] or None
        return catalog_filaments(vendor=vendor)
    out = []
    for i, h in enumerate(spec.split(",")):
        h = h.strip()
        if h:
            out.append(Filament(color_hex=h, name=f"Slot {i + 1} {h}"))
    return out


def cmd_convert(a: argparse.Namespace) -> int:
    from .pipeline import Converter
    from .assets import manual_instance
    s = _settings_from_args(a)
    inp = Path(a.input)
    if not inp.exists():
        print(f"error: {inp} does not exist", file=sys.stderr)
        return 2
    out = Path(a.output) if a.output else inp.with_suffix(".3mf")
    last = [""]

    def progress(frac: float, msg: str) -> None:
        if msg != last[0]:
            print(f"[{int(frac * 100):3d}%] {msg}")
            last[0] = msg

    conv = Converter(s, progress=progress)
    instance = None
    if a.jar or a.mods:
        instance = manual_instance(jar=Path(a.jar) if a.jar else None, mods_dir=Path(a.mods) if a.mods else None)
        if instance.jar_path is None:
            from .assets import all_vanilla_jars
            jars = all_vanilla_jars()
            if jars:
                from .assets.locator import version_key
                instance.jar_path = jars[sorted(jars, key=version_key)[-1]]
    res = conv.run(inp, out, instance=instance)
    print()
    print(f"Assets: {conv.instance.label if conv.instance else 'custom'}")
    print(f"Block size: {res.block_mm:.3f} mm, resolution {res.resolution}^3 per block (voxel {res.block_mm / res.resolution:.3f} mm)")
    if res.stats.get("relief_steps"):
        print(f"Surface relief: {res.stats['relief_steps']} voxel(s) = {res.stats['relief_steps'] * res.block_mm / res.resolution:.2f} mm")
    print(f"Print size: {res.size_text()}")
    st = res.stats
    print(f"Block types: {st.get('states')}")
    if st.get("fallback_states"):
        print(f"Fallback shapes used for {len(st['fallback_states'])} block types (e.g. {list(st['fallback_states'])[:3]})")
    if st.get("missing_blockstates"):
        print(f"Unknown blocks (no assets found): {len(st['missing_blockstates'])} e.g. {st['missing_blockstates'][:5]}")
    if res.plan:
        print("Colors:")
        print("  " + res.plan.summary().replace("\n", "\n  "))
    for w in res.warnings:
        print("warning:", w)
    for f in res.files:
        print("wrote", f)
    return 0


def cmd_info(a: argparse.Namespace) -> int:
    from .schematics import load_schematic
    s = load_schematic(a.input)
    x, y, z = s.size
    print(f"{a.input}: format={s.format} name={s.name!r} author={s.author!r} data_version={s.data_version}")
    print(f"size: X {x} x Y {y} x Z {z} blocks ; {s.block_count:,} non-air blocks ; {len(s.unique_states())} block states")
    counts = sorted(s.state_counts().items(), key=lambda kv: -kv[1])
    for st, c in counts[: a.top]:
        print(f"  {c:8,d}  {st}")
    ns = sorted({st.namespace for st in s.unique_states()})
    print("namespaces:", ", ".join(ns))
    for w in s.warnings:
        print("warning:", w)
    return 0


def cmd_instances(a: argparse.Namespace) -> int:
    from .assets import find_instances
    inst = find_instances()
    if not inst:
        print("No Minecraft installations found.")
        return 1
    for i in inst:
        print(i.describe())
    return 0


def cmd_printers(a: argparse.Namespace) -> int:
    from .printers.manager import PrinterProfiles
    profiles = PrinterProfiles()
    if a.scan:
        from .printers.discovery import discover_all
        found = discover_all(timeout=a.timeout, progress=print, use_subnet_scan=a.subnet)
        for p in found:
            print(f"{p.id:40s} {p.kind:10s} {p.name} ({p.model}) {p.host or p.serial_port or ''} {p.bed_text}")
            if a.save:
                profiles.add_or_update(p)
        if a.save:
            profiles.save()
        return 0
    if a.database:
        from .printers.database import PRINTER_SPECS
        for sp in PRINTER_SPECS:
            print(f"{sp.vendor:14s} {sp.model:32s} X {sp.bed_x:g} x Y {sp.bed_y:g} x Z {sp.bed_z:g} mm  slots={sp.slots}")
        return 0
    for p in profiles.all():
        print(f"{p.id:40s} {p.kind:10s} {p.name} ({p.model}) {p.bed_text} slots={p.multi_color_slots}")
    if not profiles.all():
        print("No saved printers. Run: mcprint printers --scan --save")
    return 0


def cmd_filaments(a: argparse.Namespace) -> int:
    from .color.filament import FilamentLibrary
    lib = FilamentLibrary()
    if a.sync:
        from .printers.manager import PrinterProfiles, sync_filaments
        info = PrinterProfiles().get(a.sync)
        if info is None:
            print(f"unknown printer {a.sync!r}", file=sys.stderr)
            return 2
        slots = sync_filaments(info, progress=print)
        for sl in slots:
            print(f"slot {sl.index + 1:2d} [{sl.group}] {sl.color_hex} {sl.material} {sl.name} {'(empty)' if not sl.loaded else ''}")
        return 0
    if a.import_slicer:
        from .color.slicer_presets import import_slicer_filaments
        fils = import_slicer_filaments()
        n = lib.add_many(fils)
        lib.save()
        print(f"imported {len(fils)} filaments from slicer presets ({n} new)")
    if a.catalog:
        from .color.catalog import catalog_filaments
        for f in catalog_filaments(vendor=a.catalog if a.catalog != "all" else None):
            print(f"{f.color_hex}  {f.label}  [{f.material}]")
        return 0
    for f in lib.filaments:
        print(f"{f.id}  {f.color_hex}  {f.label}  [{f.material}]")
    if not lib.filaments:
        print("Filament library is empty. Use --import-slicer, --sync <printer id>, or the GUI.")
    return 0


def cmd_gui(a: argparse.Namespace) -> int:
    from .app import main as gui_main
    return gui_main([])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcprint", description="Minecraft schematic -> 3D print converter")
    p.add_argument("--version", action="version", version=f"mc-print-3d {__version__}")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("convert", help="convert a schematic to a printable model")
    _add_convert_args(c)
    c.set_defaults(fn=cmd_convert)
    i = sub.add_parser("info", help="show schematic contents")
    i.add_argument("input")
    i.add_argument("--top", type=int, default=25)
    i.set_defaults(fn=cmd_info)
    n = sub.add_parser("instances", help="list Minecraft installations found on this machine")
    n.set_defaults(fn=cmd_instances)
    pr = sub.add_parser("printers", help="list / discover printers")
    pr.add_argument("--scan", action="store_true", help="discover printers on USB and the network")
    pr.add_argument("--subnet", action="store_true", help="also port-scan the local subnet")
    pr.add_argument("--save", action="store_true", help="save discovered printers as profiles")
    pr.add_argument("--timeout", type=float, default=6.0)
    pr.add_argument("--database", action="store_true", help="list the built-in printer database")
    pr.set_defaults(fn=cmd_printers)
    f = sub.add_parser("filaments", help="filament library")
    f.add_argument("--sync", help="printer profile id to read loaded filaments from (AMS, Spoolman...)")
    f.add_argument("--import-slicer", action="store_true", help="import filament colors from Bambu Studio / OrcaSlicer / PrusaSlicer presets")
    f.add_argument("--catalog", help="list catalog filaments of a vendor ('all' for everything)")
    f.set_defaults(fn=cmd_filaments)
    g = sub.add_parser("gui", help="start the desktop application")
    g.set_defaults(fn=cmd_gui)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not a.cmd:
        parser.print_help()
        return 0
    try:
        return int(a.fn(a) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
