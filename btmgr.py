"""Bluetooth audio control through BlueZ (bluetoothctl). Linux / Raspberry Pi only."""
import queue
import re
import shutil
import subprocess
import sys
import threading
import time


class BtError(Exception):
    pass


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02]")
MAC = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"


def available():
    return sys.platform.startswith("linux") and shutil.which("bluetoothctl") is not None


def _run(args, timeout=15):
    try:
        r = subprocess.run(["bluetoothctl"] + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BtError("Bluetooth did not answer in time.")
    return ANSI.sub("", (r.stdout or "") + (r.stderr or ""))


def powered():
    out = _run(["show"])
    if "no default controller" in out.lower() or "Controller" not in out:
        raise BtError("No Bluetooth adapter found.")
    m = re.search(r"Powered:\s*(yes|no)", out)
    return bool(m and m.group(1) == "yes")


def set_power(on):
    if on:
        try:
            subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=5)
        except Exception:
            pass
    out = _run(["power", "on" if on else "off"])
    if "succeeded" not in out.lower() and "Failed" in out:
        raise BtError("Could not turn Bluetooth " + ("on" if on else "off") + ". " + out.strip().splitlines()[-1])


def info(mac):
    out = _run(["info", mac])
    d = {"mac": mac, "name": mac, "paired": False, "connected": False, "trusted": False, "audio": False}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Alias:") or (line.startswith("Name:") and d["name"] == mac):
            d["name"] = line.split(":", 1)[1].strip() or mac
        elif line.startswith("Paired:"):
            d["paired"] = line.endswith("yes")
        elif line.startswith("Connected:"):
            d["connected"] = line.endswith("yes")
        elif line.startswith("Trusted:"):
            d["trusted"] = line.endswith("yes")
        elif line.startswith("Icon:") and "audio" in line:
            d["audio"] = True
        elif line.startswith("UUID:") and ("Audio Sink" in line or "Headset" in line or "Handsfree" in line):
            d["audio"] = True
    return d


def devices():
    out = _run(["devices"])
    found = []
    for line in out.splitlines():
        m = re.match(r"Device (%s)\s*(.*)" % MAC, line.strip())
        if m:
            found.append(m.group(1))
    result = [info(mac) for mac in found]
    return sorted(result, key=lambda d: (not d["connected"], not d["paired"], not d["audio"], d["name"].lower()))


class _Session:
    """A bluetoothctl process we type commands into and read answers from."""

    def __init__(self):
        self.p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.q = queue.Queue()
        self.buf = ""
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.q.put(ANSI.sub("", line))

    def send(self, text):
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()

    def wait(self, ok, bad, timeout):
        """Wait for a line matching `ok` (True) or `bad` (False). None on timeout."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            self.buf += line
            if any(re.search(p, line, re.I) for p in bad):
                return False
            if any(re.search(p, line, re.I) for p in ok):
                return True
        return None

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=4)
        except Exception:
            self.p.kill()


def scan(seconds=8):
    s = _Session()
    try:
        s.send("scan on")
        time.sleep(seconds)
        s.send("scan off")
    finally:
        s.close()
    return devices()


def pair_connect(mac, timeout=40):
    """Pair (if needed), trust and connect. The device must be in pairing mode."""
    d = info(mac)
    s = _Session()
    try:
        s.send("agent NoInputNoOutput")
        s.send("default-agent")
        if not d["paired"]:
            s.send(f"pair {mac}")
            r = s.wait([r"Pairing successful", r"AlreadyExists", r"already exists"],
                       [r"Failed to pair", r"AuthenticationFailed", r"not available"], timeout)
            if r is not True:
                raise BtError("Pairing failed. Put the device in pairing mode, keep it close, and try again."
                              if r is False else "Pairing timed out. Put the device in pairing mode and try again.")
        s.send(f"trust {mac}")
        s.wait([r"trust succeeded"], [r"Failed"], 6)
        s.send(f"connect {mac}")
        r = s.wait([r"Connection successful"], [r"Failed to connect"], timeout)
        if r is not True:
            raise BtError("Paired, but could not connect. Check the device is on and in range.")
    finally:
        s.close()


def disconnect(mac):
    _run(["disconnect", mac], timeout=15)


def forget(mac):
    out = _run(["remove", mac])
    if "not available" in out.lower():
        raise BtError("That device is not known.")
