"""The app's own screen driver: a small stand-in for the parts of curses that
ui.py uses, drawn with the standard terminal escape codes that Windows
Terminal (and every modern terminal) understands.

Used on Windows, so the app needs nothing beyond Python itself and keeps
working on every new Python version. Linux uses the built-in curses module
(or this, if curses is missing). Standard library only.
"""

import codecs
import os
import sys
import time
import unicodedata


class error(Exception):
    pass


# attributes: color pair number in the low 16 bits, flags above
A_BOLD = 1 << 16
A_DIM = 1 << 17
A_REVERSE = 1 << 18
_PAIR_MASK = 0xFFFF

COLOR_BLACK = 0
COLORS = 256
COLOR_PAIRS = 0xFFFF

# same numbers real curses uses
KEY_DOWN, KEY_UP, KEY_LEFT, KEY_RIGHT = 258, 259, 260, 261
KEY_HOME, KEY_BACKSPACE = 262, 263
KEY_DC, KEY_NPAGE, KEY_PPAGE = 330, 338, 339
KEY_ENTER, KEY_END, KEY_RESIZE = 343, 360, 410

_ESC_KEYS = {
    "[A": KEY_UP, "[B": KEY_DOWN, "[C": KEY_RIGHT, "[D": KEY_LEFT,
    "OA": KEY_UP, "OB": KEY_DOWN, "OC": KEY_RIGHT, "OD": KEY_LEFT,
    "[H": KEY_HOME, "[F": KEY_END, "OH": KEY_HOME, "OF": KEY_END,
    "[1~": KEY_HOME, "[4~": KEY_END, "[7~": KEY_HOME, "[8~": KEY_END,
    "[3~": KEY_DC, "[5~": KEY_PPAGE, "[6~": KEY_NPAGE,
}
_ESC_PREFIXES = {seq[:i] for seq in _ESC_KEYS for i in range(1, len(seq))}

# Windows console "special key" codes (sent after a \x00 or \xe0 prefix)
_WIN_KEYS = {
    "H": KEY_UP, "P": KEY_DOWN, "K": KEY_LEFT, "M": KEY_RIGHT,
    "G": KEY_HOME, "O": KEY_END, "I": KEY_PPAGE, "Q": KEY_NPAGE, "S": KEY_DC,
}

_pairs = {}
_screen = None


# ------------------------------------------------------------- console --

class _WindowsConsole(object):
    """Turns on escape-code drawing for the console and reads keys."""

    ENABLE_PROCESSED_OUTPUT = 0x0001
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    def __init__(self):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        self.msvcrt = msvcrt
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.GetStdHandle.restype = wintypes.HANDLE
        self.k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.ctypes, self.wintypes = ctypes, wintypes
        self.handle = None
        self.old_mode = None

    def start(self):
        self.handle = self.k32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = self.wintypes.DWORD()
        if not self.k32.GetConsoleMode(self.handle, self.ctypes.byref(mode)):
            raise error("not running in a console window")
        self.old_mode = mode.value
        new = mode.value | self.ENABLE_PROCESSED_OUTPUT | self.ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if not self.k32.SetConsoleMode(self.handle, new):
            raise error("this console can't draw the full screen "
                        "(needs Windows 10 or newer; Windows Terminal works best)")

    def stop(self):
        if self.handle is not None and self.old_mode is not None:
            self.k32.SetConsoleMode(self.handle, self.old_mode)

    def read(self, wait):
        """Keys pressed within `wait` seconds (may be an empty list)."""
        end = time.time() + wait
        while True:
            if self.msvcrt.kbhit():
                out = []
                while self.msvcrt.kbhit() and len(out) < 64:
                    ch = self.msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        code = self.msvcrt.getwch()
                        if code in _WIN_KEYS:
                            out.append(_WIN_KEYS[code])
                    else:
                        out.append(ch)
                if out:
                    return out
            if time.time() >= end:
                return []
            time.sleep(min(0.02, max(0.0, end - time.time())))


class _PosixConsole(object):
    """Keys straight from the terminal (used on Linux only if curses is missing)."""

    def __init__(self):
        import select
        import termios
        self.select, self.termios = select, termios
        self.fd = sys.stdin.fileno()
        self.old = None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def start(self):
        t = self.termios
        self.old = t.tcgetattr(self.fd)
        new = t.tcgetattr(self.fd)
        new[0] &= ~(t.IXON | t.ICRNL)                       # no Ctrl+S freeze
        new[3] &= ~(t.ICANON | t.ECHO)                      # keys arrive one at a time, unechoed
        new[6][t.VMIN] = 1
        new[6][t.VTIME] = 0
        t.tcsetattr(self.fd, t.TCSANOW, new)

    def stop(self):
        if self.old is not None:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.old)

    def read(self, wait):
        r, _, _ = self.select.select([self.fd], [], [], max(0.0, wait))
        if not r:
            return []
        try:
            data = os.read(self.fd, 1024)
        except OSError:
            return []
        return list(self.decoder.decode(data))


# -------------------------------------------------------------- screen --

def _terminal_size():
    for stream in (sys.stdout, sys.stdin, sys.stderr):
        try:
            s = os.get_terminal_size(stream.fileno())
            if s.columns > 0 and s.lines > 0:
                return s.lines, s.columns
        except (OSError, ValueError, AttributeError):
            continue
    return 24, 80


def _color_codes(c, base):
    """base 30 = text color, 40 = background."""
    if c is None or c < 0:
        return []
    if c < 8:
        return [str(base + c)]
    if c < 16:
        return [str(base + 60 + c - 8)]
    return ["%d;5;%d" % (base + 8, c)]


class Window(object):
    def __init__(self, console, out):
        self.console = console
        self.out = out
        self.h, self.w = _terminal_size()
        self.bg_attr = 0                       # what empty space looks like (bkgd)
        self.cells = self._blank()
        self.shown = None                      # what the terminal shows now
        self.delay = -1                        # milliseconds; -1 waits forever
        self.queue = []

    def _blank(self):
        return [[(" ", self.bg_attr)] * self.w for _ in range(self.h)]

    def bkgd(self, ch=" ", attr=0):
        if attr != self.bg_attr:
            self.bg_attr = attr
            self.shown = None                  # repaint everything in the new colors

    # drawing ------------------------------------------------------------
    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        h, w = _terminal_size()
        if (h, w) != (self.h, self.w):
            self.h, self.w = h, w
            self.shown = None
        self.cells = self._blank()

    clear = erase

    def addstr(self, y, x, text, attr=0):
        if y < 0 or y >= self.h or x < 0 or x >= self.w:
            raise error("addstr outside the window")
        row = self.cells[y]
        last = None
        for ch in text:
            if ch in "\n\r\t":
                ch = " "
            if unicodedata.combining(ch):
                if last is not None:          # accent rides on the letter before it
                    c, a = row[last]
                    row[last] = (c + ch, a)
                continue
            if x >= self.w:
                break
            if ord(ch) < 32 or ord(ch) == 127:
                ch = "?"
            row[x] = (ch, attr)
            last = x
            x += 1

    def noutrefresh(self):
        pass

    def refresh(self):
        doupdate()

    def _sgr(self, attr):
        codes = ["0"]
        if attr & A_BOLD:
            codes.append("1")
        if attr & A_DIM:
            codes.append("2")
        if attr & A_REVERSE:
            codes.append("7")
        pair = attr & _PAIR_MASK
        if pair:
            fg, bg = _pairs.get(pair, (-1, -1))
            codes += _color_codes(fg, 30) + _color_codes(bg, 40)
        return "\x1b[" + ";".join(codes) + "m"

    def _paint(self):
        parts = []
        full = self.shown is None or len(self.shown) != self.h or \
            (self.shown and len(self.shown[0]) != self.w)
        if full:
            parts.append("\x1b[0m\x1b[2J")
            self.shown = [None] * self.h
        for y in range(self.h):
            row = self.cells[y]
            if not full and self.shown[y] == row:
                continue
            parts.append("\x1b[%d;1H" % (y + 1))
            cur = None
            for ch, attr in row:
                if attr != cur:
                    parts.append(self._sgr(attr))
                    cur = attr
                parts.append(ch)
            parts.append("\x1b[0m")
            self.shown[y] = list(row)
        if len(parts) > 1 or full:
            self.out.write("".join(parts))
            self.out.flush()

    # keys ---------------------------------------------------------------
    def keypad(self, flag):
        pass

    def nodelay(self, flag):
        self.delay = 0 if flag else -1

    def timeout(self, ms):
        self.delay = ms

    def _more(self, wait):
        if not self.queue:
            self.queue.extend(self.console.read(wait))
        return bool(self.queue)

    def _escape(self):
        """After an Esc: an arrow/page key's code, or a plain Esc."""
        seq = ""
        while seq == "" or seq in _ESC_PREFIXES:
            if not self._more(0.03):
                break
            nxt = self.queue[0]
            if not isinstance(nxt, str):
                break
            cand = seq + nxt
            if cand in _ESC_KEYS:
                self.queue.pop(0)
                return _ESC_KEYS[cand]
            if cand not in _ESC_PREFIXES:
                break
            self.queue.pop(0)
            seq = cand
        if seq:
            self.queue[0:0] = list(seq)       # not a key we know: give the characters back
        return "\x1b"

    def get_wch(self):
        start = time.time()
        while True:
            h, w = _terminal_size()
            if (h, w) != (self.h, self.w):
                self.h, self.w = h, w
                self.shown = None
                self.cells = self._blank()
                return KEY_RESIZE
            if self.delay < 0:
                wait = 0.25
            else:
                wait = max(0.0, min(0.25, self.delay / 1000.0 - (time.time() - start)))
            if self._more(wait):
                item = self.queue.pop(0)
                if item == "\x1b":
                    return self._escape()
                return item
            if self.delay >= 0 and time.time() - start >= self.delay / 1000.0:
                raise error("no input")

    def getch(self):
        k = self.get_wch()
        return ord(k) if isinstance(k, str) else k


# ---------------------------------------------------- module functions --

def _console():
    return _WindowsConsole() if os.name == "nt" else _PosixConsole()


def initscr():
    global _screen
    console = _console()
    console.start()
    out = sys.stdout
    try:
        out.write("\x1b[?1049h\x1b[?7l\x1b[?25l\x1b[0m\x1b[2J\x1b[H")
        out.flush()
    except Exception:
        console.stop()
        raise
    _screen = Window(console, out)
    return _screen


def endwin():
    global _screen
    s, _screen = _screen, None
    try:
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?7h\x1b[?1049l")
        sys.stdout.flush()
    except Exception:
        pass
    if s is not None:
        s.console.stop()


def wrapper(func, *args, **kwargs):
    scr = initscr()
    try:
        return func(scr, *args, **kwargs)
    finally:
        endwin()


def doupdate():
    if _screen is not None:
        _screen._paint()


def beep():
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass


def has_colors():
    return True


def start_color():
    pass


def use_default_colors():
    pass


def init_pair(n, fg, bg):
    if not 0 < n <= COLOR_PAIRS:
        raise error("bad color pair")
    _pairs[n] = (fg, bg)


def color_pair(n):
    return n & _PAIR_MASK


def curs_set(visibility):
    return 0


def set_escdelay(ms):
    pass


def update_lines_cols():
    pass


def resize_term(rows, cols):
    pass
