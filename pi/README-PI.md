# Xtream TV on a Raspberry Pi 5

The Pi boots straight into the app: no desktop, no login. Needs the **64-bit** Raspberry Pi OS.

## Option A - installer (recommended)
1. **Flash the OS.** In Raspberry Pi Imager choose *Raspberry Pi 5* > *Raspberry Pi OS Lite (64-bit)*.
   Click *Edit settings* and set: hostname, a username/password, **Wi-Fi** (or use Ethernet), your locale,
   and enable **SSH**. Flash the SD card.
2. **Optional hands-free setup.** With the card still in your Mac, copy `pi/xtreamtv.json.example` to the
   card's boot drive as `xtreamtv.json` and fill it in (Xtream login, GitHub repo, and `auto_select`
   category words so channels are chosen for you). The app reads it on first start.
3. **Boot the Pi, then from your Mac:**
   ```
   ssh youruser@xtream-tv.local
   sudo apt update && sudo apt install -y git
   git clone https://github.com/YOU/YOUR-REPO.git xtreamtv
   cd xtreamtv && sudo ./pi/install.sh
   sudo reboot
   ```
   (No GitHub repo yet? Copy the project folder over with `scp -r XtreamTV youruser@xtream-tv.local:~/xtreamtv`.)
4. After the reboot it starts Xtream TV by itself every time you power on.

## Option B - build a flashable image (experimental)
`./pi/build-image.sh` uses pi-gen in Docker to produce a `.img` with everything pre-installed
(user `xtream`, password in `pi/pi-gen/config` - change it, SSH off by default). Slow (30-90 min) and less
battle-tested than Option A. Set `PIGEN_BRANCH` if the default 64-bit branch name has changed.

## Updating from GitHub
Settings > Updates: enter `owner/repo`, choose *Latest release* or a branch, press *Check for updates*,
then *Update now*. The previous version is kept; *Roll back* restores it. The app restarts itself.
Private repo? Create a fine-grained, read-only token for that repo and paste it in.

### Set up the repository
1. Create a repo on GitHub and upload the project files at the repo root (`main.py`, `db.py`, ... `pi/`).
2. For release updates: bump `version.py`, then on GitHub use *Releases > Draft a new release*, give it a new
   tag such as `v0.3.0`, and publish. The Pi offers it as an update. (Branch mode instead follows every
   commit on the branch you choose.)

## Day to day
- Remote: the BOXPUT dongle just works. Settings > Key Bindings > *Set up remote* teaches the buttons.
- Settings > Updates has *Restart app*, *Reboot* and *Power off*.
- Logs: `journalctl -u xtream-tv -b`  |  Stop the kiosk to get a shell on screen: `sudo systemctl stop xtream-tv`.
- Black screen after boot? The display GPU is auto-detected in `/opt/xtream-tv/start-cage.sh`; check the
  log above for a Qt/EGL error.
- Pi 5 has no hardware H.264 decoder: 1080p plays fine in software; 4K or heavy streams may stutter.

## Network (Settings > Network)
Ethernet connects by itself. For Wi-Fi: Settings > Network lists nearby networks - highlight one, press OK and
type the password with the on-screen keyboard (arrows move, OK types, the remote's Back button cancels, its
delete button erases). *Join hidden network*, *Forget selected*, *Turn Wi-Fi off* and *Test internet* are on the
same page. The installer sets up NetworkManager, the permissions the app needs, and the Wi-Fi country
(default `AU`; use `sudo WIFI_COUNTRY=US ./pi/install.sh` to change it).
Hands-free option: add `"wifi_ssid"` and `"wifi_password"` to `xtreamtv.json` - the Pi joins that network on first start.
Every text box in Settings opens the same on-screen keyboard when you press OK/Enter on it.

## 4K live TV
Settings > Playback > *Video decoder* and *4K / high-bitrate mode* (on by default on the Pi).
- **HEVC / H.265 4K** is decoded by the Pi 5's hardware decoder, up to 4K 60 fps. The app tries the Pi's
  V4L2 hardware path first and falls back to software automatically; the quality badge shows **HW** or **SW**.
- **H.264 4K** has no hardware decoder on the Pi 5, so the CPU does it: fine up to ~30 fps, 4K 60 fps may stutter.
  The 4K mode uses faster software settings and a bigger buffer to help. Prefer your provider's HEVC variant
  of a channel where there is one.
- Use **Ethernet** for 4K (roughly 25-40 Mbps per stream) and a 4K-capable HDMI cable/TV.
- Check the hardware decoder exists: `v4l2-ctl --list-devices` should list `rpi-hevc-dec`. If the badge says SW on an
  HEVC stream, make sure you installed on Raspberry Pi OS (its mpv/ffmpeg include the Pi decoder support), and see
  `journalctl -u xtream-tv -b` for mpv's `hwdec` messages.

## Power button and resuming
- The remote's power button (and the Pi 5's own power button) opens **Power Off / Restart / Cancel** on screen.
  The installer tells Linux to leave that key to the app (`/etc/systemd/logind.conf.d/50-xtream-tv.conf`),
  so nothing shuts down without you choosing it. Pick with the D-pad, OK to confirm; Back or the power
  button again cancels; the menu closes itself after 20 seconds.
- A long press on the Pi 5's own button still forces a hardware power-off, and you can always use SSH.
- The channel you were watching is saved on every channel change and again before power-off, restart,
  an update, or a system shutdown, then flushed to the SD card. After boot the app resumes on that channel.
- Not sure what your remote's power button sends? Settings > Key Bindings > *Test buttons*, then assign it to
  "Power menu" (the Set up remote wizard now includes a Power button step).
- Changing the logind setting on an already-installed Pi: re-run `sudo ./pi/install.sh`, then reboot.

## The remote must not switch the TV off
- **HDMI-CEC (the Pi side):** the installer turns CEC off (`nocec` on the `vc4-kms-v3d` overlay in
  `/boot/firmware/config.txt`). The Pi then never sends the TV a power or standby command, and the TV's own remote
  is no longer forwarded to the Pi. Keep CEC with `sudo ENABLE_CEC=1 ./pi/install.sh`. On a Pi that is already
  installed, re-run the installer and reboot. Check: `ls /dev/cec*` should find nothing after the reboot.
- **Infrared (the remote's side):** the BOXPUT remote can also transmit infrared ("IR learning"). If a button was
  taught a TV code, it beams that to the TV no matter what the Pi does. Clear the learned code (see the remote's
  manual) or put the Pi's Power menu on a different button (Settings > Key Bindings).
- **The TV's own setting:** if the TV switches off a minute after the Pi's picture disappears (during a restart or
  power-off), that is the TV's "no signal auto power off" - turn it off in the TV's menu.

### Binding the Power menu to any key
Settings > Key Bindings > *Power menu*: click a slot and press the remote's power button. This action accepts any
key - including digits, Ctrl/Cmd combinations and remote buttons Qt has no name for (stored as "Scan <number>").
The only key it can't take by hand is Esc (Esc cancels the capture); the *Set up remote* wizard can record it.
While you are typing in a text box a bound *letter* key types normally instead of opening the menu.

## First start, phone setup and settings from your phone
On a brand-new device the app first checks the network:
1. **No internet yet** -> it opens *Settings > Network* so you can join Wi-Fi (or plug in Ethernet). The
   *Phone Setup* option stays hidden until the internet is confirmed.
2. **Online** -> it opens *Phone Setup*: a QR code and a web address. Scan it with your phone (same Wi-Fi), enter your
   Xtream login, pick channels, set the time zone and options - changes show up on the TV as you make them.
   Or choose *Set up on this device instead*. Press *Done - start watching* when finished.
Later, *Settings > Phone Setup* does the same any time (while you're online). The page only runs while that screen is
open, needs the secret code in the QR, and switches itself off after 20 minutes. It needs the `qrcode` package
(installed automatically); without it you still get the web address in text.

## Date & Time
*Settings > Date & Time*: pick your time zone (searchable) and 12/24-hour clock. The app uses it straight away and also
sets the Pi's system time zone. Hands-free: `"timezone"` in `xtreamtv.json`, or `sudo TIMEZONE=Australia/Melbourne ./pi/install.sh`.
The clock is set from the internet automatically.

## Bluetooth audio
*Settings > Bluetooth*: turn Bluetooth on/off (the choice is remembered after a reboot), scan, pair and connect speakers
or headphones (put them in pairing mode first), disconnect or forget them, and pick the audio output
(*Automatic* switches to the Bluetooth device when it connects). If your remote uses Bluetooth rather than its USB
dongle, turning Bluetooth off disconnects it too.

## Diagnostics
*Settings > Diagnostics* (and the *Info* tab of the phone page): CPU temperature, power/throttling warnings, memory and
storage, network, time sync, the video decoder in use (HW/SW), dropped frames, buffer, power-cut protection status and
the latest log lines. *Save report* writes `diagnostics.txt` to the app's data folder.

## Power-cut protection
Unplugging a Pi can damage the SD card; nothing removes that risk completely, but the installer reduces it a lot:
- the app's database writes are fully synced to disk, and the settings + chosen channels are also kept in a second,
  atomically written backup file; if the database is ever damaged it is set aside and rebuilt from that backup on the
  next start (you'll see "Settings recovered")
- logs live in RAM, swap is off, `/tmp` is in RAM, no access-time writes
- the filesystem is checked and repaired at every boot (`fsck.repair=yes`)
- the hardware watchdog reboots the Pi if it ever freezes
- the channel you were watching is saved on every change and before power-off/restart
Opt-outs: `PERSIST_LOGS=1` keeps logs across reboots. For the strongest protection use good-quality media (an SSD
beats an SD card) or a small UPS; a fully read-only system with a separate data partition is possible but needs
extra setup - ask if you want it.
