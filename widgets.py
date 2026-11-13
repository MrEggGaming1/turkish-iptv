"""Video widget (libmpv render API), EPG grid guide and channel list."""
import ctypes
import sys
import time

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QOpenGLContext, QPainter, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QVBoxLayout, QWidget
from mpv import MPV, MpvGlGetProcAddressFn, MpvRenderContext

import db
import keybinds
from util import fmt_time


# libmpv's OpenGL render API renders into Qt's FBO; Qt owns the buffer swap,
# so libmpv has no swap_buffers callback. Older mpv releases nevertheless call
# glFenceSync once per frame and never retire those fences (Mesa/v3d exports
# each as an anon_inode:sync_file). Returning NULL from glFenceSync is the
# upstream mpv fix's intended behaviour for render contexts without a swapchain.
_GL_FENCE_SYNC_STUB = ctypes.CFUNCTYPE(
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint
)(lambda _condition, _flags: None)
_GL_FENCE_SYNC_STUB_PTR = ctypes.cast(_GL_FENCE_SYNC_STUB, ctypes.c_void_p).value


def _get_proc_address(_, name):
    # python-mpv passes GL symbol names as bytes. Accept str too for compatibility.
    symbol = name.decode("ascii", "ignore") if isinstance(name, bytes) else str(name)
    if symbol == "glFenceSync":
        return _GL_FENCE_SYNC_STUB_PTR
    glctx = QOpenGLContext.currentContext()
    if glctx is None:
        return 0
    addr = int(glctx.getProcAddress(name))
    return ctypes.cast(ctypes.c_void_p(addr), ctypes.c_void_p).value


class MpvWidget(QOpenGLWidget):
    """Renders mpv into an OpenGL widget. Works on macOS and Linux/Wayland alike."""
    _update_requested = Signal()
    resized = Signal()
    clicked = Signal()
    double_clicked = Signal()
    moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctx = None
        self.setMouseTracking(True)
        opts = dict(
            vo="libmpv", idle="yes", keep_open="no",
            input_default_bindings=False, input_vo_keyboard=False, osc=False,
            ytdl=False, loglevel="warn", cache="yes",
            demuxer_max_bytes="64MiB", network_timeout="10",
            demuxer_lavf_o="reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
        )
        if sys.platform.startswith("linux"):
            opts["profile"] = "fast"  # cheaper scaling: keeps 4K playback light on the Pi's GPU
        self.mpv = MPV(**opts)
        self.hwdec_choice = "no"
        self.apply_decoder("auto", False)
        self._proc_fn = MpvGlGetProcAddressFn(_get_proc_address)
        self._update_requested.connect(self.update, Qt.QueuedConnection)

    def apply_decoder(self, mode="auto", fourk=False):
        """Pick the video decoder and the 4K / high-bitrate tuning.

        Pi 5: HEVC (H.265) is decoded by the hardware V4L2 decoder (up to 4K60). H.264 has no hardware
        decoder on the Pi 5, so it falls back to the CPU. mpv drops back to software by itself whenever a
        hardware decoder is missing, so every candidate below is safe to try."""
        m = self.mpv
        if mode == "software":
            chain = ["no"]
        elif sys.platform.startswith("linux"):
            chain = ["v4l2request-copy,drm-copy", "v4l2request-copy", "drm-copy"]
            chain += ["auto-safe", "no"] if mode == "auto" else ["no"]
        else:
            chain = ["auto-safe", "no"]
        self.hwdec_choice = "no"
        for cand in chain:
            try:
                m["hwdec"] = cand
                self.hwdec_choice = cand
                break
            except Exception:
                continue
        tuning = (("vd-lavc-fast", "yes" if fourk else "no"),
                  ("vd-lavc-skiploopfilter", "nonref" if fourk else "default"),
                  ("demuxer-max-bytes", "192MiB" if fourk else "64MiB"),
                  ("demuxer-readahead-secs", "20" if fourk else "10"))
        for name, val in tuning:
            try:
                m[name] = val
            except Exception:
                pass

    def initializeGL(self):
        self.ctx = MpvRenderContext(
            self.mpv, "opengl", opengl_init_params={"get_proc_address": self._proc_fn})
        self.ctx.update_cb = self._update_requested.emit

    def paintGL(self):
        if not self.ctx:
            return
        ratio = self.devicePixelRatioF()
        w, h = int(self.width() * ratio), int(self.height() * ratio)
        self.ctx.render(flip_y=True,
                        opengl_fbo={"w": w, "h": h, "fbo": self.defaultFramebufferObject()})

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def mouseMoveEvent(self, e):
        pos = e.globalPosition()
        last = getattr(self, "_last_pos", None)
        self._last_pos = pos
        if last is not None and abs(pos.x() - last.x()) + abs(pos.y() - last.y()) >= 3:
            self.moved.emit()

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()

    def shutdown(self):
        try:
            if self.ctx:
                self.makeCurrent()
                self.ctx.free()
                self.ctx = None
            self.mpv.terminate()
        except Exception:
            pass


class ChannelList(QListWidget):
    picked = Signal(int)
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet(
            "QListWidget{background:rgba(10,12,20,235);color:white;font-size:18px;border:none;}"
            "QListWidget::item{padding:8px 12px;}"
            "QListWidget::item:selected{background:#2d6cdf;}")
        self.close_keys = (Qt.Key_Escape, Qt.Key_L)
        self.itemDoubleClicked.connect(lambda _i: self._pick())
        self.hide()

    def open(self, channels, idx):
        self.clear()
        for c in channels:
            self.addItem(f"{c['number']:>3}   {c['name']}")
        self.setCurrentRow(max(idx, 0))
        self.show()
        self.raise_()
        self.setFocus()

    def _pick(self):
        self.hide()
        self.picked.emit(self.currentRow())

    def keyPressEvent(self, e):
        if e.key() in self.close_keys:
            self.hide()
            self.closed.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._pick()
        else:
            super().keyPressEvent(e)


class GuideWidget(QWidget):
    """Time-scrolling EPG grid. Arrows move, Enter tunes, Esc/G closes."""
    tune = Signal(int)
    closed = Signal()

    SLOT_MIN, SPAN_MIN = 30, 180
    NAME_W, ROW_H, HEADER_H, INFO_H = 200, 46, 40, 110

    def __init__(self, parent):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.close_keys = (Qt.Key_Escape, Qt.Key_G)
        self.channels, self.data = [], {}
        self.row, self.top_row = 0, 0
        self.cursor, self.win_start = int(time.time()), 0
        self.hide()

    # -- data ---------------------------------------------------------------
    def open(self, channels, idx):
        self.channels = channels
        self.row, self.top_row = max(idx, 0), 0
        now = int(time.time())
        self.cursor = now
        self.win_start = now - now % (self.SLOT_MIN * 60)
        keys = [db.epg_key(c) for c in channels]
        self.data = db.programmes(keys, now - 6 * 3600, now + 48 * 3600)
        self.show()
        self.raise_()
        self.setFocus()
        self.update()

    def _progs(self, row):
        return self.data.get(db.epg_key(self.channels[row]), [])

    def _prog_at(self, row, t):
        for p in self._progs(row):
            if p[0] <= t < p[1]:
                return p
        return None

    # -- input ----------------------------------------------------------------
    def keyPressEvent(self, e):
        k = e.key()
        if not self.channels:
            if k in self.close_keys:
                self._close()
            return
        if k in self.close_keys:
            self._close()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.hide()
            self.tune.emit(self.row)
        elif k == Qt.Key_Up:
            self.row = max(0, self.row - 1)
        elif k == Qt.Key_Down:
            self.row = min(len(self.channels) - 1, self.row + 1)
        elif k == Qt.Key_PageUp:
            self.row = max(0, self.row - 8)
        elif k == Qt.Key_PageDown:
            self.row = min(len(self.channels) - 1, self.row + 8)
        elif k == Qt.Key_Right:
            p = self._prog_at(self.row, self.cursor)
            if p:
                self.cursor = p[1]
            else:
                nxt = [x for x in self._progs(self.row) if x[0] > self.cursor]
                if nxt:
                    self.cursor = nxt[0][0]
        elif k == Qt.Key_Left:
            p = self._prog_at(self.row, self.cursor)
            if p:
                self.cursor = p[0] - 1
            else:
                prev = [x for x in self._progs(self.row) if x[1] <= self.cursor]
                if prev:
                    self.cursor = prev[-1][1] - 1
        else:
            return
        slot = self.SLOT_MIN * 60
        if self.cursor < self.win_start:
            self.win_start = self.cursor - self.cursor % slot
        elif self.cursor >= self.win_start + self.SPAN_MIN * 60:
            self.win_start = self.cursor - self.cursor % slot
        self.update()

    def _close(self):
        self.hide()
        self.closed.emit()

    # -- painting -----------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(14, 16, 24, 248))
        font = QFont(self.font())
        font.setPointSize(13)
        p.setFont(font)
        fm = QFontMetrics(font)
        gx, gw = self.NAME_W, W - self.NAME_W - 20
        span = self.SPAN_MIN * 60

        def x_of(t):
            return gx + (t - self.win_start) / span * gw

        # header with time labels
        p.setPen(QColor(190, 195, 210))
        for i in range(self.SPAN_MIN // self.SLOT_MIN):
            t = self.win_start + i * self.SLOT_MIN * 60
            x = x_of(t)
            p.drawText(QRectF(x + 6, 0, 120, self.HEADER_H), Qt.AlignVCenter | Qt.AlignLeft, fmt_time(t))
            p.drawLine(int(x), 8, int(x), self.HEADER_H)

        if not self.channels:
            p.drawText(self.rect(), Qt.AlignCenter, "No channels selected yet.")
            return

        avail_h = H - self.HEADER_H - self.INFO_H
        visible = max(1, avail_h // self.ROW_H)
        if self.row < self.top_row:
            self.top_row = self.row
        elif self.row >= self.top_row + visible:
            self.top_row = self.row - visible + 1

        now = time.time()
        sel_prog = None
        for r in range(self.top_row, min(len(self.channels), self.top_row + visible)):
            y = self.HEADER_H + (r - self.top_row) * self.ROW_H
            ch = self.channels[r]
            is_row = r == self.row
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(45, 108, 223) if is_row else QColor(32, 36, 52))
            p.drawRoundedRect(QRectF(4, y + 2, self.NAME_W - 8, self.ROW_H - 4), 6, 6)
            p.setPen(QColor(255, 255, 255))
            label = fm.elidedText(f"{ch['number']}  {ch['name']}", Qt.ElideRight, self.NAME_W - 24)
            p.drawText(QRectF(14, y, self.NAME_W - 24, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft, label)

            drawn = False
            for (s, e, title, desc) in self._progs(r):
                if e <= self.win_start or s >= self.win_start + span:
                    continue
                x1, x2 = max(x_of(s), gx), min(x_of(e), gx + gw)
                rect = QRectF(x1 + 1, y + 2, x2 - x1 - 2, self.ROW_H - 4)
                selected = is_row and s <= self.cursor < e
                if selected:
                    sel_prog = (s, e, title, desc)
                    col = QColor(45, 108, 223)
                elif s <= now < e:
                    col = QColor(58, 64, 90)
                else:
                    col = QColor(36, 40, 58)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(rect, 5, 5)
                if rect.width() > 24:
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(rect.adjusted(8, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm.elidedText(title, Qt.ElideRight, int(rect.width()) - 14))
                drawn = True
            if not drawn:
                p.setPen(QColor(120, 125, 140))
                p.drawText(QRectF(gx + 10, y, gw, self.ROW_H), Qt.AlignVCenter | Qt.AlignLeft, "No guide data")

        # "now" line
        if self.win_start <= now < self.win_start + span:
            p.setPen(QColor(235, 70, 70))
            xn = int(x_of(now))
            p.drawLine(xn, self.HEADER_H, xn, self.HEADER_H + visible * self.ROW_H)

        # info panel
        iy = H - self.INFO_H
        p.setPen(QColor(60, 66, 90))
        p.drawLine(0, iy, W, iy)
        p.setPen(QColor(255, 255, 255))
        if sel_prog:
            s, e, title, desc = sel_prog
            bold = QFont(font)
            bold.setBold(True)
            bold.setPointSize(15)
            p.setFont(bold)
            p.drawText(QRectF(20, iy + 8, W - 40, 28), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{title}   ({fmt_time(s)} - {fmt_time(e)})")
            p.setFont(font)
            p.setPen(QColor(190, 195, 210))
            p.drawText(QRectF(20, iy + 38, W - 40, self.INFO_H - 44),
                       Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, desc or "")
        p.setPen(QColor(120, 125, 140))
        p.drawText(QRectF(0, H - 22, W - 16, 20), Qt.AlignRight | Qt.AlignVCenter,
                   "Arrows: browse   Enter: watch   Esc: close")


class Spinner(QWidget):
    """Small rotating arc, only animates while visible."""

    def __init__(self, size=30):
        super().__init__()
        self.setFixedSize(size, size)
        self.angle = 0
        self._timer = QTimer(self, interval=35, timeout=self._tick)

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self.angle = (self.angle + 14) % 360
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, self.width() - 6, self.height() - 6)
        pen = QPen(QColor(255, 255, 255, 40), 3)
        p.setPen(pen)
        p.drawEllipse(r)
        pen = QPen(QColor(94, 160, 255), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, -self.angle * 16, 110 * 16)


class StatusCard(QWidget):
    """Centred message card: optional spinner, a title and a dimmer detail line."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("statusCard")
        self.setStyleSheet(
            "QWidget#statusCard{background:rgba(14,16,26,236);border:1px solid rgba(255,255,255,40);"
            "border-radius:18px;} QLabel{background:transparent;}")
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 16, 28, 16)
        row.setSpacing(16)
        self.spinner = Spinner()
        row.addWidget(self.spinner, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet("color:white;font-size:21px;font-weight:600;")
        self.detail = QLabel()
        self.detail.setStyleSheet("color:#9aa3bd;font-size:15px;")
        col.addWidget(self.title)
        col.addWidget(self.detail)
        row.addLayout(col)
        self.hide()

    def set(self, title, detail="", busy=False):
        self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.spinner.setVisible(busy)
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()


class PowerMenu(QWidget):
    """Full-screen "Power Off / Restart / Cancel" menu, D-pad friendly. Dismisses itself after 20 s."""
    chosen = Signal(str)  # "poweroff", "reboot" or "cancel"

    def __init__(self, parent):
        super().__init__(parent)
        self.cancel_keys = set()
        self.keymap = {}  # key text -> action id, same bindings as the live TV page
        self._closed = False
        self.setObjectName("powerRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(
            "QWidget#powerRoot{background:rgba(5,6,10,215);}"
            "QWidget#powerCard{background:#171a26;border:1px solid #2b3047;border-radius:22px;}"
            "QLabel{background:transparent;}"
            "QLabel#powerTitle{color:white;font-size:28px;font-weight:600;}"
            "QLabel#powerSub{color:#8b92a8;font-size:14px;}"
            "QPushButton{background:#232840;color:white;border:1px solid transparent;border-radius:12px;"
            "font-size:21px;min-height:56px;min-width:300px;}"
            "QPushButton:hover{background:#2c3354;}"
            "QPushButton:focus{background:#2d6cdf;border:1px solid #8bb4ff;}"
            "QPushButton#danger:focus{background:#d84b4b;border:1px solid #ffb4b4;}")
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        card = QWidget()
        card.setObjectName("powerCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        col = QVBoxLayout(card)
        col.setContentsMargins(40, 32, 40, 32)
        col.setSpacing(12)
        title = QLabel("Power")
        title.setObjectName("powerTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Your current channel is remembered.")
        sub.setObjectName("powerSub")
        sub.setAlignment(Qt.AlignCenter)
        col.addWidget(title)
        col.addWidget(sub)
        col.addSpacing(8)
        self.btns = []
        from PySide6.QtWidgets import QPushButton
        for text, choice, name in (("Power Off", "poweroff", "danger"), ("Restart", "reboot", ""),
                                   ("Cancel", "cancel", "")):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.setAutoDefault(False)
            b.setFocusPolicy(Qt.StrongFocus)
            b.clicked.connect(lambda _c=False, c=choice: self._pick(c))
            col.addWidget(b)
            self.btns.append(b)
        outer.addWidget(card)
        self._timer = QTimer(self, singleShot=True, timeout=lambda: self._pick("cancel"))
        self.hide()

    def open(self):
        self.show()
        self.raise_()
        self.btns[0].setFocus()
        self._timer.start(20000)

    def _pick(self, choice):
        if not self._closed:
            self._closed = True
            self._timer.stop()
            self.chosen.emit(choice)

    def keyPressEvent(self, e):
        k = e.key()
        key_text = keybinds.event_to_str(e)
        action = self.keymap.get(key_text)
        if k == Qt.Key_Escape or key_text in self.cancel_keys:
            self._pick("cancel")
        elif action == "confirm" or k in (Qt.Key_Return, Qt.Key_Enter):
            fw = self.focusWidget()
            if fw is not None and hasattr(fw, "click"):
                fw.click()
        elif action == "ch_up" or k in (Qt.Key_Up, Qt.Key_Left):
            self.focusNextPrevChild(False)
        elif action == "ch_down" or k in (Qt.Key_Down, Qt.Key_Right):
            self.focusNextPrevChild(True)
        else:
            super().keyPressEvent(e)
