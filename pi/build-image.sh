#!/bin/bash
# EXPERIMENTAL: build a flashable .img with pi-gen (needs Docker and ~15 GB free; takes 30-90 min).
# Usage:  ./pi/build-image.sh            (uses PIGEN_BRANCH, default bookworm-arm64)
# The 64-bit userland is required (PySide6 has no 32-bit ARM wheels).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
BRANCH="${PIGEN_BRANCH:-bookworm-arm64}"
WORK="$HERE/.build"
command -v docker >/dev/null || { echo "Docker is required: https://www.docker.com/products/docker-desktop"; exit 1; }
mkdir -p "$WORK"
if [ ! -d "$WORK/pi-gen" ]; then
  git clone --depth 1 -b "$BRANCH" https://github.com/RPi-Distro/pi-gen.git "$WORK/pi-gen" || {
    echo "Could not clone pi-gen branch '$BRANCH'. See the branch list at https://github.com/RPi-Distro/pi-gen"
    echo "and re-run with PIGEN_BRANCH=<a 64-bit branch>."; exit 1; }
fi
cd "$WORK/pi-gen"
rm -rf stage-xtream
cp -R "$HERE/pi-gen/stage-xtream" stage-xtream
mkdir -p stage-xtream/00-xtream/files/app
rsync -a --exclude .venv --exclude .git --exclude __pycache__ --exclude 'pi/.build' "$PROJECT/" stage-xtream/00-xtream/files/app/
cp "$HERE/pi-gen/config" config
touch stage2/SKIP_IMAGES   # don't also export the plain Lite image
./build-docker.sh
echo; echo "Image(s) in: $WORK/pi-gen/deploy   (flash with Raspberry Pi Imager > Use custom)"
