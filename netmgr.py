"""Wi-Fi / Ethernet control through NetworkManager (nmcli). Linux / Raspberry Pi only."""
import re
import shutil
import socket
import subprocess
import sys
import time


class NetError(Exception):
    pass


def available():
    return sys.platform.startswith("linux") and shutil.which("nmcli") is not None


def _split(line):
    """Split nmcli --terse output: ':' separates fields, '\\:' and '\\\\' are escapes."""
    parts, cur, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


def _run(args, timeout=25):
    r = subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and re.search(r"not authori[sz]ed|insufficient privileges|permission denied",
                                       r.stderr or "", re.I):
        r = subprocess.run(["sudo", "-n", "nmcli"] + args, capture_output=True, text=True, timeout=timeout)
    return r


def _friendly(msg):
    msg = (msg or "").strip()
    last = msg.splitlines()[-1] if msg else "Network command failed."
    low = msg.lower()
    if "secrets were required" in low or "password" in low and "required" in low:
        return "A password is required."
    if "no network with ssid" in low:
        return "That network isn't in range."
    if "activation failed" in low or "(7)" in low or "(4)" in low or "wrong" in low:
        return "Couldn't connect - the password may be wrong, or the network is out of range."
    if "not authori" in low or "insufficient privileges" in low:
        return "Not allowed to change network settings - re-run the installer (pi/install.sh)."
    return last


def _ok(r):
    if r.returncode != 0:
        raise NetError(_friendly(r.stderr or r.stdout))
    return r.stdout


def devices():
    r = _run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    _ok(r)
    out = []
    for line in r.stdout.splitlines():
        p = _split(line)
        if len(p) >= 4:
            out.append({"device": p[0], "type": p[1], "state": p[2], "connection": p[3]})
    return out


def ip_of(device):
    r = _run(["-t", "-f", "IP4.ADDRESS", "device", "show", device])
    for line in r.stdout.splitlines():
        if line.startswith("IP4.ADDRESS"):
            return line.split(":", 1)[1].split("/")[0].strip()
    return ""


def wifi_enabled():
    return _run(["radio", "wifi"]).stdout.strip().lower() == "enabled"


def status():
    devs = devices()
    out = {"wifi": None, "ethernet": None, "wifi_enabled": wifi_enabled()}
    for kind, key in (("wifi", "wifi"), ("ethernet", "ethernet")):
        d = next((x for x in devs if x["type"] == kind), None)
        if not d:
            continue
        info = dict(d)
        info["ip"] = ip_of(d["device"]) if d["state"] == "connected" else ""
        info["signal"] = None
        if kind == "wifi" and d["state"] == "connected":
            r = _run(["-t", "-f", "IN-USE,SIGNAL", "device", "wifi", "list", "ifname", d["device"]])
            for line in r.stdout.splitlines():
                p = _split(line)
                if len(p) >= 2 and p[0].strip() == "*":
                    info["signal"] = int(p[1] or 0)
        out[key] = info
    return out


def known_ssids():
    r = _run(["-t", "-f", "NAME,TYPE", "connection", "show"])
    return {p[0] for p in map(_split, r.stdout.splitlines()) if len(p) >= 2 and "wireless" in p[1]}


def scan():
    _run(["device", "wifi", "rescan"])  # may be refused if scanned a moment ago - that's fine
    time.sleep(2)
    r = _run(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
    _ok(r)
    known = known_ssids()
    best = {}
    for line in r.stdout.splitlines():
        p = _split(line)
        if len(p) < 4 or not p[1].strip():
            continue
        net = {"in_use": p[0].strip() == "*", "ssid": p[1], "signal": int(p[2] or 0),
               "security": "" if p[3] in ("", "--") else p[3], "known": p[1] in known}
        old = best.get(net["ssid"])
        if old is None or net["in_use"] or (not old["in_use"] and net["signal"] > old["signal"]):
            best[net["ssid"]] = net
    return sorted(best.values(), key=lambda n: (not n["in_use"], -n["signal"]))


def connect(ssid, password=None, hidden=False, was_known=True):
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    if hidden:
        args += ["hidden", "yes"]
    r = _run(args, timeout=60)
    if r.returncode != 0:
        if not was_known:  # don't leave a broken profile behind
            _run(["connection", "delete", "id", ssid])
        msg = _friendly(r.stderr or r.stdout)
        if password and msg == "A password is required.":
            msg = "Couldn't connect - the password may be wrong."
        raise NetError(msg)


def forget(ssid):
    _ok(_run(["connection", "delete", "id", ssid]))


def set_wifi(on):
    _ok(_run(["radio", "wifi", "on" if on else "off"]))


def reconnect_ethernet(device):
    _ok(_run(["device", "connect", device], timeout=40))


def internet():
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), 3).close()
            return True
        except OSError:
            continue
    return False
