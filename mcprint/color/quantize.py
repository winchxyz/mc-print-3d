"""Color planning: reduce the model's colors to printable materials (filament slots).

Pipeline:  color histogram (color index -> exposed voxel weight)
        -> clusters (k-means in CIELAB, weighted)                       [what the user sees]
        -> materials (one per filament / print color)                   [what gets exported]
        -> lut: color index -> material id (0 = empty)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..util.color import ciede2000, lab_to_rgb, nearest_palette_index, rgb_to_lab, to_hex
from .filament import Filament


@dataclass
class Cluster:
    id: int
    rgb: tuple[int, int, int]          # representative color
    weight: int                        # exposed voxel count
    members: np.ndarray                # color indices belonging to this cluster
    material: int = 0                  # assigned material id (0 = unassigned -> skipped!)

    @property
    def hex(self) -> str:
        return to_hex(self.rgb)


@dataclass
class Material:
    id: int
    name: str
    rgb: tuple[int, int, int]
    filament: Optional[Filament] = None
    slot: Optional[int] = None         # 1-based printer slot / extruder

    @property
    def hex(self) -> str:
        return to_hex(self.rgb)

    def info(self) -> dict:
        return {"name": self.name, "color": tuple(int(c) for c in self.rgb), "slot": self.slot,
                "filament": self.filament.label if self.filament else ""}


@dataclass
class ColorPlan:
    mode: str                                   # 'clusters' | 'filaments' | 'single' | 'full'
    n_colors: int                               # size of the color index table
    clusters: list[Cluster] = field(default_factory=list)
    materials: dict[int, Material] = field(default_factory=dict)
    lut: np.ndarray = field(default_factory=lambda: np.zeros(1, dtype=np.uint16))

    def rebuild_lut(self) -> np.ndarray:
        lut = np.zeros(self.n_colors, dtype=np.uint16)
        for c in self.clusters:
            if c.material:
                lut[c.members] = c.material
        lut[0] = 0
        self.lut = lut
        return lut

    def material_info(self) -> dict[int, dict]:
        return {m.id: m.info() for m in self.materials.values()}

    def used_materials(self) -> list[Material]:
        used = {int(c.material) for c in self.clusters if c.material}
        return [self.materials[m] for m in sorted(used) if m in self.materials]

    def summary(self) -> str:
        lines = []
        total = sum(c.weight for c in self.clusters) or 1
        for c in sorted(self.clusters, key=lambda c: -c.weight):
            m = self.materials.get(c.material)
            lines.append(f"{c.hex}  {100 * c.weight / total:5.1f}%  -> {m.name if m else 'SKIPPED'}")
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------------------
def kmeans_lab(rgb: np.ndarray, weights: np.ndarray, k: int, iters: int = 40, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Weighted k-means in CIELAB.  Returns (labels (M,), centers_rgb (k,3))."""
    rgb = np.asarray(rgb, dtype=np.float64).reshape(-1, 3)
    w = np.asarray(weights, dtype=np.float64).reshape(-1)
    M = len(rgb)
    if M == 0:
        return np.zeros(0, dtype=np.int64), np.zeros((0, 3))
    k = max(1, min(k, M))
    lab = rgb_to_lab(rgb)
    rng = np.random.default_rng(seed)
    # k-means++ init (weighted)
    centers = np.empty((k, 3))
    first = int(np.argmax(w))
    centers[0] = lab[first]
    d2 = ((lab - centers[0]) ** 2).sum(1)
    for i in range(1, k):
        p = d2 * w
        if p.sum() <= 0:
            centers[i:] = lab[rng.integers(0, M, size=k - i)]
            break
        idx = int(rng.choice(M, p=p / p.sum()))
        centers[i] = lab[idx]
        d2 = np.minimum(d2, ((lab - centers[i]) ** 2).sum(1))
    labels = np.zeros(M, dtype=np.int64)
    for _ in range(iters):
        d = ((lab[:, None, :] - centers[None, :, :]) ** 2).sum(2)
        new_labels = np.argmin(d, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for i in range(k):
            sel = labels == i
            if sel.any():
                ws = w[sel]
                centers[i] = (lab[sel] * ws[:, None]).sum(0) / max(ws.sum(), 1e-9)
            else:
                centers[i] = lab[int(np.argmax(w * (d.min(1) + 1)))]
    centers_rgb = np.clip(lab_to_rgb(centers), 0, 255)
    return labels, centers_rgb


def _hist_arrays(hist: dict[int, int], palette_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx = np.asarray(sorted(i for i in hist if i != 0), dtype=np.int64)
    if len(idx) == 0:
        return idx, np.zeros((0, 3)), np.zeros(0)
    w = np.asarray([hist[int(i)] for i in idx], dtype=np.float64)
    rgb = palette_rgb[idx].astype(np.float64)
    return idx, rgb, w


def plan_clusters(hist: dict[int, int], palette_rgb: np.ndarray, k: int, seed: int = 0) -> ColorPlan:
    """Reduce to ``k`` clusters; each cluster becomes its own material colored by its centre."""
    n_colors = len(palette_rgb)
    idx, rgb, w = _hist_arrays(hist, palette_rgb)
    plan = ColorPlan(mode="clusters", n_colors=n_colors)
    if len(idx) == 0:
        plan.rebuild_lut()
        return plan
    labels, centers = kmeans_lab(rgb, w, k, seed=seed)
    order = np.argsort([-w[labels == i].sum() for i in range(len(centers))])
    cid = 1
    for i in order:
        sel = labels == i
        if not sel.any():
            continue
        c = tuple(int(round(v)) for v in centers[i])
        cl = Cluster(id=cid, rgb=c, weight=int(w[sel].sum()), members=idx[sel], material=cid)
        plan.clusters.append(cl)
        plan.materials[cid] = Material(id=cid, name=f"Color {cid} {to_hex(c)}", rgb=c, slot=cid)
        cid += 1
    plan.rebuild_lut()
    return plan


def plan_full(hist: dict[int, int], palette_rgb: np.ndarray) -> ColorPlan:
    """Every color index is its own material (full-color preview / export)."""
    n_colors = len(palette_rgb)
    plan = ColorPlan(mode="full", n_colors=n_colors)
    for i in sorted(hist):
        if i == 0:
            continue
        c = tuple(int(v) for v in palette_rgb[i])
        plan.clusters.append(Cluster(id=i, rgb=c, weight=int(hist[i]), members=np.asarray([i]), material=i))
        plan.materials[i] = Material(id=i, name=f"color_{i}", rgb=c, slot=None)
    plan.rebuild_lut()
    return plan


def plan_single(hist: dict[int, int], palette_rgb: np.ndarray, rgb=(200, 200, 200), name: str = "Single color") -> ColorPlan:
    n_colors = len(palette_rgb)
    plan = ColorPlan(mode="single", n_colors=n_colors)
    idx = np.asarray([i for i in hist if i != 0], dtype=np.int64)
    c = tuple(int(v) for v in rgb)
    plan.clusters.append(Cluster(id=1, rgb=c, weight=int(sum(hist.values())), members=idx, material=1))
    plan.materials[1] = Material(id=1, name=name, rgb=c, slot=1)
    plan.rebuild_lut()
    return plan


# --------------------------------------------------------------------------------------
# filament assignment
# --------------------------------------------------------------------------------------
def choose_filament_subset(cluster_rgb: np.ndarray, cluster_w: np.ndarray, filaments: list[Filament], max_slots: int) -> list[int]:
    """Greedy forward selection of at most ``max_slots`` filaments minimising weighted CIEDE2000 error."""
    if len(filaments) <= max_slots:
        return list(range(len(filaments)))
    lab_c = rgb_to_lab(cluster_rgb)
    lab_f = rgb_to_lab(np.asarray([f.rgb for f in filaments], dtype=np.float64))
    D = ciede2000(lab_c[:, None, :], lab_f[None, :, :])   # (C, F)
    chosen: list[int] = []
    best = np.full(len(cluster_rgb), np.inf)
    for _ in range(max_slots):
        gains = []
        for j in range(len(filaments)):
            if j in chosen:
                gains.append(-1.0)
                continue
            newbest = np.minimum(best, D[:, j])
            gains.append(float(((best - newbest) * cluster_w).sum()) if np.isfinite(best).any() else float((cluster_w * (1e6 - newbest)).sum()))
        j = int(np.argmax(gains))
        if gains[j] <= 0 and chosen:
            break
        chosen.append(j)
        best = np.minimum(best, D[:, j])
    return chosen


def assign_filaments(plan: ColorPlan, filaments: list[Filament], max_slots: Optional[int] = None,
                     metric: str = "ciede2000") -> ColorPlan:
    """Map every cluster to the nearest filament color; materials become the used filaments."""
    if not filaments or not plan.clusters:
        return plan
    rgb = np.asarray([c.rgb for c in plan.clusters], dtype=np.float64)
    w = np.asarray([c.weight for c in plan.clusters], dtype=np.float64)
    subset = list(range(len(filaments)))
    if max_slots and max_slots < len(filaments):
        subset = choose_filament_subset(rgb, w, filaments, max_slots)
    fils = [filaments[i] for i in subset]
    pal = np.asarray([f.rgb for f in fils], dtype=np.float64)
    nearest = nearest_palette_index(rgb, pal, metric=metric)
    plan.materials = {}
    plan.mode = "filaments"
    for j, f in enumerate(fils):
        mid = j + 1
        plan.materials[mid] = Material(id=mid, name=f.label, rgb=f.rgb, filament=f, slot=subset[j] + 1)
    for c, j in zip(plan.clusters, nearest.tolist()):
        c.material = j + 1
    plan.rebuild_lut()
    return plan


def set_cluster_material(plan: ColorPlan, cluster_id: int, material_id: int) -> None:
    for c in plan.clusters:
        if c.id == cluster_id:
            c.material = material_id
    plan.rebuild_lut()


def add_material(plan: ColorPlan, filament: Filament, slot: Optional[int] = None) -> Material:
    mid = max(plan.materials, default=0) + 1
    m = Material(id=mid, name=filament.label, rgb=filament.rgb, filament=filament, slot=slot or mid)
    plan.materials[mid] = m
    return m


def color_error_report(plan: ColorPlan) -> list[tuple[Cluster, float]]:
    """CIEDE2000 distance between each cluster and its assigned material (for the UI)."""
    out = []
    for c in plan.clusters:
        m = plan.materials.get(c.material)
        if not m:
            out.append((c, float("inf")))
            continue
        d = float(ciede2000(rgb_to_lab(np.asarray(c.rgb, dtype=np.float64)), rgb_to_lab(np.asarray(m.rgb, dtype=np.float64))))
        out.append((c, d))
    return out
