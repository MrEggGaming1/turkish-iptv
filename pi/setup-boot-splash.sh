#!/bin/bash
# Installs / updates the Xtream TV boot splash (Plymouth theme). Safe to run again and again. Needs root:
#   sudo /opt/xtream-tv/app/pi/setup-boot-splash.sh
# The app runs this by itself after an update when the system allows it; install.sh runs it too.
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo."; exit 1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRCTHEME="$HERE/plymouth/xtream-tv"
THEME="${XTV_THEME_DIR:-/usr/share/plymouth/themes/xtream-tv}"
BOOT="${XTV_BOOT_DIR:-/boot/firmware}"; [ -d "$BOOT" ] || BOOT=/boot
CMD="$BOOT/cmdline.txt"
CFG="$BOOT/config.txt"
say() { echo "[boot-splash] $*"; }

[ -f "$SRCTHEME/xtream-tv.plymouth" ] || { say "theme files are missing from $SRCTHEME"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

# 1. packages (only touches apt when something is missing)
need=()
for p in plymouth initramfs-tools; do dpkg -s "$p" >/dev/null 2>&1 || need+=("$p"); done
dpkg -s plymouth-label >/dev/null 2>&1 || need_label=1
if [ "${#need[@]}" -gt 0 ] || [ "${need_label:-0}" = "1" ]; then
  say "installing packages: ${need[*]:-} ${need_label:+plymouth-label}"
  apt-get update || say "apt update failed (continuing with what is cached)"
  if [ "${#need[@]}" -gt 0 ]; then
    apt-get install -y --no-install-recommends "${need[@]}" || { say "could not install ${need[*]} (no internet?)"; exit 2; }
  fi
  # the status text needs this one; the logo and bar still work without it
  [ "${need_label:-0}" = "1" ] && { apt-get install -y --no-install-recommends plymouth-label || say "plymouth-label unavailable: status text may not show"; }
fi
command -v plymouth-set-default-theme >/dev/null 2>&1 || { say "plymouth is not installed"; exit 2; }

# 2. theme files (VERSION goes last, so an interrupted run is simply retried next time)
mkdir -p "$THEME"
for f in "$SRCTHEME"/*; do [ "$(basename "$f")" = "VERSION" ] || cp -f "$f" "$THEME"/; done

# 3. make it the default and rebuild the initramfs so it shows early in boot
if ! plymouth-set-default-theme -R xtream-tv; then
  plymouth-set-default-theme xtream-tv || true
  update-initramfs -u || { say "could not rebuild the initramfs"; exit 3; }
fi

# 4. boot options (cmdline.txt is backed up first: a bad one stops the Pi booting)
if [ -f "$CMD" ]; then
  [ -f "$CMD.xtream-bak" ] || cp "$CMD" "$CMD.xtream-bak"
  for opt in quiet splash plymouth.ignore-serial-consoles; do
    grep -qw -- "$opt" "$CMD" || sed -i "1 s/\$/ $opt/" "$CMD"
  done
fi
if [ -f "$CFG" ] && ! grep -q '^auto_initramfs=1' "$CFG"; then echo 'auto_initramfs=1' >> "$CFG"; fi

cp -f "$SRCTHEME/VERSION" "$THEME/VERSION"
say "done - the new boot screen shows from the next restart"
exit 0
