"""Main window of the desktop application."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..assets import AssetStack, Instance, find_instances, manual_instance
from ..color.filament import Filament, FilamentLibrary, PrintPalette
from ..color.quantize import (ColorPlan, add_material, assign_filaments, color_error_report, plan_clusters,
                              set_cluster_material)
from ..export import EXPORT_FORMATS
from ..pipeline import Converter
from ..printers.base import PrinterInfo
from ..schematics import FILE_FILTER, Schematic
from ..settings import ConversionSettings
from ..util.color import to_hex
from .viewport import MeshViewport
from .widgets import ColorButton, ColorSwatch, FilamentDialog, swatch_pixmap
from .workers import TaskRunner

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    log_message = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"mc-print-3d {__version__} – Minecraft schematic to 3D print")
        self.resize(1480, 920)
        self.qsettings = QSettings("mc-print-3d", "mc-print-3d")
        self.settings = ConversionSettings.load()
        self.library = FilamentLibrary()
        self.runner = TaskRunner(self)
        self.converter: Optional[Converter] = None
        self.schematic_path: Optional[Path] = None
        self.schematic: Optional[Schematic] = None
        self.model = None
        self.plan: Optional[ColorPlan] = None
        self.meshes = None
        self.current_block_mm = 0.0
        self.stats: dict = {}
        self.instances: list[Instance] = []
        self.printer: Optional[PrinterInfo] = None
        self.discovered: list[PrinterInfo] = []
        self.palette: list[Filament] = [Filament.from_dict(d) for d in self.settings.filaments]
        self._build_ui()
        self._build_menu()
        self.log_message.connect(self._append_log)
        QTimer.singleShot(50, self._startup)

    # ==================================================================================
    # UI construction
    # ==================================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        self.split = QSplitter(Qt.Horizontal)
        root.addWidget(self.split)

        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(420)
        self.tabs.addTab(self._wrap(self._schematic_tab()), "1. Schematic")
        self.tabs.addTab(self._wrap(self._model_tab()), "2. Model")
        self.tabs.addTab(self._wrap(self._colors_tab()), "3. Colors")
        self.tabs.addTab(self._wrap(self._printer_tab()), "4. Printer")
        self.tabs.addTab(self._wrap(self._export_tab()), "5. Export")
        self.split.addWidget(self.tabs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        for label, view in (("Iso", "iso"), ("Top", "top"), ("Front", "front"), ("Right", "right")):
            a = QAction(label, self)
            a.triggered.connect(lambda _c=False, v=view: self.viewport.set_view(v))
            tb.addAction(a)
        fit = QAction("Fit", self)
        fit.triggered.connect(lambda: self.viewport.fit_view())
        tb.addAction(fit)
        tb.addSeparator()
        self.act_wire = QAction("Wireframe", self, checkable=True)
        self.act_wire.toggled.connect(self._toggle_wire)
        tb.addAction(self.act_wire)
        self.act_bed = QAction("Show bed", self, checkable=True, checked=True)
        self.act_bed.toggled.connect(self._toggle_bed)
        tb.addAction(self.act_bed)
        self.act_print_colors = QAction("Print colors", self, checkable=True, checked=True)
        self.act_print_colors.setToolTip("Show the reduced print colors (checked) or the original texture colors")
        self.act_print_colors.toggled.connect(lambda _c: self.remesh_preview())
        tb.addAction(self.act_print_colors)
        tb.addSeparator()
        shot = QAction("Screenshot", self)
        shot.triggered.connect(self._screenshot)
        tb.addAction(shot)
        rl.addWidget(tb)
        self.viewport = MeshViewport()
        rl.addWidget(self.viewport, 1)
        self.size_label = QLabel("No model")
        self.size_label.setStyleSheet("font-weight: bold; padding: 2px;")
        rl.addWidget(self.size_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        rl.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.status = QLabel("Ready")
        bottom.addWidget(self.status, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(lambda: self.runner.cancel_all())
        bottom.addWidget(self.cancel_btn)
        rl.addLayout(bottom)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        rl.addWidget(self.log)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.setAcceptDrops(True)

    @staticmethod
    def _wrap(w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        sa.setFrameShape(QFrame.NoFrame)
        return sa

    # ---- tab 1: schematic + assets -----------------------------------------------------
    def _schematic_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Schematic")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open schematic…")
        self.open_btn.clicked.connect(self._open_dialog)
        row.addWidget(self.open_btn)
        self.reload_btn = QPushButton("Reload")
        self.reload_btn.clicked.connect(lambda: self.open_schematic(self.schematic_path) if self.schematic_path else None)
        row.addWidget(self.reload_btn)
        gl.addLayout(row)
        self.schem_info = QLabel("Drop a .schematic / .schem / .litematic / .nbt / .mcstructure / .bp file here.")
        self.schem_info.setWordWrap(True)
        gl.addWidget(self.schem_info)
        self.block_table = QTableWidget(0, 2)
        self.block_table.setHorizontalHeaderLabels(["Block state", "Count"])
        self.block_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.block_table.verticalHeader().setVisible(False)
        self.block_table.setMaximumHeight(260)
        gl.addWidget(self.block_table)
        lay.addWidget(g)

        m = QGroupBox("Mobs (printed as figures)")
        ml = QVBoxLayout(m)
        self.include_mobs = QCheckBox("Print mobs stored in the schematic (entities)")
        self.include_mobs.setChecked(self.settings.include_mobs)
        self.include_mobs.toggled.connect(lambda _v: self._mobs_changed())
        ml.addWidget(self.include_mobs)
        self.mob_snap = QCheckBox("Turn mobs to the nearest 90° (clean faces; off keeps exact rotations)")
        self.mob_snap.setChecked(self.settings.mob_snap_yaw)
        ml.addWidget(self.mob_snap)
        self.entity_label = QLabel("No mobs in the loaded schematic.")
        self.entity_label.setWordWrap(True)
        ml.addWidget(self.entity_label)
        self.mob_list = QListWidget()
        self.mob_list.setMaximumHeight(90)
        ml.addWidget(self.mob_list)
        row = QHBoxLayout()
        from ..mobs import MOB_IDS
        self.mob_id = QComboBox()
        for mid in MOB_IDS:
            self.mob_id.addItem(mid.replace("_", " "), mid)
        row.addWidget(self.mob_id)
        self.mob_x = QDoubleSpinBox(); self.mob_y = QDoubleSpinBox(); self.mob_z = QDoubleSpinBox(); self.mob_yaw = QDoubleSpinBox()
        for sp, name in ((self.mob_x, "X"), (self.mob_y, "Y"), (self.mob_z, "Z")):
            sp.setRange(-512, 4096); sp.setDecimals(1); sp.setSingleStep(1.0); sp.setPrefix(name + " ")
            row.addWidget(sp)
        self.mob_yaw.setRange(-180, 180); sp = self.mob_yaw; sp.setDecimals(0); sp.setSingleStep(45); sp.setPrefix("yaw "); sp.setSuffix("°")
        row.addWidget(self.mob_yaw)
        add_mob = QPushButton("Add")
        add_mob.clicked.connect(self._add_mob)
        row.addWidget(add_mob)
        rm_mob = QPushButton("Remove")
        rm_mob.clicked.connect(self._remove_mob)
        row.addWidget(rm_mob)
        ml.addLayout(row)
        row = QHBoxLayout()
        self.mob_scale = QDoubleSpinBox()
        self.mob_scale.setRange(0.1, 10.0); self.mob_scale.setDecimals(2); self.mob_scale.setSingleStep(0.25)
        self.mob_scale.setValue(self.settings.mob_scale)
        self.mob_scale.setPrefix("scale ×")
        row.addWidget(self.mob_scale)
        self.skin_btn = QPushButton("Player skin…")
        self.skin_btn.clicked.connect(self._pick_skin)
        row.addWidget(self.skin_btn)
        self.skin_label = QLabel(Path(self.settings.player_skin).name if self.settings.player_skin else "Steve")
        row.addWidget(self.skin_label, 1)
        apply_mobs = QPushButton("Apply")
        apply_mobs.setToolTip("Rebuild the model with the mobs above")
        apply_mobs.clicked.connect(self.convert)
        row.addWidget(apply_mobs)
        ml.addLayout(row)
        for mspec in self.settings.extra_mobs:
            self._add_mob_item(mspec)
        lay.addWidget(m)

        a = QGroupBox("Minecraft assets (textures & models)")
        al = QVBoxLayout(a)
        self.instance_combo = QComboBox()
        self.instance_combo.currentIndexChanged.connect(self._instance_changed)
        al.addWidget(QLabel("Installation / modpack instance:"))
        al.addWidget(self.instance_combo)
        row = QHBoxLayout()
        self.include_mods = QCheckBox("Load mods from the instance")
        self.include_mods.setChecked(self.settings.include_mods)
        self.use_packs = QCheckBox("Use enabled resource packs")
        self.use_packs.setChecked(self.settings.use_resource_packs)
        row.addWidget(self.include_mods)
        row.addWidget(self.use_packs)
        al.addLayout(row)
        row = QHBoxLayout()
        add_jar = QPushButton("Add jar / zip / folder…")
        add_jar.clicked.connect(self._add_asset_path)
        row.addWidget(add_jar)
        self.extra_assets = QListWidget()
        self.extra_assets.setMaximumHeight(60)
        for p in self.settings.extra_asset_paths:
            self.extra_assets.addItem(p)
        clear_extra = QPushButton("Clear extras")
        clear_extra.clicked.connect(lambda: (self.extra_assets.clear(), self._settings_from_ui()))
        row.addWidget(clear_extra)
        al.addLayout(row)
        al.addWidget(self.extra_assets)
        self.assets_status = QLabel("Assets not loaded")
        self.assets_status.setWordWrap(True)
        al.addWidget(self.assets_status)
        self.load_assets_btn = QPushButton("Load assets")
        self.load_assets_btn.clicked.connect(lambda: self.load_assets())
        al.addWidget(self.load_assets_btn)
        lay.addWidget(a)
        lay.addStretch(1)
        return w

    # ---- tab 2: model ---------------------------------------------------------------
    def _model_tab(self) -> QWidget:
        s = self.settings
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Mode")
        f = QFormLayout(g)
        self.style = QComboBox()
        self.style.addItem("Textured – relief carved from block textures", "textured")
        self.style.addItem("Flat – smooth faces, no relief (easiest to print)", "flat")
        self.style.addItem("Plain cubes – every block becomes a simple cube", "cubes")
        self.style.setCurrentIndex(max(0, self.style.findData(s.style)))
        f.addRow("Style", self.style)
        self.build_type = QComboBox()
        self.build_type.addItem("Single model – print the whole build as one object", "solid")
        self.build_type.addItem("Modular kit – blocks with studs & sockets, assemble like LEGO", "kit")
        self.build_type.setCurrentIndex(max(0, self.build_type.findData(s.build_type)))
        f.addRow("Build", self.build_type)
        lay.addWidget(g)

        g = QGroupBox("Scale")
        f = QFormLayout(g)
        self.block_mm = QDoubleSpinBox()
        self.block_mm.setRange(0.2, 200.0)
        self.block_mm.setDecimals(3)
        self.block_mm.setValue(s.block_mm)
        self.block_mm.setSuffix(" mm")
        f.addRow("Block size", self.block_mm)
        self.target_size = QDoubleSpinBox()
        self.target_size.setRange(0, 5000)
        self.target_size.setSpecialValueText("off")
        self.target_size.setValue(s.target_size_mm or 0)
        self.target_size.setSuffix(" mm")
        self.target_size.setToolTip("Scale so that the longest horizontal side has this length (overrides block size)")
        f.addRow("Target size", self.target_size)
        self.fit_bed = QCheckBox("Scale to fill the printer bed")
        self.fit_bed.setChecked(s.fit_to_bed)
        f.addRow("", self.fit_bed)
        self.rotate = QComboBox()
        self.rotate.addItems(["0°", "90°", "180°", "270°"])
        self.rotate.setCurrentIndex((s.rotate_deg // 90) % 4)
        f.addRow("Rotate on bed", self.rotate)
        lay.addWidget(g)

        g = QGroupBox("Detail & printability")
        f = QFormLayout(g)
        self.resolution = QComboBox()
        for label, v in (("auto (from nozzle)", 0), ("2 per block", 2), ("4 per block", 4), ("8 per block", 8), ("12 per block", 12), ("16 per block (1 texel)", 16), ("24 per block", 24), ("32 per block", 32)):
            self.resolution.addItem(label, v)
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(s.resolution)))
        f.addRow("Resolution", self.resolution)
        self.nozzle = QDoubleSpinBox()
        self.nozzle.setRange(0.1, 2.0)
        self.nozzle.setSingleStep(0.1)
        self.nozzle.setValue(s.nozzle_mm)
        self.nozzle.setSuffix(" mm")
        f.addRow("Nozzle", self.nozzle)
        self.thickness = QDoubleSpinBox()
        self.thickness.setRange(0.0, 20.0)
        self.thickness.setSingleStep(0.1)
        self.thickness.setValue(s.min_thickness_mm)
        self.thickness.setSuffix(" mm")
        self.thickness.setToolTip("Thin planes, wires, panes and flower stems are thickened to at least this")
        f.addRow("Min. feature thickness", self.thickness)
        self.dilation = QSpinBox()
        self.dilation.setRange(0, 4)
        self.dilation.setValue(s.cutout_dilation)
        self.dilation.setToolTip("Grow cut-out textures by this many texels (keeps thin stems connected)")
        f.addRow("Texture thickening (texels)", self.dilation)
        self.alpha = QSpinBox()
        self.alpha.setRange(1, 255)
        self.alpha.setValue(s.alpha_threshold)
        self.alpha.setToolTip("Texture pixels with alpha below this are empty space")
        f.addRow("Alpha threshold", self.alpha)
        lay.addWidget(g)

        g = QGroupBox("Surface relief from textures")
        f = QFormLayout(g)
        self.relief = QDoubleSpinBox()
        self.relief.setRange(0.0, 5.0)
        self.relief.setSingleStep(0.1)
        self.relief.setSpecialValueText("off (flat faces)")
        self.relief.setValue(s.relief_mm)
        self.relief.setSuffix(" mm")
        self.relief.setToolTip("Depth of the grooves carved from the texture pattern: brick mortar, stone-brick cracks, plank seams... "
                               "Needs a resolution fine enough for at least one voxel of depth (auto resolution handles this).")
        f.addRow("Relief depth", self.relief)
        self.relief_mode = QComboBox()
        for label, v in (("auto (height map if the pack has one, else pattern)", "auto"), ("only real height maps (PBR packs)", "heightmap"),
                         ("pattern: minority texels recessed", "pattern"), ("darker texels recessed", "dark"), ("lighter texels recessed", "light")):
            self.relief_mode.addItem(label, v)
        self.relief_mode.setCurrentIndex(max(0, self.relief_mode.findData(s.relief_mode)))
        f.addRow("Depth from", self.relief_mode)
        lay.addWidget(g)

        self.kit_group = QGroupBox("Modular kit (block size above = unit size)")
        f = QFormLayout(self.kit_group)
        self.kit_fit = QDoubleSpinBox()
        self.kit_fit.setRange(0.0, 1.0)
        self.kit_fit.setSingleStep(0.05)
        self.kit_fit.setDecimals(2)
        self.kit_fit.setValue(s.kit_fit_mm)
        self.kit_fit.setSuffix(" mm")
        self.kit_fit.setToolTip("Clearance between stud and socket per side. 0.15 mm suits most PLA printers; larger = looser.")
        f.addRow("Fit tolerance", self.kit_fit)
        self.kit_max_len = QSpinBox()
        self.kit_max_len.setRange(1, 16)
        self.kit_max_len.setValue(s.kit_max_len)
        self.kit_max_len.setSuffix(" blocks")
        self.kit_max_len.setToolTip("Identical neighbouring cubes merge into bars up to this length")
        f.addRow("Longest bar", self.kit_max_len)
        self.kit_textured = QCheckBox("Keep textures / relief on pieces")
        self.kit_textured.setChecked(s.kit_textured)
        f.addRow("", self.kit_textured)
        self.kit_detailed = QCheckBox("Stairs, fences, flowers keep their shape (else plain cubes)")
        self.kit_detailed.setChecked(s.kit_detailed)
        f.addRow("", self.kit_detailed)
        self.kit_baseplate = QCheckBox("Studded baseplate tiles")
        self.kit_baseplate.setChecked(s.kit_baseplate)
        f.addRow("", self.kit_baseplate)
        self.kit_alternate = QCheckBox("Alternate bar direction per layer (brick bond)")
        self.kit_alternate.setChecked(s.kit_alternate)
        f.addRow("", self.kit_alternate)
        lay.addWidget(self.kit_group)
        self.style.currentIndexChanged.connect(self._mode_changed)
        self.build_type.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

        g = QGroupBox("Content")
        f = QFormLayout(g)
        self.fluids = QCheckBox("Include water / lava as solid")
        self.fluids.setChecked(s.include_fluids)
        f.addRow("", self.fluids)
        self.unknown = QComboBox()
        self.unknown.addItem("print as cube", "cube")
        self.unknown.addItem("skip", "skip")
        self.unknown.setCurrentIndex(max(0, self.unknown.findData(s.unknown_policy)))
        f.addRow("Blocks without models", self.unknown)
        self.islands = QCheckBox("Remove floating fragments smaller than")
        self.islands.setChecked(s.remove_islands)
        self.island_min = QSpinBox()
        self.island_min.setRange(1, 100000)
        self.island_min.setValue(s.island_min_blocks)
        self.island_min.setSuffix(" blocks")
        row = QHBoxLayout()
        row.addWidget(self.islands)
        row.addWidget(self.island_min)
        f.addRow("", row)
        self.ground_only = QCheckBox("Keep only parts touching the ground")
        self.ground_only.setChecked(s.keep_ground_only)
        f.addRow("", self.ground_only)
        self.fill = QCheckBox("Fill enclosed cavities (rooms) with solid")
        self.fill.setChecked(s.fill_cavities)
        f.addRow("", self.fill)
        self.base = QDoubleSpinBox()
        self.base.setRange(0, 50)
        self.base.setSpecialValueText("none")
        self.base.setValue(s.base_plate_mm)
        self.base.setSuffix(" mm")
        f.addRow("Base plate", self.base)
        self.connections = QCheckBox("Infer fence / wall / pane connections (legacy files)")
        self.connections.setChecked(s.infer_connections)
        f.addRow("", self.connections)
        self.solid_tex = QLineEdit(", ".join(s.solid_textures))
        self.solid_tex.setToolTip("Texture names treated as opaque even where transparent (comma separated)")
        f.addRow("Solid textures", self.solid_tex)
        lay.addWidget(g)

        self.convert_btn = QPushButton("Convert / update preview")
        self.convert_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        self.convert_btn.clicked.connect(self.convert)
        lay.addWidget(self.convert_btn)
        self.model_info = QLabel("")
        self.model_info.setWordWrap(True)
        lay.addWidget(self.model_info)
        lay.addStretch(1)
        return w

    # ---- tab 3: colors -----------------------------------------------------------------
    def _colors_tab(self) -> QWidget:
        s = self.settings
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Color mode")
        f = QFormLayout(g)
        self.color_mode = QComboBox()
        self.color_mode.addItem("Per block type (flat colors, clean prints)", "block")
        self.color_mode.addItem("Per texture pixel (detailed, noisier)", "texel")
        self.color_mode.addItem("Single color", "single")
        self.color_mode.setCurrentIndex(max(0, self.color_mode.findData(s.color_mode)))
        f.addRow("Mode", self.color_mode)
        self.max_colors = QSpinBox()
        self.max_colors.setRange(1, 64)
        self.max_colors.setValue(s.max_colors)
        self.max_colors.setToolTip("Number of print colors (filament slots)")
        f.addRow("Print colors", self.max_colors)
        self.single_color = ColorButton(s.single_color_hex)
        f.addRow("Single color", self.single_color)
        self.auto_assign = QCheckBox("Auto-assign clusters to the nearest filament")
        self.auto_assign.setChecked(s.auto_assign)
        f.addRow("", self.auto_assign)
        self.separate_glass = QCheckBox("Keep glass / ice / water as their own color (print with clear filament)")
        self.separate_glass.setChecked(s.separate_glass)
        f.addRow("", self.separate_glass)
        lay.addWidget(g)

        g = QGroupBox("Loaded filaments (print palette)")
        gl = QVBoxLayout(g)
        self.palette_list = QListWidget()
        self.palette_list.setMaximumHeight(160)
        gl.addWidget(self.palette_list)
        row = QHBoxLayout()
        b = QPushButton("Add…")
        b.clicked.connect(self._add_filaments)
        row.addWidget(b)
        b = QPushButton("Edit color")
        b.clicked.connect(self._edit_palette_color)
        row.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(self._remove_filament)
        row.addWidget(b)
        b = QPushButton("Sync from printer")
        b.setToolTip("Read the filaments loaded in the printer (Bambu AMS, Spoolman, MMU…)")
        b.clicked.connect(self.sync_filaments)
        row.addWidget(b)
        gl.addLayout(row)
        row = QHBoxLayout()
        b = QPushButton("Import from slicer presets")
        b.clicked.connect(self._import_slicer)
        row.addWidget(b)
        b = QPushButton("Clear")
        b.clicked.connect(lambda: (self.palette.clear(), self._refresh_palette()))
        row.addWidget(b)
        gl.addLayout(row)
        lay.addWidget(g)

        g = QGroupBox("Model colors → filament")
        gl = QVBoxLayout(g)
        self.cluster_table = QTableWidget(0, 4)
        self.cluster_table.setHorizontalHeaderLabels(["Color", "Share", "Blocks", "Print with"])
        self.cluster_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.cluster_table.verticalHeader().setVisible(False)
        self.cluster_table.setMinimumHeight(220)
        gl.addWidget(self.cluster_table)
        row = QHBoxLayout()
        b = QPushButton("Recompute colors")
        b.clicked.connect(self.replan_colors)
        row.addWidget(b)
        b = QPushButton("Auto-assign now")
        b.clicked.connect(self._auto_assign_now)
        row.addWidget(b)
        gl.addLayout(row)
        self.color_info = QLabel("")
        self.color_info.setWordWrap(True)
        gl.addWidget(self.color_info)
        lay.addWidget(g)
        lay.addStretch(1)
        self._refresh_palette()
        return w

    # ---- tab 4: printer ----------------------------------------------------------------
    def _printer_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Detect printers")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan USB + network")
        self.scan_btn.clicked.connect(self.scan_printers)
        row.addWidget(self.scan_btn)
        self.subnet_scan = QCheckBox("also port-scan subnet (slower)")
        row.addWidget(self.subnet_scan)
        gl.addLayout(row)
        self.printer_list = QListWidget()
        self.printer_list.setMaximumHeight(150)
        self.printer_list.currentRowChanged.connect(self._printer_selected)
        gl.addWidget(self.printer_list)
        row = QHBoxLayout()
        b = QPushButton("Add by IP…")
        b.clicked.connect(self._add_printer_by_ip)
        row.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(self._remove_printer)
        row.addWidget(b)
        gl.addLayout(row)
        lay.addWidget(g)

        g = QGroupBox("Or choose from the database")
        f = QFormLayout(g)
        self.vendor_combo = QComboBox()
        self.model_combo = QComboBox()
        try:
            from ..printers.database import vendors
            self.vendor_combo.addItems(vendors())
        except Exception as exc:
            log.warning("printer database unavailable: %s", exc)
        self.vendor_combo.currentTextChanged.connect(self._vendor_changed)
        f.addRow("Vendor", self.vendor_combo)
        f.addRow("Model", self.model_combo)
        b = QPushButton("Use this printer")
        b.clicked.connect(self._use_db_printer)
        f.addRow("", b)
        self._vendor_changed(self.vendor_combo.currentText())
        lay.addWidget(g)

        g = QGroupBox("Selected printer")
        f = QFormLayout(g)
        self.printer_name = QLabel("none")
        f.addRow("Printer", self.printer_name)
        row = QHBoxLayout()
        self.bed_x = QDoubleSpinBox(); self.bed_y = QDoubleSpinBox(); self.bed_z = QDoubleSpinBox()
        for sb in (self.bed_x, self.bed_y, self.bed_z):
            sb.setRange(10, 2000)
            sb.setSuffix(" mm")
            sb.valueChanged.connect(self._bed_changed)
        self.bed_x.setValue(self.settings.bed_mm[0] if self.settings.bed_mm else 256)
        self.bed_y.setValue(self.settings.bed_mm[1] if self.settings.bed_mm else 256)
        self.bed_z.setValue(self.settings.bed_mm[2] if self.settings.bed_mm else 256)
        row.addWidget(QLabel("X")); row.addWidget(self.bed_x)
        row.addWidget(QLabel("Y")); row.addWidget(self.bed_y)
        row.addWidget(QLabel("Z")); row.addWidget(self.bed_z)
        f.addRow("Bed", row)
        self.slots = QSpinBox()
        self.slots.setRange(1, 64)
        self.slots.setValue(self.settings.max_colors)
        f.addRow("Filament slots", self.slots)
        self.cred_form = QFormLayout()
        f.addRow(self.cred_form)
        self.cred_fields: dict[str, QLineEdit] = {}
        row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect / refresh")
        self.connect_btn.clicked.connect(self.connect_printer)
        row.addWidget(self.connect_btn)
        self.sync_btn = QPushButton("Sync filaments")
        self.sync_btn.clicked.connect(self.sync_filaments)
        row.addWidget(self.sync_btn)
        f.addRow("", row)
        self.printer_status = QLabel("")
        self.printer_status.setWordWrap(True)
        f.addRow(self.printer_status)
        self.slot_list = QListWidget()
        self.slot_list.setMaximumHeight(140)
        f.addRow(QLabel("Loaded filaments:"))
        f.addRow(self.slot_list)
        lay.addWidget(g)
        lay.addStretch(1)
        self._load_saved_printers()
        return w

    # ---- tab 5: export -----------------------------------------------------------------
    def _export_tab(self) -> QWidget:
        s = self.settings
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Export")
        f = QFormLayout(g)
        self.fmt = QComboBox()
        for k, label in EXPORT_FORMATS.items():
            self.fmt.addItem(label, k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(s.export_format)))
        f.addRow("Format", self.fmt)
        row = QHBoxLayout()
        self.out_dir = QLineEdit(s.output_dir or str(Path.home() / "Documents" / "mc-print-3d"))
        row.addWidget(self.out_dir, 1)
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._pick_out_dir)
        row.addWidget(b)
        f.addRow("Folder", row)
        self.out_name = QLineEdit("")
        f.addRow("File name", self.out_name)
        self.split_tiles = QCheckBox("Split into tiles that fit the bed")
        self.split_tiles.setChecked(s.split_to_bed)
        f.addRow("", self.split_tiles)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 100)
        self.margin.setValue(s.bed_margin_mm)
        self.margin.setSuffix(" mm")
        f.addRow("Bed margin", self.margin)
        lay.addWidget(g)
        self.export_btn = QPushButton("Export")
        self.export_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        self.export_btn.clicked.connect(self.export)
        lay.addWidget(self.export_btn)
        g = QGroupBox("Send to slicer")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        self.slicer_combo = QComboBox()
        row.addWidget(self.slicer_combo, 1)
        b = QPushButton("Open last export")
        b.clicked.connect(self._open_in_slicer)
        row.addWidget(b)
        gl.addLayout(row)
        b = QPushButton("Open output folder")
        b.clicked.connect(self._open_folder)
        gl.addWidget(b)
        lay.addWidget(g)
        self.export_info = QLabel("")
        self.export_info.setWordWrap(True)
        lay.addWidget(self.export_info)
        lay.addStretch(1)
        self.last_files: list[Path] = []
        return w

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        a = QAction("&Open schematic…", self, shortcut=QKeySequence.Open)
        a.triggered.connect(self._open_dialog)
        m.addAction(a)
        self.recent_menu = m.addMenu("Open recent")
        self._refresh_recent()
        a = QAction("&Export…", self, shortcut=QKeySequence("Ctrl+E"))
        a.triggered.connect(self.export)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Save settings as defaults", self)
        a.triggered.connect(lambda: (self._settings_from_ui(), self.settings.save(), self.log_message.emit("Settings saved")))
        m.addAction(a)
        a = QAction("&Quit", self, shortcut=QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        v = self.menuBar().addMenu("&View")
        for label, view in (("Isometric", "iso"), ("Top", "top"), ("Front", "front"), ("Right", "right"), ("Bottom", "bottom")):
            a = QAction(label, self)
            a.triggered.connect(lambda _c=False, vv=view: self.viewport.set_view(vv))
            v.addAction(a)
        a = QAction("Fit view (F)", self)
        a.triggered.connect(lambda: self.viewport.fit_view())
        v.addAction(a)
        t = self.menuBar().addMenu("&Tools")
        a = QAction("Detect printers", self)
        a.triggered.connect(self.scan_printers)
        t.addAction(a)
        a = QAction("Import filaments from slicer presets", self)
        a.triggered.connect(self._import_slicer)
        t.addAction(a)
        a = QAction("Reload Minecraft installations", self)
        a.triggered.connect(self._refresh_instances)
        t.addAction(a)
        h = self.menuBar().addMenu("&Help")
        a = QAction("About", self)
        a.triggered.connect(self._about)
        h.addAction(a)

    # ==================================================================================
    # startup / settings
    # ==================================================================================
    def _startup(self) -> None:
        self._refresh_instances()
        self._refresh_slicers()
        self.viewport.set_bed(self._bed())
        self.viewport.fit_view()
        self.log_message.emit(f"mc-print-3d {__version__} ready. Open a schematic to begin.")

    def _refresh_instances(self) -> None:
        self.instances = find_instances()
        self.instance_combo.blockSignals(True)
        self.instance_combo.clear()
        for inst in self.instances:
            self.instance_combo.addItem(inst.label, inst)
        self.instance_combo.addItem("Custom (add a jar below)", None)
        idx = 0
        if self.settings.instance_name:
            for i, inst in enumerate(self.instances):
                if inst.label == self.settings.instance_name:
                    idx = i
        self.instance_combo.setCurrentIndex(idx)
        self.instance_combo.blockSignals(False)
        if not self.instances:
            self.assets_status.setText("No Minecraft installation found. Add a client jar (versions/<ver>/<ver>.jar) with the button above.")

    def _refresh_slicers(self) -> None:
        self.slicer_combo.clear()
        try:
            from ..printers.slicers import find_slicers
            for app in find_slicers():
                self.slicer_combo.addItem(f"{app.name} {app.version}".strip(), app)
        except Exception as exc:
            log.warning("slicer detection failed: %s", exc)
        if self.slicer_combo.count() == 0:
            self.slicer_combo.addItem("no slicer found", None)

    def _settings_from_ui(self) -> ConversionSettings:
        s = self.settings
        s.block_mm = self.block_mm.value()
        s.target_size_mm = self.target_size.value() or None
        s.fit_to_bed = self.fit_bed.isChecked()
        s.rotate_deg = self.rotate.currentIndex() * 90
        s.resolution = int(self.resolution.currentData() or 0)
        s.nozzle_mm = self.nozzle.value()
        s.min_thickness_mm = self.thickness.value()
        s.cutout_dilation = self.dilation.value()
        s.alpha_threshold = self.alpha.value()
        s.relief_mm = self.relief.value()
        s.relief_mode = self.relief_mode.currentData() or "auto"
        s.style = self.style.currentData() or "textured"
        s.build_type = self.build_type.currentData() or "solid"
        s.kit_fit_mm = self.kit_fit.value()
        s.kit_max_len = self.kit_max_len.value()
        s.kit_textured = self.kit_textured.isChecked()
        s.kit_detailed = self.kit_detailed.isChecked()
        s.kit_baseplate = self.kit_baseplate.isChecked()
        s.kit_alternate = self.kit_alternate.isChecked()
        s.include_fluids = self.fluids.isChecked()
        s.unknown_policy = self.unknown.currentData() or "cube"
        s.remove_islands = self.islands.isChecked()
        s.island_min_blocks = self.island_min.value()
        s.keep_ground_only = self.ground_only.isChecked()
        s.fill_cavities = self.fill.isChecked()
        s.base_plate_mm = self.base.value()
        s.infer_connections = self.connections.isChecked()
        s.include_mobs = self.include_mobs.isChecked()
        s.extra_mobs = [self.mob_list.item(i).data(Qt.UserRole) for i in range(self.mob_list.count())]
        s.mob_scale = self.mob_scale.value()
        s.mob_snap_yaw = self.mob_snap.isChecked()
        s.solid_textures = [t.strip() for t in self.solid_tex.text().split(",") if t.strip()]
        s.color_mode = self.color_mode.currentData() or "block"
        s.max_colors = self.max_colors.value()
        s.single_color_hex = self.single_color.color()
        s.auto_assign = self.auto_assign.isChecked()
        s.separate_glass = self.separate_glass.isChecked()
        s.filaments = [f.to_dict() for f in self.palette]
        s.export_format = self.fmt.currentData() or "3mf"
        s.split_to_bed = self.split_tiles.isChecked()
        s.bed_margin_mm = self.margin.value()
        s.bed_mm = list(self._bed())
        s.output_dir = self.out_dir.text()
        s.include_mods = self.include_mods.isChecked()
        s.use_resource_packs = self.use_packs.isChecked()
        s.extra_asset_paths = [self.extra_assets.item(i).text() for i in range(self.extra_assets.count())]
        inst = self.instance_combo.currentData()
        s.instance_name = inst.label if inst else ""
        return s

    def _bed(self) -> tuple[float, float, float]:
        return (self.bed_x.value(), self.bed_y.value(), self.bed_z.value())

    def _mode_changed(self, *_args) -> None:
        textured = (self.style.currentData() or "textured") == "textured"
        self.relief.setEnabled(textured)
        self.relief_mode.setEnabled(textured)
        kit = (self.build_type.currentData() or "solid") == "kit"
        self.kit_group.setEnabled(kit)
        self.kit_textured.setEnabled(kit and textured)
        if hasattr(self, "export_btn"):
            self.export_btn.setText("Export kit (pieces, plates, guide)" if kit else "Export")

    # ==================================================================================
    # schematic
    # ==================================================================================
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        for url in ev.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self.open_schematic(p)
                break

    def _open_dialog(self) -> None:
        start = self.qsettings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Open schematic", start, FILE_FILTER)
        if path:
            self.open_schematic(path)

    def open_schematic(self, path) -> None:
        path = Path(path)
        if not path.exists():
            QMessageBox.warning(self, "Not found", f"{path} does not exist")
            return
        self.qsettings.setValue("last_dir", str(path.parent))
        self._add_recent(path)
        self.schematic_path = path
        self.out_name.setText(path.stem)
        self._settings_from_ui()

        def task(progress, cancel):
            conv = Converter(self.settings, progress=progress, cancel=cancel)
            return conv.load(path)

        self._busy(True, f"Loading {path.name}")
        self.runner.start("load", task, self._loaded, self._task_failed, self._on_progress)

    def _loaded(self, schem: Schematic) -> None:
        self.schematic = schem
        x, y, z = schem.size
        ns = sorted({st.namespace for st in schem.unique_states()})
        self.schem_info.setText(
            f"<b>{Path(self.schematic_path).name}</b> ({schem.format})<br>"
            f"Size: X {x} × Y {y} × Z {z} blocks · {schem.block_count:,} blocks · {len(schem.unique_states())} block states<br>"
            f"Namespaces: {', '.join(ns[:12])}{'…' if len(ns) > 12 else ''}"
            + (f"<br><span style='color:#c80'>{'; '.join(schem.warnings)}</span>" if schem.warnings else ""))
        counts = sorted(schem.state_counts().items(), key=lambda kv: -kv[1])
        self.block_table.setRowCount(min(len(counts), 200))
        for r, (st, c) in enumerate(counts[:200]):
            self.block_table.setItem(r, 0, QTableWidgetItem(str(st)))
            self.block_table.setItem(r, 1, QTableWidgetItem(f"{c:,}"))
        self.block_table.resizeColumnToContents(1)
        self._show_entities(schem)
        self._busy(False, f"Loaded {schem.block_count:,} blocks")
        self.log_message.emit(f"Loaded {self.schematic_path}: X {x} x Y {y} x Z {z}, {schem.block_count:,} blocks")
        mod_ns = [n for n in ns if n != "minecraft"]
        if mod_ns:
            self._suggest_instance(mod_ns)
        # go straight to a preview
        self.convert()

    def _show_entities(self, schem: Schematic) -> None:
        from ..mobs import mob_model, normalize_mob_id
        counts: dict[str, int] = {}
        unsupported: dict[str, int] = {}
        for e in schem.entities:
            key = normalize_mob_id(e.id)
            (counts if mob_model(e.id) is not None else unsupported)[key] = (counts if mob_model(e.id) is not None else unsupported).get(key, 0) + 1
        if not schem.entities:
            self.entity_label.setText("No mobs in the loaded schematic.")
            return
        txt = ", ".join(f"{n} × {k.replace('_', ' ')}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        msg = f"In the schematic: {txt}" if counts else "In the schematic: no printable mobs"
        if unsupported:
            msg += "  ·  skipped (no model): " + ", ".join(f"{n} × {k}" for k, n in sorted(unsupported.items(), key=lambda kv: -kv[1])[:8])
        self.entity_label.setText(msg)

    def _add_mob_item(self, spec: dict) -> None:
        props = spec.get("props") or {}
        extra = f" {props}" if props else ""
        item = QListWidgetItem(f"{spec['id']} @ X {spec['x']:g}  Y {spec['y']:g}  Z {spec['z']:g}  yaw {spec.get('yaw', 0):g}°{extra}")
        item.setData(Qt.UserRole, dict(spec))
        self.mob_list.addItem(item)

    def _add_mob(self) -> None:
        spec = {"id": self.mob_id.currentData(), "x": self.mob_x.value(), "y": self.mob_y.value(), "z": self.mob_z.value(),
                "yaw": self.mob_yaw.value(), "props": {}}
        self._add_mob_item(spec)
        self._mobs_changed()

    def _remove_mob(self) -> None:
        for item in self.mob_list.selectedItems():
            self.mob_list.takeItem(self.mob_list.row(item))
        self._mobs_changed()

    def _mobs_changed(self) -> None:
        self._settings_from_ui()

    def _pick_skin(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, "Player skin (64x64 PNG)", "", "PNG images (*.png)")
        if path:
            self.settings.player_skin = path
            self.skin_label.setText(Path(path).name)
        elif self.settings.player_skin:
            self.settings.player_skin = ""
            self.skin_label.setText("Steve")

    def _suggest_instance(self, namespaces: list[str]) -> None:
        conv = Converter(self.settings)
        best = conv.pick_instance(self.schematic)
        if best is not None:
            for i in range(self.instance_combo.count()):
                if self.instance_combo.itemData(i) is best and i != self.instance_combo.currentIndex():
                    self.log_message.emit(f"Schematic uses mods ({', '.join(namespaces[:6])}); switching assets to '{best.label}'")
                    self.instance_combo.setCurrentIndex(i)
                    break

    def _add_recent(self, path: Path) -> None:
        rec = [str(path)] + [p for p in (self.qsettings.value("recent", []) or []) if p != str(path)]
        self.qsettings.setValue("recent", rec[:10])
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.qsettings.value("recent", []) or []:
            a = QAction(p, self)
            a.triggered.connect(lambda _c=False, pp=p: self.open_schematic(pp))
            self.recent_menu.addAction(a)

    # ==================================================================================
    # assets
    # ==================================================================================
    def _instance_changed(self, _idx: int) -> None:
        self.converter = None
        self.assets_status.setText("Assets not loaded (instance changed)")

    def _add_asset_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Add jar / zip / resource pack", str(Path.home()),
                                              "Jar, zip or pack (*.jar *.zip);;All files (*)")
        if not path:
            d = QFileDialog.getExistingDirectory(self, "Add resource pack folder")
            path = d
        if path:
            self.extra_assets.addItem(path)
            self.converter = None

    def load_assets(self, then=None) -> None:
        self._settings_from_ui()
        inst = self.instance_combo.currentData()
        extras = [Path(p) for p in self.settings.extra_asset_paths]
        if inst is None:
            jars = [p for p in extras if p.suffix.lower() == ".jar"]
            if not jars:
                QMessageBox.information(self, "No assets", "Choose a Minecraft installation or add a client jar first.")
                return
            inst = manual_instance(jar=jars[0])
        settings = self.settings

        def task(progress, cancel):
            conv = Converter(settings, progress=progress, cancel=cancel)
            conv.build_assets(instance=inst)
            return conv

        self._busy(True, "Loading assets")
        self.runner.start("assets", task, lambda conv: self._assets_loaded(conv, then), self._task_failed, self._on_progress)

    def _assets_loaded(self, conv: Converter, then) -> None:
        self.converter = conv
        n = len(conv.assets) if conv.assets else 0
        ns = len(conv.assets.namespaces()) if conv.assets else 0
        self.assets_status.setText(f"Loaded {n} asset sources, {ns} namespaces from '{conv.instance.label if conv.instance else 'custom'}'")
        self._busy(False, "Assets ready")
        if then:
            then()

    # ==================================================================================
    # conversion
    # ==================================================================================
    def convert(self) -> None:
        if self.schematic_path is None:
            self._open_dialog()
            return
        self._settings_from_ui()
        if self.converter is None or self.converter.assets is None:
            self.load_assets(then=self.convert)
            return
        conv = self.converter
        conv.settings = self.settings
        path = self.schematic_path
        palette = list(self.palette)

        def task(progress, cancel):
            conv._progress, conv._cancel = progress, cancel
            schem = conv.load(path)
            block_mm = conv.compute_block_mm(schem)
            model, stats = conv.build_model(schem, block_mm)
            progress(0.6, "Planning colors")
            plan = conv.plan_colors(model, filaments=palette)
            ms = conv.mesh(model, plan, block_mm)
            return schem, model, stats, plan, ms, block_mm

        self._busy(True, "Converting")
        self.runner.start("convert", task, self._converted, self._task_failed, self._on_progress)

    def _converted(self, result) -> None:
        schem, model, stats, plan, ms, block_mm = result
        self.schematic, self.model, self.stats, self.plan, self.meshes, self.current_block_mm = schem, model, stats, plan, ms, block_mm
        self.viewport.set_meshes(ms, fit=True)
        self._update_size_label()
        x, y, z = stats.get("blocks", (0, 0, 0))
        st = stats.get("states", {})
        info = (f"Block size {block_mm:.3f} mm, {stats.get('resolution')}³ voxels per block (voxel {stats.get('voxel_mm', 0):.3f} mm)<br>"
                f"Blocks X {x} × Y {y} × Z {z} · triangles {ms.triangle_count:,}<br>"
                f"Block types: {st.get('ok', 0)} modelled, {st.get('fallback', 0)} fallback shapes, {st.get('skipped', 0)} skipped")
        if stats.get("islands_removed_blocks"):
            info += f"<br>Removed {stats['islands_removed_blocks']} floating blocks"
        if stats.get("cavity_blocks_filled"):
            info += f"<br>Filled {stats['cavity_blocks_filled']} enclosed blocks"
        if stats.get("relief_steps"):
            info += f"<br>Texture relief: {stats['relief_steps']} voxel(s) = {stats['relief_steps'] * block_mm / max(stats.get('resolution', 1), 1):.2f} mm deep"
        if stats.get("connections_inferred"):
            info += f"<br>Inferred connections for {stats['connections_inferred']} fence/wall/pane blocks"
        missing = stats.get("missing_blockstates") or []
        if missing:
            info += f"<br><span style='color:#c80'>No assets for {len(missing)} block types: {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''} – check the instance/mods.</span>"
        self.model_info.setText(info)
        self._refresh_clusters()
        self._busy(False, f"Preview ready – {ms.triangle_count:,} triangles")
        self.log_message.emit(f"Converted: {self._size_text()} ; {ms.triangle_count:,} triangles ; states {st}")
        if stats.get("fallback_states"):
            self.log_message.emit("Fallback shapes: " + ", ".join(list(stats["fallback_states"])[:10]))

    def remesh_preview(self) -> None:
        """Re-mesh with the current plan (after color/filament changes)."""
        if self.model is None or self.plan is None or self.converter is None:
            return
        conv, model, plan, block_mm = self.converter, self.model, self.plan, self.current_block_mm
        full = not self.act_print_colors.isChecked()

        def task(progress, cancel):
            conv._progress, conv._cancel = progress, cancel
            return conv.mesh(model, plan, block_mm, full_color=full)

        self._busy(True, "Updating preview")
        self.runner.start("remesh", task, self._remeshed, self._task_failed, self._on_progress)

    def _remeshed(self, ms) -> None:
        self.meshes = ms
        self.viewport.set_meshes(ms, fit=False)
        self._update_size_label()
        self._busy(False, f"Preview updated – {ms.triangle_count:,} triangles")

    def replan_colors(self) -> None:
        if self.model is None or self.converter is None:
            return
        self._settings_from_ui()
        self.converter.settings = self.settings
        if self.settings.color_mode == "block" and self.model.flat_patterns is None:
            self.model.build_flat_patterns()
        self.plan = self.converter.plan_colors(self.model, filaments=list(self.palette))
        self._refresh_clusters()
        self.remesh_preview()

    def _auto_assign_now(self) -> None:
        if self.plan is None:
            return
        if not self.palette:
            QMessageBox.information(self, "No filaments", "Add filaments to the print palette first (or sync from your printer).")
            return
        assign_filaments(self.plan, list(self.palette), max_slots=self.max_colors.value())
        self._refresh_clusters()
        self.remesh_preview()

    def _size_text(self) -> str:
        if self.meshes is None:
            return ""
        lo, hi = self.meshes.bounds()
        d = hi - lo
        return f"X {d[0]:.1f} mm × Y {d[1]:.1f} mm × Z {d[2]:.1f} mm"

    def _update_size_label(self) -> None:
        if self.meshes is None:
            self.size_label.setText("No model")
            return
        txt = f"Print size: {self._size_text()}"
        bx, by, bz = self._bed()
        lo, hi = self.meshes.bounds()
        d = hi - lo
        if d[0] > bx or d[1] > by or d[2] > bz:
            txt += f"   <span style='color:#e55'>exceeds bed {bx:g}×{by:g}×{bz:g} mm – scale down, rotate, or enable tile splitting</span>"
        else:
            txt += f"   <span style='color:#6c6'>fits bed {bx:g}×{by:g}×{bz:g} mm</span>"
        self.size_label.setText(txt)

    # ==================================================================================
    # colors
    # ==================================================================================
    def _refresh_palette(self) -> None:
        self.palette_list.clear()
        for i, f in enumerate(self.palette):
            it = QListWidgetItem(swatch_pixmap(f.color_hex), f"Slot {i + 1}: {f.label} [{f.material}] {f.color_hex}")
            self.palette_list.addItem(it)
        if hasattr(self, "max_colors") and self.palette:
            self.max_colors.setValue(max(1, min(64, len(self.palette))))

    def _add_filaments(self) -> None:
        dlg = FilamentDialog(self, library=self.library)
        if dlg.exec() and dlg.result_filaments:
            self.palette.extend(dlg.result_filaments)
            self._refresh_palette()
            self._after_palette_change()

    def _edit_palette_color(self) -> None:
        row = self.palette_list.currentRow()
        if row < 0 or row >= len(self.palette):
            return
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.palette[row].color_hex), self, "Filament color")
        if c.isValid():
            self.palette[row].color_hex = c.name().upper()
            self._refresh_palette()
            self._after_palette_change()

    def _remove_filament(self) -> None:
        row = self.palette_list.currentRow()
        if 0 <= row < len(self.palette):
            del self.palette[row]
            self._refresh_palette()
            self._after_palette_change()

    def _import_slicer(self) -> None:
        try:
            from ..color.slicer_presets import import_slicer_filaments
            fils = import_slicer_filaments()
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        if not fils:
            QMessageBox.information(self, "Nothing found", "No filament presets found in Bambu Studio / OrcaSlicer / PrusaSlicer configuration.")
            return
        added = self.library.add_many(fils)
        self.library.save()
        dlg = FilamentDialog(self, library=self.library)
        dlg.source.setCurrentIndex(2)
        self.log_message.emit(f"Imported {len(fils)} filament presets ({added} new in library)")
        if dlg.exec() and dlg.result_filaments:
            self.palette.extend(dlg.result_filaments)
            self._refresh_palette()
            self._after_palette_change()

    def _after_palette_change(self) -> None:
        self._settings_from_ui()
        if self.plan is not None and self.auto_assign.isChecked():
            self.replan_colors()

    def _refresh_clusters(self) -> None:
        plan = self.plan
        self.cluster_table.blockSignals(True)
        self.cluster_table.setRowCount(0)
        if plan is None:
            self.cluster_table.blockSignals(False)
            return
        total = sum(c.weight for c in plan.clusters) or 1
        block_names = self._cluster_block_names()
        mats = list(plan.materials.values())
        self.cluster_table.setRowCount(len(plan.clusters))
        for r, c in enumerate(sorted(plan.clusters, key=lambda c: -c.weight)):
            sw = QTableWidgetItem(c.hex)
            sw.setIcon(swatch_pixmap(c.hex))
            sw.setFlags(Qt.ItemIsEnabled)
            self.cluster_table.setItem(r, 0, sw)
            share = QTableWidgetItem(f"{100 * c.weight / total:5.1f}%")
            share.setFlags(Qt.ItemIsEnabled)
            self.cluster_table.setItem(r, 1, share)
            names = QTableWidgetItem(", ".join(block_names.get(c.id, [])[:5]))
            names.setFlags(Qt.ItemIsEnabled)
            names.setToolTip("\n".join(block_names.get(c.id, [])))
            self.cluster_table.setItem(r, 2, names)
            combo = QComboBox()
            combo.addItem("(skip – do not print)", 0)
            for m in mats:
                combo.addItem(swatch_pixmap(m.hex), f"{m.name}", m.id)
            combo.addItem("+ new filament…", -1)
            combo.setCurrentIndex(max(0, combo.findData(c.material)))
            combo.currentIndexChanged.connect(lambda _i, cid=c.id, cb=combo: self._cluster_assigned(cid, cb))
            self.cluster_table.setCellWidget(r, 3, combo)
        self.cluster_table.resizeColumnsToContents()
        self.cluster_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.cluster_table.blockSignals(False)
        report = color_error_report(plan)
        worst = max((d for _c, d in report if d != float("inf")), default=0.0)
        used = plan.used_materials()
        self.color_info.setText(f"{len(plan.clusters)} model colors → {len(used)} filaments. "
                                f"Worst color mismatch ΔE = {worst:.1f} " + ("(good)" if worst < 12 else "(visible difference; add closer filaments)"))

    def _cluster_block_names(self) -> dict[int, list[str]]:
        """cluster id -> block names whose dominant color falls in that cluster."""
        out: dict[int, list[str]] = {}
        if self.model is None or self.plan is None:
            return out
        try:
            member_to_cluster = {}
            for c in self.plan.clusters:
                for m in c.members.tolist():
                    member_to_cluster[int(m)] = c.id
            flat = self.settings.color_mode == "block"
            pats = self.model.flat_patterns if (flat and self.model.flat_patterns) else self.model.patterns
            import numpy as np
            used = np.unique(self.model.blocks)
            for i in used.tolist():
                if i == 0 or pats[i].is_empty:
                    continue
                cid = member_to_cluster.get(int(pats[i].dominant))
                if cid is not None:
                    name = self.model.palette[i].path
                    if name not in out.setdefault(cid, []):
                        out[cid].append(name)
        except Exception as exc:
            log.debug("cluster names failed: %s", exc)
        return out

    def _cluster_assigned(self, cluster_id: int, combo: QComboBox) -> None:
        if self.plan is None:
            return
        mid = combo.currentData()
        if mid == -1:
            dlg = FilamentDialog(self, library=self.library, allow_multi=False)
            if dlg.exec() and dlg.result_filaments:
                f = dlg.result_filaments[0]
                self.palette.append(f)
                self._refresh_palette()
                m = add_material(self.plan, f, slot=len(self.palette))
                set_cluster_material(self.plan, cluster_id, m.id)
            self._refresh_clusters()
        else:
            set_cluster_material(self.plan, cluster_id, int(mid))
        self.remesh_preview()

    # ==================================================================================
    # printers
    # ==================================================================================
    def _load_saved_printers(self) -> None:
        try:
            from ..printers.manager import PrinterProfiles
            self.profiles = PrinterProfiles()
        except Exception as exc:
            log.warning("printer profiles unavailable: %s", exc)
            self.profiles = None
            return
        self.discovered = list(self.profiles.all())
        self._refresh_printer_list()
        sel = self.profiles.selected
        if sel is not None:
            for i, p in enumerate(self.discovered):
                if p.id == sel.id:
                    self.printer_list.setCurrentRow(i)

    def _refresh_printer_list(self) -> None:
        self.printer_list.blockSignals(True)
        self.printer_list.clear()
        for p in self.discovered:
            where = p.host or p.serial_port or ""
            self.printer_list.addItem(f"{p.name}  [{p.kind}]  {p.model}  {where}  {p.bed_text}")
        self.printer_list.blockSignals(False)

    def scan_printers(self) -> None:
        subnet = self.subnet_scan.isChecked()

        def task(progress, cancel):
            from ..printers.discovery import discover_all
            return discover_all(timeout=6.0, progress=lambda m: progress(0.5, m), use_subnet_scan=subnet)

        self._busy(True, "Scanning for printers")
        self.runner.start("scan", task, self._scanned, self._task_failed, self._on_progress)

    def _scanned(self, found: list[PrinterInfo]) -> None:
        known = {p.id: p for p in self.discovered}
        for p in found:
            if p.id in known:
                old = known[p.id]
                p.credentials = {**p.credentials, **old.credentials}
                if not p.filament_slots:
                    p.filament_slots = old.filament_slots
                self.discovered[self.discovered.index(old)] = p
            else:
                self.discovered.append(p)
        self._refresh_printer_list()
        self._busy(False, f"Found {len(found)} printer(s)")
        self.log_message.emit(f"Printer scan: {len(found)} found: " + ", ".join(f"{p.name} ({p.kind})" for p in found))
        if found and self.printer_list.currentRow() < 0:
            self.printer_list.setCurrentRow(0)

    def _add_printer_by_ip(self) -> None:
        host, ok = QInputDialog.getText(self, "Add printer", "IP address or hostname:")
        if not ok or not host.strip():
            return
        host = host.strip()

        def task(progress, cancel):
            from ..printers.discovery import probe_host
            progress(0.5, f"Probing {host}")
            return probe_host(host, timeout=3.0)

        def done(found):
            if not found:
                # let the user pick the kind manually
                kinds = ["bambu", "moonraker", "octoprint", "prusalink", "duet", "elegoo"]
                kind, ok2 = QInputDialog.getItem(self, "Printer type", f"Nothing answered at {host}. Choose the printer type:", kinds, 0, False)
                if not ok2:
                    self._busy(False, "")
                    return
                found = [PrinterInfo(id=f"{kind}:{host}", kind=kind, name=f"{kind} at {host}", host=host,
                                     port={"bambu": 8883, "moonraker": 7125, "elegoo": 3030}.get(kind, 80))]
            self._scanned(found)

        self._busy(True, f"Probing {host}")
        self.runner.start("probe", task, done, self._task_failed, self._on_progress)

    def _remove_printer(self) -> None:
        row = self.printer_list.currentRow()
        if 0 <= row < len(self.discovered):
            p = self.discovered.pop(row)
            if self.profiles:
                self.profiles.remove(p.id)
                self.profiles.save()
            self._refresh_printer_list()

    def _printer_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.discovered):
            return
        self.printer = self.discovered[row]
        p = self.printer
        self.printer_name.setText(f"{p.name} – {p.model or p.kind}")
        if p.bed_x and p.bed_y and p.bed_z:
            for sb, v in ((self.bed_x, p.bed_x), (self.bed_y, p.bed_y), (self.bed_z, p.bed_z)):
                sb.blockSignals(True)
                sb.setValue(v)
                sb.blockSignals(False)
            self._bed_changed()
        if p.nozzle_mm:
            self.nozzle.setValue(p.nozzle_mm)
        self.slots.setValue(p.multi_color_slots)
        self.max_colors.setValue(p.multi_color_slots)
        self._build_credential_fields(p)
        self.printer_status.setText(f"{p.status or ''} {('firmware ' + p.firmware) if p.firmware else ''}")
        self._refresh_slot_list(p.filament_slots)
        if self.profiles:
            self.profiles.add_or_update(p)
            self.profiles.selected_id = p.id
            self.profiles.save()

    def _build_credential_fields(self, p: PrinterInfo) -> None:
        while self.cred_form.rowCount():
            self.cred_form.removeRow(0)
        self.cred_fields = {}
        try:
            from ..printers.manager import get_backend
            be = get_backend(p.kind)
            reqs = be.required_credentials() if be else []
        except Exception:
            reqs = []
        if p.kind == "bambu":
            reqs = [("serial_number", "Printer serial number", False)] + [r for r in reqs if r[0] != "serial_number"]
        for key, label, secret in reqs:
            le = QLineEdit(str(p.credentials.get(key, "") or (p.serial_number if key == "serial_number" else "") or ""))
            if secret:
                le.setEchoMode(QLineEdit.Password)
            self.cred_form.addRow(label, le)
            self.cred_fields[key] = le

    def _apply_credentials(self) -> None:
        if not self.printer:
            return
        for k, le in self.cred_fields.items():
            v = le.text().strip()
            if v:
                self.printer.credentials[k] = v
                if k == "serial_number":
                    self.printer.serial_number = v
        if self.profiles:
            self.profiles.add_or_update(self.printer)
            self.profiles.save()

    def _vendor_changed(self, vendor: str) -> None:
        self.model_combo.clear()
        try:
            from ..printers.database import specs_for_vendor
            for sp in specs_for_vendor(vendor):
                self.model_combo.addItem(f"{sp.model}  ({sp.bed_x:g}×{sp.bed_y:g}×{sp.bed_z:g} mm, {sp.slots} slots)", sp)
        except Exception:
            pass

    def _use_db_printer(self) -> None:
        sp = self.model_combo.currentData()
        if sp is None:
            return
        from ..printers.database import spec_to_printer_info
        info = spec_to_printer_info(sp)
        if info.id not in {p.id for p in self.discovered}:
            self.discovered.append(info)
            self._refresh_printer_list()
        self.printer_list.setCurrentRow([p.id for p in self.discovered].index(info.id))

    def _bed_changed(self) -> None:
        if not hasattr(self, "viewport"):
            return
        self.viewport.set_bed(self._bed())
        self._update_size_label()

    def connect_printer(self) -> None:
        if not self.printer:
            QMessageBox.information(self, "No printer", "Select or scan for a printer first.")
            return
        self._apply_credentials()
        info = self.printer

        def task(progress, cancel):
            from ..printers.manager import connect_printer
            return connect_printer(info, progress=lambda m: progress(0.5, m))

        self._busy(True, f"Connecting to {info.name}")
        self.runner.start("connect", task, self._connected, self._task_failed, self._on_progress)

    def _connected(self, info: PrinterInfo) -> None:
        row = self.printer_list.currentRow()
        if 0 <= row < len(self.discovered):
            info.credentials = {**self.discovered[row].credentials, **info.credentials}
            self.discovered[row] = info
        self.printer = info
        self._refresh_printer_list()
        self.printer_list.setCurrentRow(row)
        self._busy(False, f"Connected: {info.name} {info.status or ''}")
        self.log_message.emit(f"Printer {info.name}: {info.model} bed {info.bed_text} nozzle {info.nozzle_mm} slots {info.multi_color_slots}")
        if info.filament_slots:
            self._refresh_slot_list(info.filament_slots)

    def sync_filaments(self) -> None:
        if not self.printer:
            QMessageBox.information(self, "No printer", "Select a printer on the Printer tab first.")
            self.tabs.setCurrentIndex(3)
            return
        self._apply_credentials()
        info = self.printer

        def task(progress, cancel):
            from ..printers.manager import sync_filaments
            return sync_filaments(info, progress=lambda m: progress(0.5, m))

        self._busy(True, f"Reading filaments from {info.name}")
        self.runner.start("sync", task, self._synced, self._task_failed, self._on_progress)

    def _synced(self, slots) -> None:
        if self.printer is not None:
            self.printer.filament_slots = list(slots)
            if self.profiles:
                self.profiles.add_or_update(self.printer)
                self.profiles.save()
        self._refresh_slot_list(slots)
        pal = PrintPalette.from_slots(slots)
        self.palette = list(pal.filaments)
        self._refresh_palette()
        self._busy(False, f"Synced {len(self.palette)} loaded filaments")
        self.log_message.emit("Filaments: " + ", ".join(f"{f.color_hex} {f.label}" for f in self.palette))
        self.tabs.setCurrentIndex(2)
        self._after_palette_change()

    def _refresh_slot_list(self, slots) -> None:
        self.slot_list.clear()
        for s in slots or []:
            txt = f"{s.group + ' ' if s.group else ''}slot {s.index + 1}: {s.name or s.material} [{s.material}] {s.color_hex}"
            if not s.loaded:
                txt += " (empty)"
            if s.remaining_pct is not None:
                txt += f" {s.remaining_pct:.0f}% left"
            self.slot_list.addItem(QListWidgetItem(swatch_pixmap(s.color_hex), txt))

    # ==================================================================================
    # export
    # ==================================================================================
    def _pick_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def export(self) -> None:
        if self.meshes is None or self.model is None or self.plan is None or self.converter is None:
            QMessageBox.information(self, "Nothing to export", "Convert a schematic first.")
            return
        self._settings_from_ui()
        out_dir = Path(self.out_dir.text() or ".")
        name = self.out_name.text().strip() or (self.schematic_path.stem if self.schematic_path else "model")
        fmt = self.fmt.currentData()
        conv = self.converter
        conv.settings = self.settings
        model, plan, block_mm, ms = self.model, self.plan, self.current_block_mm, self.meshes
        split = self.split_tiles.isChecked()
        title = name

        kit_mode = self.settings.build_type == "kit"

        def task(progress, cancel):
            conv._progress, conv._cancel = progress, cancel
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / name
            if kit_mode:
                from ..kit import build_kit, write_kit
                kit, plates = build_kit(model, plan, conv.kit_settings(block_mm), progress=progress)
                return write_kit(kit, plates, out_dir / f"{name}_kit", title, progress=progress)
            if split:
                _combined, files = conv._export_tiles(model, plan, block_mm, out, fmt, title)
                return files
            progress(0.5, "Writing files")
            return conv.export(ms, out, fmt, title=title)

        self._busy(True, "Exporting")
        self.runner.start("export", task, self._exported, self._task_failed, self._on_progress)

    def _exported(self, files: list[Path]) -> None:
        self.last_files = list(files)
        self._busy(False, f"Exported {len(files)} file(s)")
        self.export_info.setText("Wrote:<br>" + "<br>".join(str(f) for f in files))
        self.log_message.emit("Exported: " + ", ".join(str(f) for f in files))
        self.settings.save()

    def _open_in_slicer(self) -> None:
        app = self.slicer_combo.currentData()
        if app is None:
            QMessageBox.information(self, "No slicer", "No slicer application was found on this computer.")
            return
        if not self.last_files:
            QMessageBox.information(self, "Nothing exported", "Export a model first.")
            return
        from ..printers.slicers import open_in_slicer
        target = next((f for f in self.last_files if f.suffix.lower() in (".3mf", ".stl", ".obj")), self.last_files[0])
        try:
            open_in_slicer(app, str(target))
            self.log_message.emit(f"Opened {target.name} in {app.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Failed", str(exc))

    def _open_folder(self) -> None:
        d = self.out_dir.text()
        if not d:
            return
        Path(d).mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(d)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    # ==================================================================================
    # misc
    # ==================================================================================
    def _toggle_wire(self, on: bool) -> None:
        self.viewport.wireframe = on
        self.viewport.update()

    def _toggle_bed(self, on: bool) -> None:
        self.viewport.show_bed = on
        self.viewport._line_dirty = True
        self.viewport.update()

    def _screenshot(self) -> None:
        img = self.viewport.screenshot()
        path, _ = QFileDialog.getSaveFileName(self, "Save screenshot", str(Path(self.out_dir.text() or ".") / "preview.png"), "PNG (*.png)")
        if path:
            img.save(path)
            self.log_message.emit(f"Screenshot saved to {path}")

    def _busy(self, on: bool, msg: str) -> None:
        self.cancel_btn.setEnabled(on)
        self.convert_btn.setEnabled(not on)
        self.export_btn.setEnabled(not on)
        if msg:
            self.status.setText(msg)
        if not on:
            self.progress.setValue(1000 if msg.lower().startswith(("preview", "exported", "loaded", "connected", "synced", "found", "assets")) else 0)

    def _on_progress(self, frac: float, msg: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        self.status.setText(msg)

    def _task_failed(self, msg: str, tb: str) -> None:
        self._busy(False, f"Error: {msg}")
        self.log_message.emit(f"ERROR: {msg}")
        if tb:
            log.error(tb)
        if msg != "Cancelled":
            QMessageBox.warning(self, "Operation failed", msg)

    def _append_log(self, msg: str) -> None:
        self.log.appendPlainText(time.strftime("%H:%M:%S ") + msg)

    def _about(self) -> None:
        QMessageBox.about(self, "About mc-print-3d",
                          f"<b>mc-print-3d {__version__}</b><br>Converts Minecraft schematics (MCEdit, Sponge/WorldEdit, Litematica, "
                          "structure NBT, Bedrock, Axiom) into 3D-printable multi-color models using the real block models and textures "
                          "of your Minecraft installation and mods.<br><br>Printer detection: Bambu Lab (LAN), Klipper/Moonraker, OctoPrint, "
                          "PrusaLink, Duet, Elegoo, USB serial.<br>Filament sync: Bambu AMS, Spoolman, Happy Hare, OctoPrint spool plugins, slicer presets.")

    def closeEvent(self, ev) -> None:
        try:
            self._settings_from_ui()
            self.settings.save()
        except Exception:
            pass
        self.runner.cancel_all()
        super().closeEvent(ev)
