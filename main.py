"""Xtream TV - prototype live TV player (macOS first, Raspberry Pi later)."""
import json
import locale
import os
import signal
import socket
import subprocess
import sys
import time

import requests
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QKeySequence, QPixmap, QSurfaceFormat
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

import db
import keybinds
import netmgr
import provision
import util
from util import DEFAULT_FOURK, fmt_time, on_main, run_bg
from xtream import UA, XtreamClient

ICON_DIR = db.data_dir() / "icons"
ICON_DIR.mkdir(exist_ok=True)

PANEL = "background-color: rgba(10,12,20,205); color: white; border-radius: 12px;"



def internet_ok():
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), 2).close()
            return True
        except OSError:
            continue
    return False


def _fetch_catalog(client):
    cats, streams = client.live_categories(), client.live_streams()
    db.replace_catalog(cats, streams)
    return len(streams)


class AppHooks:
    """What the settings pages may ask of the running app."""

    def __init__(self, win):
        self.win = win

    def stats(self):
        return self.win.player_stats()

    def audio_devices(self):
        return self.win.audio_devices()

    def set_audio_device(self, name):
        self.win.set_audio_device(name)

    def phone_start(self):
        return self.win.phone_start()

    def phone_stop(self):
        self.win.phone_stop()

    def phone_connected(self):
        return self.win.phone_connected()


class MainWindow(QMainWindow):
    BACKOFF = [1, 2, 4, 8, 15, 30]
    MAX_ATTEMPTS = 8
    CATALOG_TTL, EPG_TTL = 12 * 3600, 6 * 3600

    def __init__(self):
        super().__init__()
        from widgets import ChannelList, GuideWidget, MpvWidget
        self.setWindowTitle("Xtream TV")
        self.resize(1280, 720)
        self.setFocusPolicy(Qt.StrongFocus)
        self.video = MpvWidget(self)
        self.video.setFocusPolicy(Qt.NoFocus)
        self.setCentralWidget(self.video)

        self.client = None
        self.channels, self.index, self.last_index = [], -1, None
        self.buf = ""
        self.state, self.gen, self.attempt = "idle", 0, 0
        self.ext = "m3u8"
        self.play_started = self.last_pos_time = self.playing_since = 0.0
        self.last_pos = None
        self._epg_busy = False
        self._status_kind = ""
        self.settings_panel = None
        self.power_menu = None
        self.phone = None
        self.hooks = AppHooks(self)
        self.corner_pos = "br"
        self._icon_busy = set()
        self.pending = None
        self.binds, self.keymap = {}, {}
        self.confirm_switch, self.banner_secs = True, 5
        self.minimalistic = False
        self._banner_h = 150
        self.q = {}
        self._corner_until = 0.0
        self._last_cursor = None
        self._cursor_hidden = False
        self._retune = False

        self._build_overlays(ChannelList, GuideWidget)
        self._build_menu()
        self._wire_mpv()
        self.load_prefs()
        QApplication.instance().installEventFilter(self)  # power key works from anywhere

        self.watch = QTimer(self, interval=1000, timeout=self._watchdog)
        self.watch.start()
        self.retry_timer = QTimer(self, singleShot=True, timeout=self._do_retry)
        self.num_timer = QTimer(self, singleShot=True, timeout=self._commit_number)
        self.refresh_timer = QTimer(self, interval=15 * 60 * 1000, timeout=self._maybe_refresh)
        self.refresh_timer.start()
        self.cursor_timer = QTimer(self, interval=200, timeout=self._poll_cursor)
        self.cursor_timer.start()

    # ------------------------------------------------------------------ UI setup
    def _build_overlays(self, ChannelList, GuideWidget):
        v = self.video
        from widgets import StatusCard
        self.status_card = StatusCard(v)
        self.num_lbl = QLabel(v)
        self.num_lbl.setStyleSheet(PANEL + "font-size:40px;font-weight:bold;padding:10px 24px;")
        self.vol_lbl = QLabel(v)
        self.vol_lbl.setStyleSheet(PANEL + "font-size:24px;padding:10px 20px;")

        self.banner = QWidget(v)
        self.banner.setAttribute(Qt.WA_StyledBackground, True)
        self.banner.setStyleSheet("QWidget#b{" + PANEL + "} QLabel{background:transparent;color:white;}")
        self.banner.setObjectName("b")
        outer = QHBoxLayout(self.banner)
        outer.setContentsMargins(20, 14, 24, 14)
        outer.setSpacing(18)
        self.b_icon = QLabel()
        self.b_icon.setFixedSize(104, 104)
        self.b_icon.setAlignment(Qt.AlignCenter)
        self.b_icon.setStyleSheet("background:rgba(255,255,255,28);border-radius:12px;"
                                  "font-size:34px;font-weight:bold;color:#cfd6ee;")
        outer.addWidget(self.b_icon)
        lay = QVBoxLayout()
        outer.addLayout(lay, 1)
        self.b_title, self.b_now, self.b_next = QLabel(), QLabel(), QLabel()
        self.b_title.setStyleSheet("font-size:28px;font-weight:bold;")
        self.b_now.setStyleSheet("font-size:20px;")
        self.b_next.setStyleSheet("font-size:17px;color:#aab;")
        self.b_bar = QProgressBar()
        self.b_bar.setTextVisible(False)
        self.b_bar.setFixedHeight(6)
        self.b_bar.setStyleSheet("QProgressBar{background:#333;border-radius:3px;}"
                                 "QProgressBar::chunk{background:#2d6cdf;border-radius:3px;}")
        self.b_hint = QLabel()
        self.b_hint.setStyleSheet("font-size:16px;font-weight:bold;color:#6ea8ff;")
        self.b_hint.hide()
        for w in (self.b_title, self.b_now, self.b_bar, self.b_next, self.b_hint):
            lay.addWidget(w)

        self.corner = QWidget(v)
        self.corner.setAttribute(Qt.WA_StyledBackground, True)
        self.corner.setObjectName("corner")
        self.corner.setStyleSheet(
            "QWidget#corner{" + PANEL + "} "
            "QLabel{background:transparent;color:white;font-size:18px;}")
        cl = QHBoxLayout(self.corner)
        cl.setContentsMargins(16, 6, 8, 6)
        cl.setSpacing(8)
        self.q_lbl = QLabel("Quality: --")
        btn_css = ("QPushButton{background:rgba(255,255,255,30);color:white;font-size:22px;"
                   "border:none;border-radius:8px;}QPushButton:hover{background:#2d6cdf;}")
        self.mute_btn = QPushButton("\U0001F50A")
        self.mute_btn.setToolTip("Mute")
        self.gear = QPushButton("\u2699")
        self.gear.setToolTip("Settings")
        for btn in (self.mute_btn, self.gear):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFixedSize(40, 36)
            btn.setStyleSheet(btn_css)
        self.mute_btn.clicked.connect(lambda: self.toggle_mute())
        self.gear.clicked.connect(lambda: self.open_settings())
        cl.addWidget(self.q_lbl)
        cl.addWidget(self.mute_btn)
        cl.addWidget(self.gear)
        self.corner.hide()

        self.list = ChannelList(v)
        self.guide = GuideWidget(v)
        self.list.picked.connect(lambda i: (self.setFocus(), self.tune(i)))
        self.list.closed.connect(self.setFocus)
        self.guide.tune.connect(lambda i: (self.setFocus(), self.tune(i)))
        self.guide.closed.connect(self.setFocus)

        self.t_status = QTimer(self, singleShot=True, timeout=self.status_card.hide)
        self.t_vol = QTimer(self, singleShot=True, timeout=self.vol_lbl.hide)
        self.t_banner = QTimer(self, singleShot=True, timeout=self._banner_timeout)
        for w in (self.num_lbl, self.vol_lbl, self.banner):
            w.hide()

        v.resized.connect(self._layout_overlays)
        v.clicked.connect(self.setFocus)
        v.double_clicked.connect(self.toggle_fullscreen)

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("File")
        for text, key, fn in (("Settings...", "Ctrl+,", lambda: self.open_settings()),
                              ("Manage Channels...", "Ctrl+K", lambda: self.open_settings("channels")),
                              ("Refresh Channels && Guide", "Ctrl+R", lambda: self.refresh_all(True))):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            fm.addAction(a)
        vm = mb.addMenu("View")
        fs = QAction("Toggle Fullscreen", self)
        fs.setShortcut(QKeySequence(QKeySequence.StandardKey.FullScreen))
        fs.triggered.connect(self.toggle_fullscreen)
        vm.addAction(fs)
        for text, fn in (("TV Guide (G)", self.open_guide), ("Channel List (L)", self.open_list)):
            a = QAction(text, self)
            a.triggered.connect(fn)
            vm.addAction(a)
        hm = mb.addMenu("Help")
        ha = QAction("Keyboard Shortcuts", self)
        ha.triggered.connect(lambda: self.open_settings("keys"))
        hm.addAction(ha)

    def _layout_overlays(self):
        w, h = self.video.width(), self.video.height()
        self.guide.setGeometry(0, 0, w, h)
        self.list.setGeometry(0, 0, min(380, w), h)
        if self.settings_panel is not None:
            self.settings_panel.setGeometry(0, 0, w, h)
        if self.power_menu is not None:
            self.power_menu.setGeometry(0, 0, w, h)
        margin = 30
        top, left = self.corner_pos[0] == "t", self.corner_pos[1] == "l"
        self.corner.adjustSize()
        cw, ch = self.corner.width(), self.corner.height()
        self.corner.move(margin if left else w - cw - margin, margin if top else h - ch - margin)

        bw, bh = min(w - 60, 1000), self._banner_h
        bottom_clear = 40 if top else 104  # leave room for the corner row when it sits at the bottom
        self.banner.setGeometry((w - bw) // 2, h - bottom_clear - bh, bw, bh)

        dodge = ch + 12  # transient labels step aside if the corner is on their side at the top
        self.vol_lbl.move(margin, margin + (dodge if top and left else 0))
        self.num_lbl.move(w - self.num_lbl.width() - margin, margin + (dodge if top and not left else 0))

        # loading/status card: centred between the info banner and the top of the screen
        sc = self.status_card
        banner_top = h - bottom_clear - bh
        sc.move((w - sc.width()) // 2, max(20, banner_top // 2 - sc.height() // 2))

    def _show_label(self, lbl, text, timer=None, ms=0):
        lbl.setText(text)
        lbl.adjustSize()
        self._layout_overlays()
        lbl.show()
        lbl.raise_()
        if timer:
            timer.start(ms) if ms else timer.stop()

    def status(self, title, detail="", sticky=False, busy=False, kind="info"):
        self._status_kind = kind
        self.status_card.set(title, detail, busy)
        self._layout_overlays()
        self.status_card.show()
        if self.settings_panel is None or kind == "power":
            self.status_card.raise_()
        if sticky:
            self.t_status.stop()
        else:
            self.t_status.start(3500)

    def clear_status(self):
        self._status_kind = ""
        self.t_status.stop()
        self.status_card.hide()

    # ------------------------------------------------------------------ mpv hooks
    def _wire_mpv(self):
        mpv = self.video.mpv

        @mpv.property_observer("idle-active")
        def _idle(_name, value):
            if value:
                on_main(self._on_idle)

        @mpv.property_observer("video-params")
        def _vparams(_name, value):
            on_main(lambda v=value: self._set_vparams(v))

        @mpv.property_observer("video-format")
        def _vformat(_name, value):
            on_main(lambda v=value: self._set_q("fmt", v))

        @mpv.property_observer("container-fps")
        def _vfps(_name, value):
            on_main(lambda v=value: self._set_q("fps", v))

        @mpv.property_observer("paused-for-cache")
        def _cache(_name, value):
            on_main(lambda b=bool(value): self._on_buffering(b))

    def _on_buffering(self, buffering):
        if self.state != "playing":
            return
        if buffering:
            self.status("Buffering", busy=True, sticky=True, kind="buffer")
        elif self._status_kind == "buffer":
            self.clear_status()

    def _on_idle(self):
        if self.state in ("loading", "playing") and time.time() - self.play_started > 1.5:
            self.recover("Stream ended")

    # ------------------------------------------------------------------ startup / data
    def _provision_wifi(self):
        """One-shot Wi-Fi join from the provisioning file. Returns True if a connection attempt was started."""
        ssid = db.get("wifi_ssid", "")
        if not ssid or not netmgr.available():
            return False
        pw = db.get("wifi_password", "")
        db.put("wifi_ssid", "")
        db.put("wifi_password", "")  # one-shot: don't keep the Wi-Fi password around
        run_bg(lambda: netmgr.connect(ssid, pw or None, False, False),
               lambda _r: self.status("Wi-Fi connected", ssid),
               lambda err: self.status("Wi-Fi setup failed", err))
        return True

    def _startup_extras(self):
        self._apply_audio_device()
        if db.RECOVERED:
            QTimer.singleShot(3000, lambda: self.status(
                "Settings recovered", "Your settings were restored after an unexpected power loss."))
        if db.get("bt_enabled", "1") == "0":  # Bluetooth was turned off in settings: keep it off after reboot
            import btmgr
            if btmgr.available():
                run_bg(lambda: btmgr.set_power(False), None, lambda _e: None)

    def _first_run(self, waiting_for_wifi):
        """No login yet: get the network working first, then offer phone / on-device setup."""
        self.status("Welcome", "Setting things up - checking the network...", sticky=True, busy=True, kind="welcome")

        def decide(online):
            self.clear_status()
            if self.settings_panel is None:
                self.open_settings("phone" if online else "network", first_run=True)

        QTimer.singleShot(12000 if waiting_for_wifi else 300, lambda: run_bg(netmgr.internet, decide))

    def start(self):
        wifi_started = self._provision_wifi()
        self._startup_extras()
        self.video.mpv.volume = int(db.get("volume", "70"))
        self.reload_channels()
        self.client = self.make_client()
        if not self.client:
            self._first_run(wifi_started)
            return
        self.after_login()

    def make_client(self):
        s, u, p = db.get("server"), db.get("username"), db.get("password")
        return XtreamClient(s, u, p) if (s and u and p) else None

    def after_login(self):
        self.ext = db.get("stream_ext", "m3u8")
        QTimer.singleShot(45000, self._auto_check_update)
        if self.channels:
            last = int(db.get("last_stream_id", "0") or 0)
            idx = next((i for i, c in enumerate(self.channels) if c["stream_id"] == last), 0)
            self.tune(idx, banner=False)
        self._maybe_refresh()

    def reload_channels(self):
        cur = self.channels[self.index]["stream_id"] if 0 <= self.index < len(self.channels) else None
        self.channels = db.my_channels()
        self.index = next((i for i, c in enumerate(self.channels) if c["stream_id"] == cur), -1)

    def _stale(self, key, ttl):
        return time.time() - float(db.get(key, "0") or 0) > ttl

    def _maybe_refresh(self):
        if not self.client:
            return
        if self._stale("catalog_ts", self.CATALOG_TTL) or not db.categories():
            self.refresh_all()
        elif self._stale("epg_ts", self.EPG_TTL):
            self.refresh_epg()
        elif not self.channels and self.settings_panel is None:
            self.open_settings("channels")

    def refresh_all(self, manual=False):
        if not self.client:
            return
        if manual:
            self.status("Updating channels and guide", busy=True, sticky=True, kind="update")
        c = self.client
        run_bg(lambda: _fetch_catalog(c), self._catalog_done,
               lambda err: self.status("Update failed", err))

    def _catalog_done(self, n):
        db.put("catalog_ts", time.time())
        self.reload_channels()
        if not self.channels:  # optional hands-free setup from the provisioning file
            try:
                terms = json.loads(db.get("auto_select", "[]") or "[]")
            except ValueError:
                terms = []
            if terms:
                picked = db.select_by_terms(terms)
                db.put("auto_select", "[]")
                if picked:
                    self.reload_channels()
                    self.status("Channels selected", f"{picked} channels added from your setup file.")
        if self._status_kind == "update":
            self.clear_status()
        if not self.channels:
            self.status("Channels loaded", f"{n} available - pick the ones you want.")
            if self.settings_panel is None:
                self.open_settings("channels")
            return
        if self.state == "idle" or self._retune:
            self._retune = False
            self.tune(max(self.index, 0), banner=False)
        self.refresh_epg()

    def refresh_epg(self):
        if not self.client or not self.channels or self._epg_busy:
            return
        self._epg_busy = True
        chans, client = list(self.channels), self.client

        def job():
            wanted = {c["epg_id"] for c in chans if c["epg_id"]}
            rows = []
            if wanted:
                try:
                    rows = client.fetch_xmltv(wanted)
                except Exception:
                    rows = []
            have = {r[0] for r in rows}
            for c in [c for c in chans if db.epg_key(c) not in have][:200]:
                try:
                    for s, e, t, d in client.short_epg(c["stream_id"]):
                        rows.append((db.epg_key(c), s, e, t, d))
                except Exception:
                    pass
            db.replace_epg(rows)
            return len(rows)

        def done(_n):
            self._epg_busy = False
            db.put("epg_ts", time.time())
            if self.banner.isVisible():
                self._refresh_banner()

        def fail(_err):
            self._epg_busy = False

        run_bg(job, done, fail)

    # ------------------------------------------------------------------ dialogs
    def open_settings(self, page=None, first_run=False):
        """Settings are an in-window overlay (no separate dialog), so they work on a Pi kiosk."""
        from dialogs import SettingsPanel
        page = page if isinstance(page, str) else "account"
        if self.settings_panel is not None:
            self.settings_panel.go(page)
            return
        self.num_timer.stop()
        self.buf = ""
        self.num_lbl.hide()
        self.corner.hide()
        self.banner.hide()
        panel = SettingsPanel(self.video, page, hooks=self.hooks, first_run=first_run)
        panel.finished.connect(self._settings_done)
        panel.restart_requested.connect(self.restart_app)
        panel.power_requested.connect(self.power)
        self.settings_panel = panel
        self._layout_overlays()
        panel.show()
        panel.raise_()
        panel.nav.setFocus()

    def open_manager(self):
        self.open_settings("channels")

    def _settings_done(self, saved, creds_changed, channels_changed):
        panel, self.settings_panel = self.settings_panel, None
        if panel is not None:
            panel.hide()
            panel.deleteLater()
        self.setFocus()
        if not saved:
            return
        self.load_prefs()
        self.update_quality()
        self._layout_overlays()
        if creds_changed:
            self.client = self.make_client()
            self._retune = True
            self.clear_status()
            self.refresh_all(True)
        elif channels_changed:
            self._channels_changed()

    def _channels_changed(self):
        self.reload_channels()
        if not self.channels:
            self.video.mpv.stop()
            self.state = "idle"
            self.status("No channels selected")
            return
        if self.index < 0:
            self.tune(0)
        self.refresh_epg()

    def _phone_changed(self, kinds):
        """Something was changed from the phone page."""
        if "creds" in kinds:
            self.client = self.make_client()
            self._retune = True
            self.clear_status()
            self.refresh_all(True)
        if "prefs" in kinds:
            self.load_prefs()
            self.update_quality()
            self._layout_overlays()
        if "channels" in kinds:
            self._channels_changed()
        if self.settings_panel is not None:
            self.settings_panel.reload_from_db()

    # ---- phone setup server (runs only while the Phone Setup page is open)
    def phone_start(self):
        import phone
        self.phone_stop()
        self.phone = phone.PhoneServer(on_change=lambda kinds: on_main(lambda: self._phone_changed(kinds)))
        return self.phone.start()

    def phone_stop(self):
        if self.phone is not None:
            self.phone.stop()
            self.phone = None

    def phone_connected(self):
        return bool(self.phone and self.phone.connected)

    # ---- player information for Diagnostics, audio output choice, time zone
    def _raw(self, name):
        try:
            return self.video.mpv[name]
        except Exception:
            return None

    def player_stats(self):
        ch = self.channels[self.index] if 0 <= self.index < len(self.channels) else None
        w = self._prop("video-params/w", "dwidth", "width")
        h = self._prop("video-params/h", "dheight", "height")
        vf, fps = self._prop("video-format"), self._prop("container-fps", "estimated-vf-fps")
        hw = self._prop("hwdec-current")
        video = ""
        if w and h:
            video = f"{int(w)}x{int(h)}" + (f"  {str(vf).upper()}" if vf else "")
            try:
                video += f"  {round(float(fps))} fps" if fps else ""
            except (TypeError, ValueError):
                pass
        drops, ddrops, cache = self._raw("frame-drop-count"), self._raw("decoder-frame-drop-count"), self._raw("demuxer-cache-duration")
        stats = {"channel": f"{ch['number']}  {ch['name']}" if ch else "", "state": self.state, "video": video,
                 "decoder": ("hardware (" + str(hw) + ")" if hw and str(hw) != "no" else "software") if video else "",
                 "ext": self.ext, "attempts": self.attempt}
        if drops is not None:
            stats["drops"] = f"{drops} (decoder {ddrops if ddrops is not None else 0})"
        if isinstance(cache, (int, float)):
            stats["buffer"] = f"{cache:.1f} s"
        return stats

    def audio_devices(self):
        try:
            return [(d.get("name", ""), d.get("description", "")) for d in self.video.mpv["audio-device-list"]]
        except Exception:
            return []

    def set_audio_device(self, name):
        db.put("audio_device", name)
        self._apply_audio_device()

    def _apply_audio_device(self):
        name = db.get("audio_device", "auto")
        try:
            if name != "auto" and name not in {n for n, _d in self.audio_devices()}:
                name = "auto"  # the saved device (e.g. headphones) isn't connected right now
            self.video.mpv["audio-device"] = name
        except Exception:
            pass

    def _apply_timezone(self, tz):
        first = not hasattr(self, "_tz_applied")
        if getattr(self, "_tz_applied", None) == tz:
            return
        self._tz_applied = tz
        if tz:
            os.environ["TZ"] = tz
        else:
            os.environ.pop("TZ", None)
        if hasattr(time, "tzset"):
            time.tzset()
        if tz and not first and sys.platform.startswith("linux"):  # also set the Pi's system time zone
            run_bg(lambda: subprocess.run(["sudo", "-n", "/usr/bin/timedatectl", "set-timezone", tz],
                                          capture_output=True, text=True, timeout=20), None, lambda _e: None)

    def save_state(self):
        """Remember what you were watching, and make sure it is on disk before power goes."""
        self.phone_stop()
        try:
            if 0 <= self.index < len(self.channels):
                db.put("last_stream_id", self.channels[self.index]["stream_id"])
            db.checkpoint()
            if hasattr(os, "sync"):
                os.sync()
        except Exception:
            pass

    def restart_app(self):
        self.save_state()
        self.watch.stop()
        self.video.shutdown()
        if os.environ.get("XTREAMTV_KIOSK") == "1":
            QApplication.instance().quit()  # systemd restarts the kiosk service
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def power(self, action):
        """action: 'poweroff' or 'reboot'. The channel is saved first and resumes after the restart."""
        self.save_state()
        if not sys.platform.startswith("linux"):  # dev machine: just quit / relaunch the app
            if action == "reboot":
                self.restart_app()
            else:
                QApplication.instance().quit()
            return
        restarting = action == "reboot"
        self.status("Restarting" if restarting else "Powering off", "Saving your place...",
                    busy=True, sticky=True, kind="power")

        def job():
            return subprocess.run(["sudo", "-n", "/usr/bin/systemctl", action], capture_output=True,
                                  text=True, timeout=30)

        def done(r):
            if r.returncode != 0:
                why = (r.stderr or "").strip()[-120:] or "Permission problem - re-run the installer."
                self.status("Could not restart" if restarting else "Could not power off", why)

        run_bg(job, done, lambda err: self.status("Could not power off", err))

    def open_power_menu(self):
        if self.power_menu is not None:
            return
        from widgets import PowerMenu
        prev = QApplication.focusWidget()
        menu = PowerMenu(self.video)
        menu.cancel_keys = {k for k in self.binds.get("power", []) + self.binds.get("back", []) if k}
        self.power_menu = menu

        def done(choice):
            self.power_menu = None
            menu.hide()
            menu.deleteLater()
            (prev if prev is not None else self).setFocus()
            if choice in ("poweroff", "reboot"):
                self.power(choice)

        menu.chosen.connect(done)
        self.num_timer.stop()
        self.buf = ""
        self.num_lbl.hide()
        menu.setGeometry(0, 0, self.video.width(), self.video.height())
        menu.open()

    def eventFilter(self, obj, ev):
        # The power key opens the menu wherever you are (settings, keyboard, ...), except while a key is
        # being learned in Key Bindings / the remote wizard, where it must be recorded instead.
        if ev.type() == QEvent.KeyPress and self.power_menu is None and not ev.isAutoRepeat():
            if self.keymap.get(keybinds.event_to_str(ev)) == "power":
                fw = QApplication.focusWidget()
                learning = self.settings_panel is not None and (
                    getattr(fw, "_capturing", False) or type(fw).__name__ == "CaptureArea")
                typing = (self.settings_panel is not None and self.settings_panel._osk is not None) \
                    or isinstance(fw, QLineEdit)
                typed = typing and bool(ev.text()) and ev.text().isprintable()  # a letter being typed
                if not learning and not typed:
                    self.open_power_menu()
                    return True
        return super().eventFilter(obj, ev)

    def _auto_check_update(self):
        import updater
        if db.get("update_check_start", "1") != "1" or not db.get("update_repo"):
            return
        run_bg(lambda: updater.check(updater.config_from_db()), self._update_found, lambda _e: None)

    def _update_found(self, info):
        installed = db.get("installed_ref", "")
        if installed and installed != info["ref"] and self.settings_panel is None:
            self.status("Update available", f"{info['label']}  -  open Settings > Updates")

    def load_prefs(self):
        self.binds = keybinds.load()
        self.keymap = keybinds.build_keymap(self.binds)
        self.confirm_switch = db.get("confirm_switch", "1") == "1"
        self.banner_secs = max(2, int(db.get("banner_secs", "5") or 5))
        self.minimalistic = db.get("minimalistic", "0") == "1"
        self._apply_timezone(db.get("timezone", ""))
        util.set_clock24(db.get("clock24", "1") == "1")
        pos = db.get("corner_pos", "br")
        self.corner_pos = pos if pos in ("tl", "tr", "bl", "br") else "br"
        for widget, action in ((self.guide, "guide"), (self.list, "list")):
            keys = [keybinds.to_qt_key(k) for k in self.binds.get(action, []) + self.binds.get("back", [])]
            widget.close_keys = tuple([Qt.Key_Escape] + [k for k in keys if k is not None])
        if not self.confirm_switch:
            self.pending = None
        prefs = (db.get("decode_mode", "auto"), db.get("fourk_mode", DEFAULT_FOURK) == "1")
        if prefs != getattr(self, "_dec_prefs", None):
            first = getattr(self, "_dec_prefs", None) is None
            self._dec_prefs = prefs
            self.video.apply_decoder(*prefs)
            if not first and self.state in ("loading", "playing"):
                self.retry_now()  # new decoder settings apply to the next stream open

    def open_guide(self):
        self.guide.open(self.channels, self.index)

    def open_list(self):
        self.list.open(self.channels, self.index)

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    # ------------------------------------------------------------------ playback + auto-reconnect
    def tune(self, idx, banner=True):
        if not self.channels or not self.client:
            self.status("No channels yet", "Open Settings > Channels to choose some.")
            return
        idx %= len(self.channels)
        if idx != self.index:
            self.last_index = self.index if self.index >= 0 else None
        self.index = idx
        ch = self.channels[idx]
        db.put("last_stream_id", ch["stream_id"])
        self.gen += 1
        self.pending = None
        self.retry_timer.stop()
        self.attempt = 0
        self.ext = db.get("stream_ext", "m3u8")
        self._play_current()
        if banner:
            self.show_banner()

    def _play_current(self):
        ch = self.channels[self.index]
        self.state = "loading"
        self.play_started = self.last_pos_time = time.time()
        self.last_pos = None
        self.playing_since = 0.0
        self.q = {}
        self.status("Loading", ch["name"], sticky=True, busy=True, kind="loading")
        try:
            self.video.mpv.play(self.client.stream_url(ch["stream_id"], self.ext))
        except Exception as e:  # noqa
            self.recover(f"Player error: {e}")

    def _watchdog(self):
        if self.corner.isVisible():
            self.update_quality()
        if self.state not in ("loading", "playing"):
            return
        mpv = self.video.mpv
        try:
            if mpv.pause:
                self.last_pos_time = time.time()
                return
            pos = mpv.time_pos
        except Exception:
            return
        now = time.time()
        if pos is not None and pos != self.last_pos:
            self.last_pos, self.last_pos_time = pos, now
            if self.state == "loading":
                self.state, self.playing_since = "playing", now
                if self._status_kind in ("loading", "reconnect", "nonet", "limit"):
                    self.clear_status()
            elif now - self.playing_since > 15:
                self.attempt = 0  # stable again
            return
        limit = 15 if self.state == "loading" else 8
        if now - self.last_pos_time > limit:
            self.recover("Stream stalled")

    def recover(self, reason):
        if self.state in ("reconnecting", "failed", "idle"):
            return
        self.attempt += 1
        name = self.channels[self.index]["name"]
        if self.attempt > self.MAX_ATTEMPTS:
            self.state = "failed"
            self.video.mpv.stop()
            self.status(f"{name} is unavailable", "Press the retry key, or change channel.",
                        sticky=True, kind="failed")
            return
        self.state = "reconnecting"
        self.status("Reconnecting", f"{reason}  \u00B7  attempt {self.attempt}", sticky=True,
                    busy=True, kind="reconnect")
        if self.attempt == 3:
            self._check_connection_limit()
        self.retry_timer.start(self.BACKOFF[min(self.attempt - 1, len(self.BACKOFF) - 1)] * 1000)

    def _do_retry(self):
        gen = self.gen
        run_bg(internet_ok, lambda ok: self._after_check(ok, gen))

    def _after_check(self, ok, gen):
        if gen != self.gen or self.state != "reconnecting":
            return
        if not ok:
            self.status("No internet connection", "Waiting for the network  \u00B7  Settings > Network",
                        sticky=True, busy=True, kind="nonet")
            self.retry_timer.start(3000)
            return
        if self.attempt >= 2 and self.attempt % 2 == 0:
            self.ext = "ts" if self.ext == "m3u8" else "m3u8"  # try the other format
        self._play_current()

    def _check_connection_limit(self):
        gen, client = self.gen, self.client

        def done(data):
            if gen != self.gen or self.state != "reconnecting":
                return
            ui = data.get("user_info", {})
            try:
                active, mx = int(ui.get("active_cons")), int(ui.get("max_connections"))
            except (TypeError, ValueError):
                return
            if active >= mx:
                self.status("Connection limit reached",
                            f"{active}/{mx} connections in use - close a stream on another device. Retrying...",
                            sticky=True, busy=True, kind="limit")

        run_bg(client.account_info, done)

    def retry_now(self):
        if self.index >= 0 and self.client:
            self.gen += 1
            self.retry_timer.stop()
            self.attempt = 0
            self._play_current()

    # ------------------------------------------------------------------ channel info / numbers / volume
    def _refresh_banner(self):
        if self.pending is not None:
            self.show_banner(self.pending, preview=True)
        else:
            self.show_banner()

    def _banner_timeout(self):
        self.pending = None
        self.banner.hide()

    def show_banner(self, idx=None, preview=False):
        if not self.channels or self.settings_panel is not None:
            return
        idx = self.index if idx is None else idx
        if not (0 <= idx < len(self.channels)):
            return
        ch = self.channels[idx]
        now, nxt = db.now_next(db.epg_key(ch))
        self.b_title.setText(f"{ch['number']}   {ch['name']}")
        self._set_icon(ch)
        if now:
            self.b_now.setText(f"Now   {fmt_time(now[0])} - {fmt_time(now[1])}   {now[2]}")
            total = max(1, now[1] - now[0])
            self.b_bar.setValue(int(100 * (time.time() - now[0]) / total))
        else:
            self.b_now.setText("No guide information")
            self.b_bar.setValue(0)
        self.b_next.setText(f"Next  {fmt_time(nxt[0])}   {nxt[2]}" if nxt else "")
        if preview:
            hint = (f"Press {keybinds.first_pretty(self.binds.get('confirm'))} to watch   \u00B7   "
                    f"{keybinds.first_pretty(self.binds.get('back'))} to cancel")
            if 0 <= self.index < len(self.channels):
                hint += f"      (still watching {self.channels[self.index]['name']})"
            self.b_hint.setText(hint)
            self.b_hint.show()
        else:
            self.b_hint.hide()
        self._banner_h = 178 if preview else 150
        self._layout_overlays()
        self.banner.show()
        self.banner.raise_()
        self.t_banner.start((max(self.banner_secs, 6) if preview else self.banner_secs) * 1000)
        self.show_corner()

    def step(self, delta):
        """Channel up/down: preview first (current keeps playing) or switch immediately."""
        if not self.channels:
            return
        if self.confirm_switch:
            if self.pending is None:
                self.pending = max(self.index, 0)  # first press: show where you are now
            else:
                self.pending = (self.pending + delta) % len(self.channels)
            self.show_banner(self.pending, preview=True)
        else:
            self.tune(self.index + delta)

    # ------------------------------------------------------------------ icon + quality + corner
    def _set_icon(self, ch):
        path = ICON_DIR / f"{ch['stream_id']}.img"
        if path.exists():
            pm = QPixmap(str(path))
            if not pm.isNull():
                self.b_icon.setPixmap(pm.scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        self.b_icon.clear()
        self.b_icon.setText(str(ch["number"]))
        self._fetch_icon(ch)

    def _fetch_icon(self, ch):
        url, sid = ch.get("icon") or "", ch["stream_id"]
        if not url.startswith(("http://", "https://")) or sid in self._icon_busy:
            return
        self._icon_busy.add(sid)  # failures stay in the set so we don't hammer a dead URL
        path = ICON_DIR / f"{sid}.img"

        def job():
            r = requests.get(url, timeout=8, headers={"User-Agent": UA})
            r.raise_for_status()
            if not (100 < len(r.content) < 3_000_000):
                raise ValueError("bad icon size")
            path.write_bytes(r.content)
            return sid

        def done(_):
            self._icon_busy.discard(sid)
            if 0 <= self.index < len(self.channels) and self.channels[self.index]["stream_id"] == sid:
                self._set_icon(self.channels[self.index])

        run_bg(job, done)

    def _prop(self, *names):
        for name in names:
            try:
                v = self.video.mpv[name]
            except Exception:
                continue
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            if v not in (None, "", 0):
                return v
        return None

    def _set_q(self, key, value):
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if value:
            self.q[key] = value
        if self.corner.isVisible():
            self.update_quality()

    def _set_vparams(self, v):
        if isinstance(v, dict):
            for key in ("w", "h"):
                val = v.get(key) or v.get(key.encode())
                if val:
                    self.q[key] = val
        if self.corner.isVisible():
            self.update_quality()

    def update_quality(self):
        q = self.q
        w = q.get("w") or self._prop("video-params/w", "dwidth", "width")
        h = q.get("h") or self._prop("video-params/h", "dheight", "height")
        tier = None
        if self.state in ("idle", "failed", "reconnecting") or not (w and h):
            text = "Quality: --"
        else:
            w, h = int(w), int(h)
            tier = "4K" if h >= 2000 else "FHD" if h >= 1000 else "HD" if h >= 700 else "SD"
            codec = {"h264": "H.264", "hevc": "HEVC", "mpeg2video": "MPEG-2", "av1": "AV1"}
            parts = [tier, f"{w}\u00D7{h}"]
            vf = q.get("fmt") or self._prop("video-format")
            if vf:
                parts.append(codec.get(str(vf), str(vf).upper()))
            fps = q.get("fps") or self._prop("container-fps", "estimated-vf-fps")
            try:
                if fps and float(fps) > 0:
                    parts.append(f"{round(float(fps))} fps")
            except (TypeError, ValueError):
                pass
            hw = self._prop("hwdec-current")
            parts.append("HW" if hw and str(hw) != "no" else "SW")
            text = "  \u00B7  ".join(parts)
        if self.minimalistic:  # just 4K / FHD / HD / SD
            text = tier or "--"
        if text != self.q_lbl.text():
            print("[quality]", text, "| raw:", q, flush=True)
            self.q_lbl.setText(text)
            self._layout_overlays()

    def show_corner(self):
        if self.settings_panel is not None:
            return
        self._corner_until = time.time() + 6
        self.update_quality()
        self.corner.show()
        self.corner.raise_()

    def _poll_cursor(self):
        """Show the corner when the pointer really moves; hide it after a few idle seconds.
        Uses the real cursor position, so Qt's synthetic mouse events can't keep it alive."""
        pos = QCursor.pos()
        last, self._last_cursor = self._last_cursor, pos
        if last is not None and self.isActiveWindow() and \
                abs(pos.x() - last.x()) + abs(pos.y() - last.y()) >= 4:
            if self.video.rect().contains(self.video.mapFromGlobal(pos)):
                self.show_corner()
        if self.corner.isVisible() and time.time() > self._corner_until:
            self.corner.hide()
            self.video.update()
        idle = (not self.corner.isVisible() and self.settings_panel is None
                and not self.guide.isVisible() and not self.list.isVisible())
        if idle != self._cursor_hidden:
            self._cursor_hidden = idle
            self.video.setCursor(Qt.BlankCursor) if idle else self.video.unsetCursor()

    def digit(self, d):
        n = len(self.channels)
        if not n:
            return
        self.buf += d
        if len(self.buf) > len(str(n)):
            self.buf = d
        num = int(self.buf)
        name = self.channels[num - 1]["name"] if 1 <= num <= n else ""
        self._show_label(self.num_lbl, self.buf + (f"   {name}" if name else ""))
        if num * 10 > n:
            self.num_timer.stop()
            QTimer.singleShot(350, self._commit_number)  # brief pause so you see what you typed
        else:
            self.num_timer.start(2000)

    def _commit_number(self, force=False):
        if not self.buf:
            return
        num, self.buf = int(self.buf), ""
        self.num_timer.stop()
        self.num_lbl.hide()
        if not (1 <= num <= len(self.channels)):
            self.status("No such channel", str(num))
        elif self.confirm_switch and not force:
            self.pending = num - 1  # preview; the confirm key switches
            self.show_banner(self.pending, preview=True)
        else:
            self.tune(num - 1)

    def set_volume(self, v):
        v = max(0, min(100, int(v)))
        self.video.mpv.volume = v
        db.put("volume", v)
        self.show_volume()

    def show_volume(self):
        mpv = self.video.mpv
        vol = int(mpv.volume or 0)
        if mpv.mute:
            text = "\U0001F507  Muted"
        else:
            filled = round(vol / 10)
            bar = "\u2588" * filled + "\u2591" * (10 - filled)
            text = f"\U0001F50A  {bar}  {vol}%"
        self._show_label(self.vol_lbl, text, self.t_vol, 1800)

    def toggle_mute(self):
        self.video.mpv.mute = not self.video.mpv.mute
        self.mute_btn.setText("\U0001F507" if self.video.mpv.mute else "\U0001F50A")
        self.show_volume()

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, e):
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            return super().keyPressEvent(e)
        k = e.key()
        if Qt.Key_0 <= k <= Qt.Key_9 and not (e.modifiers() & ~Qt.KeypadModifier):
            self.digit(chr(k))
            return
        action = self.keymap.get(keybinds.event_to_str(e))
        if action is None:
            return super().keyPressEvent(e)
        self.do_action(action)

    def do_action(self, action):
        vol = int(self.video.mpv.volume or 0)
        if action == "ch_up":
            self.step(1)
        elif action == "ch_down":
            self.step(-1)
        elif action == "confirm":
            if self.buf:
                self._commit_number(force=True)
            elif self.pending is not None:
                if self.pending == self.index:  # already on it - just dismiss, don't restart the stream
                    self.pending = None
                    self.show_banner()
                else:
                    self.tune(self.pending)
            else:
                self.show_banner()
        elif action == "back":
            if self.buf:
                self.buf = ""
                self.num_lbl.hide()
            elif self.pending is not None:
                self.pending = None
                self.banner.hide()
            elif self.isFullScreen():
                self.showNormal()
        elif action == "last":
            if self.last_index is not None and self.channels:
                self.tune(self.last_index)
        elif action == "vol_up":
            self.set_volume(vol + 5)
        elif action == "vol_down":
            self.set_volume(vol - 5)
        elif action == "mute":
            self.toggle_mute()
        elif action == "info":
            self.pending = None
            self.show_banner()
        elif action == "guide":
            self.open_guide()
        elif action == "list":
            self.open_list()
        elif action == "fullscreen":
            self.toggle_fullscreen()
        elif action == "retry":
            self.retry_now()
        elif action == "settings":
            self.open_settings()
        elif action == "power":
            self.open_power_menu()

    def closeEvent(self, e):
        self.watch.stop()
        if self.phone:
            self.phone.stop()
        if self.client:
            self.client.close()
            self.client = None
        self.video.shutdown()
        super().closeEvent(e)


def main():
    if sys.platform == "darwin":
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 2)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
    # Read the saved interface scale before QApplication is created: Qt applies
    # QT_SCALE_FACTOR during application initialisation.
    db.init()
    try:
        scale = float(db.get("ui_scale", "1.0"))
    except (TypeError, ValueError):
        scale = 1.0
    scale = min(3.0, max(1.0, scale))
    os.environ["QT_SCALE_FACTOR"] = str(scale)

    app = QApplication(sys.argv)
    app.setApplicationName("Xtream TV")
    locale.setlocale(locale.LC_NUMERIC, "C")  # required by libmpv
    util.init_bridge()
    provision.apply()
    kiosk = os.environ.get("XTREAMTV_KIOSK") == "1" or "--kiosk" in sys.argv
    try:
        win = MainWindow()
    except (ImportError, OSError) as e:
        QMessageBox.critical(
            None, "libmpv not found",
            f"Could not load libmpv:\n{e}\n\nInstall it with:  brew install mpv\n"
            "and launch using run.command.")
        return 1
    if kiosk:
        win.menuBar().hide()
        win.showFullScreen()
    else:
        win.show()
    signal.signal(signal.SIGTERM, lambda *_: app.quit())  # systemd stop / shutdown
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    app.aboutToQuit.connect(win.save_state)
    QTimer.singleShot(200, win.start)
    win.setFocus()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
