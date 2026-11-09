# Xtream TV (prototype)

Live TV player for Xtream Codes providers. macOS first; same code targets a Raspberry Pi 5.

## Run on macOS
Double-click `run.command` (or `./run.command`). First run installs mpv (Homebrew), a virtualenv and packages.

## Using it
- Everything (settings, channel picking, key bindings) is inside the app window - no pop-up windows.
- Up/Down previews channels starting from the current one; Enter switches. Digits type a channel number.
- Settings: S, Cmd+, or the gear icon. Pages: Account, Channels, Playback, Display, Key Bindings.
- Key Bindings has two slots per action: use the second for your remote's buttons (click the slot,
  then press the button on the remote). Remotes that act as a keyboard work with no extra setup.

## Where data lives
~/Library/Application Support/XtreamTV/cache.db (SQLite). Credentials are plain text for now.

## Remote (BOXPUT BPR1S Plus and similar)
Plug the USB dongle in (or pair over Bluetooth): it behaves as a keyboard + air mouse, so no drivers are needed.
Open Settings > Key Bindings > "Set up remote" and press each remote button when asked, or "Test buttons"
to see what each one sends. The pointer hides itself when idle and reappears when you move the air mouse.
This remote has no number keys, so change channels with D-pad Up/Down + OK.

## Raspberry Pi appliance and GitHub updates
See pi/README-PI.md: install on a Pi 5 so it boots straight into the app, and update it from your own
GitHub repository via Settings > Updates.
