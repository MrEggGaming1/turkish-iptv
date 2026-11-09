"""System and player diagnostics: temperature, throttling, memory, network, decoder, power-cut protection."""
import html
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import db
from version import __version__

START = time.time()
LEVEL_COLOR = {"ok": "#4cd08a", "warn": "#f0b429", "bad": "#ff6b6b", "info": "#cfd5ea"}
THROTTLE_BITS = {0: "Under-voltage right now", 1: "CPU speed capped right now", 2: "Throttled right now",
                 3: "Temperature limit reached right now", 16: "Under-voltage has happened",
                 17: "CPU speed cap has happened", 18: "Throttling has happened",
                 19: "Temperature limit has happened"}


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _read(path):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def _human(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h}h {m}m"


def _gb(n):
    return f"{n / 1024 ** 3:.1f} GB"


def protection():
    """Checks for the power-cut safeguards the Pi installer sets up. [(label, ok, detail)]"""
    out = []
    volatile = os.path.exists("/etc/systemd/journald.conf.d/50-xtream-volatile.conf") or not os.path.isdir("/var/log/journal")
    out.append(("Logs kept in RAM (no constant card writes)", volatile, ""))
    swaps = _read("/proc/swaps").splitlines()
    out.append(("Swap file off (fewer card writes)", len(swaps) <= 1, ""))
    out.append(("Filesystem auto-repair at boot", "fsck.repair=yes" in _read("/proc/cmdline"), ""))
    wd = _run(["systemctl", "show", "-p", "RuntimeWatchdogUSec", "--value"])
    out.append(("Hardware watchdog (reboots if frozen)", os.path.exists("/dev/watchdog") and wd not in ("", "0", "infinity"), wd))
    out.append(("Settings database healthy", db.healthy(), ""))
    out.append(("Settings backup file present", db.BACKUP_PATH.exists(), ""))
    return out


def collect(player=None):
    """Return [(section title, [(label, value, level)])]. `player` is a dict supplied by the app."""
    linux = sys.platform.startswith("linux")
    sections = []

    app = [("Version", __version__, "info"),
           ("App running for", _human(time.time() - START), "info"),
           ("Data folder", str(db.data_dir()), "info")]
    rec = db.get("recovered_at", "")
    if rec:
        app.append(("Last database recovery", time.strftime("%d %b %Y %H:%M", time.localtime(int(rec))) +
                    " (damage from an unexpected power loss was repaired)", "warn"))
    else:
        app.append(("Database recoveries", "none", "ok"))
    sections.append(("App", app))

    sysrows = []
    model = _read("/proc/device-tree/model").replace("\x00", "")
    if model:
        sysrows.append(("Model", model, "info"))
    up = _read("/proc/uptime").split()
    if up:
        sysrows.append(("System uptime", _human(float(up[0])), "info"))
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    if temp.isdigit():
        t = int(temp) / 1000
        sysrows.append(("CPU temperature", f"{t:.1f} \u00B0C", "bad" if t >= 80 else "warn" if t >= 70 else "ok"))
    thr = _run(["vcgencmd", "get_throttled"])
    if "=" in thr:
        try:
            val = int(thr.split("=")[1], 16)
            now_bits = [THROTTLE_BITS[b] for b in (0, 1, 2, 3) if val & (1 << b)]
            past_bits = [THROTTLE_BITS[b] for b in (16, 17, 18, 19) if val & (1 << b)]
            if not val:
                sysrows.append(("Power / throttling", "all good", "ok"))
            else:
                if now_bits:
                    sysrows.append(("Power / throttling (now)", "; ".join(now_bits), "bad"))
                if past_bits:
                    sysrows.append(("Power / throttling (since boot)", "; ".join(past_bits), "warn"))
        except ValueError:
            pass
    try:
        l1, l5, l15 = os.getloadavg()
        sysrows.append(("CPU load (1 / 5 / 15 min)", f"{l1:.2f} / {l5:.2f} / {l15:.2f}", "info"))
    except (OSError, AttributeError):
        pass
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            mem[k] = int(v.split()[0]) * 1024
    if "MemTotal" in mem and "MemAvailable" in mem:
        free = mem["MemAvailable"]
        sysrows.append(("Memory free", f"{_gb(free)} of {_gb(mem['MemTotal'])}", "warn" if free < 300 * 1024 ** 2 else "ok"))
    try:
        du = shutil.disk_usage("/")
        sysrows.append(("Storage free", f"{_gb(du.free)} of {_gb(du.total)}", "warn" if du.free < 1024 ** 3 else "ok"))
    except OSError:
        pass
    sections.append(("System", sysrows))

    tz = os.environ.get("TZ") or _read("/etc/timezone") or time.tzname[0]
    timerows = [("Local time", time.strftime("%a %d %b %Y  %H:%M:%S"), "info"), ("Time zone", tz, "info")]
    sync = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) if linux else ""
    if sync:
        timerows.append(("Clock synchronised", "yes" if sync == "yes" else "no - the guide times may be wrong",
                         "ok" if sync == "yes" else "warn"))
    sections.append(("Time", timerows))

    netrows = []
    try:
        import netmgr
        netrows.append(("Internet", "reachable" if netmgr.internet() else "not reachable",
                        "ok" if netmgr.internet() else "bad"))
        if netmgr.available():
            st = netmgr.status()
            for key, name in (("ethernet", "Ethernet"), ("wifi", "Wi-Fi")):
                d = st.get(key)
                if not d:
                    continue
                up_ = d["state"] == "connected"
                extra = (f"  {d['ip']}" if d.get("ip") else "") + (f"  signal {d['signal']}%" if d.get("signal") is not None else "")
                netrows.append((name, ("connected" + extra) if up_ else d["state"], "ok" if up_ else "info"))
    except Exception:
        pass
    sections.append(("Network", netrows))

    if player:
        prow = []
        order = [("Playing", "channel"), ("State", "state"), ("Video", "video"), ("Decoder", "decoder"),
                 ("Dropped frames", "drops"), ("Buffer", "buffer"), ("Stream format", "ext"),
                 ("Reconnect attempts", "attempts")]
        for label, key in order:
            if player.get(key) not in (None, ""):
                lvl = "warn" if key == "drops" and str(player[key]).split()[0].isdigit() and int(str(player[key]).split()[0]) > 100 else "info"
                prow.append((label, str(player[key]), lvl))
        sections.append(("Player", prow))

    if linux:
        sections.append(("Power-cut protection",
                         [(lbl, ("on" if ok else "off") + (f"  ({d})" if d else ""), "ok" if ok else "warn")
                          for lbl, ok, d in protection()]))
        log = _run(["journalctl", "-u", "xtream-tv", "-n", "18", "--no-pager", "-o", "short-iso"], timeout=6)
        lines = [ln[:160] for ln in log.splitlines() if ln.strip()] or ["(not available here)"]
        sections.append(("Recent log", [("", ln, "info") for ln in lines]))
    return sections


def to_text(sections):
    out = [f"Xtream TV diagnostics - {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    for title, rows in sections:
        out.append(f"\n[{title}]")
        for label, value, _lvl in rows:
            out.append(f"{label + ': ' if label else ''}{value}")
    return "\n".join(out) + "\n"


def to_html(sections):
    parts = []
    for title, rows in sections:
        parts.append(f'<p style="margin:14px 0 4px;color:#aab1c8;font-size:12px;font-weight:600;">{html.escape(title.upper())}</p>')
        parts.append('<table cellspacing="0" cellpadding="3">')
        for label, value, lvl in rows:
            c = LEVEL_COLOR.get(lvl, "#cfd5ea")
            if label:
                parts.append(f'<tr><td style="color:#8b92a8;padding-right:18px;white-space:nowrap">{html.escape(label)}</td>'
                             f'<td style="color:{c}">{html.escape(value)}</td></tr>')
            else:
                parts.append(f'<tr><td colspan="2" style="color:#8b92a8;font-family:Menlo,monospace;font-size:11px">{html.escape(value)}</td></tr>')
        parts.append("</table>")
    return "".join(parts)
