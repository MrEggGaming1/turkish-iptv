"""Optional first-boot configuration from a file, so a Pi can be set up without a keyboard.

Put xtreamtv.json on the SD card's boot partition (the one that shows up on a Mac/PC), e.g.:
{"server": "http://host:8080", "username": "u", "password": "p",
 "update_repo": "owner/repo", "auto_select": ["Australia", "News"]}
It is applied again only if its contents change."""
import hashlib
import json
from pathlib import Path

import db

PATHS = [Path("/boot/firmware/xtreamtv.json"), Path("/boot/xtreamtv.json"), db.data_dir() / "xtreamtv.json"]
TEXT_KEYS = ("server", "username", "password", "stream_ext", "update_repo", "update_branch",
             "update_mode", "update_token", "wifi_ssid", "wifi_password", "decode_mode", "fourk_mode", "timezone", "clock24")


def apply():
    for p in PATHS:
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if db.get("provision_hash") == digest:
            return False
        try:
            data = json.loads(raw)
        except ValueError:
            return False
        for k in TEXT_KEYS:
            if str(data.get(k, "")).strip():
                db.put(k, str(data[k]).strip())
        terms = data.get("auto_select")
        if isinstance(terms, list) and terms:
            db.put("auto_select", json.dumps([str(t) for t in terms]))
        db.put("provision_hash", digest)
        return True
    return False
