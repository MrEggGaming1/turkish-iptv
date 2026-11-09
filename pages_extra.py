"""Extra settings pages: Date & Time, Phone Setup (QR code), Bluetooth audio, Diagnostics."""
import zoneinfo
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget)

import btmgr
import db
import diagnostics
from util import run_bg

FALLBACK_ZONES = ["Australia/Melbourne", "Australia/Sydney", "Australia/Brisbane", "Australia/Adelaide",
                  "Australia/Perth", "Pacific/Auckland", "Europe/London", "Europe/Paris", "America/New_York",
                  "America/Chicago", "America/Los_Angeles", "Asia/Tokyo", "Asia/Singapore", "UTC"]


# --------------------------------------------------------------------------- Date & Time
class TimePage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search time zones (e.g. Melbourne)")
        self.list = QListWidget()
        self.list.setMinimumHeight(190)
        self.now = QLabel("")
        self.now.setObjectName("card")
        self.clock24 = QCheckBox("24-hour clock")
        zones = sorted(zoneinfo.available_timezones()) or FALLBACK_ZONES
        self.zones = [("Automatic - use the system setting", "")] + [(z.replace("_", " "), z) for z in zones]
        self._value = db.get("timezone", "")
        lay.addWidget(self.filter)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.now)
        lay.addWidget(self.clock24)
        self._timer = QTimer(self, interval=1000, timeout=self._preview)
        self.filter.textChanged.connect(self._fill)
        self.list.currentRowChanged.connect(self._picked)
        self.clock24.toggled.connect(lambda _on: self._preview())
        self.reload()

    def value(self):
        return self._value

    def reload(self):
        self._value = db.get("timezone", "")
        self.clock24.setChecked(db.get("clock24", "1") == "1")
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.blockSignals(False)
        self._fill()
        self._preview()

    def _fill(self, _text=None):
        text = self.filter.text().strip().lower().replace("_", " ")
        self.list.blockSignals(True)
        self.list.clear()
        row = -1
        for label, val in self.zones:
            if text and text not in label.lower():
                continue
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, val)
            self.list.addItem(it)
            if val == self._value:
                row = self.list.count() - 1
        self.list.blockSignals(False)
        if row >= 0:
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))
        self._preview()

    def _picked(self, row):
        it = self.list.item(row) if row >= 0 else None
        if it is not None:
            self._value = it.data(Qt.UserRole) or ""
            self._preview()

    def _preview(self):
        try:
            if self._value:
                dt, name = datetime.now(zoneinfo.ZoneInfo(self._value)), self._value
            else:
                dt, name = datetime.now().astimezone(), "system time zone"
            fmt = "%H:%M:%S" if self.clock24.isChecked() else "%I:%M:%S %p"
            self.now.setText(f"{name}\n{dt.strftime('%A %d %B %Y')}    {dt.strftime(fmt).lstrip('0')}")
        except Exception:
            self.now.setText("Unknown time zone")

    def on_show(self):
        self._timer.start()

    def on_hide(self):
        self._timer.stop()

    def shutdown(self):
        self._timer.stop()


# --------------------------------------------------------------------------- Phone setup
class QrWidget(QWidget):
    def __init__(self, size=290):
        super().__init__()
        self.setFixedSize(size, size)
        self.matrix = None

    def set_text(self, text):
        try:
            import qrcode
            qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(text)
            qr.make(fit=True)
            self.matrix = qr.get_matrix()
        except Exception:
            self.matrix = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(self.rect(), 14, 14)
        if not self.matrix:
            p.setPen(QColor("#444"))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, "QR code unavailable.\nType the address instead.")
            return
        n = len(self.matrix)
        cell = max(2, (self.width() - 16) // (n + 4))  # 2-module quiet zone each side
        off = (self.width() - cell * n) // 2
        p.setBrush(QColor("black"))
        for y, row in enumerate(self.matrix):
            for x, on in enumerate(row):
                if on:
                    p.drawRect(off + x * cell, off + y * cell, cell, cell)


class PhonePage(QWidget):
    """Runs the phone-setup web server only while this page is open."""

    def __init__(self, hooks, go_account, done):
        super().__init__()
        self.hooks, self._alive = hooks, True
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(28)
        self.qr = QrWidget()
        row.addWidget(self.qr, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(10)
        steps = QLabel("1.  Connect your phone to the same Wi-Fi / network as this device.\n"
                       "2.  Point your phone's camera at the code (or type the address below).\n"
                       "3.  Enter your Xtream login and choose channels on your phone.")
        steps.setWordWrap(True)
        self.url = QLabel("")
        self.url.setObjectName("card")
        self.url.setWordWrap(True)
        self.url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel("Starting...")
        self.status.setObjectName("sub")
        self.status.setWordWrap(True)
        col.addWidget(steps)
        col.addWidget(self.url)
        col.addWidget(self.status)
        btns = QHBoxLayout()
        self.b_new = QPushButton("New code")
        self.b_dev = QPushButton("Set up on this device instead")
        self.b_done = QPushButton("Done - start watching")
        self.b_done.setObjectName("primary")
        for b in (self.b_new, self.b_dev, self.b_done):
            b.setAutoDefault(False)
            btns.addWidget(b)
        btns.addStretch()
        col.addLayout(btns)
        note = QLabel("The page works only while this screen is open, needs the secret code from the QR, "
                      "and switches itself off after 20 minutes.")
        note.setObjectName("sub")
        note.setWordWrap(True)
        col.addWidget(note)
        col.addStretch()
        row.addLayout(col, 1)
        self.b_new.clicked.connect(self._restart)
        self.b_dev.clicked.connect(go_account)
        self.b_done.clicked.connect(done)
        self._timer = QTimer(self, interval=1500, timeout=self._tick)

    def _start(self):
        try:
            info = self.hooks.phone_start()
        except Exception as e:  # noqa
            self.url.setText("Could not start the phone page.")
            self.status.setText(str(e))
            return
        self.qr.set_text(info["url"])
        self.url.setText(f"Address (type it if the code won't scan):\n{info['url']}")
        self.status.setText("Waiting for your phone...")

    def _restart(self):
        self.hooks.phone_stop()
        self._start()

    def _tick(self):
        if not self._alive:
            return
        if self.hooks.phone_connected():
            self.status.setText('<span style="color:#4cd08a">Phone connected \u2713</span> - changes show up here '
                                "as you make them.")
        else:
            self.status.setText("Waiting for your phone...")

    def on_show(self):
        self._start()
        self._timer.start()

    def on_hide(self):
        self._timer.stop()
        self.hooks.phone_stop()

    def shutdown(self):
        self._alive = False
        self.on_hide()


# --------------------------------------------------------------------------- Bluetooth audio
class BluetoothPage(QWidget):
    def __init__(self, hooks):
        super().__init__()
        self.hooks, self._alive, self._busy, self._on = hooks, True, False, True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.ok = btmgr.available()
        if not self.ok:
            msg = QLabel("Bluetooth audio is managed by the Raspberry Pi's Bluetooth service.\n\n"
                         "This computer handles Bluetooth itself, so there is nothing to set up here.")
            msg.setObjectName("sub")
            msg.setWordWrap(True)
            lay.addWidget(msg)
            lay.addStretch()
            return
        self.card = QLabel("Checking Bluetooth...")
        self.card.setObjectName("card")
        self.card.setTextFormat(Qt.RichText)
        lay.addWidget(self.card)
        row = QHBoxLayout()
        self.b_power = QPushButton("Bluetooth: ...")
        self.b_scan = QPushButton("Scan for devices")
        self.b_connect = QPushButton("Connect")
        self.b_disc = QPushButton("Disconnect")
        self.b_forget = QPushButton("Forget")
        self.buttons = (self.b_power, self.b_scan, self.b_connect, self.b_disc, self.b_forget)
        for b in self.buttons:
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setMinimumHeight(130)
        lay.addWidget(self.list, 1)
        arow = QHBoxLayout()
        arow.addWidget(QLabel("Audio output"))
        self.audio = QComboBox()
        self.audio.setMinimumWidth(320)
        self.b_apply = QPushButton("Use this output")
        self.b_apply.setAutoDefault(False)
        arow.addWidget(self.audio, 1)
        arow.addWidget(self.b_apply)
        lay.addLayout(arow)
        self.msg = QLabel("Put your speaker or headphones in pairing mode, then press Scan.")
        self.msg.setObjectName("sub")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        warn = QLabel("Turning Bluetooth off stops it being used for audio. If your remote connects over "
                      "Bluetooth (rather than its USB dongle), it will disconnect too.")
        warn.setObjectName("sub")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        self.b_power.clicked.connect(self._toggle)
        self.b_scan.clicked.connect(self._scan)
        self.b_connect.clicked.connect(self._connect)
        self.b_disc.clicked.connect(self._disconnect)
        self.b_forget.clicked.connect(self._forget)
        self.b_apply.clicked.connect(self._apply_audio)
        self.list.itemActivated.connect(lambda _i: self._connect())
        self.list.itemDoubleClicked.connect(lambda _i: self._connect())

    # ---- plumbing
    def shutdown(self):
        self._alive = False

    def on_hide(self):
        pass

    def on_show(self):
        if self.ok and not self._busy:
            self.refresh()
        if self.ok:
            self._fill_audio()

    def _say(self, text, kind="info"):
        color = {"info": "#8b92a8", "good": "#4cd08a", "bad": "#ff6b6b"}[kind]
        self.msg.setText(f'<span style="color:{color}">{text}</span>')

    def _task(self, fn, ok, text):
        self._busy = True
        for b in self.buttons:
            b.setEnabled(False)
        self.list.setEnabled(False)
        self._say(text)

        def finish():
            self._busy = False
            self.list.setEnabled(True)
            for b in self.buttons:
                b.setEnabled(True)

        def done(res):
            if self._alive:
                finish()
                ok(res)

        def fail(err):
            if self._alive:
                finish()
                self._say(err, "bad")

        run_bg(fn, done, fail)

    def _selected(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    # ---- status + list
    def refresh(self):
        def job():
            on = btmgr.powered()
            return on, (btmgr.devices() if on else [])

        self._task(job, self._got, "Checking Bluetooth...")

    def _got(self, res):
        on, devs = res
        self._on = on
        dot = '<span style="color:%s">\u25CF</span>' % ("#4cd08a" if on else "#7b839e")
        conn = [d["name"] for d in devs if d["connected"]]
        self.card.setText(f"Bluetooth &nbsp;&nbsp;{dot} {'On' if on else 'Off'}"
                          + (f" &nbsp;&middot;&nbsp; connected: {', '.join(conn)}" if conn else ""))
        self.b_power.setText("Turn Bluetooth off" if on else "Turn Bluetooth on")
        for b in (self.b_scan, self.b_connect, self.b_disc, self.b_forget):
            b.setEnabled(on)
        self._show_devices(devs)
        if not on:
            self._say("Bluetooth is off. Turn it on to connect speakers or headphones.")

    def _show_devices(self, devs):
        self.list.clear()
        for d in devs:
            tag = "   \u2713 connected" if d["connected"] else ("   paired" if d["paired"] else "")
            icon = "\U0001F3A7" if d["audio"] else "\u2022"
            it = QListWidgetItem(f"{icon}   {d['name']}   {d['mac']}{tag}")
            it.setData(Qt.UserRole, d)
            self.list.addItem(it)
        if devs:
            self.list.setCurrentRow(0)

    # ---- actions
    def _toggle(self):
        on = not self._on

        def job():
            btmgr.set_power(on)
            db.put("bt_enabled", "1" if on else "0")  # remembered across reboots

        self._task(job, lambda _r: self.refresh(), "Turning Bluetooth " + ("on" if on else "off") + "...")

    def _scan(self):
        self._task(lambda: btmgr.scan(8),
                   lambda devs: (self._show_devices(devs),
                                 self._say(f"Found {len(devs)} device(s). Select yours and press Connect." if devs
                                           else "Nothing found. Make sure the device is in pairing mode.")),
                   "Scanning for 8 seconds...")

    def _connect(self):
        d = self._selected()
        if not d or self._busy:
            return
        if d["connected"]:
            self._say(f"{d['name']} is already connected.", "good")
            return
        self._task(lambda: btmgr.pair_connect(d["mac"]),
                   lambda _r: (self._say(f"Connected to {d['name']}. Sound should now play through it.", "good"),
                               self.refresh(), QTimer.singleShot(2500, self._fill_audio)),
                   f"Pairing with {d['name']}... (keep it in pairing mode)")

    def _disconnect(self):
        d = self._selected()
        if d:
            self._task(lambda: btmgr.disconnect(d["mac"]),
                       lambda _r: (self._say(f"Disconnected {d['name']}.", "good"), self.refresh(),
                                   QTimer.singleShot(1500, self._fill_audio)),
                       f"Disconnecting {d['name']}...")

    def _forget(self):
        d = self._selected()
        if d:
            self._task(lambda: btmgr.forget(d["mac"]),
                       lambda _r: (self._say(f"Forgot {d['name']}.", "good"), self.refresh()),
                       f"Forgetting {d['name']}...")

    # ---- audio output choice (via mpv)
    def _fill_audio(self):
        if not self._alive:
            return
        cur = db.get("audio_device", "auto")
        self.audio.clear()
        self.audio.addItem("Automatic (recommended)", "auto")
        try:
            for name, desc in self.hooks.audio_devices():
                if name != "auto":
                    self.audio.addItem(desc or name, name)
        except Exception:
            pass
        self.audio.setCurrentIndex(max(0, self.audio.findData(cur)))

    def _apply_audio(self):
        name = self.audio.currentData() or "auto"
        self.hooks.set_audio_device(name)
        self._say("Audio output set." if name != "auto" else "Audio output set to automatic.", "good")


# --------------------------------------------------------------------------- Diagnostics
class DiagnosticsPage(QWidget):
    def __init__(self, hooks):
        super().__init__()
        self.hooks, self._alive, self._busy, self._sections = hooks, True, False, []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        row = QHBoxLayout()
        self.b_refresh = QPushButton("Refresh now")
        self.b_save = QPushButton("Save report")
        for b in (self.b_refresh, self.b_save):
            b.setAutoDefault(False)
            row.addWidget(b)
        self.note = QLabel("Updates every few seconds while this page is open.")
        self.note.setObjectName("sub")
        row.addWidget(self.note, 1)
        lay.addLayout(row)
        self.view = QLabel("Collecting...")
        self.view.setTextFormat(Qt.RichText)
        self.view.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.view.setWordWrap(True)
        self.view.setStyleSheet("background:transparent;font-size:13px;padding:4px 8px;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.view)
        lay.addWidget(scroll, 1)
        self._timer = QTimer(self, interval=3000, timeout=self.refresh)
        self.b_refresh.clicked.connect(self.refresh)
        self.b_save.clicked.connect(self._save)

    def refresh(self):
        if self._busy or not self._alive:
            return
        self._busy = True
        stats = self.hooks.stats() if self.hooks else {}  # read on the UI thread

        def done(sections):
            self._busy = False
            if self._alive:
                self._sections = sections
                self.view.setText(diagnostics.to_html(sections))

        def fail(_err):
            self._busy = False

        run_bg(lambda: diagnostics.collect(stats), done, fail)

    def _save(self):
        path = db.data_dir() / "diagnostics.txt"
        try:
            path.write_text(diagnostics.to_text(self._sections or diagnostics.collect()), encoding="utf-8")
            self.note.setText(f"Saved to {path}")
        except OSError as e:
            self.note.setText(f"Could not save: {e}")

    def on_show(self):
        self.refresh()
        self._timer.start()

    def on_hide(self):
        self._timer.stop()

    def shutdown(self):
        self._alive = False
        self._timer.stop()
