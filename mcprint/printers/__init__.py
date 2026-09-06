"""Printer detection, connection and filament synchronization.

Every integration lives in its own module (``bambu``, ``moonraker``, ``octoprint``,
``prusalink``, ``duet``, ``elegoo``, ``serial_detect``) and registers itself in
:data:`~mcprint.printers.base.BACKENDS` when imported.  Nothing is imported at package import
time – call :func:`load_backends` (or use :mod:`mcprint.printers.manager`) so the CLI starts
fast and a missing optional dependency never breaks the app.
"""
from __future__ import annotations

import logging
from importlib import import_module

from .base import BACKENDS, FilamentSlot, PrinterError, PrinterInfo

log = logging.getLogger(__name__)

__all__ = ["PrinterInfo", "FilamentSlot", "PrinterError", "BACKENDS", "load_backends",
           "BACKEND_MODULES"]

#: Backend modules, in the order they should be offered to the user.
BACKEND_MODULES: tuple[str, ...] = ("bambu", "moonraker", "octoprint", "prusalink", "duet",
                                    "elegoo", "serial_detect")


def load_backends() -> dict:
    """Import every backend module (each one guarded) and return :data:`BACKENDS`.

    Modules that do not exist yet, or that fail to import because an optional dependency is
    missing, are logged and skipped – the remaining backends stay usable.
    """
    for name in BACKEND_MODULES:
        try:
            import_module(f".{name}", __name__)
        except Exception as exc:
            log.debug("printer backend %r not loaded: %s", name, exc)
    return BACKENDS
