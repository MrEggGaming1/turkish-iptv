"""Pi boot splash: after an app update, install/refresh the Plymouth theme if the system allows it.

The app runs as a normal user, so this only works when that user may use `sudo` without a password (the
default for the first user on Raspberry Pi OS). Otherwise the app shows the one command to run by hand."""
import os
import subprocess
import sys
from pathlib import Path

import db

APP_DIR = Path(__file__).resolve().parent
SCRIPT = APP_DIR / "pi" / "setup-boot-splash.sh"
SHIPPED = APP_DIR / "pi" / "plymouth" / "xtream-tv"
INSTALLED = Path("/usr/share/plymouth/themes/xtream-tv")
OPT_OUT = Path("/opt/xtream-tv/no-boot-splash")
MAX_TRIES = 3
MANUAL_CMD = f"sudo bash {SCRIPT}"


def _version(folder):
    try:
        return (Path(folder) / "VERSION").read_text().strip()
    except OSError:
        return ""


def needed():
    """True on a Pi kiosk when the shipped theme is newer than (or missing from) the system."""
    if not (sys.platform.startswith("linux") and os.environ.get("XTREAMTV_KIOSK") == "1"):
        return False
    shipped = _version(SHIPPED)
    return bool(shipped) and SCRIPT.exists() and not OPT_OUT.exists() and shipped != _version(INSTALLED)


def tries_key():
    return "splash_tries_" + _version(SHIPPED)


def tries():
    try:
        return int(db.get(tries_key(), "0") or 0)
    except ValueError:
        return 0


def give_up():
    db.put(tries_key(), str(MAX_TRIES))


def install():
    """Run the setup script with sudo. Returns (state, detail): state is 'ok', 'nosudo' or 'fail'."""
    try:
        if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10).returncode != 0:
            return "nosudo", ""
    except (OSError, subprocess.SubprocessError):
        return "nosudo", ""
    try:
        # nice: apt and the initramfs rebuild must not make the video stutter
        r = subprocess.run(["sudo", "-n", "nice", "-n", "19", "/bin/bash", str(SCRIPT)],
                           capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return "fail", "it took too long"
    if r.returncode == 0:
        return "ok", ""
    lines = [ln.replace("[boot-splash] ", "") for ln in (r.stdout + r.stderr).strip().splitlines() if ln.strip()]
    return "fail", (lines[-1] if lines else f"exit code {r.returncode}")[:120]
