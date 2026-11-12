"""Screen color modes: normal (the bpytop look), day (high contrast for sunlight)
and night (dim, so it doesn't light up the cab).

Every color in the app is written once, for the normal look; this file maps
each one to its day and night equivalent, so new screens get all three for free.
  day    black on white, everything bold, dark colors only (each reads on white
         at 4.5:1 contrast or better, the usual bar for readable text)
  night  black background, everything dimmed; warnings stay brighter than the rest
"""

MODES = ("auto", "day", "night", "normal")
NAMES = {"auto": "auto (day/night by the sun)", "day": "day · high contrast",
         "night": "night · dim", "normal": "normal"}

_LEVELS = (0, 95, 135, 175, 215, 255)
_BASIC = [(0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0), (0, 0, 128), (128, 0, 128),
          (0, 128, 128), (192, 192, 192), (128, 128, 128), (255, 0, 0), (0, 255, 0),
          (255, 255, 0), (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)]


def rgb(c):
    """xterm-256 color number -> (r, g, b)."""
    if c < 16:
        return _BASIC[c]
    if c < 232:
        c -= 16
        return _LEVELS[c // 36], _LEVELS[(c // 6) % 6], _LEVELS[c % 6]
    v = 8 + 10 * (c - 232)
    return v, v, v


def nearest(r, g, b):
    """Closest color in the 6x6x6 cube or gray ramp (16-255)."""
    def lvl(v):
        return min(range(6), key=lambda i: abs(_LEVELS[i] - v))
    cube = 16 + 36 * lvl(r) + 6 * lvl(g) + lvl(b)
    gray_v = min(23, max(0, int(round(((r + g + b) / 3.0 - 8) / 10.0))))
    gray = 232 + gray_v

    def dist(c):
        cr, cg, cb = rgb(c)
        return (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
    return min((cube, gray), key=dist)


def luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _scaled(c, limit):
    """The same hue, darkened until its luminance is at most `limit`."""
    r, g, b = rgb(c)
    k = 1.0
    while k > 0:
        n = nearest(r * k, g * k, b * k)
        if luminance(n) <= limit:
            return n
        k -= 0.04
    return 16


# Hand-picked equivalents of every color the app uses: normal -> (day, night).
# Day colors all reach at least 4.5:1 contrast on white; night colors are dim
# but still readable on black, with warnings kept a step brighter.
_PAIRS = {
    # text and frames
    253: (16, 244), 231: (16, 246), 245: (16, 240), 242: (236, 238), 239: (237, 236),
    250: (16, 244),
    # box colors and states
    167: (124, 131), 179: (18, 137), 65: (22, 65), 131: (88, 95), 61: (19, 60),
    137: (94, 94), 73: (23, 66), 108: (22, 65), 214: (130, 136), 203: (124, 131),
    # alert severity
    201: (90, 127), 196: (160, 160), 208: (130, 130), 220: (94, 136),
    # temperature scale
    93: (54, 54), 99: (55, 55), 63: (19, 61), 69: (20, 25), 75: (25, 31), 79: (23, 30),
    114: (22, 65), 148: (58, 101), 184: (94, 136),
    # chance-of-rain scale
    60: (60, 60), 67: (25, 60), 68: (25, 24), 74: (24, 25), 39: (25, 25), 33: (19, 25),
    27: (18, 25),
}

def adapt(spec, mode):
    """(color, flags) for the normal look -> the same for day or night."""
    if mode not in ("day", "night"):
        return spec
    color, flags = spec
    pick = _PAIRS.get(color)
    if mode == "day":
        return (pick[0] if pick else _scaled(color, 0.15), "bold")   # bold everything, nothing dim
    return (pick[1] if pick else _scaled(color, 0.10), "")           # no bold: it brightens text


def background(mode):
    """Background color for the mode; None keeps the terminal's own."""
    return {"day": 231, "night": 16}.get(mode)
