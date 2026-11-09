"""On-screen keyboard overlay for remote / D-pad text entry (also accepts a real keyboard)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

ARROWS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)
LETTERS = ["1234567890", "qwertyuiop", "asdfghjkl@", "zxcvbnm._-"]
SYMBOLS = ["1234567890", "!@#$%^&*()", "-_=+[]{}\\|", ";:'\",.<>/?~"]

STYLE = """
QWidget#oskroot { background: rgba(5,6,10,225); }
QWidget#oskcard { background: #171a26; border: 1px solid #2b3047; border-radius: 18px; }
QLabel#osktitle { font-size: 20px; font-weight: 600; color: white; background: transparent; }
QLabel#oskhint { font-size: 12px; color: #8b92a8; background: transparent; }
QLabel#oskdisplay { background: #0e1017; border: 1px solid #2b3047; border-radius: 10px; padding: 12px 16px;
                    font-family: Menlo, monospace; font-size: 22px; color: white; min-height: 34px; }
QPushButton#oskkey { background: #232840; border: 1px solid transparent; border-radius: 8px; font-size: 20px;
                     min-height: 46px; min-width: 38px; padding: 0 8px; color: white; }
QPushButton#oskkey:hover { background: #2c3354; }
QPushButton#oskkey:focus { background: #2d6cdf; border: 1px solid #8bb4ff; }
QPushButton#oskkey[on="true"] { background: #1f3d7a; }
"""


class KeyButton(QPushButton):
    nav = Signal(int)

    def __init__(self, text=""):
        super().__init__(text)
        self.setObjectName("oskkey")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoDefault(False)

    def keyPressEvent(self, e):
        k = e.key()
        if k in ARROWS:
            self.nav.emit(k)
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
        else:
            e.ignore()  # typing, Backspace, Esc and Space are handled by the keyboard overlay


class OnScreenKeyboard(QWidget):
    done = Signal(str)
    cancelled = Signal()

    def __init__(self, parent, title, text="", password=False):
        super().__init__(parent)
        self.setObjectName("oskroot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.setFocusPolicy(Qt.StrongFocus)
        self.value, self.password, self.reveal = text, password, False
        self.shift, self.symbols = False, False

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        card = QWidget()
        card.setObjectName("oskcard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setMaximumWidth(980)
        col = QVBoxLayout(card)
        col.setContentsMargins(28, 22, 28, 22)
        col.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("osktitle")
        self.display = QLabel()
        self.display.setObjectName("oskdisplay")
        col.addWidget(t)
        col.addWidget(self.display)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.rows = []
        for r in range(4):
            row = []
            for c in range(10):
                b = KeyButton()
                b.clicked.connect(lambda _c=False, btn=b: self._type(btn.property("ch")))
                b.nav.connect(lambda k, btn=b: self._move(btn, k))
                grid.addWidget(b, r, c)
                row.append(b)
            self.rows.append(row)
        col.addLayout(grid)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.b_shift = self._ctl("Shift", self._toggle_shift)
        self.b_sym = self._ctl("123", self._toggle_symbols)
        slash = self._ctl("/", lambda: self._type("/"))
        colon = self._ctl(":", lambda: self._type(":"))
        space = self._ctl("Space", lambda: self._type(" "))
        back = self._ctl("\u232B", self._backspace)
        self.b_show = self._ctl("Show", self._toggle_reveal)
        cancel = self._ctl("Cancel", self.cancelled.emit)
        ok = self._ctl("Done", lambda: self.done.emit(self.value))
        ok.setStyleSheet("QPushButton#oskkey{background:#2d6cdf;font-weight:600;}"
                         "QPushButton#oskkey:focus{background:#4a86f0;border:1px solid #cfe0ff;}")
        self.bottom_row = [self.b_shift, self.b_sym, slash, colon, space, back]
        if password:
            self.bottom_row.append(self.b_show)
        else:
            self.b_show.hide()
        self.bottom_row += [cancel, ok]
        for b in self.bottom_row:
            bottom.addWidget(b, 4 if b is space else 1)
        col.addLayout(bottom)
        hint = QLabel("Arrows move  \u00B7  OK types  \u00B7  Back button / Esc cancels  \u00B7  "
                      "a real keyboard works too")
        hint.setObjectName("oskhint")
        hint.setAlignment(Qt.AlignCenter)
        col.addWidget(hint)
        outer.addWidget(card)
        self.all_rows = self.rows + [self.bottom_row]
        self._relabel()
        self._refresh()

    def _ctl(self, text, fn):
        b = KeyButton(text)
        b.clicked.connect(lambda _c=False: fn())
        b.nav.connect(lambda k, btn=b: self._move(btn, k))
        return b

    def showEvent(self, e):
        super().showEvent(e)
        self.rows[1][0].setFocus()

    # ------------------------------------------------------------ layout / navigation
    def _relabel(self):
        layout = SYMBOLS if self.symbols else LETTERS
        for r, row in enumerate(self.rows):
            for c, b in enumerate(row):
                ch = layout[r][c]
                if self.shift and not self.symbols:
                    ch = ch.upper()
                b.setProperty("ch", ch)
                b.setText(ch.replace("&", "&&"))
        self.b_sym.setText("ABC" if self.symbols else "123")
        self.b_shift.setProperty("on", "true" if self.shift else "false")
        self.b_shift.style().unpolish(self.b_shift)
        self.b_shift.style().polish(self.b_shift)

    def _move(self, btn, key):
        for r, row in enumerate(self.all_rows):
            if btn in row:
                c = row.index(btn)
                break
        else:
            return
        if key == Qt.Key_Left:
            r2, c2 = r, max(0, c - 1)
        elif key == Qt.Key_Right:
            r2, c2 = r, min(len(row) - 1, c + 1)
        else:
            r2 = max(0, min(len(self.all_rows) - 1, r + (1 if key == Qt.Key_Down else -1)))
            tgt = self.all_rows[r2]
            c2 = round(c * (len(tgt) - 1) / max(1, len(row) - 1))
        self.all_rows[r2][min(c2, len(self.all_rows[r2]) - 1)].setFocus()

    # ------------------------------------------------------------ editing
    def _refresh(self):
        shown = self.value if (not self.password or self.reveal) else "\u2022" * len(self.value)
        self.display.setText((shown or " ") + "\u2581")

    def _type(self, ch):
        if not ch:
            return
        self.value += ch
        if self.shift:
            self.shift = False
            self._relabel()
        self._refresh()

    def _backspace(self):
        self.value = self.value[:-1]
        self._refresh()

    def _toggle_shift(self):
        self.shift = not self.shift
        self._relabel()

    def _toggle_symbols(self):
        self.symbols = not self.symbols
        self._relabel()

    def _toggle_reveal(self):
        self.reveal = not self.reveal
        self.b_show.setText("Hide" if self.reveal else "Show")
        self._refresh()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.cancelled.emit()
        elif k == Qt.Key_Backspace:
            self._backspace()
        elif k == Qt.Key_Space:
            self._type(" ")
        elif e.text() and e.text().isprintable():
            self._type(e.text())
        else:
            super().keyPressEvent(e)
