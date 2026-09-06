"""3MF writer: one mesh object per print color with base materials, assembled into one build item.

Also supports a Bambu Studio / OrcaSlicer flavoured project file that carries per-part extruder
(filament slot) assignments and the filament colors, so the model opens with colors pre-assigned.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

import numpy as np

from ..voxel.mesher import Mesh, MeshSet

NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
 <Default Extension="png" ContentType="image/png"/>
</Types>
"""
RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _mesh_xml(obj_id: int, mesh: Mesh, pid: Optional[int], pindex: Optional[int], name: str) -> str:
    v = mesh.vertices.astype(np.float64)
    t = mesh.triangles.astype(np.int64)
    parts = [f'  <object id="{obj_id}" type="model" name="{escape(name)}"']
    if pid is not None and pindex is not None:
        parts.append(f' pid="{pid}" pindex="{pindex}"')
    parts.append(">\n   <mesh>\n    <vertices>\n")
    parts.append("".join(f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>\n' for x, y, z in v.tolist()))
    parts.append("    </vertices>\n    <triangles>\n")
    parts.append("".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in t.tolist()))
    parts.append("    </triangles>\n   </mesh>\n  </object>\n")
    return "".join(parts)


def write_3mf(path: str | Path, meshes: MeshSet, title: str = "mc-print-3d model",
              flavor: str = "generic") -> Path:
    """Write a 3MF.  flavor='generic' (any slicer) or 'bambu' (adds Bambu/Orca project metadata)."""
    path = Path(path)
    if path.suffix.lower() != ".3mf":
        path = path.with_suffix(".3mf")
    ms = meshes.meshes
    mat_lines = []
    for i, m in enumerate(ms):
        info = meshes.materials.get(m.material, {})
        color = info.get("color", m.color)
        hexcol = "#{:02X}{:02X}{:02X}FF".format(*[int(c) for c in color[:3]])
        mat_lines.append(f'   <base name="{escape(str(info.get("name") or m.name or f"material_{m.material}"))}" displaycolor="{hexcol}"/>')
    objects = []
    comp = []
    next_id = 2
    for i, m in enumerate(ms):
        info = meshes.materials.get(m.material, {})
        name = str(info.get("name") or m.name or f"material_{m.material}")
        objects.append(_mesh_xml(next_id, m, 1, i, name))
        comp.append(f'    <component objectid="{next_id}"/>')
        next_id += 1
    asm_id = next_id
    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{NS_CORE}">\n',
        f' <metadata name="Title">{escape(title)}</metadata>\n',
        ' <metadata name="Application">mc-print-3d</metadata>\n',
        ' <resources>\n',
        '  <basematerials id="1">\n', "\n".join(mat_lines), "\n  </basematerials>\n",
        "".join(objects),
    ]
    if len(ms) > 1:
        xml.append(f'  <object id="{asm_id}" type="model" name="{escape(title)}">\n   <components>\n' + "\n".join(comp) + "\n   </components>\n  </object>\n")
        build_id = asm_id
    else:
        build_id = 2 if ms else asm_id
    xml.append(" </resources>\n <build>\n")
    if ms:
        xml.append(f'  <item objectid="{build_id}"/>\n')
    xml.append(" </build>\n</model>\n")
    model_xml = "".join(xml)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model_xml)
        if flavor == "bambu":
            z.writestr("Metadata/model_settings.config", _bambu_model_settings(meshes, asm_id if len(ms) > 1 else build_id, title))
            z.writestr("Metadata/project_settings.config", _bambu_project_settings(meshes))
    return path


def _bambu_model_settings(meshes: MeshSet, obj_id: int, title: str) -> str:
    """Per-part extruder assignment understood by Bambu Studio / OrcaSlicer."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>", f'  <object id="{obj_id}">',
             f'    <metadata key="name" value="{escape(title)}"/>', '    <metadata key="extruder" value="1"/>']
    for i, m in enumerate(meshes.meshes):
        info = meshes.materials.get(m.material, {})
        slot = int(info.get("slot") or (i + 1))
        name = str(info.get("name") or m.name or f"material_{m.material}")
        lines.append(f'    <part id="{i + 2}" subtype="normal_part">')
        lines.append(f'      <metadata key="name" value="{escape(name)}"/>')
        lines.append(f'      <metadata key="extruder" value="{slot}"/>')
        lines.append(f'      <metadata key="source_object_id" value="0"/>')
        lines.append(f'      <metadata key="source_volume_id" value="{i}"/>')
        lines.append(f'      <mesh_stat face_count="{m.triangle_count}" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>')
        lines.append("    </part>")
    lines.append("  </object>")
    lines.append("  <plate>")
    lines.append('    <metadata key="plater_id" value="1"/>')
    lines.append('    <metadata key="plater_name" value=""/>')
    lines.append('    <metadata key="locked" value="false"/>')
    lines.append("    <model_instance>")
    lines.append(f'      <metadata key="object_id" value="{obj_id}"/>')
    lines.append('      <metadata key="instance_id" value="0"/>')
    lines.append('      <metadata key="identify_id" value="1"/>')
    lines.append("    </model_instance>")
    lines.append("  </plate>")
    lines.append("  <assemble>")
    lines.append(f'    <assemble_item object_id="{obj_id}" instance_id="0" transform="1 0 0 0 1 0 0 0 1 0 0 0" offset="0 0 0"/>')
    lines.append("  </assemble>")
    lines.append("</config>")
    return "\n".join(lines) + "\n"


def _bambu_project_settings(meshes: MeshSet) -> str:
    import json
    slots: dict[int, tuple[int, int, int]] = {}
    for i, m in enumerate(meshes.meshes):
        info = meshes.materials.get(m.material, {})
        slot = int(info.get("slot") or (i + 1))
        slots[slot] = tuple(int(c) for c in info.get("color", m.color)[:3])
    n = max(slots) if slots else 1
    colors = []
    for s in range(1, n + 1):
        c = slots.get(s, (255, 255, 255))
        colors.append("#{:02X}{:02X}{:02X}".format(*c))
    return json.dumps({"filament_colour": colors, "version": "01.00.00.00", "generated_by": "mc-print-3d"}, indent=2)


def read_3mf_summary(path: str | Path) -> dict:
    """Parse object/triangle counts and material colors back (verification / tests)."""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    ns = {"m": NS_CORE}
    objs = root.findall(".//m:object", ns)
    tri = sum(len(o.findall(".//m:triangle", ns)) for o in objs)
    mats = [b.get("displaycolor") for b in root.findall(".//m:base", ns)]
    return {"objects": len(objs), "triangles": tri, "materials": mats,
            "build_items": len(root.findall(".//m:item", ns))}
