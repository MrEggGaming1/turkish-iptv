"""Rebindable keys. Each action has two slots (e.g. keyboard + remote). Digits 0-9 are fixed."""
import json

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence

import db


def _q(name):
    return getattr(Qt, name, None)


def _name(key):
    if key is None:
        return ""
    try:
        return QKeySequence(key).toString(QKeySequence.PortableText)
    except Exception:
        return ""


# (action id, label, default key, default alternate key - typical remote / media keys)
ACTIONS = [
    ("ch_up", "Next channel (browse)", _q("Key_Up"), _q("Key_PageUp")),
    ("ch_down", "Previous channel (browse)", _q("Key_Down"), _q("Key_PageDown")),
    ("confirm", "Confirm / switch to browsed channel", _q("Key_Return"), _q("Key_Select")),
    ("back", "Cancel / back", _q("Key_Escape"), _q("Key_Back")),
    ("last", "Last channel", _q("Key_Backspace"), None),
    ("vol_up", "Volume up", _q("Key_Right"), _q("Key_VolumeUp")),
    ("vol_down", "Volume down", _q("Key_Left"), _q("Key_VolumeDown")),
    ("mute", "Mute", _q("Key_M"), _q("Key_VolumeMute")),
    ("info", "Channel info", _q("Key_I"), _q("Key_Info")),
    ("guide", "TV guide", _q("Key_G"), _q("Key_Guide")),
    ("list", "Channel list", _q("Key_L"), None),
    ("fullscreen", "Fullscreen", _q("Key_F"), None),
    ("retry", "Retry stream", _q("Key_R"), None),
    ("settings", "Settings", _q("Key_S"), _q("Key_Menu")),
    ("power", "Power menu (any key can be used)", _q("Key_PowerOff"), _q("Key_Sleep")),
]
LABELS = {a: label for a, label, _k1, _k2 in ACTIONS}


def defaults():
    return {a: [_name(k1), _name(k2)] for a, _l, k1, k2 in ACTIONS}


def load():
    binds = defaults()
    try:
        stored = json.loads(db.get("keybinds", "{}") or "{}")
    except (ValueError, TypeError):
        stored = {}
    for a, v in stored.items():
        if a not in binds:
            continue
        if isinstance(v, str):  # older single-key format
            binds[a] = [v, binds[a][1]]
        elif isinstance(v, list):
            v = [str(x) for x in v][:2]
            binds[a] = v + [""] * (2 - len(v))
    return binds


def save(binds):
    db.put("keybinds", json.dumps(binds))


def build_keymap(binds):
    km = {}
    for action, keys in binds.items():
        for k in keys:
            if k and k not in km:
                km[k] = action
    return km


def event_to_str(e):
    """Key event -> portable text like 'Up', 'M', 'Volume Mute'. Enter == Return; keypad flag ignored.
    Keys Qt has no name for (some remote buttons) become 'Scan <code>' so they can still be bound."""
    k = e.key()
    if k == Qt.Key_Enter:
        k = Qt.Key_Return
    mods = e.modifiers() & ~Qt.KeypadModifier
    try:
        text = QKeySequence(QKeyCombination(mods, Qt.Key(k))).toString(QKeySequence.PortableText)
    except Exception:
        text = QKeySequence(k).toString(QKeySequence.PortableText)
    if (k == Qt.Key_unknown or not text) and e.nativeScanCode() > 0:
        return f"Scan {e.nativeScanCode()}"
    return text


def to_qt_key(text):
    if not text:
        return None
    try:
        seq = QKeySequence.fromString(text, QKeySequence.PortableText)
        return seq[0].key() if seq.count() else None
    except Exception:
        return None


_PRETTY = {"Return": "Enter", "Up": "\u2191", "Down": "\u2193", "Left": "\u2190", "Right": "\u2192",
           "PgUp": "Page Up", "PgDown": "Page Down"}


def pretty(text):
    return _PRETTY.get(text, text) if text else "Unassigned"


def first_pretty(keys):
    for k in keys or []:
        if k:
            return pretty(k)
    return "Unassigned"
