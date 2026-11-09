"""Small helpers: background jobs that call back on the Qt main thread."""
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, Qt, Signal, Slot


class _Bridge(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:
            traceback.print_exc()


_bridge = None
DEFAULT_FOURK = "1" if sys.platform.startswith("linux") else "0"


def init_bridge():
    global _bridge
    _bridge = _Bridge()


def on_main(fn):
    _bridge.call.emit(fn)


def run_bg(fn, on_done=None, on_fail=None):
    def job():
        try:
            res = fn()
        except Exception as e:  # noqa
            msg = str(e) or e.__class__.__name__
            if on_fail:
                on_main(lambda: on_fail(msg))
            return
        if on_done:
            on_main(lambda: on_done(res))
    threading.Thread(target=job, daemon=True).start()


CLOCK24 = True


def set_clock24(on):
    global CLOCK24
    CLOCK24 = bool(on)


def fmt_time(ts):
    lt = time.localtime(ts)
    if CLOCK24:
        return time.strftime("%H:%M", lt)
    return time.strftime("%I:%M %p", lt).lstrip("0")
