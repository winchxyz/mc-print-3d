import numpy as np

from mcprint.color.filament import Filament
from mcprint.color.quantize import (assign_filaments, choose_filament_subset, color_error_report, kmeans_lab,
                                    plan_clusters, plan_full, plan_single, set_cluster_material)


def _palette_and_hist():
    # 0 = empty marker, then reds, greens, blues with slight variations
    pal = np.array([[0, 0, 0],
                    [200, 20, 20], [210, 30, 25], [190, 15, 30],
                    [20, 200, 20], [30, 210, 25],
                    [20, 20, 200], [25, 30, 210], [15, 25, 190]], dtype=np.uint8)
    hist = {1: 100, 2: 80, 3: 60, 4: 50, 5: 40, 6: 30, 7: 20, 8: 10}
    return pal, hist


def test_kmeans_groups_similar_colors():
    pal, hist = _palette_and_hist()
    idx = np.array(sorted(hist))
    labels, centers = kmeans_lab(pal[idx], np.array([hist[i] for i in idx]), 3)
    assert len(centers) == 3
    # reds together, greens together, blues together
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4]
    assert labels[5] == labels[6] == labels[7]
    assert len({labels[0], labels[3], labels[5]}) == 3


def test_plan_clusters_and_lut():
    pal, hist = _palette_and_hist()
    plan = plan_clusters(hist, pal, 3)
    assert len(plan.clusters) == 3
    assert len(plan.materials) == 3
    lut = plan.lut
    assert lut[0] == 0
    assert lut[1] == lut[2] == lut[3]
    assert lut[6] == lut[7] == lut[8]
    assert lut[1] != lut[4] != lut[6]
    # heaviest cluster first
    assert plan.clusters[0].weight >= plan.clusters[1].weight >= plan.clusters[2].weight


def test_assign_filaments_and_subset():
    pal, hist = _palette_and_hist()
    plan = plan_clusters(hist, pal, 3)
    fils = [Filament("#FF0000", "red"), Filament("#00FF00", "green"), Filament("#0000FF", "blue"),
            Filament("#FFFFFF", "white"), Filament("#000000", "black")]
    assign_filaments(plan, fils, max_slots=3)
    used = plan.used_materials()
    names = sorted(m.filament.name for m in used)
    assert names == ["blue", "green", "red"]
    for c, err in color_error_report(plan):
        assert err < 30
    # manual override: send the blue cluster to the red material
    blue = next(c for c in plan.clusters if c.rgb[2] > 150)
    red_mat = next(m for m in plan.materials.values() if m.filament.name == "red")
    set_cluster_material(plan, blue.id, red_mat.id)
    assert plan.lut[6] == red_mat.id


def test_choose_subset_prefers_matching_colors():
    rgb = np.array([[250, 10, 10], [10, 10, 250]], dtype=float)
    w = np.array([100.0, 100.0])
    fils = [Filament("#FFFFFF"), Filament("#FF0000"), Filament("#0000FF"), Filament("#00FF00")]
    chosen = choose_filament_subset(rgb, w, fils, 2)
    assert sorted(chosen) == [1, 2]


def test_single_and_full():
    pal, hist = _palette_and_hist()
    s = plan_single(hist, pal, rgb=(1, 2, 3))
    assert (s.lut[1:] == 1).all()
    f = plan_full(hist, pal)
    assert len(f.materials) == 8
    assert f.lut[5] == 5
