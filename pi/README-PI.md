# Xtream TV on a Raspberry Pi 5

The Pi boots straight into the app: no desktop, no login. It needs the **64-bit** Raspberry Pi OS (Lite is best).

## Quick install - one command, about 15 minutes
1. **Flash the OS.** In Raspberry Pi Imager choose *Raspberry Pi 5* > *Raspberry Pi OS (other)* >
   *Raspberry Pi OS Lite (64-bit)*. Click *Edit settings* and set: hostname (for example `xtream-tv`), a
   username/password, **Wi-Fi** (or plan to use Ethernet), your locale, and enable **SSH**. Flash the SD card.
2. **Optional - hands-free setup.** With the card still in your computer, copy `pi/xtreamtv.json.example` to the card's
   boot drive as `xtreamtv.json` and fill it in (Xtream login, GitHub repo, `auto_select` category words so channels
   are chosen for you, Wi-Fi, time zone). The app reads it on first start. Skip this and the Pi walks you through
   setup on screen, including setting it up from your phone.
3. **Power the Pi on, then run one command.** From your computer: `ssh youruser@xtream-tv.local` (your username and
   hostname), then paste this, replacing `YOU/YOUR-REPO` with your GitHub repository (both places):

       curl -fsSL https://raw.githubusercontent.com/YOU/YOUR-REPO/main/pi/install.sh | sudo bash -s -- YOU/YOUR-REPO

That is all. It needs no further input: it downloads the app, installs everything it needs, and **restarts the Pi by
itself**. When the Pi comes back, Xtream TV starts on its own, every time you power on. On a brand-new device the app
first checks the network, then offers phone setup (see *First start* below).

**What the command installs and sets up:** the app (in `/opt/xtream-tv`) with its own Python environment and packages;
the `cage` display compositor and `mpv` video player; Wi-Fi/Ethernet support (NetworkManager) and the permissions the
app's Network page needs; Bluetooth and sound (PipeWire); the boot screen; power-cut protection; the on-screen power
button menu; the system service that starts the app at boot; and your repository name in *Settings > Updates*.

**Good to know**
- Your repository must be **public**. For a private one use this single line instead, with a read-only token:
  `curl -fsSL -H "Authorization: Bearer TOKEN" https://raw.githubusercontent.com/YOU/YOUR-REPO/main/pi/install.sh | sudo GITHUB_TOKEN=TOKEN bash -s -- YOU/YOUR-REPO`
  and, once the app is running, paste the same token into *Settings > Updates > Access token*.
- It is safe to run again, for example to repair an install or change an option. It always downloads the **latest**
  version from GitHub and restarts the Pi. (Don't re-run `./pi/install.sh` from an old copy of the project folder:
  that puts the old version back over anything the app has updated since.)
- Options go after `sudo`, for example `sudo NO_REBOOT=1 WIFI_COUNTRY=US bash -s -- YOU/YOUR-REPO`:

  | Option | Effect |
  |---|---|
  | `NO_REBOOT=1` | Don't restart at the end (restart later with `sudo reboot`) |
  | `WIFI_COUNTRY=US` | Wi-Fi country code (default `AU`; without a country, 5 GHz networks stay hidden) |
  | `TIMEZONE=Australia/Melbourne` | Set the time zone (otherwise use *Settings > Date & Time*) |
  | `BOOT_SPLASH=0` | Skip the boot screen (the app then never installs it by itself either) |
  | `ENABLE_CEC=1` | Keep HDMI-CEC on (default: off, see below) |
  | `PERSIST_LOGS=1` | Keep logs across reboots (default: logs live in RAM to spare the SD card) |
  | `BRANCH=name` | Download a branch other than `main` |
  | `KIOSK_USER=name` | The user the app runs as (default: the user who ran `sudo`) |

- No GitHub, or no internet from the Pi to GitHub? Copy the project folder over (`scp -r turkish-iptv youruser@xtream-tv.local:~/`),
  then on the Pi run `cd ~/turkish-iptv && sudo bash pi/install.sh`. The Pi still needs internet for the system packages.

**If the command stops with a message**

| Message | What to do |
|---|---|
| needs the 64-bit Raspberry Pi OS | Re-flash with *Raspberry Pi OS Lite (64-bit)* |
| No internet connection | Plug in Ethernet or fix Wi-Fi, then run the command again |
| Could not download ... | Check the `YOU/YOUR-REPO` spelling, that the repo is public (or use the private-repo line), and the branch |
| does not look like the Xtream TV project | `main.py` and `pi/install.sh` must be at the top of the repository on GitHub |
| Anything else / the connection dropped | Just run the same command again; it picks up where it can and repairs the rest |

Black screen after the restart? See *Day to day* below.

## Option B - build a flashable image (experimental)
`./pi/build-image.sh` uses pi-gen in Docker to produce a `.img` with everything pre-installed
(user `xtream`, password in `pi/pi-gen/config` - change it, SSH off by default). Slow (30-90 min) and less
battle-tested than the quick install. Set `PIGEN_BRANCH` if the default 64-bit branch name has changed.

## Updating from GitHub
*Settings > Updates*. The repository is already filled in (`https://github.com/MrEggGaming1/turkish-iptv` unless you
changed it, or the quick install was given another one). Press *Check for updates*; if one is found, press *Update now*.
The app restarts itself. Your login, channels and settings are kept.
- **Version:** stays on *Latest (recommended)*. After *Check for updates* the list also holds recent older versions
  (releases, or commits when the source is a branch); pick one and press *Install this version* to go to it.
- **Update source:** *Latest release (recommended)* only offers versions you publish as a GitHub release, and installs
  the code as it was at that release's tag. New commits are not offered until you publish a new release.
  *Latest commit on a branch* offers every new commit on that branch (default `main`).
- **Safety net:** the new version is test-loaded before the app restarts; if it can't start, the old version is put
  back automatically. The previous version is also kept, and *Roll back* restores it.
- A private repo needs a fine-grained, read-only access token pasted into *Access token*.

An update replaces the app's files only. System settings (the service, boot options, permissions) are set by the
install command; re-run it to change them. The boot screen is the exception: the app refreshes it by itself after an
update (see *Boot splash*).

### Set up the repository
1. Create a repo on GitHub and upload the project files at the repo root (`main.py`, `db.py`, ... `pi/`). Afterwards
   check on GitHub that `pi/` and its subfolders (`pi/plymouth/xtream-tv/`) are there: some upload methods skip folders.
2. For release updates: bump `version.py`, then on GitHub use *Releases > Draft a new release*, give it a new tag such
   as `v1.0.6`, and publish. The Pi then offers it as an update.

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
(default `AU`; change it with the `WIFI_COUNTRY` option).
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

## Boot splash
On power-on the Pi shows your logo, a smooth progress bar and short status text ("Checking storage...",
"Connecting to the network...", "Starting Xtream TV...") instead of scrolling boot text. On Power off / Restart it shows
"Saving your place and shutting down..." with the bar draining. It is a Plymouth theme in `pi/plymouth/xtream-tv/`
(`logo.png` is your artwork with feathered edges).
- **Installing from Settings > Updates:** a minute after the app restarts on the new version it installs the needed
  packages (`plymouth`, `plymouth-label`, `initramfs-tools`) and the theme by itself, in the background, when the Pi is
  online. A message says when it is done; the splash appears from the *next* restart. This needs the app's user to be
  allowed `sudo` without a password, which is the default for the first user on Raspberry Pi OS. If it isn't, the app
  shows the one command to run over SSH instead: `sudo bash /opt/xtream-tv/app/pi/setup-boot-splash.sh`.
  It tries at most 3 times per splash version, and only when the shipped theme is newer than the installed one.
- The install command sets it up. Skip it for good with the `BOOT_SPLASH=0` install option
  (that also stops the app installing it by itself).
- The script edits `cmdline.txt` (adds `splash`; the original is saved as `cmdline.txt.xtream-bak`) and sets
  `auto_initramfs=1` in `config.txt`.
- Change the picture: replace `logo.png` in `pi/plymouth/xtream-tv/` and raise the number in `VERSION`; the next update
  re-installs the theme by itself.
- Preview without rebooting (from SSH): `sudo plymouthd; sudo plymouth show-splash; sleep 10; sudo plymouth quit`.
- Broken splash? Remove `splash` from `cmdline.txt` (or restore the `.xtream-bak` copy); the Pi then boots as before.
- The splash hands over to the app when it starts, so there can be a brief black moment before the first channel appears.

## Power button and resuming
- The remote's power button (and the Pi 5's own power button) opens **Power Off / Restart / Cancel** on screen.
  The installer tells Linux to leave that key to the app (`/etc/systemd/logind.conf.d/50-xtream-tv.conf`),
  so nothing shuts down without you choosing it. Pick with the same Up/Down and Enter/OK keys you use on the live TV
  page (whatever you set in Key Bindings); Back or the power button again cancels; the menu closes itself after 20 seconds.
- A long press on the Pi 5's own button still forces a hardware power-off, and you can always use SSH.
- The channel you were watching is saved on every channel change and again before power-off, restart,
  an update, or a system shutdown, then flushed to the SD card. After boot the app resumes on that channel.
- Not sure what your remote's power button sends? Settings > Key Bindings > *Test buttons*, then assign it to
  "Power menu" (the Set up remote wizard now includes a Power button step).
- Changing the logind setting on an already-installed Pi: run the install command again (it restarts the Pi).

## The remote must not switch the TV off
- **HDMI-CEC (the Pi side):** the installer turns CEC off (`nocec` on the `vc4-kms-v3d` overlay in
  `/boot/firmware/config.txt`). The Pi then never sends the TV a power or standby command, and the TV's own remote
  is no longer forwarded to the Pi. Keep CEC with the `ENABLE_CEC=1` install option. On a Pi that is already
  installed, run the install command again with it. Check: `ls /dev/cec*` should find nothing after the reboot.
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
sets the Pi's system time zone. Hands-free: `"timezone"` in `xtreamtv.json`, or the `TIMEZONE` install option.
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
Opt-out: the `PERSIST_LOGS=1` install option keeps logs across reboots. For the strongest protection use good-quality media (an SSD
beats an SD card) or a small UPS; a fully read-only system with a separate data partition is possible but needs
extra setup - ask if you want it.
