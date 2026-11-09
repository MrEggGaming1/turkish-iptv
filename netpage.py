"""Settings > Network page: Ethernet status and Wi-Fi scan / connect / forget."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

import netmgr
from util import run_bg


def _bars(signal):
    n = 1 + (signal >= 25) + (signal >= 50) + (signal >= 75)
    return ("\u2582\u2584\u2586\u2588")[:n].ljust(4, "\u00B7")


class NetworkPage(QWidget):
    def __init__(self, ask_text):
        super().__init__()
        self.ask_text = ask_text
        self._alive, self._busy, self._wifi_on = True, False, True
        self._eth_dev = ""
        self.on_online = None  # set by the settings panel: called with True/False after a status check
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.ok = netmgr.available()
        if not self.ok:
            msg = QLabel("Network settings are managed through NetworkManager on the Raspberry Pi.\n\n"
                         "This computer handles its own network connection, so there is nothing to set up here.")
            msg.setObjectName("sub")
            msg.setWordWrap(True)
            lay.addWidget(msg)
            lay.addStretch()
            return

        self.card = QLabel("Checking the network...")
        self.card.setObjectName("card")
        self.card.setTextFormat(Qt.RichText)
        lay.addWidget(self.card)

        row = QHBoxLayout()
        self.b_refresh = QPushButton("Refresh")
        self.b_power = QPushButton("Wi-Fi: ...")
        self.b_hidden = QPushButton("Join hidden network")
        self.b_forget = QPushButton("Forget selected")
        self.b_eth = QPushButton("Reconnect Ethernet")
        self.b_test = QPushButton("Test internet")
        for b in (self.b_refresh, self.b_power, self.b_hidden, self.b_forget, self.b_eth, self.b_test):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)

        self.list = QListWidget()
        self.list.setMinimumHeight(170)
        lay.addWidget(self.list, 1)
        self.msg = QLabel("Select a network and press Enter (or double-click) to connect.")
        self.msg.setObjectName("sub")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)

        self.b_refresh.clicked.connect(lambda: self.refresh())
        self.b_power.clicked.connect(self._toggle_wifi)
        self.b_hidden.clicked.connect(self._hidden)
        self.b_forget.clicked.connect(self._forget)
        self.b_eth.clicked.connect(self._reconnect_eth)
        self.b_test.clicked.connect(self._test)
        self.list.itemDoubleClicked.connect(lambda _i: self._connect_selected())
        self.list.itemActivated.connect(lambda _i: self._connect_selected())

    # ------------------------------------------------------------ plumbing
    def shutdown(self):
        self._alive = False

    def on_hide(self):
        pass

    def on_show(self):
        if self.ok and not self._busy:
            self.refresh()

    def _say(self, text, kind="info"):
        color = {"info": "#8b92a8", "good": "#4cd08a", "bad": "#ff6b6b"}[kind]
        self.msg.setText(f'<span style="color:{color}">{text}</span>')

    def _set_busy(self, busy, text=None):
        self._busy = busy
        for b in (self.b_refresh, self.b_power, self.b_hidden, self.b_forget, self.b_eth, self.b_test):
            b.setEnabled(not busy)
        self.list.setEnabled(not busy)
        if text:
            self._say(text)

    def _task(self, fn, ok, busy_text):
        self._set_busy(True, busy_text)

        def done(res):
            if self._alive:
                self._set_busy(False)
                ok(res)

        def fail(err):
            if self._alive:
                self._set_busy(False)
                self._say(err, "bad")

        run_bg(fn, done, fail)

    # ------------------------------------------------------------ status + scan
    def refresh(self):
        def job():
            st = netmgr.status()
            nets = netmgr.scan() if st["wifi_enabled"] and st["wifi"] else []
            return st, nets

        self._task(job, self._got, "Looking for networks...")

    def _got(self, res):
        st, nets = res
        if self.on_online:
            run_bg(netmgr.internet, lambda ok: self._alive and self.on_online(ok))
        self._wifi_on = st["wifi_enabled"]
        e, w = st["ethernet"], st["wifi"]
        self._eth_dev = e["device"] if e else ""
        dot = lambda ok: '<span style="color:%s">\u25CF</span>' % ("#4cd08a" if ok else "#7b839e")
        lines = []
        if e:
            up = e["state"] == "connected"
            lines.append(f"Ethernet &nbsp;&nbsp;{dot(up)} {'Connected' if up else 'Not connected (' + e['state'] + ')'}"
                         + (f" &nbsp;&middot;&nbsp; {e['ip']}" if e["ip"] else ""))
        else:
            lines.append(f"Ethernet &nbsp;&nbsp;{dot(False)} Not available")
        if not w:
            lines.append(f"Wi-Fi &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{dot(False)} Not available")
        elif not st["wifi_enabled"]:
            lines.append(f"Wi-Fi &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{dot(False)} Turned off")
        elif w["state"] == "connected":
            sig = f" &nbsp;&middot;&nbsp; {w['signal']}%" if w["signal"] is not None else ""
            lines.append(f"Wi-Fi &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{dot(True)} {w['connection']}{sig}"
                         f" &nbsp;&middot;&nbsp; {w['ip']}")
        else:
            lines.append(f"Wi-Fi &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{dot(False)} Not connected")
        self.card.setText("<br>".join(lines))
        self.b_power.setText("Turn Wi-Fi off" if self._wifi_on else "Turn Wi-Fi on")
        self.b_power.setEnabled(bool(w))
        self.b_hidden.setEnabled(bool(w) and self._wifi_on)
        self.b_eth.setEnabled(bool(e))
        self.list.clear()
        for n in nets:
            lock = "\U0001F512" if n["security"] else "  "
            tag = "   \u2713 connected" if n["in_use"] else ("   saved" if n["known"] else "")
            it = QListWidgetItem(f"{_bars(n['signal'])}   {n['ssid']}   {lock}{tag}")
            it.setData(Qt.UserRole, n)
            self.list.addItem(it)
        if nets:
            self.list.setCurrentRow(0)
            self._say("Select a network and press Enter (or double-click) to connect.")
        elif w and self._wifi_on:
            self._say("No Wi-Fi networks found. Try Refresh.")
        else:
            self._say("Plug in Ethernet, or turn Wi-Fi on to find networks.")

    # ------------------------------------------------------------ actions
    def _selected(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _connect_selected(self):
        n = self._selected()
        if not n or self._busy:
            return
        if n["in_use"]:
            self._say(f"Already connected to {n['ssid']}.", "good")
        elif "802.1X" in n["security"]:
            self._say("Enterprise (802.1X) networks can't be set up here.", "bad")
        elif n["security"] and not n["known"]:
            self.ask_text(f"Password for {n['ssid']}", "", True,
                          lambda pw, s=n["ssid"]: self._do_connect(s, pw or None, False, False))
        else:
            self._do_connect(n["ssid"], None, False, n["known"])

    def _do_connect(self, ssid, pw, hidden, known):
        self._task(lambda: netmgr.connect(ssid, pw, hidden, known),
                   lambda _r: (self._say(f"Connected to {ssid}.", "good"), self.refresh()),
                   f"Connecting to {ssid}...")

    def _hidden(self):
        def got_ssid(ssid):
            if ssid.strip():
                self.ask_text(f"Password for {ssid.strip()} (leave empty if open)", "", True,
                              lambda pw, s=ssid.strip(): self._do_connect(s, pw or None, True, False))
        self.ask_text("Hidden network name (SSID)", "", False, got_ssid)

    def _forget(self):
        n = self._selected()
        if not n:
            return
        if not n["known"]:
            self._say("That network isn't saved.")
            return
        self._task(lambda: netmgr.forget(n["ssid"]),
                   lambda _r: (self._say(f"Forgot {n['ssid']}.", "good"), self.refresh()),
                   f"Forgetting {n['ssid']}...")

    def _toggle_wifi(self):
        on = not self._wifi_on
        self._task(lambda: netmgr.set_wifi(on), lambda _r: self.refresh(),
                   "Turning Wi-Fi " + ("on" if on else "off") + "...")

    def _reconnect_eth(self):
        dev = self._eth_dev
        self._task(lambda: netmgr.reconnect_ethernet(dev),
                   lambda _r: (self._say("Ethernet reconnected.", "good"), self.refresh()),
                   "Reconnecting Ethernet...")

    def _test(self):
        self._task(netmgr.internet,
                   lambda ok: self._say("Internet is reachable \u2713" if ok else "No internet connection.",
                                        "good" if ok else "bad"),
                   "Testing the connection...")
