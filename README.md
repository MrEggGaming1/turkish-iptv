# Xtream TV

A live TV player for Xtream Codes (IPTV) providers. It runs on a Mac, and on a **Raspberry Pi 5** as an
appliance that boots straight into the app (no desktop, no login), controlled with a USB/Bluetooth remote.

## Install

**Raspberry Pi 5** - flash Raspberry Pi OS Lite (64-bit), then run one command on the Pi. It installs everything
it needs and restarts itself:

    curl -fsSL https://raw.githubusercontent.com/YOU/YOUR-REPO/main/pi/install.sh | sudo bash -s -- YOU/YOUR-REPO

(Replace `YOU/YOUR-REPO` with your GitHub repository.) Full steps, options and troubleshooting: **[pi/README-PI.md](pi/README-PI.md)**.

**Mac** - double-click `run.command` (or run `./run.command`). It needs [Homebrew](https://brew.sh); on the first
run it installs mpv, a Python 3.10-3.13 and a virtualenv with the packages from `requirements.txt` by itself.

## Using it
Everything - settings, channel picking, key bindings - is inside the app window; there are no pop-up windows.

| Key (default) | Remote / alternate | Action |
|---|---|---|
| Up / Down | Page Up / Page Down | Browse to the next / previous channel (starting from the current one) |
| Enter | Select | Switch to the browsed channel |
| Esc | Back | Cancel / back |
| Backspace | - | Last channel |
| Right / Left | Volume Up / Volume Down | Volume up / down |
| M | Volume Mute | Mute |
| I | Info | Channel info |
| G | Guide | TV guide |
| L | - | Channel list |
| F | - | Fullscreen |
| R | - | Retry the stream |
| S | Menu | Settings (also the gear icon, or Cmd+, in the Mac menu) |
| - | Power Off / Sleep | Power menu (Power Off / Restart / Cancel) |

Digits type a channel number. Every key can be changed in *Settings > Key Bindings*, which has two slots per
action: use the second for your remote's buttons (click the slot, then press the button on the remote).
Remotes that act as a keyboard work with no extra setup. The Power menu uses the same Up / Down / Enter keys.

## Settings pages
- **Account** - your provider's server, username and password.
- **Phone Setup** - set the app up from your phone via a QR code (appears once the internet is confirmed).
- **Channels** - pick which channels you want, and their order.
- **Playback** - stream format, video decoder, 4K / high-bitrate mode, and an independent 1x-5x audio boost for
  quiet TV audio (separate from the normal volume).
- **Display** - where the stream-quality badge and the settings / mute buttons sit, a minimalistic mode, the interface
  size, and *Remove from the start of channel names* (0-10 characters, 0 = show full names). That last one hides a
  provider prefix such as `TR| `: with 3, `TR| TRT 1` shows as `TRT 1`. Only the display changes; the stored names
  stay as your provider sends them.
- **Date & Time** - time zone (searchable) and 12/24-hour clock.
- **Key Bindings** - the keys above, plus *Set up remote* and *Test buttons*.
- **Network** - Wi-Fi and Ethernet (Raspberry Pi).
- **Bluetooth** - pair speakers or headphones, choose the audio output (Raspberry Pi).
- **Diagnostics** - temperature, memory, network, video decoder and recent log lines.
- **Updates** - update the app from GitHub (the repository is pre-filled). It installs the latest version by default;
  after *Check for updates* you can pick an older one from the *Version* list. *Roll back* restores the previous
  version. See [pi/README-PI.md](pi/README-PI.md).

## Remote (BOXPUT BPR1S Plus and similar)
Plug the USB dongle in (or pair over Bluetooth): it behaves as a keyboard plus an air mouse, so no drivers are needed.
Open *Settings > Key Bindings > Set up remote* and press each remote button when asked, or *Test buttons* to see
what each one sends. The pointer hides itself when idle and reappears when you move the air mouse. This remote has
no number keys, so change channels with D-pad Up/Down + OK.

## Where data lives
A SQLite file, `cache.db`, holds your settings and channel list. Your login is stored there as plain text, so
protect the device and any backups of it.
- Mac: `~/Library/Application Support/XtreamTV/`
- Raspberry Pi: `~/.local/share/xtreamtv/` in the home folder of the user the app runs as.

## Project layout
`main.py` is the app; `pi/` holds the Raspberry Pi installer, boot-screen theme and image builder.
