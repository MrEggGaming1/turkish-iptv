"""In-app settings panel (no separate windows - works on a Pi kiosk) with channel selection,
display options and key bindings."""
import os
import sys
import time
from functools import partial

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

import db
import keybinds
import netmgr
import updater
from netpage import NetworkPage
from pages_extra import BluetoothPage, DiagnosticsPage, PhonePage, TimePage
from osk import OnScreenKeyboard
from util import DEFAULT_FOURK, on_main, run_bg
from version import __version__
from xtream import XtreamClient

STYLE = """
QWidget#settingsPanel { background: #14161f; }
QLabel { color: #e6e9f5; font-size: 14px; background: transparent; }
QLabel#h1 { font-size: 26px; font-weight: 600; color: white; }
QLabel#sub { color: #8b92a8; font-size: 13px; }
QLabel#field { color: #aab1c8; font-size: 12px; font-weight: 600; }
QLabel#card { background: #1b1e2b; border: 1px solid #2b3047; border-radius: 10px; padding: 14px 16px;
              color: #cfd5ea; font-size: 13px; }
QListWidget#nav { background: #0e1017; border: none; outline: 0; color: #9aa2bb; font-size: 15px;
                  padding-top: 18px; border-radius: 0; }
QListWidget#nav::item { padding: 11px 22px; border-left: 3px solid transparent; }
QListWidget#nav::item:selected { background: #1a2036; color: white; border-left: 3px solid #2d6cdf; }
QListWidget#nav::item:hover:!selected { background: #151925; }
QLineEdit, QComboBox, QSpinBox { background: #1b1e2b; border: 1px solid #2b3047; border-radius: 8px;
                                 padding: 9px 12px; color: white; font-size: 14px;
                                 selection-background-color: #2d6cdf; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #2d6cdf; }
QComboBox QAbstractItemView { background: #1b1e2b; color: white; border: 1px solid #2b3047;
                              selection-background-color: #2d6cdf; outline: 0; }
QListWidget { background: #1b1e2b; border: 1px solid #2b3047; border-radius: 10px; color: #e6e9f5;
              font-size: 14px; outline: 0; }
QListWidget::item { padding: 6px 10px; }
QListWidget::item:selected { background: #2d6cdf; color: white; }
QCheckBox { color: #e6e9f5; font-size: 14px; spacing: 10px; background: transparent; }
QCheckBox:focus { color: #7fb0ff; }
QCheckBox::indicator { width: 20px; height: 20px; border-radius: 6px; border: 1px solid #3a4160;
                       background: #1b1e2b; }
QCheckBox::indicator:checked { background: #2d6cdf; border: 1px solid #2d6cdf; }
QPushButton { background: #232840; color: white; border: 1px solid transparent; border-radius: 8px;
              padding: 10px 20px; font-size: 14px; }
QPushButton:hover { background: #2c3354; }
QPushButton:focus { border: 1px solid #5b95ff; }
QPushButton:disabled { background: #1a1d2b; color: #5d6480; }
QPushButton#primary { background: #2d6cdf; font-weight: 600; }
QPushButton#primary:hover { background: #3b7bf0; }
QPushButton#primary:disabled { background: #25375e; color: #7d8bb0; }
QPushButton#key { background: #1b1e2b; border: 1px solid #2b3047; font-family: Menlo, monospace;
                  min-width: 120px; padding: 9px 12px; }
QPushButton#key:hover, QPushButton#key:focus { border: 1px solid #2d6cdf; }
QPushButton#key[capturing="true"] { background: #2d6cdf; border: 1px solid #5b95ff; }
QPushButton#clear { padding: 0; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
                    background: transparent; color: #7b839e; }
QPushButton#clear:hover { color: white; background: #2c3354; }
QLabel#capture { background: #1b1e2b; border: 2px dashed #3a4160; border-radius: 12px; color: #e6e9f5;
                 font-size: 16px; padding: 16px; }
QLabel#capture[active="true"] { border: 2px solid #2d6cdf; background: #18203a; }
QWidget#screen { background: #0b0d14; border: 2px solid #2b3047; border-radius: 14px; }
QPushButton#cornerbtn { background: #1b1e2b; border: 1px solid #2b3047; font-size: 12px; padding: 8px 12px; }
QPushButton#cornerbtn:checked { background: #2d6cdf; border: 1px solid #5b95ff; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #2b3047; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QFrame#sep { background: #232840; max-height: 1px; min-height: 1px; }
"""

PAGES = ["account", "phone", "channels", "playback", "display", "time", "keys", "network", "bluetooth",
         "diagnostics", "updates"]


class KeyCaptureButton(QPushButton):
    """Click, then press a key (or a remote button). Esc cancels."""
    captured = Signal(str)
    message = Signal(str)

    def __init__(self):
        super().__init__("")
        self.setObjectName("key")
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self._capturing, self._text = False, ""
        self.allow_any = False  # the Power menu key may be any key (digits, combos, unnamed keys)
        self.clicked.connect(self._start)

    def set_key_text(self, text):
        self._text = text
        if not self._capturing:
            self.setText(text)

    def _set_capturing(self, on):
        self._capturing = on
        self.setProperty("capturing", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText("Press a key..." if on else self._text)

    def _start(self):
        self._set_capturing(True)
        self.setFocus()

    def focusOutEvent(self, e):
        if self._capturing:
            self._set_capturing(False)
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self._capturing:
            return super().keyPressEvent(e)
        k = e.key()
        if k in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_CapsLock) or \
                (k == Qt.Key_unknown and e.nativeScanCode() <= 0):
            return
        if k == Qt.Key_Escape:
            self._set_capturing(False)
            return
        if not self.allow_any and e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.message.emit("Cmd/Ctrl combinations are reserved for the menu. Pick another key.")
            return
        if not self.allow_any and Qt.Key_0 <= k <= Qt.Key_9:
            self.message.emit("Digits 0-9 are always used to type channel numbers.")
            return
        self._set_capturing(False)
        self.captured.emit(keybinds.event_to_str(e))


WIZARD_STEPS = [
    ("confirm", "OK / centre button"),
    ("back", "Back button"),
    ("ch_up", "D-pad Up  (next channel)"),
    ("ch_down", "D-pad Down  (previous channel)"),
    ("vol_up", "Volume +"),
    ("vol_down", "Volume -"),
    ("mute", "Mute"),
    ("last", "Delete / backspace button  (last channel)"),
    ("guide", "TV guide  (try the square button)"),
    ("info", "Channel info  (try the triangle button)"),
    ("list", "Channel list  (try the circle button)"),
    ("settings", "Settings / menu"),
    ("power", "Power button  (opens Power Off / Restart / Cancel)"),
]


class CaptureArea(QLabel):
    """Focusable box that reports every key / remote button pressed while it has focus."""
    key = Signal(str)

    def __init__(self):
        super().__init__("")
        self.setObjectName("capture")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(150)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setProperty("active", "false")

    def _active(self, on):
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def focusInEvent(self, e):
        self._active(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._active(False)
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_CapsLock) or \
                (e.key() == Qt.Key_unknown and e.nativeScanCode() <= 0):
            return
        e.accept()
        self.key.emit(keybinds.event_to_str(e))


class NavList(QListWidget):
    enter_page = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter):
            self.enter_page.emit()
            return
        super().keyPressEvent(e)


class CornerPicker(QWidget):
    changed = Signal(str)
    NAMES = {"tl": "Top left", "tr": "Top right", "bl": "Bottom left", "br": "Bottom right"}

    def __init__(self, value):
        super().__init__()
        self.setObjectName("screen")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(340, 200)
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 12, 12, 12)
        place = {"tl": (0, 0, Qt.AlignTop | Qt.AlignLeft), "tr": (0, 1, Qt.AlignTop | Qt.AlignRight),
                 "bl": (1, 0, Qt.AlignBottom | Qt.AlignLeft), "br": (1, 1, Qt.AlignBottom | Qt.AlignRight)}
        self.btns = {}
        for key, (r, c, al) in place.items():
            b = QPushButton(f"{self.NAMES[key]}")
            b.setObjectName("cornerbtn")
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.setFixedSize(130, 40)
            b.setFocusPolicy(Qt.StrongFocus)
            b.clicked.connect(partial(self.set_value, key))
            self.btns[key] = b
            grid.addWidget(b, r, c, al)
        mid = QLabel("Quality + settings button")
        mid.setObjectName("sub")
        mid.setAlignment(Qt.AlignCenter)
        grid.addWidget(mid, 0, 0, 2, 2, Qt.AlignCenter)
        self.value = value if value in place else "br"
        self.set_value(self.value)

    def set_value(self, key, _checked=False):
        self.value = key
        for k, b in self.btns.items():
            b.setChecked(k == key)
        self.changed.emit(key)


def _sep():
    f = QFrame()
    f.setObjectName("sep")
    return f


class ChannelsPage(QWidget):
    """Pick channels from the provider list; the order on the right is the channel numbering."""

    def __init__(self):
        super().__init__()
        self.dirty = False
        self.mine = db.my_channels(include_missing=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        cats = db.categories()
        if not cats:
            msg = QLabel("Your channel list hasn't loaded yet.\n\nSave your account details first - "
                         "the channels will download, then come back to this page.")
            msg.setObjectName("sub")
            msg.setWordWrap(True)
            lay.addWidget(msg)
            lay.addStretch()
            self.ready = False
            return
        self.ready = True

        self.cat = QComboBox()
        self.cat.addItem("All categories", None)
        for c in cats:
            self.cat.addItem(c["name"], c["id"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search channels...")
        self.avail = QListWidget()
        self.avail.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.hint = QLabel("")
        self.hint.setObjectName("sub")
        add_btn = QPushButton("Add selected  \u25B6")
        add_all = QPushButton("Add all shown  \u25B6\u25B6")

        self.mine_list = QListWidget()
        self.mine_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        up, down, rem = QPushButton("Move up"), QPushButton("Move down"), QPushButton("Remove")

        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Provider channels</b>"))
        left.addWidget(self.cat)
        left.addWidget(self.search)
        left.addWidget(self.avail, 1)
        left.addWidget(self.hint)
        lb = QHBoxLayout()
        lb.addWidget(add_btn)
        lb.addWidget(add_all)
        left.addLayout(lb)

        right = QVBoxLayout()
        right.addWidget(QLabel("<b>My channels</b>  (order = channel number)"))
        right.addWidget(self.mine_list, 1)
        rb = QHBoxLayout()
        rb.addWidget(up)
        rb.addWidget(down)
        rb.addWidget(rem)
        right.addLayout(rb)

        cols = QHBoxLayout()
        cols.setSpacing(18)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        lay.addLayout(cols, 1)

        self.cat.currentIndexChanged.connect(self.refresh_avail)
        self.search.textChanged.connect(self.refresh_avail)
        add_btn.clicked.connect(lambda: self.add(self.avail.selectedItems()))
        add_all.clicked.connect(lambda: self.add([self.avail.item(i) for i in range(self.avail.count())]))
        self.avail.itemDoubleClicked.connect(lambda it: self.add([it]))
        up.clicked.connect(lambda: self.move(-1))
        down.clicked.connect(lambda: self.move(1))
        rem.clicked.connect(self.remove)
        self.refresh_avail()
        self.refresh_mine()

    def selected_ids(self):
        return [m["stream_id"] for m in self.mine]

    def reload(self):
        if not self.ready:
            return
        self.mine = db.my_channels(include_missing=True)
        self.dirty = False
        self.refresh_avail()
        self.refresh_mine()

    def refresh_avail(self):
        rows = db.find_channels(self.cat.currentData(), self.search.text().strip())
        have = {m["stream_id"] for m in self.mine}
        self.avail.clear()
        for r in rows:
            if r["stream_id"] in have:
                continue
            it = QListWidgetItem(f"{r['name']}    \u00B7 {r.get('cat_name') or ''}")
            it.setData(Qt.UserRole, r)
            self.avail.addItem(it)
        self.hint.setText("Showing the first 1500 - refine your search." if len(rows) >= 1500 else
                          f"{self.avail.count()} channels shown")

    def refresh_mine(self, select=()):
        self.mine_list.clear()
        for i, m in enumerate(self.mine):
            flag = "   (missing from provider)" if m.get("missing") else ""
            self.mine_list.addItem(f"{i + 1:>3}   {m['name']}{flag}")
        for r in select:
            self.mine_list.item(r).setSelected(True)

    def add(self, items):
        for it in items:
            self.mine.append(it.data(Qt.UserRole))
        self.dirty = True
        self.refresh_avail()
        self.refresh_mine()
        self.mine_list.scrollToBottom()

    def remove(self):
        rows = {self.mine_list.row(i) for i in self.mine_list.selectedItems()}
        self.mine = [m for i, m in enumerate(self.mine) if i not in rows]
        self.dirty = True
        self.refresh_avail()
        self.refresh_mine()

    def move(self, delta):
        rows = sorted(self.mine_list.row(i) for i in self.mine_list.selectedItems())
        sel = set(rows)
        for r in (rows if delta < 0 else reversed(rows)):
            t = r + delta
            if 0 <= t < len(self.mine) and t not in sel:
                self.mine[r], self.mine[t] = self.mine[t], self.mine[r]
                sel.discard(r)
                sel.add(t)
        self.dirty = True
        self.refresh_mine(sel)


class _NoHooks:
    """Stand-in when the panel is created without the app hooks."""

    def stats(self):
        return {}

    def audio_devices(self):
        return []

    def set_audio_device(self, name):
        pass

    def phone_start(self):
        raise RuntimeError("Phone setup is not available.")

    def phone_stop(self):
        pass

    def phone_connected(self):
        return False


class SettingsPanel(QWidget):
    """Full-window overlay, child of the video widget. Emits finished(saved, creds_changed,
    channels_changed) and expects the owner to hide/delete it."""
    finished = Signal(bool, bool, bool)
    restart_requested = Signal()
    power_requested = Signal(str)

    def __init__(self, parent, page="account", hooks=None, first_run=False):
        super().__init__(parent)
        self.hooks = hooks or _NoHooks()
        self.first_run = first_run
        self._live = {}
        self._cur = None
        self.setObjectName("settingsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.setFocusPolicy(Qt.StrongFocus)
        self.creds_changed = False
        self._verified = None
        self._done = False
        self._osk = None
        self._upd_busy = False
        self._upd_info = None
        self.binds = keybinds.load()
        self.mode, self._wiz_i, self._wiz_busy = None, 0, False
        self._tmr = QTimer(self, singleShot=True, timeout=self._mode_timeout)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.nav = NavList()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(230)
        for t in ("Account", "Phone Setup", "Channels", "Playback", "Display", "Date & Time", "Key Bindings",
                  "Network", "Bluetooth", "Diagnostics", "Updates"):
            self.nav.addItem(t)
        root.addWidget(self.nav)

        right = QVBoxLayout()
        right.setContentsMargins(36, 30, 36, 24)
        self.stack = QStackedWidget()
        self.channels_page = ChannelsPage()
        self.stack.addWidget(self._account_page())
        self.stack.addWidget(self._phone_page())
        self.stack.addWidget(self._channels_page())
        self.stack.addWidget(self._playback_page())
        self.stack.addWidget(self._display_page())
        self.stack.addWidget(self._time_page())
        self.stack.addWidget(self._keys_page())
        self.stack.addWidget(self._network_page())
        self.stack.addWidget(self._bluetooth_page())
        self.stack.addWidget(self._diag_page())
        self.stack.addWidget(self._updates_page())
        right.addWidget(self.stack, 1)
        row = QHBoxLayout()
        hint = QLabel("Esc closes without saving")
        hint.setObjectName("sub")
        row.addWidget(hint)
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.setAutoDefault(False)
        row.addWidget(cancel)
        row.addWidget(self.save_btn)
        right.addLayout(row)
        root.addLayout(right, 1)

        self._wire_children(self)
        self.nav.item(PAGES.index("phone")).setHidden(True)  # shown only once the internet is confirmed
        run_bg(netmgr.internet, self._online)

        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.enter_page.connect(self._enter_page)
        self.go(page)
        cancel.clicked.connect(self.cancel)
        self.save_btn.clicked.connect(self._save)

    def _wire_children(self, root):
        titles = {self.server: "Server URL", self.user: "Username", self.pw: "Password",
                  self.u_repo: "GitHub repository", self.u_branch: "Branch", self.u_token: "Access token"}
        for w in root.findChildren(QPushButton):
            w.setFocusPolicy(Qt.StrongFocus)  # macOS buttons are not focusable by default
        for w in root.findChildren(QCheckBox):
            w.setFocusPolicy(Qt.StrongFocus)
        for le in root.findChildren(QLineEdit):  # remote-friendly text entry: Enter opens the on-screen keyboard
            le.setProperty("osk_title", titles.get(le, le.placeholderText() or "Enter text"))
            le.installEventFilter(self)
        for lw in root.findChildren(QListWidget):
            if lw is not self.nav:
                lw.installEventFilter(self)

    def _online(self, ok):
        """The Phone Setup option only appears once the device is online (set up networking first)."""
        if self._done:
            return
        self.nav.item(PAGES.index("phone")).setHidden(not ok)
        cur = PAGES[self.nav.currentRow()] if self.nav.currentRow() >= 0 else ""
        if ok and self.first_run and cur == "network" and not db.get("server"):
            self.go("phone")
        elif not ok and cur == "phone":
            self.go("network")

    def reload_from_db(self):
        """Changes made from the phone arrive while this panel is open - refresh the widgets."""
        self.server.setText(db.get("server", ""))
        self.user.setText(db.get("username", ""))
        self.pw.setText(db.get("password", ""))
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(db.get("stream_ext", "m3u8"))))
        self.confirm.setChecked(db.get("confirm_switch", "1") == "1")
        self.secs.setValue(int(db.get("banner_secs", "5") or 5))
        self.dec.setCurrentIndex(max(0, self.dec.findData(db.get("decode_mode", "auto"))))
        self.fourk.setChecked(db.get("fourk_mode", DEFAULT_FOURK) == "1")
        self.corner.set_value(db.get("corner_pos", "br"))
        self.minimal.setChecked(db.get("minimalistic", "0") == "1")
        self.time_page.reload()
        if self.channels_page.ready:
            self.channels_page.reload()
        elif db.categories():  # the channel list finished loading while we were open
            new = ChannelsPage()
            self._channels_lay.replaceWidget(self.channels_page, new)
            self.channels_page.deleteLater()
            self.channels_page = new
            self._wire_children(new)

    def _shutdown_pages(self):
        for page in self._live.values():
            try:
                page.shutdown()
            except Exception:
                pass

    def showEvent(self, e):
        super().showEvent(e)
        self.nav.setFocus()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._osk is not None:
            self._osk.setGeometry(self.rect())

    def _on_nav(self, row):
        self.stack.setCurrentIndex(row)
        name = PAGES[row] if 0 <= row < len(PAGES) else ""
        if self._cur and self._cur != name and self._cur in self._live:
            hide = getattr(self._live[self._cur], "on_hide", None)
            if hide:
                hide()
        self._cur = name
        if name in self._live:
            self._live[name].on_show()

    # ---------------------------------------------------------------- on-screen keyboard
    def ask_text(self, title, text="", password=False, callback=None):
        if self._osk is not None:
            return
        prev = self.focusWidget()
        osk = OnScreenKeyboard(self, title, text, password)
        self._osk = osk

        def close():
            self._osk = None
            osk.hide()
            osk.deleteLater()
            (prev or self.nav).setFocus()

        osk.done.connect(lambda t: (close(), callback(t) if callback else None))
        osk.cancelled.connect(close)
        osk.setGeometry(self.rect())
        osk.show()
        osk.raise_()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if isinstance(obj, QLineEdit) and self._osk is None:
            opens = (t == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter)) or \
                    (t == QEvent.MouseButtonRelease and os.environ.get("XTREAMTV_KIOSK") == "1")
            if opens:
                self.ask_text(obj.property("osk_title") or "Enter text", obj.text(),
                              obj.echoMode() == QLineEdit.Password, obj.setText)
                return True
        elif isinstance(obj, QListWidget) and t == QEvent.KeyPress:  # D-pad: step out of lists at the edges
            k = ev.key()
            if k == Qt.Key_Left:
                self.nav.setFocus()
                return True
            if k == Qt.Key_Up and obj.currentRow() <= 0:
                self.focusNextPrevChild(False)
                return True
            if k == Qt.Key_Down and obj.currentRow() >= obj.count() - 1:
                self.focusNextPrevChild(True)
                return True
        return super().eventFilter(obj, ev)

    def _network_page(self):
        w, lay = self._page("Network", "Connect with Ethernet (automatic) or pick a Wi-Fi network. "
                                       "Passwords are typed with the on-screen keyboard.")
        self.net_page = NetworkPage(self.ask_text)
        self.net_page.on_online = self._online
        self._live["network"] = self.net_page
        lay.addWidget(self.net_page, 1)
        return w

    def _phone_page(self):
        w, lay = self._page("Phone Setup", "Set up Xtream TV from your phone - type your login and pick channels "
                                           "with a real keyboard. Changes appear here as you make them.")
        self.phone_page = PhonePage(self.hooks, lambda: self.go("account"), lambda: self._save())
        self._live["phone"] = self.phone_page
        lay.addWidget(self.phone_page, 1)
        return w

    def _time_page(self):
        w, lay = self._page("Date & Time", "Choose your time zone so the TV guide shows the right times. "
                                           "Applied when you press Save.")
        self.time_page = TimePage()
        self._live["time"] = self.time_page
        lay.addWidget(self.time_page, 1)
        return w

    def _bluetooth_page(self):
        w, lay = self._page("Bluetooth", "Play the sound through Bluetooth speakers or headphones. "
                                         "You can turn Bluetooth off here at any time.")
        self.bt_page = BluetoothPage(self.hooks)
        self._live["bluetooth"] = self.bt_page
        lay.addWidget(self.bt_page, 1)
        return w

    def _diag_page(self):
        w, lay = self._page("Diagnostics", "Temperature, power, decoder, network and protection status - "
                                           "handy when something looks wrong.")
        self.diag_page = DiagnosticsPage(self.hooks)
        self._live["diagnostics"] = self.diag_page
        lay.addWidget(self.diag_page, 1)
        return w

    # ---------------------------------------------------------------- navigation
    def go(self, page):
        self.nav.setCurrentRow(PAGES.index(page) if page in PAGES else 0)
        self.nav.setFocus()

    def _enter_page(self):
        for w in self.stack.currentWidget().findChildren(QWidget):
            if w.focusPolicy() != Qt.NoFocus and w.isEnabled() and not isinstance(w, (QScrollArea, QFrame)):
                w.setFocus()
                return

    def keyPressEvent(self, e):
        k, fw = e.key(), self.focusWidget()
        if k == Qt.Key_Escape:
            if not self._upd_busy:
                self.cancel()
            return
        if isinstance(fw, (QPushButton, QCheckBox)):  # remote-friendly focus movement
            if k == Qt.Key_Down:
                self.focusNextPrevChild(True)
                return
            if k == Qt.Key_Up:
                self.focusNextPrevChild(False)
                return
            if k == Qt.Key_Left and not isinstance(fw, KeyCaptureButton):
                self.nav.setFocus()
                return
            if k in (Qt.Key_Return, Qt.Key_Enter):
                fw.click() if isinstance(fw, QPushButton) else fw.toggle()
                return
        super().keyPressEvent(e)

    def cancel(self):
        if not self._done and not self._upd_busy:
            self._done = True
            self._shutdown_pages()
            self.finished.emit(False, False, False)

    # ---------------------------------------------------------------- page builders
    def _page(self, title, subtitle):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        h = QLabel(title)
        h.setObjectName("h1")
        s = QLabel(subtitle)
        s.setObjectName("sub")
        s.setWordWrap(True)
        lay.addWidget(h)
        lay.addWidget(s)
        lay.addSpacing(14)
        return w, lay

    def _field(self, lay, label, widget):
        l = QLabel(label.upper())
        l.setObjectName("field")
        lay.addWidget(l)
        lay.addWidget(widget)
        lay.addSpacing(8)

    def _account_page(self):
        w, lay = self._page("Account", "Your Xtream Codes login. Changing these reloads your channels.")
        self.server = QLineEdit(db.get("server", ""))
        self.server.setPlaceholderText("http://provider.example.com:8080")
        self.user = QLineEdit(db.get("username", ""))
        self.pw = QLineEdit(db.get("password", ""))
        self.pw.setEchoMode(QLineEdit.Password)
        show = QCheckBox("Show password")
        show.toggled.connect(lambda on: self.pw.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self._field(lay, "Server URL", self.server)
        self._field(lay, "Username", self.user)
        self._field(lay, "Password", self.pw)
        lay.addWidget(show)
        lay.addSpacing(12)
        row = QHBoxLayout()
        self.test_btn = QPushButton("Test connection")
        self.test_btn.setAutoDefault(False)
        self.test_btn.clicked.connect(lambda: self._test())
        self.status = QLabel("")
        self.status.setWordWrap(True)
        row.addWidget(self.test_btn)
        row.addWidget(self.status, 1)
        lay.addLayout(row)
        self.details = QLabel("")
        self.details.setObjectName("card")
        self.details.hide()
        lay.addSpacing(6)
        lay.addWidget(self.details)
        lay.addStretch()
        return w

    def _channels_page(self):
        w, lay = self._page("Channels", "Choose which channels appear in your TV list and set their order. "
                                        "The position in My channels is the channel number.")
        lay.addWidget(self.channels_page, 1)
        self._channels_lay = lay
        return w

    def _playback_page(self):
        w, lay = self._page("Playback", "How channels play and how browsing behaves.")
        self.fmt = QComboBox()
        self.fmt.addItem("HLS (.m3u8) - recommended", "m3u8")
        self.fmt.addItem("MPEG-TS (.ts)", "ts")
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(db.get("stream_ext", "m3u8"))))
        self._field(lay, "Stream format", self.fmt)
        note = QLabel("If a channel keeps failing, try the other format. The player also swaps formats "
                      "automatically when reconnecting.")
        note.setObjectName("sub")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addSpacing(10)
        lay.addWidget(_sep())
        lay.addSpacing(10)
        self.confirm = QCheckBox("Browse channels first, switch only when I press the confirm key")
        self.confirm.setChecked(db.get("confirm_switch", "1") == "1")
        lay.addWidget(self.confirm)
        sub = QLabel("Up/Down previews channels (starting from the current one) while it keeps playing.")
        sub.setObjectName("sub")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addSpacing(14)
        self.secs = QSpinBox()
        self.secs.setRange(2, 30)
        self.secs.setSuffix(" seconds")
        self.secs.setValue(int(db.get("banner_secs", "5") or 5))
        self.secs.setFixedWidth(160)
        self._field(lay, "Channel info display time", self.secs)
        lay.addWidget(_sep())
        lay.addSpacing(10)
        self.dec = QComboBox()
        self.dec.addItem("Automatic (recommended)", "auto")
        self.dec.addItem("Hardware decoding preferred", "hardware")
        self.dec.addItem("Software decoding only", "software")
        self.dec.setCurrentIndex(max(0, self.dec.findData(db.get("decode_mode", "auto"))))
        self._field(lay, "Video decoder", self.dec)
        self.fourk = QCheckBox("4K / high-bitrate mode (bigger buffer, lighter software decoding)")
        self.fourk.setChecked(db.get("fourk_mode", DEFAULT_FOURK) == "1")
        lay.addWidget(self.fourk)
        info = QLabel("Raspberry Pi 5: HEVC / H.265 streams are decoded by the Pi's hardware (up to 4K 60 fps). "
                      "The Pi 5 has no H.264 hardware decoder, so H.264 uses the CPU - 4K H.264 works up to "
                      "about 30 fps. For 4K channels pick the HEVC / H.265 version if your provider offers one, "
                      "and use Ethernet (4K needs roughly 25-40 Mbps). The quality badge shows HW or SW.")
        info.setObjectName("sub")
        info.setWordWrap(True)
        lay.addSpacing(6)
        lay.addWidget(info)
        lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(w)
        return scroll

    def _display_page(self):
        w, lay = self._page("Display", "Where the stream quality and the settings / mute buttons sit on screen.")
        self.corner = CornerPicker(db.get("corner_pos", "br"))
        lay.addWidget(self.corner)
        lay.addSpacing(14)
        self.minimal = QCheckBox("Minimalistic")
        self.minimal.setChecked(db.get("minimalistic", "0") == "1")
        lay.addWidget(self.minimal)
        mnote = QLabel("Shows only the quality - 4K, FHD, HD or SD - in the corner, "
                       "instead of resolution, codec, frame rate and decoder.")
        mnote.setObjectName("sub")
        mnote.setWordWrap(True)
        lay.addWidget(mnote)
        note = QLabel("They appear when the channel info shows or when you move the mouse, then fade away.")
        note.setObjectName("sub")
        note.setWordWrap(True)
        lay.addSpacing(8)
        lay.addWidget(note)
        lay.addStretch()
        return w

    def _keys_page(self):
        w, lay = self._page(
            "Key Bindings",
            "Click a slot, then press a key - or a button on your remote. Remotes that act as a keyboard "
            "(USB dongle, Bluetooth, or HDMI-CEC on the Pi) work automatically; use the second column for "
            "your remote's buttons. Digits 0-9 always type channel numbers.")
        rrow = QHBoxLayout()
        wiz = QPushButton("Set up remote")
        wiz.setObjectName("primary")
        tst = QPushButton("Test buttons")
        wiz.setAutoDefault(False)
        tst.setAutoDefault(False)
        wiz.clicked.connect(self._start_wizard)
        tst.clicked.connect(self._start_test)
        rrow.addWidget(wiz)
        rrow.addWidget(tst)
        rrow.addStretch()
        lay.addLayout(rrow)
        self.capture = CaptureArea()
        self.capture.key.connect(self._on_capture_key)
        self.capture.hide()
        lay.addWidget(self.capture)
        self.wiz_bar = QWidget()
        wb = QHBoxLayout(self.wiz_bar)
        wb.setContentsMargins(0, 0, 0, 0)
        skip, stop = QPushButton("Skip this one"), QPushButton("Stop")
        skip.setAutoDefault(False)
        stop.setAutoDefault(False)
        skip.clicked.connect(self._wiz_skip)
        stop.clicked.connect(lambda: self._stop_mode("Stopped. Anything captured so far is kept - press Save."))
        wb.addWidget(skip)
        wb.addWidget(stop)
        wb.addStretch()
        self.wiz_bar.hide()
        lay.addWidget(self.wiz_bar)
        lay.addSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        for col, text in ((1, "KEY"), (3, "ALTERNATE / REMOTE")):
            h = QLabel(text)
            h.setObjectName("field")
            grid.addWidget(h, 0, col, 1, 2)
        self.key_btns = {}
        for r, (action, label, _k1, _k2) in enumerate(keybinds.ACTIONS, start=1):
            grid.addWidget(QLabel(label), r, 0)
            for slot, col in ((0, 1), (1, 3)):
                b = KeyCaptureButton()
                b.allow_any = action == "power"
                b.captured.connect(lambda k, a=action, s=slot: self._rebind(a, s, k))
                b.message.connect(lambda m: self.keynote.setText(m))
                x = QPushButton("\u2715")
                x.setObjectName("clear")
                x.setToolTip("Clear")
                x.setAutoDefault(False)
                x.clicked.connect(lambda _c=False, a=action, s=slot: self._clear(a, s))
                self.key_btns[(action, slot)] = b
                grid.addWidget(b, r, col)
                grid.addWidget(x, r, col + 1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        self.keynote = QLabel("")
        self.keynote.setObjectName("sub")
        self.keynote.setWordWrap(True)
        reset = QPushButton("Reset to defaults")
        reset.setAutoDefault(False)
        reset.clicked.connect(self._reset_keys)
        row.addWidget(self.keynote, 1)
        row.addWidget(reset)
        lay.addLayout(row)
        self._refresh_keys()
        return w

    # ---------------------------------------------------------------- key bindings
    def _refresh_keys(self):
        for (a, s), b in self.key_btns.items():
            b.set_key_text(keybinds.pretty(self.binds[a][s]) if self.binds[a][s] else "-")

    def _rebind(self, action, slot, key):
        if key in self.binds[action]:
            self.keynote.setText(f"\"{keybinds.pretty(key)}\" is already set for {keybinds.LABELS[action]}.")
            return
        note = ""
        for a, keys in self.binds.items():
            for s in (0, 1):
                if keys[s] == key and (a, s) != (action, slot):
                    keys[s] = ""
                    note = f"\"{keybinds.pretty(key)}\" was used by {keybinds.LABELS[a]} - that slot is now empty."
        self.binds[action][slot] = key
        self.keynote.setText(note)
        self._refresh_keys()

    def _clear(self, action, slot):
        self.binds[action][slot] = ""
        self._refresh_keys()

    def _reset_keys(self):
        self.binds = keybinds.defaults()
        self.keynote.setText("Defaults restored (applied when you press Save).")
        self._refresh_keys()

    # ---------------------------------------------------------------- updates
    def _updates_page(self):
        w, lay = self._page("Updates", "Update this app from your GitHub repository. Your login, channels and "
                                       "settings are kept, and the previous version is backed up so you can roll back.")
        self.ver = QLabel()
        self.ver.setObjectName("card")
        self._refresh_version_card()
        lay.addWidget(self.ver)
        lay.addSpacing(10)
        self.u_repo = QLineEdit(db.get("update_repo", ""))
        self.u_repo.setPlaceholderText("owner/repository   or   https://github.com/owner/repository")
        self.u_mode = QComboBox()
        self.u_mode.addItem("Latest release (recommended)", "release")
        self.u_mode.addItem("Latest commit on a branch", "branch")
        self.u_mode.setCurrentIndex(max(0, self.u_mode.findData(db.get("update_mode", "release"))))
        self.u_branch = QLineEdit(db.get("update_branch", "main"))
        self.u_token = QLineEdit(db.get("update_token", ""))
        self.u_token.setEchoMode(QLineEdit.Password)
        self.u_token.setPlaceholderText("Only needed for private repositories (read-only token)")
        self._field(lay, "GitHub repository", self.u_repo)
        self._field(lay, "Update source", self.u_mode)
        self._field(lay, "Branch (used for the branch source)", self.u_branch)
        self._field(lay, "Access token (optional)", self.u_token)
        self.u_mode.currentIndexChanged.connect(lambda: self.u_branch.setEnabled(self.u_mode.currentData() == "branch"))
        self.u_branch.setEnabled(self.u_mode.currentData() == "branch")
        self.u_auto = QCheckBox("Check for updates when the app starts")
        self.u_auto.setChecked(db.get("update_check_start", "1") == "1")
        lay.addWidget(self.u_auto)
        lay.addSpacing(10)
        row = QHBoxLayout()
        self.u_check = QPushButton("Check for updates")
        self.u_apply = QPushButton("Update now")
        self.u_apply.setObjectName("primary")
        self.u_apply.setEnabled(False)
        self.u_back = QPushButton("Roll back")
        self.u_back.setEnabled(updater.has_backup())
        for b in (self.u_check, self.u_apply, self.u_back):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.u_status = QLabel("")
        self.u_status.setWordWrap(True)
        lay.addWidget(self.u_status)
        self.u_notes = QLabel("")
        self.u_notes.setObjectName("card")
        self.u_notes.setWordWrap(True)
        self.u_notes.hide()
        lay.addWidget(self.u_notes)
        lay.addSpacing(8)
        lay.addWidget(_sep())
        lay.addSpacing(8)
        sysl = QLabel("SYSTEM")
        sysl.setObjectName("field")
        lay.addWidget(sysl)
        prow = QHBoxLayout()
        restart = QPushButton("Restart app")
        restart.setAutoDefault(False)
        restart.clicked.connect(self.restart_requested.emit)
        prow.addWidget(restart)
        if sys.platform.startswith("linux"):
            for text, action in (("Reboot", "reboot"), ("Power off", "poweroff")):
                b = QPushButton(text)
                b.setAutoDefault(False)
                b.clicked.connect(lambda _c=False, a=action: self.power_requested.emit(a))
                prow.addWidget(b)
        prow.addStretch()
        lay.addLayout(prow)
        lay.addStretch()
        self.u_check.clicked.connect(self._upd_check)
        self.u_apply.clicked.connect(self._upd_apply)
        self.u_back.clicked.connect(self._upd_rollback)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(w)
        return scroll

    def _refresh_version_card(self):
        label = db.get("installed_label", "")
        self.ver.setText(f"Version {__version__}\nInstalled from: "
                         f"{label or 'local files (not updated from GitHub yet)'}")

    def _upd_cfg(self):
        cfg = {"repo": self.u_repo.text().strip(), "mode": self.u_mode.currentData(),
               "branch": self.u_branch.text().strip() or "main", "token": self.u_token.text().strip()}
        db.put("update_repo", cfg["repo"])
        db.put("update_mode", cfg["mode"])
        db.put("update_branch", cfg["branch"])
        db.put("update_token", cfg["token"])
        db.put("update_check_start", "1" if self.u_auto.isChecked() else "0")
        return cfg

    def _upd_set(self, text, kind="info"):
        color = {"info": "#8b92a8", "good": "#4cd08a", "bad": "#ff6b6b"}[kind]
        self.u_status.setText(f'<span style="color:{color}">{text}</span>')

    def _upd_busy_set(self, busy):
        self._upd_busy = busy
        self.save_btn.setEnabled(not busy)
        self.u_check.setEnabled(not busy)
        self.u_apply.setEnabled(not busy and self._upd_info is not None
                                and db.get("installed_ref", "") != self._upd_info["ref"])
        self.u_back.setEnabled(not busy and updater.has_backup())

    def _upd_check(self):
        cfg = self._upd_cfg()
        self._upd_info = None
        self.u_notes.hide()
        self._upd_busy_set(True)
        self._upd_set("Checking GitHub...")

        def ok(info):
            if self._done:
                return
            self._upd_info = info
            self._upd_busy_set(False)
            if db.get("installed_ref", "") == info["ref"]:
                self._upd_set(f"You're up to date ({info['label']}).", "good")
            else:
                self._upd_set(f"Update available: {info['label']}", "good")
                notes = (info.get("notes") or "").strip()
                if notes:
                    self.u_notes.setText(notes[:700] + ("..." if len(notes) > 700 else ""))
                    self.u_notes.show()

        def fail(err):
            if self._done:
                return
            self._upd_busy_set(False)
            self._upd_set(err, "bad")

        run_bg(lambda: updater.check(cfg), ok, fail)

    def _upd_apply(self):
        if not self._upd_info:
            return
        cfg, info = self._upd_cfg(), self._upd_info
        self._upd_busy_set(True)
        self._upd_set("Starting update...")

        def progress(text):
            on_main(lambda: self._upd_set(text))

        def ok(_r):
            self._upd_busy_set(False)
            self._refresh_version_card()
            self._upd_set("Updated \u2713  Restarting the app...", "good")
            QTimer.singleShot(1800, self.restart_requested.emit)

        def fail(err):
            self._upd_busy_set(False)
            self._upd_set(f"Update failed - nothing was changed. {err}", "bad")

        run_bg(lambda: updater.apply(info, cfg, progress), ok, fail)

    def _upd_rollback(self):
        self._upd_busy_set(True)
        self._upd_set("Rolling back...")

        def ok(_r):
            self._upd_busy_set(False)
            self._refresh_version_card()
            self._upd_set("Rolled back \u2713  Restarting the app...", "good")
            QTimer.singleShot(1800, self.restart_requested.emit)

        def fail(err):
            self._upd_busy_set(False)
            self._upd_set(err, "bad")

        run_bg(updater.rollback, ok, fail)

    # ---------------------------------------------------------------- remote setup / tester
    def _start_wizard(self):
        self.mode, self._wiz_i, self._wiz_busy = "wizard", 0, False
        self.capture.show()
        self.wiz_bar.show()
        self.capture.setFocus()
        self._wiz_show()

    def _start_test(self):
        self.mode = "test"
        self.wiz_bar.hide()
        self.capture.show()
        self.capture.setText("Press any button on your remote or keyboard.\n\nI'll show what it does.\n\n"
                             "(ends after 10 seconds without a press)")
        self.capture.setFocus()
        self._tmr.start(10000)

    def _stop_mode(self, msg=""):
        self.mode = None
        self._tmr.stop()
        self.wiz_bar.hide()
        if msg:
            self.capture.setText(msg)
        else:
            self.capture.hide()
        self.nav.setFocus()

    def _mode_timeout(self):
        if self.mode == "wizard":
            self._wiz_skip()
        elif self.mode == "test":
            self._stop_mode("Test finished.")

    def _wiz_show(self):
        self._wiz_busy = False
        if self._wiz_i >= len(WIZARD_STEPS):
            self._refresh_keys()
            self._stop_mode("Remote set up \u2713\n\nReview the Alternate column, then press Save.")
            return
        _action, label = WIZARD_STEPS[self._wiz_i]
        self.capture.setText(f"Step {self._wiz_i + 1} of {len(WIZARD_STEPS)}\n\nPress the remote button for\n"
                             f"{label}\n\n(skips by itself after 12 seconds)")
        self._tmr.start(12000)

    def _wiz_skip(self):
        if self.mode == "wizard":
            self._wiz_i += 1
            self._wiz_show()

    def _on_capture_key(self, key):
        if self._wiz_busy or self.mode is None:
            return
        if self.mode == "wizard" and len(key) == 1 and key.isdigit() and WIZARD_STEPS[self._wiz_i][0] != "power":
            self.capture.setText("Digits 0-9 are always used for channel numbers - try another button.")
            return
        if self.mode == "test":
            action = keybinds.build_keymap(self.binds).get(key)
            self.capture.setText(f"{keybinds.pretty(key)}\n\n\u2192  "
                                 f"{keybinds.LABELS.get(action, 'not assigned to anything')}")
            self._tmr.start(10000)
            return
        action = WIZARD_STEPS[self._wiz_i][0]
        self._wiz_busy = True
        self._tmr.stop()
        already = key in self.binds[action]
        self._rebind(action, 1, key)
        self.capture.setText(f"Got  {keybinds.pretty(key)}  \u2713" + ("\n\n(already set)" if already else ""))
        self._wiz_i += 1
        QTimer.singleShot(700, self._wiz_show)

    # ---------------------------------------------------------------- account test / save
    def _set_status(self, text, kind="info"):
        color = {"info": "#8b92a8", "good": "#4cd08a", "bad": "#ff6b6b"}[kind]
        self.status.setText(f'<span style="color:{color}">{text}</span>')

    def _client(self):
        return XtreamClient(self.server.text(), self.user.text(), self.pw.text())

    def _filled(self):
        return all(x.text().strip() for x in (self.server, self.user, self.pw))

    def _test(self, then=None):
        if not self._filled():
            self.go("account")
            self._set_status("Enter server, username and password.", "bad")
            return
        c = self._client()
        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self._set_status("Connecting...", "info")

        def ok(data):
            if self._done:
                return
            self.test_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self._verified = (c.server, c.username, c.password)
            ui = data.get("user_info", {})
            exp = ui.get("exp_date")
            try:
                exp_txt = time.strftime("%d %b %Y", time.localtime(int(exp))) if exp else "Never"
            except (TypeError, ValueError):
                exp_txt = "Unknown"
            self._set_status("Connected", "good")
            self.details.setText(
                f"Status: {ui.get('status', '?')}     Expires: {exp_txt}\n"
                f"Connections: {ui.get('active_cons', '?')} / {ui.get('max_connections', '?')} in use")
            self.details.show()
            if then:
                then()

        def fail(err):
            if self._done:
                return
            self.test_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.go("account")
            self._set_status(f"Could not connect: {err}", "bad")

        run_bg(c.account_info, ok, fail)

    def _save(self):
        if not self._filled():
            self.go("account")
            self._set_status("Enter server, username and password.", "bad")
            return
        c = self._client()
        cur = (c.server, c.username, c.password)
        stored = (db.get("server", ""), db.get("username", ""), db.get("password", ""))
        self.creds_changed = cur != stored
        if self.creds_changed and self._verified != cur:
            self._test(then=self._finish)
        else:
            self._finish()

    def _finish(self):
        if self._done:
            return
        c = self._client()
        db.put("server", c.server)
        db.put("username", c.username)
        db.put("password", c.password)
        db.put("stream_ext", self.fmt.currentData())
        db.put("confirm_switch", "1" if self.confirm.isChecked() else "0")
        db.put("banner_secs", self.secs.value())
        db.put("decode_mode", self.dec.currentData())
        db.put("fourk_mode", "1" if self.fourk.isChecked() else "0")
        db.put("corner_pos", self.corner.value)
        db.put("minimalistic", "1" if self.minimal.isChecked() else "0")
        db.put("timezone", self.time_page.value())
        db.put("clock24", "1" if self.time_page.clock24.isChecked() else "0")
        self._upd_cfg()
        keybinds.save(self.binds)
        ch_changed = False
        if self.channels_page.ready and self.channels_page.dirty:
            db.set_my_channels(self.channels_page.selected_ids())
            ch_changed = True
        self._done = True
        self._shutdown_pages()
        self.finished.emit(True, self.creds_changed, ch_changed)
