"""Assembly instructions for a kit: parts list (CSV) and a self-contained HTML build guide with layer maps."""
from __future__ import annotations

import csv
from pathlib import Path
from xml.sax.saxutils import escape

from ..util.color import contrast_text_color, to_hex
from .layout import Plate
from .pieces import Kit


def write_parts_csv(kit: Kit, path: Path, plate_of: dict[str, list[int]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["piece", "block", "size_units", "axis", "quantity", "color_hex", "material", "stud", "socket", "base_tile", "width_mm", "depth_mm", "height_mm", "plates"])
        for p in kit.pieces:
            fp = kit.footprints.get(p.id, (0, 0, 0))
            mat = kit.material_info.get(p.material, {})
            size = f"{p.units_xz[0]}x{p.units_xz[1]}x{p.units_y}" if p.axis == "g" else f"{p.length}x1"
            w.writerow([p.id, p.label if p.axis == "g" else str(p.state), size, p.axis, p.count, to_hex(p.rgb), mat.get("name", p.material),
                        int(p.has_stud), int(p.has_socket), int(p.base_tile), f"{fp[0]:.1f}", f"{fp[1]:.1f}", f"{fp[2]:.1f}",
                        " ".join(str(i) for i in plate_of.get(p.id, []))])
    return path


def write_guide_html(kit: Kit, plates: list[Plate], path: Path, title: str, plate_of: dict[str, list[int]]) -> Path:
    s = kit.settings
    X, Y, Z = kit.size_blocks
    cell = 22 if max(X, Z) <= 40 else max(8, int(880 / max(X, Z)))
    parts = []
    parts.append(f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)} – assembly guide</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;background:#fafafa}}
h1{{margin:0 0 4px}} h2{{margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;font-size:13px}} th,td{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
.sw{{display:inline-block;width:14px;height:14px;border:1px solid #555;vertical-align:middle;margin-right:6px}}
.layer{{display:inline-block;margin:10px 18px 10px 0;vertical-align:top}} .layer h3{{margin:4px 0}}
svg{{background:#fff;border:1px solid #bbb}} .note{{color:#555;font-size:13px}}
</style></head><body>""")
    parts.append(f"<h1>{escape(title)} – modular kit</h1>")
    parts.append(f"<p class='note'>Model {X} × {Z} blocks footprint, {Y} layers · unit {s.unit_mm:g} mm · assembled size "
                 f"X {X * s.unit_mm:.0f} mm × Y {Z * s.unit_mm:.0f} mm × Z {Y * s.unit_mm:.0f} mm · "
                 f"{kit.total_pieces} pieces of {len(kit.pieces)} types · stud {s.stud_w:.1f} mm, socket clearance {s.fit_tolerance_mm:.2f} mm per side.</p>")
    parts.append("<h2>How to build</h2><ol class='note'>"
                 "<li>Print the plates (one filament color per plate) and the baseplate tiles. Pieces stand stud-up on the bed; nothing needs supports except detailed blocks with overhangs.</li>"
                 "<li>Lay out the baseplate tiles. Layer 1 is the bottom layer; press each piece onto the studs below it. Bars alternate direction between layers so walls interlock.</li>"
                 "<li>Piece ids (P001…) are printed nowhere – sort pieces by color and size using the parts table; the layer maps show which piece goes where and its orientation (long side along the arrow).</li>"
                 "<li>If studs are too tight, sand them lightly or re-export with a larger fit tolerance; too loose: smaller tolerance or a drop of glue.</li></ol>")
    # parts table
    parts.append("<h2>Parts</h2><table><tr><th>Piece</th><th>Block</th><th>Size</th><th>Qty</th><th>Color</th><th>Plates</th><th>Connectors</th></tr>")
    for p in kit.pieces:
        mat = kit.material_info.get(p.material, {})
        conn = ("stud " if p.has_stud else "") + ("socket " if p.has_socket else "") + ("base-tile" if p.base_tile else "")
        parts.append(f"<tr><td><b>{p.id}</b></td><td>{escape(p.state.path)}</td><td>{p.length}×1</td><td>{p.count}</td>"
                     f"<td><span class='sw' style='background:{to_hex(p.rgb)}'></span>{escape(str(mat.get('name', to_hex(p.rgb))))}</td>"
                     f"<td>{' '.join(str(i) for i in plate_of.get(p.id, []))}</td><td>{conn.strip()}</td></tr>")
    parts.append("</table>")
    if kit.baseplates:
        parts.append("<h2>Baseplate tiles</h2><table><tr><th>Tile</th><th>Cells X</th><th>Cells Z</th><th>Size</th></tr>")
        for bp in kit.baseplates:
            parts.append(f"<tr><td>{bp.id}</td><td>{bp.x0 + 1}–{bp.x1}</td><td>{bp.z0 + 1}–{bp.z1}</td>"
                         f"<td>{(bp.x1 - bp.x0) * s.unit_mm:.0f} × {(bp.z1 - bp.z0) * s.unit_mm:.0f} mm</td></tr>")
        parts.append("</table>")
    # plates
    parts.append("<h2>Print plates</h2><table><tr><th>Plate</th><th>Filament</th><th>Pieces</th></tr>")
    for pl in plates:
        mat = kit.material_info.get(pl.material, {})
        parts.append(f"<tr><td>{pl.index}</td><td><span class='sw' style='background:{to_hex(pl.rgb)}'></span>{escape(str(mat.get('name', to_hex(pl.rgb))))}</td><td>{pl.count}</td></tr>")
    parts.append("</table>")
    # layer maps
    parts.append("<h2>Layer maps</h2><p class='note'>Top view. X runs left to right, Z (Minecraft south) runs top to bottom, exactly like the schematic seen from above. Layer 1 sits on the baseplate.</p>")
    layers = kit.layers()
    ghosts: dict[int, list] = {}            # layer -> [(placement, dx, dz)]
    for pl in kit.placements:
        if pl.piece.axis == "g":
            for dx, dy, dz in pl.piece.cells:
                if dy > 0:
                    ghosts.setdefault(pl.y + dy, []).append((pl, dx, dz))
            continue
        for k in range(1, pl.units_y):
            ghosts.setdefault(pl.y + k, []).append((pl, 0, 0))
    all_layers = sorted(set(layers) | set(ghosts))
    for y in all_layers:
        parts.append(f"<div class='layer'><h3>Layer {y + 1}</h3>")
        parts.append(f"<svg width='{X * cell + 1}' height='{Z * cell + 1}' viewBox='0 0 {X * cell + 1} {Z * cell + 1}'>")
        for pl, gdx, gdz in ghosts.get(y, []):
            x0, y0 = (pl.x + gdx) * cell, (pl.z + gdz) * cell
            parts.append(f"<rect x='{x0 + 0.5}' y='{y0 + 0.5}' width='{cell}' height='{cell}' fill='{to_hex(pl.piece.rgb)}' fill-opacity='0.35' stroke='#333' stroke-dasharray='3,2'/>")
            if cell >= 14:
                parts.append(f"<text x='{x0 + cell / 2}' y='{y0 + cell / 2 + 4}' font-size='{max(7, cell * 0.38):.0f}' text-anchor='middle' fill='#333'>↑{pl.piece.id[1:].lstrip('0')}</text>")
        for pl in layers.get(y, []):
            p = pl.piece
            if p.axis == "g":
                col = to_hex(p.rgb)
                txt = contrast_text_color(p.rgb)
                for dx, dy, dz in p.cells:
                    if dy:
                        continue
                    cx, cy = (pl.x + dx) * cell, (pl.z + dz) * cell
                    parts.append(f"<rect x='{cx + 0.5}' y='{cy + 0.5}' width='{cell}' height='{cell}' fill='{col}' stroke='#333' stroke-width='1'/>")
                    parts.append(f"<rect x='{cx + 2.5}' y='{cy + 2.5}' width='{cell - 4}' height='{cell - 4}' fill='none' stroke='{txt}' stroke-dasharray='1,2' stroke-width='1'/>")
                gx = min(dx for dx, dy, dz in p.cells if dy == 0); gz = min(dz for dx, dy, dz in p.cells if dy == 0)
                if cell >= 14:
                    parts.append(f"<text x='{(pl.x + gx) * cell + cell / 2}' y='{(pl.z + gz) * cell + cell / 2 + 4}' font-size='{max(7, cell * 0.42):.0f}' text-anchor='middle' fill='{txt}'>{p.id[1:].lstrip('0') or '0'}</text>")
                continue
            w_units, d_units = (p.length, 1) if p.axis == "x" else (1, p.length)
            if p.axis == "y":
                w_units, d_units = 1, 1
            x0, y0 = pl.x * cell, pl.z * cell
            w, h = w_units * cell, d_units * cell
            col = to_hex(p.rgb)
            parts.append(f"<rect x='{x0 + 0.5}' y='{y0 + 0.5}' width='{w}' height='{h}' fill='{col}' stroke='#333' stroke-width='1'/>")
            if cell >= 14:
                txt = contrast_text_color(p.rgb)
                label = p.id[1:].lstrip("0") or "0"
                parts.append(f"<text x='{x0 + w / 2}' y='{y0 + h / 2 + 4}' font-size='{max(7, cell * 0.42):.0f}' text-anchor='middle' fill='{txt}'>{label}</text>")
                if not p.cube:
                    parts.append(f"<rect x='{x0 + 2.5}' y='{y0 + 2.5}' width='{w - 4}' height='{h - 4}' fill='none' stroke='{txt}' stroke-dasharray='2,2' stroke-width='1'/>")
        # grid
        for i in range(X + 1):
            parts.append(f"<line x1='{i * cell + 0.5}' y1='0' x2='{i * cell + 0.5}' y2='{Z * cell}' stroke='#e2e2e2' stroke-width='0.5'/>")
        for j in range(Z + 1):
            parts.append(f"<line x1='0' y1='{j * cell + 0.5}' x2='{X * cell}' y2='{j * cell + 0.5}' stroke='#e2e2e2' stroke-width='0.5'/>")
        parts.append("</svg></div>")
    parts.append("<p class='note'>Numbers are piece ids without the leading P; dashed outlines mark detailed (non-cube) pieces whose orientation follows the block's facing in the schematic. "
                 "A faded cell with ↑ belongs to a two-block-tall piece (door, tall plant) or a mob figure placed on the layer below; "
                 "figures (dotted outline) are one piece with a socket under every ground cell.</p>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
