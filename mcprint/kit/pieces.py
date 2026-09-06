"""Modular kit: turn a voxel model into printable pieces with studs and sockets.

Every Minecraft block becomes one unit cell of ``unit_mm``.  Runs of identical full-cube blocks
are merged into bars (1x2 ... 1xN) and the merge direction alternates per layer, so the assembled
walls are bonded like brickwork.  Each piece carries a square stud on top of every unit and a
matching socket underneath (LEGO-style clutch); blocks with detailed geometry (stairs, fences,
flowers, doors...) keep their shape and get a thin base tile with a socket when their own bottom is
too thin.  Piece geometry is exact (millimetre boxes), bodies may keep voxel textures and relief.

Print axes: X right, Y back, Z up.  A run along Minecraft Z lies along print Y.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..color.quantize import ColorPlan
from ..schematics.base import BlockState
from ..voxel.boxmesh import BoxModel, VoxelBody
from ..voxel.grid import VoxelModel, carve_relief
from ..voxel.mesher import MeshSet


@dataclass
class KitSettings:
    unit_mm: float = 10.0                 # size of one block
    fit_tolerance_mm: float = 0.15        # radial clearance between stud and socket
    stud_ratio: float = 0.5               # stud width as a fraction of the unit
    stud_height_mm: float = 0.0           # 0 = automatic (max(1.6 mm, 0.18 * unit))
    chamfer_mm: float = 0.3               # stud top / socket entry chamfer (stepped)
    max_piece_len: int = 6                # longest bar in units
    alternate_layers: bool = True         # even layers merge along X, odd along Z
    textured: bool = False                # keep voxel textures / relief on piece faces
    detailed: bool = True                 # non-cube blocks keep their model geometry
    skip_non_cube: bool = False           # drop non-cube blocks entirely
    baseplate: bool = True                # studded ground plate under the model
    baseplate_thickness_mm: float = 2.4
    bed_mm: tuple[float, float, float] = (256.0, 256.0, 256.0)
    bed_margin_mm: float = 6.0
    spacing_mm: float = 4.0

    @property
    def stud_w(self) -> float:
        return max(3.0, self.unit_mm * self.stud_ratio)

    @property
    def stud_h(self) -> float:
        return self.stud_height_mm if self.stud_height_mm > 0 else max(1.6, 0.18 * self.unit_mm)

    @property
    def socket_w(self) -> float:
        return self.stud_w + 2 * self.fit_tolerance_mm

    @property
    def socket_d(self) -> float:
        return self.stud_h + 0.2

    @property
    def base_tile_mm(self) -> float:
        return self.socket_d + 0.8


@dataclass
class PieceType:
    id: str
    key: tuple
    block_index: int
    state: BlockState
    length: int                      # units along its axis
    axis: str                        # 'x' | 'z' (Minecraft axes)
    cube: bool                       # full cube body
    material: int                    # print material id (from the color plan)
    rgb: tuple[int, int, int]
    has_stud: bool
    has_socket: bool
    base_tile: bool = False
    count: int = 0
    mesh: Optional[MeshSet] = None

    @property
    def footprint_mm(self) -> tuple[float, float]:
        return (0.0, 0.0)  # filled by build_piece_mesh

    @property
    def label(self) -> str:
        n = self.state.path
        return f"{n} {self.length}x1" if self.length > 1 else n


@dataclass
class Placement:
    piece: PieceType
    x: int
    y: int
    z: int                           # Minecraft block coords of the run's first cell


@dataclass
class Baseplate:
    id: str
    x0: int; z0: int; x1: int; z1: int   # block cell range [x0, x1), [z0, z1)
    mesh: Optional[MeshSet] = None


@dataclass
class Kit:
    settings: KitSettings
    pieces: list[PieceType] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    baseplates: list[Baseplate] = field(default_factory=list)
    size_blocks: tuple[int, int, int] = (0, 0, 0)     # X, Y, Z
    material_info: dict = field(default_factory=dict)
    footprints: dict[str, tuple[float, float, float]] = field(default_factory=dict)   # piece id -> (w, d, h) mm

    @property
    def total_pieces(self) -> int:
        return sum(p.count for p in self.pieces)

    def layers(self) -> dict[int, list[Placement]]:
        out: dict[int, list[Placement]] = {}
        for pl in self.placements:
            out.setdefault(pl.y, []).append(pl)
        return out


# --------------------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------------------
def extract_pieces(model: VoxelModel, plan: ColorPlan, settings: KitSettings) -> Kit:
    blocks = model.blocks
    Y, Z, X = blocks.shape
    pats = model.flat_patterns if model.flat_patterns else model.patterns
    n_pal = len(model.palette)
    is_full = np.array([p.is_full for p in model.patterns], dtype=bool)
    nonempty = np.array([not p.is_empty for p in model.patterns], dtype=bool)
    pal_rgb = model.colors.palette()

    def material_of(i: int) -> tuple[int, tuple[int, int, int]]:
        p = pats[i]
        m = int(plan.lut[p.dominant]) if p.dominant < len(plan.lut) else 0
        if m == 0:
            # unassigned / skipped color: fall back to nearest material by average color
            m = min(plan.materials, key=lambda k: sum((a - b) ** 2 for a, b in zip(plan.materials[k].rgb, p.avg_rgb or (128, 128, 128)))) if plan.materials else 0
        rgb = plan.materials[m].rgb if m in plan.materials else tuple(int(v) for v in pal_rgb[p.dominant])
        return m, tuple(int(v) for v in rgb)

    kit = Kit(settings=settings, size_blocks=(X, Y, Z), material_info=plan.material_info())
    types: dict[tuple, PieceType] = {}
    used = np.zeros_like(blocks, dtype=bool)
    counter = [0]

    def piece_for(idx: int, length: int, axis: str) -> PieceType:
        cube = bool(is_full[idx])
        axis_key = axis if (length > 1 and (settings.textured or not cube)) else "-"
        key = (idx, length, axis_key)
        pt = types.get(key)
        if pt is None:
            counter[0] += 1
            mat, rgb = material_of(idx)
            stud, socket, base = _connector_flags(model.patterns[idx], settings, cube)
            pt = PieceType(id=f"P{counter[0]:03d}", key=key, block_index=idx, state=model.palette[idx], length=length,
                           axis=axis, cube=cube, material=mat, rgb=rgb, has_stud=stud, has_socket=socket, base_tile=base)
            types[key] = pt
        pt.count += 1
        return pt

    for y in range(Y):
        axis = "x" if (y % 2 == 0 or not settings.alternate_layers) else "z"
        if axis == "x":
            for z in range(Z):
                x = 0
                while x < X:
                    idx = int(blocks[y, z, x])
                    if idx == 0 or not nonempty[idx] or (settings.skip_non_cube and not is_full[idx]):
                        x += 1
                        continue
                    length = 1
                    if is_full[idx]:
                        while (x + length < X and length < settings.max_piece_len and int(blocks[y, z, x + length]) == idx):
                            length += 1
                    kit.placements.append(Placement(piece_for(idx, length, "x"), x, y, z))
                    x += length
        else:
            for x in range(X):
                z = 0
                while z < Z:
                    idx = int(blocks[y, z, x])
                    if idx == 0 or not nonempty[idx] or (settings.skip_non_cube and not is_full[idx]):
                        z += 1
                        continue
                    length = 1
                    if is_full[idx]:
                        while (z + length < Z and length < settings.max_piece_len and int(blocks[y, z + length, x]) == idx):
                            length += 1
                    kit.placements.append(Placement(piece_for(idx, length, "z"), x, y, z))
                    z += length
    kit.pieces = sorted(types.values(), key=lambda p: (-p.count, p.id))
    if settings.baseplate and X > 0 and Z > 0:
        kit.baseplates = _plan_baseplates(X, Z, settings)
    return kit


def _connector_flags(pattern, settings: KitSettings, cube: bool) -> tuple[bool, bool, bool]:
    """(has_stud, has_socket, needs_base_tile) for a block pattern."""
    if cube:
        return True, True, False
    occ = pattern.colors != 0
    n = occ.shape[0]
    u = settings.unit_mm / n
    c = n / 2.0
    # stud footprint (plus margin) must be solid on the top layer; socket footprint (plus wall) on the bottom
    def covered(layer: np.ndarray, half_mm: float) -> bool:
        r = half_mm / u
        lo, hi = int(np.floor(c - r)), int(np.ceil(c + r))
        lo, hi = max(lo, 0), min(hi, n)
        return bool(layer[lo:hi, lo:hi].all())
    top = occ[-1]                      # [z, x]
    bottom = occ[0]
    stud = covered(top, settings.stud_w / 2 + 0.6)
    socket_direct = covered(bottom, settings.socket_w / 2 + 1.0) and covered(occ[min(int(np.ceil(settings.socket_d / u)), n - 1)], settings.socket_w / 2 + 1.0)
    return stud, True, not socket_direct


def _plan_baseplates(X: int, Z: int, s: KitSettings) -> list[Baseplate]:
    max_x = max(1, int((s.bed_mm[0] - 2 * s.bed_margin_mm) // s.unit_mm))
    max_z = max(1, int((s.bed_mm[1] - 2 * s.bed_margin_mm) // s.unit_mm))
    out = []
    nx = -(-X // max_x)
    nz = -(-Z // max_z)
    i = 0
    for iz in range(nz):
        for ix in range(nx):
            x0, x1 = round(ix * X / nx), round((ix + 1) * X / nx)
            z0, z1 = round(iz * Z / nz), round((iz + 1) * Z / nz)
            if x1 > x0 and z1 > z0:
                i += 1
                out.append(Baseplate(id=f"B{i:02d}", x0=x0, z0=z0, x1=x1, z1=z1))
    return out


# --------------------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------------------
def _add_stud(bm: BoxModel, cx: float, cy: float, z: float, s: KitSettings, material: int) -> None:
    w = s.stud_w / 2
    c = min(s.chamfer_mm, s.stud_h / 2, w / 2)
    bm.add((cx - w, cy - w, z), (cx + w, cy + w, z + s.stud_h - c), material)
    bm.add((cx - w + c, cy - w + c, z + s.stud_h - c), (cx + w - c, cy + w - c, z + s.stud_h), material)


def _cut_socket(bm: BoxModel, cx: float, cy: float, z0: float, s: KitSettings) -> None:
    w = s.socket_w / 2
    c = s.chamfer_mm
    bm.cut((cx - w, cy - w, z0 - 1.0), (cx + w, cy + w, z0 + s.socket_d))
    bm.cut((cx - w - c, cy - w - c, z0 - 1.0), (cx + w + c, cy + w + c, z0 + c))   # entry chamfer (stepped)


def piece_extent(piece: PieceType, s: KitSettings) -> tuple[float, float]:
    U = s.unit_mm
    return (piece.length * U, U) if piece.axis == "x" else (U, piece.length * U)


def build_piece_mesh(piece: PieceType, model: VoxelModel, s: KitSettings) -> MeshSet:
    U = s.unit_mm
    w, d = piece_extent(piece, s)
    bm = BoxModel()
    mat = max(1, piece.material)
    if piece.cube and not s.textured:
        bm.add((0, 0, 0), (w, d, U), mat)
    else:
        pat = model.patterns[piece.block_index]
        occ_mc = pat.colors != 0
        if s.textured and model.relief_steps and pat.relief is not None:
            steps = int(model.relief_steps)
            pad = steps + 1
            colors = np.pad(pat.colors, pad)
            rel = np.pad(pat.relief, pad)
            carve_relief(colors, rel, steps)
            occ_mc = colors[pad:-pad, pad:-pad, pad:-pad] != 0
        occ_print = occ_mc[:, ::-1, :]            # [Z, Y, X]
        reps = piece.length
        tiled = np.concatenate([occ_print] * reps, axis=2) if piece.axis == "x" else np.concatenate([occ_print] * reps, axis=1)
        n = occ_mc.shape[0]
        bm.add_body(VoxelBody(tiled, None, (0.0, 0.0, 0.0), U / n, mat))
        if piece.base_tile:
            bm.add((0, 0, 0), (w, d, s.base_tile_mm), mat)
    centres = [((i + 0.5) * U, U / 2) for i in range(piece.length)] if piece.axis == "x" else [(U / 2, (i + 0.5) * U) for i in range(piece.length)]
    if piece.has_socket:
        for cx, cy in centres:
            _cut_socket(bm, cx, cy, 0.0, s)
    if piece.has_stud:
        for cx, cy in centres:
            _add_stud(bm, cx, cy, U, s, mat)
    info = {mat: {"name": f"{piece.id} {piece.label}", "color": tuple(int(v) for v in piece.rgb)}}
    return bm.mesh(material_info=info, single_material=mat)


def build_baseplate_mesh(bp: Baseplate, s: KitSettings, material: int = 1, rgb=(120, 120, 120)) -> MeshSet:
    U = s.unit_mm
    w = (bp.x1 - bp.x0) * U
    d = (bp.z1 - bp.z0) * U
    bm = BoxModel()
    t = s.baseplate_thickness_mm
    bm.add((0, 0, 0), (w, d, t), material)
    for ix in range(bp.x1 - bp.x0):
        for iz in range(bp.z1 - bp.z0):
            _add_stud(bm, (ix + 0.5) * U, (iz + 0.5) * U, t, s, material)
    return bm.mesh(material_info={material: {"name": f"baseplate {bp.id}", "color": tuple(rgb)}}, single_material=material)


def build_all_meshes(kit: Kit, model: VoxelModel) -> None:
    s = kit.settings
    for p in kit.pieces:
        p.mesh = build_piece_mesh(p, model, s)
        lo, hi = p.mesh.bounds()
        kit.footprints[p.id] = tuple(float(v) for v in (hi - lo))
    for bp in kit.baseplates:
        bp.mesh = build_baseplate_mesh(bp, s)
