"""Minimal Xtream Codes API client."""
import base64
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

import requests

UA = "Mozilla/5.0 (XtreamTV prototype)"


class XtreamError(Exception):
    pass


def _b64(s):
    if not s:
        return ""
    try:
        return base64.b64decode(s).decode("utf-8", "replace")
    except Exception:
        return str(s)


def parse_xmltv_time(s):
    """'20261105120000 +0100' -> unix timestamp."""
    s = s.strip()
    dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    tz = s[14:].strip()
    off = 0
    if len(tz) == 5 and tz[0] in "+-":
        off = (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60) * (1 if tz[0] == "+" else -1)
    return int(dt.timestamp() - off)


class XtreamClient:
    def __init__(self, server, username, password):
        server = server.strip().rstrip("/")
        if not server.startswith(("http://", "https://")):
            server = "http://" + server
        self.server = server
        self.username = username.strip()
        self.password = password.strip()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    def _api(self, action=None, timeout=45, **params):
        p = {"username": self.username, "password": self.password}
        if action:
            p["action"] = action
        p.update(params)
        r = self.session.get(f"{self.server}/player_api.php", params=p, timeout=timeout)
        try:
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                raise XtreamError("Server did not return valid JSON (check the server URL).")
        finally:
            r.close()

    def close(self):
        """Release pooled HTTP connections when this client is no longer needed."""
        self.session.close()

    def account_info(self):
        data = self._api(timeout=20)
        ui = data.get("user_info") if isinstance(data, dict) else None
        if not ui or str(ui.get("auth")) != "1":
            raise XtreamError("Login failed - check username and password.")
        status = ui.get("status")
        if status and str(status).lower() != "active":
            raise XtreamError(f"Account status: {status}")
        return data

    def live_categories(self):
        data = self._api("get_live_categories")
        if not isinstance(data, list):
            raise XtreamError("Could not load live categories.")
        return data

    def live_streams(self):
        data = self._api("get_live_streams", timeout=120)
        if not isinstance(data, list):
            raise XtreamError("Could not load live channels.")
        return data

    def stream_url(self, stream_id, ext="m3u8"):
        u, p = quote(self.username, safe=""), quote(self.password, safe="")
        return f"{self.server}/live/{u}/{p}/{stream_id}.{ext}"

    def short_epg(self, stream_id, limit=12):
        data = self._api("get_short_epg", timeout=20, stream_id=stream_id, limit=limit)
        out = []
        for it in (data or {}).get("epg_listings", []) or []:
            try:
                out.append((int(it["start_timestamp"]), int(it["stop_timestamp"]),
                            _b64(it.get("title")), _b64(it.get("description"))))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def fetch_xmltv(self, wanted, past=2 * 3600, future=48 * 3600):
        """Stream-parse xmltv.php keeping only wanted channels in a time window."""
        now = time.time()
        with self.session.get(
            f"{self.server}/xmltv.php",
            params={"username": self.username, "password": self.password},
            stream=True, timeout=(10, 120),
        ) as r:
            r.raise_for_status()
            r.raw.decode_content = True
            rows, root = [], None
            for ev, el in ET.iterparse(r.raw, events=("start", "end")):
                if ev == "start":
                    if root is None:
                        root = el
                    continue
                if el.tag == "programme":
                    ch = el.get("channel")
                    if ch in wanted:
                        try:
                            s, e = parse_xmltv_time(el.get("start")), parse_xmltv_time(el.get("stop"))
                        except Exception:
                            s = e = 0
                        if e > now - past and s < now + future:
                            title = (el.findtext("title") or "").strip()
                            desc = (el.findtext("desc") or "").strip()
                            rows.append((ch, s, e, title, desc))
                    if root is not None:
                        root.clear()
            return rows
