"""Desktop application entry point: ``python -m mcprint gui`` or ``mcprint-gui``."""
from __future__ import annotations

import logging
import sys


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is required for the GUI:  pip install PySide6", file=sys.stderr)
        return 1
    from .gui.viewport import default_surface_format
    QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("mc-print-3d")
    app.setOrganizationName("mc-print-3d")
    from .gui.main_window import MainWindow
    win = MainWindow()
    win.show()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        win.open_schematic(files[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
