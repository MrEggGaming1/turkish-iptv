#!/bin/bash
# Xtream TV - Raspberry Pi 5 appliance installer.
# On a fresh Raspberry Pi OS Lite (64-bit) this is ONE command - it downloads the app, installs everything it
# needs and restarts the Pi, which then boots straight into Xtream TV:
#   curl -fsSL https://raw.githubusercontent.com/YOU/YOUR-REPO/main/pi/install.sh | sudo bash -s -- YOU/YOUR-REPO
# From a copy of the project folder instead:   sudo bash pi/install.sh
# Options (put them after sudo, e.g.  sudo NO_REBOOT=1 bash -s -- YOU/YOUR-REPO):
#   NO_REBOOT=1  BOOT_SPLASH=0  ENABLE_CEC=1  PERSIST_LOGS=1  WIFI_COUNTRY=US  TIMEZONE=Australia/Melbourne
#   BRANCH=main (which branch to download)  GITHUB_TOKEN=... (private repository)  KIOSK_USER=name
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run this with sudo (put 'sudo' in front of 'bash', or 'sudo bash pi/install.sh')."; exit 1
fi
if [ "$(uname -m)" != "aarch64" ]; then
  echo "This needs the 64-bit Raspberry Pi OS (aarch64). Detected: $(uname -m)"
  echo "In Raspberry Pi Imager choose: Raspberry Pi OS (other) > Raspberry Pi OS Lite (64-bit)."; exit 1
fi
if [ "${XTREAM_CHROOT:-0}" != "1" ] && ! getent hosts deb.debian.org >/dev/null 2>&1 \
   && ! getent hosts archive.raspberrypi.com >/dev/null 2>&1; then
  echo "No internet connection. Connect the Pi to Ethernet or Wi-Fi and run this again."; exit 1
fi

# ---- one-command install: piped from curl there is no project folder next to this script, so download it first
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then HERE="$(cd "$(dirname "$SELF")" && pwd)"; else HERE=""; fi
if [ -z "$HERE" ] || [ ! -f "$HERE/../main.py" ]; then
  REPO="${1:-${REPO:-}}"
  REPO="${REPO#https://github.com/}"; REPO="${REPO%.git}"; REPO="${REPO%/}"
  if [ -z "$REPO" ]; then
    echo "Tell the installer which GitHub repository holds the app, for example:"
    echo "  curl -fsSL https://raw.githubusercontent.com/YOU/YOUR-REPO/main/pi/install.sh | sudo bash -s -- YOU/YOUR-REPO"
    exit 1
  fi
  echo -e "\n\033[1;34m==> Downloading $REPO (${BRANCH:-main})\033[0m"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update </dev/null
  apt-get install -y --no-install-recommends curl ca-certificates tar </dev/null
  XTV_TMP="$(mktemp -d)"
  auth=()
  if [ -n "${GITHUB_TOKEN:-}" ]; then auth=(-H "Authorization: Bearer $GITHUB_TOKEN"); fi
  if ! curl -fsSL --retry 3 "${auth[@]}" -o "$XTV_TMP/app.tgz" \
       "${XTV_TARBALL_URL:-https://api.github.com/repos/$REPO/tarball/${BRANCH:-main}}"; then
    echo "Could not download $REPO (branch ${BRANCH:-main}). Check the name, that the repository is public"
    echo "(or add GITHUB_TOKEN=<read-only token> after sudo), and that the Pi is online."; exit 1
  fi
  mkdir "$XTV_TMP/src"
  tar -xzf "$XTV_TMP/app.tgz" -C "$XTV_TMP/src"
  MAIN="$(find "$XTV_TMP/src" -maxdepth 3 -name main.py -print -quit)"
  if [ -z "$MAIN" ] || [ ! -f "$(dirname "$MAIN")/pi/install.sh" ]; then
    echo "$REPO does not look like the Xtream TV project (main.py and pi/install.sh were not found)."; exit 1
  fi
  export REPO XTV_TMP
  exec bash "$(dirname "$MAIN")/pi/install.sh"
fi
exec </dev/null   # nothing below should ever read from the terminal or the pipe

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT=/opt/xtream-tv
KUSER="${KIOSK_USER:-${SUDO_USER:-}}"
if [ -z "$KUSER" ] || [ "$KUSER" = "root" ]; then KUSER=xtream; fi
log() { echo -e "\n\033[1;34m==> $*\033[0m"; }

echo "Installing Xtream TV. This takes about 5-15 minutes and needs no input - the Pi restarts itself at the end."
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  cage mpv python3 python3-venv python3-pip rsync ca-certificates sudo \
  libegl1 libgles2 libgl1 libegl-mesa0 libgbm1 libgl1-mesa-dri \
  libxkbcommon0 libwayland-client0 libwayland-cursor0 libwayland-egl1 \
  libfontconfig1 libdbus-1-3 libglib2.0-0 fonts-dejavu-core \
  network-manager wireless-regdb iw rfkill tzdata
# nice-to-have packages: skipped quietly if this release names them differently
for p in fonts-noto-color-emoji pipewire pipewire-pulse wireplumber libopengl0 libinput10 libxcb1 v4l-utils polkitd policykit-1 \
         bluez libspa-0.2-bluetooth libraspberrypi-bin; do
  apt-get install -y --no-install-recommends "$p" || echo "(optional package $p skipped)"
done

log "Setting up user '$KUSER'"
if ! id "$KUSER" >/dev/null 2>&1; then useradd -m -s /bin/bash "$KUSER"; fi
for g in video render input audio plugdev netdev bluetooth systemd-journal; do
  if getent group "$g" >/dev/null; then usermod -aG "$g" "$KUSER"; fi
done

log "Installing the app to $ROOT/app"
mkdir -p "$ROOT/app"
if [ "$SRC" != "$ROOT/app" ]; then
  rsync -a --delete --exclude '.venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
        --exclude 'pi/.build' "$SRC/" "$ROOT/app/"
fi
# remember the GitHub repository so Settings > Updates is already filled in (skipped if you supplied your own xtreamtv.json)
if [ -n "${REPO:-}" ]; then
  BOOTDIR=/boot/firmware; [ -d "$BOOTDIR" ] || BOOTDIR=/boot
  if [ -d "$BOOTDIR" ] && [ ! -f "$BOOTDIR/xtreamtv.json" ]; then
    printf '{\n  "update_repo": "%s"\n}\n' "$REPO" > "$BOOTDIR/xtreamtv.json"
  fi
fi
if [ ! -x "$ROOT/venv/bin/python" ]; then python3 -m venv "$ROOT/venv"; fi
"$ROOT/venv/bin/python" -m pip install --upgrade pip
"$ROOT/venv/bin/python" -m pip install --prefer-binary -r "$ROOT/app/requirements.txt"

log "Writing the kiosk launcher and service"
cat > "$ROOT/run-kiosk.sh" <<'EOF'
#!/bin/bash
export XTREAMTV_KIOSK=1
export QT_QPA_PLATFORM=wayland
export QT_WAYLAND_DISABLE_WINDOWDECORATION=1
export PYTHONUNBUFFERED=1
cd /opt/xtream-tv/app
exec /opt/xtream-tv/venv/bin/python main.py
EOF
cat > "$ROOT/start-cage.sh" <<'EOF'
#!/bin/bash
# The Pi 5 has separate render (v3d) and display (vc4) devices: point the compositor at the display one.
for c in /dev/dri/card*; do
  n="$(basename "$c")"
  drv="$(basename "$(readlink -f "/sys/class/drm/$n/device/driver" 2>/dev/null)" 2>/dev/null || true)"
  case "$drv" in *vc4*) export WLR_DRM_DEVICES="$c"; break ;; esac
done
export WLR_NO_HARDWARE_CURSORS=1
# VMs have no real GPU: let the compositor use software rendering there (ignored when a GPU is present)
export WLR_RENDERER_ALLOW_SOFTWARE=1
exec /usr/bin/cage -d -- /opt/xtream-tv/run-kiosk.sh
EOF
chmod 755 "$ROOT/run-kiosk.sh" "$ROOT/start-cage.sh"

cat > /etc/systemd/system/xtream-tv.service <<EOF
[Unit]
Description=Xtream TV
After=systemd-user-sessions.service getty@tty1.service plymouth-quit-wait.service
After=dbus.socket systemd-logind.service
Wants=dbus.socket systemd-logind.service
Conflicts=getty@tty1.service
ConditionPathExists=/dev/tty0
StartLimitIntervalSec=0

[Service]
Type=simple
User=$KUSER
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
ExecStart=$ROOT/start-cage.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

# lets the in-app Reboot / Power off buttons (and, as a fallback, the Network page) work without a password
cat > /etc/sudoers.d/xtream-tv <<EOF
$KUSER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/nmcli, /usr/bin/timedatectl set-timezone *
EOF
chmod 440 /etc/sudoers.d/xtream-tv
visudo -c -q -f /etc/sudoers.d/xtream-tv

log "Network (Wi-Fi / Ethernet) support"
# the app's Network page talks to NetworkManager: allow members of 'netdev' to change connections
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/50-xtream-network.rules <<'EOF'
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 && subject.isInGroup("netdev")) {
    return polkit.Result.YES;
  }
});
EOF
systemctl enable NetworkManager.service || true
rfkill unblock wifi || true
# Wi-Fi needs a regulatory country or 5 GHz networks stay hidden. Override: sudo WIFI_COUNTRY=US bash pi/install.sh
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_wifi_country "${WIFI_COUNTRY:-AU}" || true
fi

# the app updates itself from GitHub, so the user must own its folder
chown -R "$KUSER":"$KUSER" "$ROOT"

log "Bluetooth audio and sound"
systemctl enable bluetooth.service || true
rfkill unblock bluetooth || true
# sound server for the kiosk user's session (Bluetooth speakers/headphones use it)
systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service 2>/dev/null || true

log "Time zone"
# optional: sudo TIMEZONE=Australia/Melbourne bash pi/install.sh   (otherwise set it in the app: Settings > Date & Time)
if [ -n "${TIMEZONE:-}" ] && [ -f "/usr/share/zoneinfo/$TIMEZONE" ]; then
  ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
  echo "$TIMEZONE" > /etc/timezone
fi
systemctl enable systemd-timesyncd.service 2>/dev/null || true

log "Protecting against power cuts"
# 1. Logs live in RAM: no constant writes to the SD card (keep them with: sudo PERSIST_LOGS=1 bash pi/install.sh)
if [ "${PERSIST_LOGS:-0}" != "1" ]; then
  mkdir -p /etc/systemd/journald.conf.d
  cat > /etc/systemd/journald.conf.d/50-xtream-volatile.conf <<'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=50M
EOF
fi
# 2. Scratch space in RAM, no access-time writes
if ! grep -qE '^[[:space:]]*tmpfs[[:space:]]+/tmp[[:space:]]' /etc/fstab; then
  echo 'tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,size=128m 0 0' >> /etc/fstab
fi
sed -i -E '/[[:space:]]ext4[[:space:]]/{/noatime/!s/([[:space:]]ext4[[:space:]]+)([^[:space:]]+)/\1\2,noatime/}' /etc/fstab
# 3. No swap file churning the card
if [ -f /etc/dphys-swapfile ]; then sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=0/' /etc/dphys-swapfile; fi
systemctl disable dphys-swapfile.service 2>/dev/null || true
swapoff -a 2>/dev/null || true
# 4. If the Pi ever freezes, the hardware watchdog reboots it by itself
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/50-xtream-watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
EOF

log "Power button: let the app show its Power Off / Restart menu"
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/50-xtream-tv.conf <<'EOF'
[Login]
HandlePowerKey=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
EOF
systemctl kill -s HUP systemd-logind 2>/dev/null || true

log "Quiet, fast boot"
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
CFG=/boot/firmware/config.txt;  [ -f "$CFG" ] || CFG=/boot/config.txt
if [ -f "$CMD" ]; then
  for opt in quiet loglevel=3 logo.nologo vt.global_cursor_default=0 consoleblank=0 fsck.repair=yes; do
    grep -qw -- "$opt" "$CMD" || sed -i "1 s/\$/ $opt/" "$CMD"
  done
fi
if [ -f "$CFG" ] && ! grep -q '^disable_splash=1' "$CFG"; then echo 'disable_splash=1' >> "$CFG"; fi

log "Boot splash (logo, progress bar, friendly status text)"
# Opt out:  sudo BOOT_SPLASH=0 bash pi/install.sh   (the app then never installs it by itself either)
if [ "${BOOT_SPLASH:-1}" = "1" ]; then
  rm -f "$ROOT/no-boot-splash"
  bash "$SRC/pi/setup-boot-splash.sh" || echo "(boot splash not set up now - the app retries by itself after it starts)"
else
  touch "$ROOT/no-boot-splash"
  echo "(boot splash skipped)"
fi

# HDMI-CEC off: the Pi never switches the TV on/off, and the TV's own remote isn't forwarded to the Pi,
# so only the USB remote controls it. To keep CEC instead:  sudo ENABLE_CEC=1 bash pi/install.sh
if [ -f "$CFG" ] && [ "${ENABLE_CEC:-0}" != "1" ]; then
  sed -i -E '/^dtoverlay=vc4-kms-v3d(,|$)/{/nocec/!s/$/,nocec/}' "$CFG"
fi

systemctl daemon-reload || true
systemctl enable xtream-tv.service
systemctl set-default multi-user.target || true

log "Done"
if [ -n "${XTV_TMP:-}" ]; then rm -rf "$XTV_TMP"; fi
echo "Xtream TV is installed."
echo "If something looks wrong after the restart:   journalctl -u xtream-tv -b"
if [ "${NO_REBOOT:-0}" = "1" ] || [ "${XTREAM_CHROOT:-0}" = "1" ]; then
  echo "Restart the Pi to start it:   sudo reboot"
else
  echo "Restarting in 15 seconds to start it (press Ctrl+C to cancel and restart later with: sudo reboot)..."
  sleep 15
  systemctl reboot || reboot
fi
