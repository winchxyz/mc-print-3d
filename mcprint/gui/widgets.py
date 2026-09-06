"""Small reusable Qt widgets: color swatches, color buttons, filament pickers."""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ..color.filament import Filament
from ..util.color import contrast_text_color, parse_hex, to_hex


def swatch_pixmap(hex_color: str, size: int = 18) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor(hex_color))
    p = QPainter(pm)
    p.setPen(QColor(60, 60, 60))
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return pm


class ColorSwatch(QLabel):
    def __init__(self, hex_color: str = "#808080", size: int = 18, parent=None):
        super().__init__(parent)
        self._size = size
        self.set_color(hex_color)

    def set_color(self, hex_color: str) -> None:
        self.setPixmap(swatch_pixmap(hex_color, self._size))
        self.setToolTip(hex_color)


class ColorButton(QPushButton):
    """Button showing a color; click opens a QColorDialog."""

    changed = Signal(str)

    def __init__(self, hex_color: str = "#C8C8C8", parent=None):
        super().__init__(parent)
        self._hex = "#C8C8C8"
        self.set_color(hex_color)
        self.clicked.connect(self._pick)
        self.setFixedWidth(110)

    def color(self) -> str:
        return self._hex

    def set_color(self, hex_color: str) -> None:
        try:
            rgb = parse_hex(hex_color)
        except ValueError:
            return
        self._hex = to_hex(rgb)
        self.setText(self._hex)
        self.setStyleSheet(f"QPushButton {{ background: {self._hex}; color: {contrast_text_color(rgb)}; font-weight: bold; }}")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._hex), self, "Pick color")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self._hex)


class FilamentDialog(QDialog):
    """Pick filaments from the library / catalog / slicer presets, or add a custom color."""

    def __init__(self, parent=None, library=None, allow_multi: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Choose filaments")
        self.resize(560, 520)
        self.library = library
        self.result_filaments: list[Filament] = []
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItems(["My library", "Catalog", "Slicer presets"])
        self.vendor = QComboBox()
        self.material = QComboBox()
        self.search = QLineEdit()
        self.search.setPlaceholderText("search…")
        top.addWidget(QLabel("Source:"))
        top.addWidget(self.source)
        top.addWidget(QLabel("Vendor:"))
        top.addWidget(self.vendor)
        top.addWidget(QLabel("Material:"))
        top.addWidget(self.material)
        top.addWidget(self.search, 1)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection if allow_multi else QListWidget.SingleSelection)
        lay.addWidget(self.list, 1)
        custom = QHBoxLayout()
        self.custom_color = ColorButton("#FFFFFF")
        self.custom_name = QLineEdit()
        self.custom_name.setPlaceholderText("custom filament name")
        self.custom_material = QLineEdit("PLA")
        self.custom_material.setFixedWidth(70)
        add_custom = QPushButton("Add custom")
        add_custom.clicked.connect(self._add_custom)
        custom.addWidget(self.custom_color)
        custom.addWidget(self.custom_name, 1)
        custom.addWidget(self.custom_material)
        custom.addWidget(add_custom)
        lay.addLayout(custom)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._all: list[Filament] = []
        self.source.currentIndexChanged.connect(self._reload)
        self.vendor.currentIndexChanged.connect(self._refilter)
        self.material.currentIndexChanged.connect(self._refilter)
        self.search.textChanged.connect(self._refilter)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        self._reload()

    def _reload(self) -> None:
        src = self.source.currentIndex()
        if src == 0:
            self._all = list(self.library.filaments) if self.library else []
        elif src == 1:
            from ..color.catalog import catalog_filaments
            self._all = catalog_filaments()
        else:
            try:
                from ..color.slicer_presets import import_slicer_filaments
                self._all = import_slicer_filaments()
            except Exception:
                self._all = []
        vendors = sorted({f.vendor for f in self._all if f.vendor})
        mats = sorted({f.material for f in self._all if f.material})
        for combo, items in ((self.vendor, vendors), (self.material, mats)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("all")
            combo.addItems(items)
            combo.blockSignals(False)
        self._refilter()

    def _refilter(self) -> None:
        v = self.vendor.currentText()
        m = self.material.currentText()
        q = self.search.text().strip().lower()
        self.list.clear()
        for f in self._all:
            if v != "all" and f.vendor != v:
                continue
            if m != "all" and f.material != m:
                continue
            label = f"{f.label}  [{f.material}]  {f.color_hex}"
            if q and q not in label.lower():
                continue
            it = QListWidgetItem(swatch_pixmap(f.color_hex), label)
            it.setData(Qt.UserRole, f)
            self.list.addItem(it)

    def _add_custom(self) -> None:
        f = Filament(color_hex=self.custom_color.color(), name=self.custom_name.text() or self.custom_color.color(),
                     material=self.custom_material.text() or "PLA", source="manual")
        if self.library is not None:
            self.library.add(f)
            self.library.save()
        self.result_filaments.append(f)
        self.accept()

    def _accept(self) -> None:
        for it in self.list.selectedItems():
            self.result_filaments.append(it.data(Qt.UserRole))
        self.accept()


def hline() -> QWidget:
    from PySide6.QtWidgets import QFrame
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f


def form(pairs: Iterable[tuple[str, QWidget]]) -> QFormLayout:
    fl = QFormLayout()
    fl.setLabelAlignment(Qt.AlignRight)
    for label, w in pairs:
        fl.addRow(label, w)
    return fl
