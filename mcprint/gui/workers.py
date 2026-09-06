"""Run long operations in a QThread with progress / cancel / error signals."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal

log = logging.getLogger(__name__)


class Worker(QThread):
    """Executes ``fn(progress, cancel)`` in a thread.

    ``progress(frac, msg)`` may be called from the worker; ``cancel()`` returns True once
    :meth:`request_cancel` was called.  Results arrive through ``done(result)``; exceptions
    through ``failed(message, traceback)``.
    """

    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, fn: Callable[..., Any], parent: Optional[QObject] = None, name: str = "task"):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False
        self.name = name

    def request_cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # noqa: D401
        try:
            result = self._fn(self._emit_progress, self.cancelled)
        except Exception as exc:  # pragma: no cover - UI path
            from ..voxel import Cancelled
            if isinstance(exc, Cancelled):
                self.failed.emit("Cancelled", "")
            else:
                log.exception("worker %s failed", self.name)
                self.failed.emit(str(exc) or exc.__class__.__name__, traceback.format_exc())
            return
        self.done.emit(result)

    def _emit_progress(self, frac: float, msg: str) -> None:
        self.progress.emit(float(frac), str(msg))


class TaskRunner(QObject):
    """Keeps at most one worker alive per key and wires the signals to callbacks."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._workers: dict[str, Worker] = {}

    def is_running(self, key: str) -> bool:
        w = self._workers.get(key)
        return bool(w and w.isRunning())

    def start(self, key: str, fn: Callable[..., Any], on_done: Callable[[Any], None],
              on_error: Callable[[str, str], None], on_progress: Optional[Callable[[float, str], None]] = None) -> Worker:
        old = self._workers.get(key)
        if old and old.isRunning():
            old.request_cancel()
            old.wait(5000)
        w = Worker(fn, self, name=key)
        w.done.connect(on_done)
        w.failed.connect(on_error)
        if on_progress:
            w.progress.connect(on_progress)
        w.finished.connect(lambda: self._cleanup(key, w))
        self._workers[key] = w
        w.start()
        return w

    def cancel(self, key: str) -> None:
        w = self._workers.get(key)
        if w and w.isRunning():
            w.request_cancel()

    def cancel_all(self) -> None:
        for w in list(self._workers.values()):
            if w.isRunning():
                w.request_cancel()
                w.wait(10000)

    def _cleanup(self, key: str, w: Worker) -> None:
        if self._workers.get(key) is w:
            self._workers.pop(key, None)
        w.deleteLater()
