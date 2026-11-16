"""Run blocking operations in Qt's thread pool and return results on the UI thread."""
import logging

from PySide6.QtCore import QObject, QThreadPool, Qt, Signal


class BackgroundTask(QObject):
    finished = Signal(object, object)

    def __init__(self, work, finished):
        super().__init__()
        self._work = work
        self.finished.connect(finished, Qt.QueuedConnection)
        QThreadPool.globalInstance().start(self._run)

    def _run(self):
        try:
            result = self._work()
        except Exception as exc:
            logging.getLogger("PetPal").exception("Background operation failed")
            result = exc
        finally:
            self._work = None
        self.finished.emit(self, result)
