#!/bin/bash -e
install -d "${ROOTFS_DIR}/tmp/xtream-src"
cp -R files/app/. "${ROOTFS_DIR}/tmp/xtream-src/"
on_chroot << CHROOT
KIOSK_USER=${FIRST_USER_NAME} XTREAM_CHROOT=1 bash /tmp/xtream-src/pi/install.sh
CHROOT
rm -rf "${ROOTFS_DIR}/tmp/xtream-src"
