"""Drawing the dashboard: colors, boxes and each panel (weather, alerts,
location, rain graph, picker, alert text). Mixed into the UI class in ui.py;
it reads the app's state but never changes it.
"""

import re
import textwrap
import time

from . import __version__, geo, hospitals, locate, sun, theme, views
from . import places as places_mod
from .cluster import Cluster
from .term import curses

UNI = {
    "h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "bars": " ▁▂▃▄▅▆▇█", "base": "▁", "deg": "°", "dot": "·", "tri": "▲",
    "sel": "›", "ell": "…", "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "approx": "≈", "updown": "↑↓",
}
ASCII = {
    "h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "bars": " ..::++##", "base": "_", "deg": "", "dot": "-", "tri": "!",
    "sel": ">", "ell": "...", "spin": "|/-\\", "approx": "~", "updown": "up/dn",
}

# name: (256-color, 16-color, extra attributes)
THEME = {
    "line": (239, 8, "dim"),
    "title": (231, 15, "bold"),
    "label": (245, 7, "dim"),
    "value": (253, 15, ""),
    "dim": (242, 8, "dim"),
    "hot": (167, 9, "bold"),
    "loc": (179, 11, "bold"),
    "now": (65, 2, ""),
    "alerts": (131, 1, ""),
    "precip": (61, 4, ""),
    "pick": (137, 3, ""),
    "where": (73, 6, ""),
    "ok": (108, 10, ""),
    "warn": (214, 11, "bold"),
    "err": (203, 9, "bold"),
}

TEMP_256 = [(-100, 93), (10, 99), (25, 63), (33, 69), (40, 75), (50, 79), (60, 114),
            (70, 148), (78, 184), (85, 214), (92, 208), (100, 196)]
TEMP_16 = [(-100, 12), (33, 14), (50, 10), (65, 11), (80, 3), (90, 9)]
POP_256 = [(0, 60), (20, 67), (30, 68), (40, 74), (50, 39), (60, 33), (70, 27),
           (80, 63), (90, 99)]
POP_16 = [(0, 4), (30, 12), (60, 14)]
SEVERITY = {"Extreme": (201, 13, "bold"), "Severe": (196, 9, "bold"),
            "Moderate": (208, 11, "bold"), "Minor": (220, 11, "")}


TEMP_SCALE = (20, 100)          # the temperature graph's bottom and top, never rescaled


def parts_len(parts):
    return sum(len(p[0]) for p in parts)


def first_fit(candidates, width):
    """The first list of parts that fits in `width` (the last one if none do), so
    a line loses whole pieces instead of being cut off mid-word."""
    for c in candidates:
        if parts_len(c) <= width:
            return c
    return candidates[-1]


def _stop(stops, v):
    out = stops[0][1]
    for threshold, color in stops:
        if v >= threshold:
            out = color
    return out


def temp_spec(f, bold=True):
    if f is None:
        return THEME["value"]
    return (_stop(TEMP_256, f), _stop(TEMP_16, f), "bold" if bold else "")


def pop_spec(p):
    return (_stop(POP_256, p), _stop(POP_16, p), "bold" if p >= 60 else "")


def sev_spec(sev):
    return SEVERITY.get(sev, (250, 7, "bold"))


class Palette(object):
    """Picks 256, 16 or 8 colors (or none) depending on the terminal, in the
    normal, day (high contrast) or night (dim) look."""

    def __init__(self, want="auto"):
        self.n = 0
        self.pairs = {}
        self.default_bg = -1
        self.bg = -1
        self.mode = "normal"
        self.max_pairs = 0
        if want == "mono" or not curses.has_colors():
            return
        try:
            curses.start_color()
        except curses.error:
            return
        try:
            curses.use_default_colors()
        except curses.error:
            self.default_bg = self.bg = curses.COLOR_BLACK
        colors = getattr(curses, "COLORS", 8)
        self.n = 256 if colors >= 256 else 16 if colors >= 16 else 8
        if want in ("16", "8"):
            self.n = min(self.n, int(want))
        self.max_pairs = getattr(curses, "COLOR_PAIRS", 64)

    def set_mode(self, mode):
        """'normal', 'day' or 'night'. Returns True if the look changed."""
        if mode == self.mode or self.n == 0:
            return False
        self.mode = mode
        bg = theme.background(mode)
        if bg is None:
            self.bg = self.default_bg
        else:
            self.bg = bg[0] if self.n >= 256 else bg[1] if self.n >= 16 else bg[1] % 8
        self.pairs = {}                    # pair numbers get reused with the new background
        return True

    def base(self):
        """Attribute for empty space: paints the background in day and night looks."""
        if self.n == 0 or self.mode == "normal":
            return 0
        return self(THEME["value"])

    def _pair(self, fg):
        if fg in self.pairs:
            return curses.color_pair(self.pairs[fg])
        nid = len(self.pairs) + 1
        if nid >= self.max_pairs:
            return 0
        try:
            curses.init_pair(nid, fg, self.bg)
        except curses.error:
            return 0
        self.pairs[fg] = nid
        return curses.color_pair(nid)

    def __call__(self, spec):
        if isinstance(spec, str):
            spec = THEME[spec]
        c256, c16, flags = theme.adapt(spec, self.mode)
        a = curses.A_BOLD if "bold" in flags else 0
        if self.n == 0:
            return a | (curses.A_DIM if "dim" in flags else 0)
        if self.n >= 256:
            fg = c256
        elif self.n >= 16:
            fg = c16
        else:
            fg = c16 % 8
            if c16 == 8:
                fg, a = 7, a | curses.A_DIM
            elif c16 > 8:
                a |= curses.A_BOLD
        return a | self._pair(fg)


class Panels(Cluster):
    """Drawing methods for UI (see ui.py). Big windows get the gauge cluster
    (cluster.py); smaller ones the compact layout below."""

    def put(self, y, x, text, spec="value", extra=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w or not text:
            return x
        if x < 0:
            text, x = text[-x:], 0
        text = text[: max(0, w - x)]
        if y == h - 1 and x + len(text) >= w:
            text = text[: w - x - 1]          # curses can't write the very last cell
        if not text:
            return x
        attr = (self.pal(spec) if not isinstance(spec, int) else spec) | extra
        try:
            self.scr.addstr(y, x, text, attr)
        except (curses.error, UnicodeEncodeError, ValueError):
            pass
        return x + len(text)

    def runs(self, y, x, parts, maxw=None):
        """Draw [(text, spec), ...] left to right, clipped to maxw. A part can carry
        a third item: extra attributes such as reverse video."""
        end = x + (maxw if maxw is not None else 10 ** 6)
        for part in parts:
            if x >= end:
                break
            text = part[0][: end - x]
            x = self.put(y, x, text, part[1], part[2] if len(part) > 2 else 0)
        return x

    def box(self, y, x, h, w, title, color, right=None, right_spec="label"):
        g = self.g
        if h < 2 or w < 4:
            return
        self.put(y, x, g["tl"] + g["h"] * (w - 2) + g["tr"], color)
        for r in range(1, h - 1):
            self.put(y + r, x, g["v"], color)
            self.put(y + r, x + w - 1, g["v"], color)
        self.put(y + h - 1, x, g["bl"] + g["h"] * (w - 2) + g["br"], color)
        if title and w > len(title) + 4:
            self.put(y, x + 2, " %s " % title, "title")
        if right and w > len(title or "") + len(right) + 10:
            self.put(y, x + w - len(right) - 4, " %s " % right, right_spec)

    def draw(self):
        self.last_draw = time.time()
        self.dirty = False
        if self.msg and time.time() > self.msg[2]:
            self.msg = None
        try:
            self.scr.bkgd(" ", self.pal.base())      # white in day mode, black at night
        except (curses.error, AttributeError):
            pass
        self.scr.erase()
        H, W = self.scr.getmaxyx()
        min_h = 14
        if W < 40 or H < min_h:
            text = "make the window bigger (40x%d)" % min_h
            self.put(H // 2, max(0, (W - len(text)) // 2), text, "warn")
            self.scr.noutrefresh()
            curses.doupdate()
            return
        vl = self.view_loc()
        entry = self.store.entry(vl) if vl else {}
        self.draw_header(W, entry)
        self.draw_footer(H, W)
        if self.mode == "alerts":
            self.draw_alerts_view(1, 0, H - 2, W, entry)
        elif self.mode == "rain":
            self.box_precip(1, 0, H - 2, W, entry)
        else:
            self.draw_main(1, 0, H - 2, W, entry)
            if self.mode == "pick":
                self.draw_picker(H, W)
            elif self.mode == "save":
                self.draw_save(H, W)
        self.scr.noutrefresh()
        curses.doupdate()

    def draw_header(self, W, entry):
        g = self.g
        self.put(0, 0, g["h"] * W, "line")
        H = self.scr.getmaxyx()[0]
        if self.status_on_screen(H, W):
            x = self.put(0, 2, " weather ", "title")
            if W >= 70:
                x = self.put(0, x, __version__ + " ", "dim")
        else:
            # no DATA LINK / GPS boxes on this screen: show both as colored tags instead
            x = 2
            for lab, (word, spec, _) in (("LINK", self.link_state()), ("GPS", self.gps_state())):
                x = self.put(0, x, " %s %s " % (lab, word), spec, curses.A_REVERSE | curses.A_BOLD) + 1
        right_end = W - 2
        vl = self.view_loc()
        name, label = self.header_name(entry)
        flashing = self.alarm_on()
        if flashing:
            first = self.alarm_items[0]
            where = (" %d mi ahead" % max(1, round(first["km"] / 1.609344))) if first.get("ahead") \
                else ""
            status, level = "%s %s: %s%s" % (g["tri"], self.alarm_label, first["event"], where), \
                sev_spec(first["severity"])
        elif self.fetcher.busy:
            spin = g["spin"][int(time.time() * 8) % len(g["spin"])]
            status, level = "%s updating" % spin, "ok"
        elif vl:
            status, level = views.overall_status(entry, False)
        elif self.follow:
            status, level = "", "warn"            # the label already says "waiting for location"
        else:
            status, level = "no location yet", "warn"
        if not flashing:
            level = {"busy": "ok"}.get(level, level)
        # the location name wins over the status text when space is tight
        name_need = min(len(name), 18) + 4 if name else 0
        room_for_status = right_end - x - name_need - 3
        if len(status) > room_for_status and not flashing:
            short = re.sub(r"^updated (.*?)( ago)?( · .*)?$", r"\1\2", status)
            short = re.sub(r"^data link down · (saved data is )?", "offline · ", short)
            short = short.replace("offline · showing saved data", "offline")
            status = short if len(short) <= room_for_status else ""
            if status and len(status) > room_for_status:
                status = ""
        if flashing and len(status) > room_for_status:
            status = status[: max(0, room_for_status)]
        sx = right_end - len(status) - 2 if status else right_end
        if name:
            room = sx - x - 4
            text = " %s " % name
            if len(text) > room:
                text = text[: max(0, room - 1)] + g["ell"] + " "
            x2 = self.put(0, x + 1, text, "loc")
            room = sx - x2 - 3
            if label and room >= 12:
                if len(label) > room:
                    label = label[: room - 1] + g["ell"]
                x2 = self.put(0, x2, label + " ", "label")
            x = x2
        clock = time.strftime("%I:%M %p").lstrip("0")
        cx = (W - len(clock)) // 2 - 1
        if cx > x + 2 and cx + len(clock) + 4 < sx:
            self.put(0, cx, " %s " % clock, "value")
        if status and sx > x + 1:
            blink = curses.A_REVERSE if flashing and int(time.time() * 2) % 2 == 0 else 0
            self.put(0, sx, " %s " % status, level, blink)


    def header_name(self, entry):
        """(name, label) for the header, e.g. ('following', '2.3 mi NW of Six Mile, SC')."""
        if self.follow:
            vl = self.follow_loc
            if not vl:
                return "following", "waiting for location"
            label = self.place_label(vl["lat"], vl["lon"])[0]
            return "following", label if self.fix else "last known: " + label
        if not self.loc:
            return "", ""
        name = self.loc["name"]
        pts = entry.get("points") or {}
        label = self.loc.get("label") or ""
        if not label and pts.get("city"):
            label = "near %s, %s" % (pts["city"], pts["state"])
        if label and label in name:
            label = ""
        return name, label

    def draw_footer(self, H, W):
        g = self.g
        y = H - 1
        items = {
            "main": [("f", "stop following" if self.follow else "follow"), ("l", "location"),
                     ("r", "refresh"), ("a", "alerts"), ("p", "rain"), ("d", "display"),
                     ("s", "save"), ("1-9", "saved"), ("q", "quit")],
            "rain": [("esc", "back")],
            "pick": [(g["updown"], "select"), ("enter", "open"), ("del", "forget saved"),
                     ("esc", "back")],
            "save": [("enter", "save"), ("ctrl+u", "clear"), ("esc", "cancel")],
            "alerts": [(g["updown"], "scroll"), ("pgup/pgdn", "page"), ("esc", "back")],
        }[self.mode]
        x0 = 1
        if self.driving:                          # only what's safe to press while moving
            x0 = self.put(y, 1, " DRIVING ", "warn", curses.A_REVERSE) + 2
            if self.mode == "main":
                items = [("a", "alerts"), ("p", "rain"), ("d", "display"), ("r", "refresh"),
                         ("q", "quit")]
        msg_w = 0
        if self.msg:
            text, level, _ = self.msg
            msg_w = min(len(text) + 2, W - 2)
            self.put(y, W - msg_w - 1, " " + text[: msg_w - 2] + " ", level)
        limit = W - msg_w - 1 - x0
        last = items[-1]
        keep, used = [], len(last[0]) + len(last[1]) + 3
        for key, word in items[:-1]:
            need = len(key) + len(word) + 3
            if 1 + used + need > limit:
                break
            keep.append((key, word))
            used += need
        if 1 + used <= limit:
            keep.append(last)
        x = x0
        for key, word in keep:
            x = self.put(y, x, key, "hot")
            x = self.put(y, x + 1, word, "label") + 2

    # main screen --------------------------------------------------------
    def draw_main(self, y, x, h, w, entry):
        """Big windows: the gauge cluster (cluster.py). Smaller ones: weather strip on
        top, alerts in the middle, location at the bottom."""
        if not self.view_loc() and not self.follow:
            self.box(y, x, h, w, "weather", "line")
            self.put(y + 2, x + 3, "No location yet. Press l to pick one, or f to follow", "value")
            self.put(y + 3, x + 3, "your live location.", "value")
            return
        if self.cluster_fits(h, w):
            self.draw_cluster(y, x, h, w, entry)
            return
        # weather gets a graph when there's room; alerts and location split the rest evenly
        wh = self.weather_h(h)
        rest = h - wh
        ah = rest // 2
        if self.status_side(w, wh):
            sw = max(22, w // 3)
            self.box_weather(y, x, wh, w - sw, entry)
            self.box_status(y, x + w - sw, wh, sw)
        else:
            self.box_weather(y, x, wh, w, entry)
        self.box_alerts(y + wh, x, ah, w, entry)
        self.box_location(y + wh + ah, x, rest - ah, w)

    @staticmethod
    def weather_h(h):
        return 8 if h >= 22 else 7 if h >= 20 else 6 if h >= 18 else 5 if h >= 16 else \
            4 if h >= 13 else 3

    def status_on_screen(self, H, W):
        """Are the DATA LINK and GPS boxes on screen (so the top bar needn't repeat them)?"""
        if self.mode != "main" or (not self.view_loc() and not self.follow):
            return False
        return self.cluster_fits(H - 2, W) or self.status_side(W, self.weather_h(H - 2))

    def status_side(self, w, wh):
        """DATA LINK and GPS get their own boxes beside the weather when there's room;
        otherwise they're badges in the top bar."""
        return w >= 70 and wh >= 4

    def badge(self, y, x, w, word, spec):
        """A solid colored bar with the word in the middle: readable from the driver's seat."""
        text = word.center(max(len(word) + 2, w))[:w]
        self.put(y, x, text, spec, curses.A_REVERSE | curses.A_BOLD)

    def box_status(self, y, x, h, w):
        """DATA LINK (internet) and GPS, each a big colored badge plus one line of detail."""
        link = self.link_state()
        gps = self.gps_state()
        gdetail = gps[2]
        gspec2 = "label"
        if gdetail is None and self.fix:
            bars, word = self.gps_strength(self.fix)
            bits = [self.bar_text(bars) + " " + word]
            if self.fix.sats:
                bits.append("%d sats" % self.fix.sats)
            if self.fix.acc_m:
                bits.append(locate.fmt_accuracy(self.fix.acc_m))
            age = time.time() - self.fix.time
            if age > 10:                          # positions have stopped coming in
                bits = ["fix %s old" % views.ago(age).replace(" ago", "")] + bits
                gspec2 = "warn"
            sep = " %s " % self.g["dot"]
            if len(sep.join(bits)) > w - 4:       # narrow: drop the word, keep the numbers
                bits = [b.replace(" " + word, "") for b in bits]
            gdetail = sep.join(bits)
        hint = {"ROUGH": "plug in the GPS for a 3D FIX", "NO FIX": "needs a clear view of the sky",
                "NO GPS": "plug in the GPS (or check the cable)",
                "2D FIX": "too few satellites for elevation"}.get(gps[0], "")
        boxes = [("DATA LINK", link[0], link[1], link[2], "label", "", ""),
                 ("GPS", gps[0], gps[1], gdetail or "", gspec2,
                  self.gps_port() if self.follow else "", hint)]
        if h >= 6:
            h1 = h // 2
            for (title, word, spec, detail, dspec, right, more), (by, bh) in zip(
                    boxes, ((y, h1), (y + h1, h - h1))):
                self.box(by, x, bh, w, title, spec, right, "dim")
                iw = w - 4
                rows = 3 if bh >= 7 else 1          # big screens: a 3-row block of color
                if bh >= 3:
                    self.big_badge(by + 1, x + 2, iw, rows, word, spec)
                if bh >= rows + 3:
                    self.put(by + 1 + rows, x + 2, detail[:iw], dspec)
                if bh >= rows + 4 and more:
                    self.put(by + 2 + rows, x + 2, more[:iw], "label")
        else:                                     # short screen: one box, two badge rows
            self.box(y, x, h, w, "status", "line")
            iw = w - 4
            for i, (title, word, spec, _, _, _, _) in enumerate(boxes[: max(0, h - 2)]):
                lab = "LINK" if title == "DATA LINK" else "GPS"
                self.put(y + 1 + i, x + 2, lab.ljust(5), "label")
                self.badge(y + 1 + i, x + 7, iw - 5, word, spec)

    def gps_port(self):
        """'COM3' or '/dev/ttyACM0' while a GPS receiver is talking, else ''."""
        g = self.locator.gps if self.locator else None
        return (g.port or "") if g else ""

    def box_weather(self, y, x, h, w, entry, title="weather", spacer=False):
        """What it's like now, what's coming (rain, highs and lows, daylight), and a
        24-hour graph: chance of rain when rain is likely, otherwise temperature.
        When the station's reading is old, this hour's forecast stands in (and the
        title says so in amber)."""
        g = self.g
        n = views.view_now(entry)
        p = views.view_precip(entry)
        if n["ok"]:
            right = views.now_source(n, g["dot"])
            rspec = "warn" if n["stale"] else "label"
        else:
            right, rspec = None, "label"
        self.box(y, x, h, w, title, "now", right, rspec)
        iy, ix, ih, iw = y + 1, x + 2, h - 2, w - 4
        if ih < 1:
            return
        sep = (" %s " % g["dot"], "label")
        if n["ok"]:
            head = []
            if n["temp"] is not None:
                head.append(("%d%sF" % (n["temp"], g["deg"]), temp_spec(n["temp"])))
            if n["desc"]:
                head += [("  ", "label"), (n["desc"], "value")]
            feels = wind = hum = []
            if n["feels"] is not None and n["temp"] is not None and n["feels"] != n["temp"]:
                feels = [sep, ("feels %d%s" % (n["feels"], g["deg"]), "value")]
            if n["wind"]:
                wind = [sep, ("wind ", "label"), (n["wind"], "value")]
            if n["humidity"] is not None:
                hum = [sep, ("humidity ", "label"), ("%d%%" % n["humidity"], "value")]
            # most to least complete; the first that fits is used (wind outlasts the rest)
            line1 = first_fit([head + feels + wind + hum, head + wind + hum, head + feels + wind,
                               head + wind, head + wind[:1] + wind[2:], head], iw)
        elif entry.get("fatal"):
            line1 = [(entry["fatal"], "err")]
        elif self.fetcher.busy:
            line1 = [("loading" + g["ell"], "label")]
        else:
            line1 = [("no current conditions", "warn")] + \
                ([(" (%s)" % n["error"], "label")] if n["error"] else [])
        self.runs(iy, ix, line1, iw)
        if ih < 2:
            return
        sun_text = self.sun_text()
        line2 = []
        if p["age"] is not None and p["age"] > 90 * 60 and p["hours"]:
            line2.append(("forecast from %s: " % views.ago(p["age"]), "warn"))
        rainy = bool(p["hours"]) and max(hh[2] for hh in p["hours"]) >= 30
        gap = 1 if spacer and ih >= 10 else 0     # a blank row between the text and the graph
        graph_rows = ih - 3 - gap                  # two lines of text, the graph, its hours
        if gap and graph_rows % 2 == 0:
            gap, graph_rows = gap + 1, graph_rows - 1   # odd, so 60 F sits exactly in the middle
        hl = views.temp_range(p)
        hl_full = hl_short = []
        if hl and ih >= 3:
            hi_t, lo_t = hl[0][0], hl[1][0]
            hl_full = [("high ", "label"), ("%d%s" % (hi_t, g["deg"]), temp_spec(hi_t)),
                       (" %s" % hl[0][1], "label"), ("  low ", "label"),
                       ("%d%s" % (lo_t, g["deg"]), temp_spec(lo_t)),
                       (" %s" % hl[1][1], "label"), sep]
            hl_short = [("hi ", "label"), ("%d%s" % (hi_t, g["deg"]), temp_spec(hi_t)),
                        (" lo ", "label"), ("%d%s" % (lo_t, g["deg"]), temp_spec(lo_t)), sep]
        outlook = views.rain_outlook(p)
        rest, total = [], []
        if outlook:
            text, pop = outlook
            rest = [(text, pop_spec(pop) if pop >= 30 else "value")]
            if p["total_in"] is not None and p["total_in"] >= 0.01:
                total = [sep, ("%s%.2f in" % (g["approx"], p["total_in"]), "value")]
        elif entry.get("hourly"):
            rest = [("the saved forecast has run out", "warn")]
        elif not self.fetcher.busy:
            rest = [("no forecast" + ((" (%s)" % p["hourly_err"]) if p["hourly_err"]
                                      else ""), "warn")]
        # the rain outlook is kept over the exact high/low times when space is tight
        line2 += first_fit([hl_full + rest + total, hl_full + rest, hl_short + rest + total,
                            hl_short + rest, rest], iw - parts_len(line2))
        used = parts_len(line2)
        room = iw - (len(sun_text) + 3 if sun_text else 0)
        if sun_text and used > room:
            sun_text, room = "", iw                 # the forecast matters more
        self.runs(iy + 1, ix, line2, room)
        if sun_text:
            self.put(iy + 1, ix + iw - len(sun_text), sun_text, "label")
        if graph_rows >= 1 and p["hours"]:
            if rainy:
                self.rain_graph(iy + 2 + gap, ix, graph_rows, iw, p["hours"])
            else:
                self.temp_graph(iy + 2 + gap, ix, graph_rows, iw, p["hours"])

    def temp_graph(self, iy, ix, bar_rows, iw, hours):
        """Bars for the temperature each hour on a fixed 20-100 F scale (so a bar the
        same height is always the same temperature), colored by temperature."""
        g = self.g
        temps = [hh[3] for hh in hours if hh[3] is not None]
        if not temps:
            return
        lo, hi = TEMP_SCALE                         # fixed: a bar's height always means the same
        span = float(hi - lo)
        axis_w = 5 if iw >= 40 else 0
        bx0 = ix + axis_w + (1 if axis_w else 0)
        avail = ix + iw - bx0
        bw = max(1, avail // 24)
        gap = 1 if bw >= 3 else 0
        fill = bw - gap
        bx0 += max(0, (avail - bw * 24) // 2)
        if axis_w and bar_rows >= 2:
            self.put(iy, ix, ("%d%s" % (hi, g["deg"])).rjust(4), temp_spec(hi, bold=False))
            if bar_rows >= 2:
                self.put(iy + bar_rows - 1, ix, ("%d%s" % (lo, g["deg"])).rjust(4),
                         temp_spec(lo, bold=False))
            if bar_rows >= 3 and bar_rows % 2:    # odd rows: the middle one is exactly 60
                mid = (lo + hi) // 2
                self.put(iy + bar_rows // 2, ix, ("%d%s" % (mid, g["deg"])).rjust(4),
                         temp_spec(mid, bold=False))
        bars = g["bars"]
        for i, (st, off, pop, temp, short) in enumerate(hours[:24]):
            bx = bx0 + i * bw
            if temp is None or bx + fill > ix + iw:
                continue
            # always at least a sliver, so the coldest hour still shows
            frac = max(0.0, min(1.0, (temp - lo) / span))
            eighths = 1 + int(round(frac * (bar_rows * 8 - 1)))
            spec = temp_spec(temp, bold=False)
            for r in range(bar_rows):
                level = max(0, min(8, eighths - r * 8))
                if level:
                    self.put(iy + bar_rows - 1 - r, bx, bars[level] * fill, spec)
        self.hour_labels(iy + bar_rows, ix, iw, hours, bx0, bw)

    def hour_labels(self, ly, ix, iw, hours, bx0, bw):
        step = next((k for k in (1, 2, 3, 4, 6, 12) if k * bw >= 4), 12)
        last_end = -1
        for i, (st, off, pop, temp, short) in enumerate(hours[:24]):
            if views._local(st, off).hour % step:
                continue
            bx = bx0 + i * bw
            lab = views.fmt_hour(st, off, short=True)
            if bx <= last_end or bx + len(lab) > ix + iw:
                continue
            self.put(ly, bx, lab, "label")
            last_end = bx + len(lab)

    def sun_text(self):
        """'sunset 5:34 PM · 1h 52m of light' or 'sunrise 6:58 AM' (worked out offline)."""
        vl = self.view_loc()
        if not vl:
            return ""
        st = sun.status(vl["lat"], vl["lon"])
        if not st["next"]:
            return "sun up all day" if st["day"] else "sun down all day"
        what, t = st["next"]
        clock = time.strftime("%I:%M %p", time.localtime(t)).lstrip("0")
        if what == "sunset":
            left = int(st["left"] // 60)
            light = ("%dh %02dm" % (left // 60, left % 60)) if left >= 60 else "%d min" % left
            return "sunset %s %s %s of light" % (clock, self.g["dot"], light)
        return "sunrise " + clock

    def er_parts(self, lat, lon):
        """[('ER ', ..), ('~9 mi SE', ..), (' · Oconee Memorial Hospital, Seneca', ..)]."""
        key = (round(lat, 2), round(lon, 2))
        if key != self._er_key and self.hospitals.ready():
            hit = self.hospitals.nearest(lat, lon)
            self._er_key, self._er_val = key, None
            if hit:
                h, km = hit
                mi = km / 1.609344
                dist = "%s%d mi" % (self.g["approx"], max(1, round(mi)))
                if mi >= 1:
                    dist += " " + geo.compass(geo.bearing(lat, lon, h.lat, h.lon))
                self._er_val = (h, dist)
        if not self._er_val:
            return []
        h, dist = self._er_val
        where = " %s %s, %s" % (self.g["dot"], hospitals.short_name(h, 34), h.city)
        if h.listed:
            return [("nearest ER ", "label"), (dist, "value"), (where, "label")]
        return [("nearest hospital ", "label"), (dist, "value"), (where, "label"),
                (" (ER not listed)", "warn")]

    # location ----------------------------------------------------------
    LABEL_W = 7

    def gps_strength(self, fix):
        """(bars 1-5, word) from the GPS's own accuracy estimate (or satellite count)."""
        acc, sats = fix.acc_m, fix.sats or 0
        if acc is not None:
            n = 5 if acc <= 5 else 4 if acc <= 10 else 3 if acc <= 25 else 2 if acc <= 60 else 1
        else:
            n = 4 if sats >= 9 else 3 if sats >= 6 else 2 if sats >= 4 else 1
        return n, ("excellent", "poor", "fair", "fair", "good", "excellent")[n]

    def box_location(self, y, x, h, w):
        """Where you are, laid out as labeled rows that never move around:
            HERE   1.1 mi SSW of Central, SC · Pickens Co
            SPEED  64 mph  ↑ N  · climbing 5%
            ELEV   853 ft  ▂▃▅▆▇▆▅▃  last 5 mi 640-1,210 ft
            AHEAD  Norris 4 mi ~4 min · Liberty 9 mi ~8 min
            ER     ≈11 mi W · Oconee Memorial Hospital, Seneca
            TRIP   34.2 mi · 41 min moving · top 66 · start 14 mi SE
            GRID   17S LU 3698 4208   34.7078, -82.7800
        On a short window the least useful rows are left out (the rest keep their order).
        GPS signal has its own box now, so it isn't repeated here."""
        fix = self.fix if self.follow else None
        right, rspec = ("following", "label") if self.follow else ("manual", "dim")
        self.box(y, x, h, w, "location", "where", right, rspec)
        iy, ix, ih, iw = y + 1, x + 2, h - 2, w - 4
        if ih < 1:
            return
        rows = self.location_rows(fix)
        keep = sorted(range(len(rows)), key=lambda i: rows[i][0])[:ih]
        lw = self.LABEL_W if iw >= 50 else 0
        for row, i in enumerate(sorted(keep)):
            _, label, parts, rtext = rows[i]
            if lw:
                self.put(iy + row, ix, label.ljust(lw)[:lw], "label")
            elif label in ("ER", "AHEAD", "TRIP", "ELEV"):
                parts = [(label.lower() if label != "ER" else "ER", "label"), (" ", "label")] + parts
            used = sum(len(p[0]) for p in parts)
            room = iw - lw
            if rtext and used + len(rtext) + 3 <= room:
                self.put(iy + row, ix + iw - len(rtext), rtext, "dim")
                room -= len(rtext) + 2
            self.runs(iy + row, ix + lw, parts, room)

    def bar_text(self, n):
        on, off = ("▮", "▯") if self.g is UNI else ("#", "-")
        return on * n + off * (5 - n)

    def location_rows(self, fix):
        """[(priority, label, parts, right_text)] in display order; lower priority
        numbers stay longest when the box is short."""
        g = self.g
        dot = " %s " % g["dot"]
        vl = self.view_loc()
        rows = []
        if not vl:
            rows.append((0, "HERE", [("waiting for a location" + g["ell"], "warn")], ""))
            status = dot.join(self.locator.describe()) if self.locator else ""
            if status:
                rows.append((1, "GPS", [(status, "warn")], ""))
            return rows
        place, county = self.place_label(vl["lat"], vl["lon"])
        lost = self.follow and not fix
        here = [(("last known: " if lost else "") + place, "warn" if lost else "loc")]
        if county:
            here.append((dot + county, "value"))
        if self.follow and self.entered and time.time() - self.entered[1] < 600:
            here.append(("  entered %s" % views.ago(time.time() - self.entered[1]), "hot"))
        rows.append((0, "HERE", here, ""))

        gps = fix is not None and fix.source == "gps"
        terrain = self.terrain() if gps else None
        t = self.trip if self.follow else None
        if self.follow:
            if gps and fix.speed_mph is not None:
                stopped = t and t.get("stopped_at")
                if stopped and fix.speed_mph < 2:
                    speed = [("stopped", "title"), (" %s" % views.ago(time.time() - stopped)
                                                    .replace(" ago", ""), "label")]
                else:
                    speed = [("%d" % round(fix.speed_mph), "title"), (" mph  ", "label")]
                    if fix.course is not None:
                        arrows = "↑↗→↘↓↙←↖" if g is UNI else ""
                        arrow = (arrows[int((fix.course % 360) / 45 + 0.5) % 8] + " ") if arrows else ""
                        speed += [(arrow + views.compass(fix.course), "title"),
                                  (" %d%s" % (round(fix.course) % 360, g["deg"]), "label")]
                    gr = terrain and terrain["grade"]
                    if gr is not None:
                        if abs(gr) < 2:
                            speed.append((dot + "level", "label"))
                        else:
                            speed.append((dot + ("climbing" if gr > 0 else "descending") +
                                          " %d%%" % round(abs(gr)), "warn" if abs(gr) >= 6 else "value"))
                rows.append((1, "SPEED", speed, ""))
            elif fix:
                rows.append((6, "SPEED", [("needs the GPS (Windows location has no speed)", "dim")], ""))
        if terrain:
            elev = [("{:,} ft".format(int(round(terrain["alt_ft"]))), "value")]
            spark = terrain["spark"]
            if len(spark) >= 8 and terrain["span_km"] >= 1.0:
                elev.append(("  " + self.sparkline([ft for _, ft in spark], 16), "now"))
                lo, hi = min(ft for _, ft in spark), max(ft for _, ft in spark)
                elev.append(("  last %d mi %s-%s ft" % (max(1, round(terrain["span_km"] / 1.609)),
                                                       "{:,}".format(int(lo)), "{:,}".format(int(hi))),
                             "label"))
            if terrain["drop_ft"] is not None and terrain["drop_ft"] >= 500:
                elev.append((dot + "long descent -%s ft in 2 mi" % "{:,}".format(int(terrain["drop_ft"])),
                             "warn"))
            rows.append((4, "ELEV", elev, ""))
        elif gps and fix.alt_m is not None:
            rows.append((4, "ELEV", [("{:,} ft".format(int(round(fix.alt_m * locate.M_TO_FT))),
                                      "value")], ""))
        elif not self.follow:
            elev = (self.store.entry(vl).get("hourly") or {}).get("elev_ft")
            if elev is not None:
                rows.append((3, "ELEV", [("{:,} ft".format(elev), "value"),
                                         ("  forecast area", "dim")], ""))
        towns = self.towns_ahead() if gps else []
        if towns:
            spd = fix.speed_mph or 0
            parts = []
            for i, (name, km) in enumerate(towns):
                mi = km / 1.609344
                parts += [(dot if i else "", "label"), (name, "value"),
                          (" %d mi" % max(1, round(mi)), "label")]
                if spd >= 10:
                    parts.append((" ~%d min" % max(1, round(mi / spd * 60)), "label"))
            rows.append((2, "AHEAD", parts, ""))
        er = self.er_parts(vl["lat"], vl["lon"])
        if er:
            rows.append((3 if self.follow else 1, "ER", er[1:] if er[0][0].startswith("nearest ER")
                         else er, ""))
        if t and t.get("start") and (t["km"] >= 0.1 or t["moving"] >= 60):
            mi = t["km"] / 1.609344
            trip = [("%.1f mi" % mi, "value")]
            if t["moving"] >= 60:
                m = int(t["moving"] // 60)
                trip += [(dot + (("%dh %02dm" % (m // 60, m % 60)) if m >= 60 else "%d min" % m),
                          "value"), (" moving", "label")]
            if t["top"] >= 3:
                trip += [(dot + "top ", "label"), ("%d" % round(t["top"]), "value")]
            s_lat, s_lon, _ = t["start"]
            km = geo.km_between(vl["lat"], vl["lon"], s_lat, s_lon)
            if km >= 0.8:
                trip += [(dot + "start ", "label"), ("%d mi %s" % (
                    max(1, round(km / 1.609344)),
                    geo.compass(geo.bearing(vl["lat"], vl["lon"], s_lat, s_lon))), "value")]
            rows.append((5, "TRIP", trip, ""))
        grid = geo.mgrs(vl["lat"], vl["lon"])
        H, W = self.scr.getmaxyx()
        # the port is in the GPS box's title; repeat it here only when that box isn't shown
        port = self.gps_port() if gps and not self.status_on_screen(H, W) else ""
        rows.append((6 if self.follow else 2, "GRID",
                     ([(grid, "title")] if grid else []) +
                     [("   %.4f, %.4f" % (vl["lat"], vl["lon"]), "label")], port))
        if not self.follow:
            rows.append((4, "", [("press ", "label"), ("f", "hot"),
                                 (" for live GPS: speed, grade, towns ahead, trip", "label")], ""))
        return rows

    def sparkline(self, values, width):
        """Values squeezed into `width` block characters, low to high."""
        if not values:
            return ""
        bars = self.g["bars"]
        n = len(values)
        cols = [values[min(n - 1, int(i * n / width))] for i in range(width)] if n >= width else values
        lo, hi = min(cols), max(cols)
        span = (hi - lo) or 1.0
        return "".join(bars[1 + int((v - lo) / span * 7)] for v in cols)

    def box_alerts(self, y, x, h, w, entry):
        """One list: official alerts (▲) and news reports (●), see views.build_reports."""
        a = views.view_alerts(entry)
        reports = self.reports
        off = sum(1 for r in reports if r["tier"] == "official")
        rep_n = len(reports) - off
        bits = (["%d official" % off] if off else []) + (["%d reported" % rep_n] if rep_n else [])
        right = (" %s " % self.g["dot"]).join(bits) or ("none" if a["known"] else None)
        first = next((r for r in reports if r["sev"]), None)
        rspec = sev_spec(first["sev"]) if first else ("label" if bits else "dim")
        if a["known"] and not a["trusted"]:
            right, rspec = "out of date", "warn"
        flash_on = self.alarm_on() and int(time.time() * 2) % 2 == 0
        border = sev_spec(self.alarm_items[0]["severity"]) if flash_on else \
            "warn" if a["known"] and not a["trusted"] else "alerts"
        self.box(y, x, h, w, "alerts", border, right, rspec)
        if flash_on and w > 12:
            self.put(y, x + 2, " alerts ", border, curses.A_REVERSE)
        iy, ix, ih, iw = y + 1, x + 2, h - 2, w - 4
        note = self.alert_note()
        if note and ih >= 2:
            self.put(iy + ih - 1, ix, note[0][:iw], note[1])
            ih -= 1
        for i, (parts, source) in enumerate(self.alert_rows(a, ih)[:ih]):
            used = sum(len(p[0]) for p in parts)
            if source and used + len(source) + 2 <= iw:
                self.put(iy + i, ix + iw - len(source), source, "dim")
                self.runs(iy + i, ix, parts, iw - len(source) - 2)
            else:
                self.runs(iy + i, ix, parts, iw)

    def alert_note(self):
        if self.rough():
            return ("location approximate (%s)" % self.rough(), "warn")
        if self.follow and not self.fix and self.view_loc():
            return ("for your last known location", "warn")
        return None

    CAT_SPEC = {"road": "pick", "fire": "hot", "police": "precip", "missing": "loc",
                "utility": "where", "storm": "now", "space": "precip", "news": "label"}

    def report_spec(self, r):
        return sev_spec(r["sev"]) if r["sev"] else self.CAT_SPEC.get(r["cat"], "value")

    def alert_rows(self, a, ih, status=True):
        """[(parts, source)] for the alerts box: a DATA LINK DOWN banner when alerts are out
        of date, then one line per report:
            ▲ TORNADO WARNING  4 mi ahead · until 4:15 PM                NWS
            ● CRASH  US 123 NB · lanes closed · Clemson, 2 mi SW · 40m   3 reports"""
        g = self.g
        rows = []
        if not status:
            pass
        elif not a["known"]:
            if self.follow and not self.view_loc():
                return [([("waiting for your location" + g["ell"], "label")], "")]
            if self.fetcher.busy:
                rows.append(([("checking" + g["ell"], "label")], ""))
            elif a["error"] in views.OFFLINE_ERRORS:
                rows.append(([(" DATA LINK DOWN ", "warn", curses.A_REVERSE),
                              (" alerts not checked yet", "warn")], ""))
            else:
                rows.append(([("alerts unknown" + ((": " + a["error"]) if a["error"] else ""),
                               "warn")], ""))
        elif not a["trusted"]:
            rows.append(([(" DATA LINK DOWN " if a["offline"] else " ALERTS NOT UPDATING ", "warn",
                           curses.A_REVERSE), (" last checked %s" % views.ago(a["age"]), "warn")], ""))
        if status and a["known"] and not any(r["where"] == "here" for r in self.reports):
            rows.append(([("none over you" if self.reports else "none active",
                           "ok" if a["trusted"] else "warn")] +
                         ([] if a["trusted"] else [(" as of %s" % views.ago(a["age"]), "warn")]), ""))
        for r in self.reports:
            spec = self.report_spec(r)
            mark = g["tri"] if r["tier"] == "official" else ("●" if g is UNI else "*")
            rest = [x for x in r["detail"] + [r["where"], r["when"]] if x]
            rows.append(([(mark + " ", spec if r["tier"] == "official" else "label"),
                          (r["kind"], spec), ("  " + (" %s " % g["dot"]).join(rest), "value")],
                         r["source"]))
        if len(rows) > ih and ih >= 1:
            shown = rows[: ih - 1] if ih > 1 else rows[:1]
            hidden = len(rows) - len(shown)
            if ih > 1:
                shown.append(([("+%d more %s press a" % (hidden, g["dot"]), "label")], ""))
            rows = shown
        return rows

    def rain_graph(self, iy, ix, bar_rows, iw, hours, axis=True):
        """Bars for the chance of rain each hour, with hour labels on the row below."""
        g = self.g
        axis_w = 5 if (axis and iw >= 40) else 0
        bx0 = ix + axis_w + (1 if axis_w else 0)
        avail = ix + iw - bx0
        bw = max(1, avail // 24)                 # columns per hour
        gap = 1 if bw >= 3 else 0
        fill = bw - gap
        bx0 += max(0, (avail - bw * 24) // 2)    # center the graph in the box
        col = lambda i: bx0 + i * bw
        if axis_w:
            self.put(iy, ix, "100%", "dim")
            if bar_rows >= 2:
                self.put(iy + bar_rows - 1, ix, "  0%", "dim")
            if bar_rows >= 5:
                self.put(iy + (bar_rows - 1) // 2, ix, " 50%", "dim")
        bars = g["bars"]
        for i, (st, off, pop, temp, short) in enumerate(hours):
            bx = col(i)
            if bx + fill > ix + iw:
                break
            eighths = int(round(pop / 100.0 * bar_rows * 8))
            spec = pop_spec(pop)
            for r in range(bar_rows):
                level = max(0, min(8, eighths - r * 8))
                yy = iy + bar_rows - 1 - r
                if level == 0:
                    if r == 0:
                        self.put(yy, bx, g["base"] * fill, "line")
                    continue
                self.put(yy, bx, bars[level] * fill, spec)
        # hour labels under the bars, on round hours
        ly = iy + bar_rows
        step = next((k for k in (1, 2, 3, 4, 6, 12) if k * bw >= 4), 12)
        last_end = -1
        for i, (st, off, pop, temp, short) in enumerate(hours):
            hour = views._local(st, off).hour
            if hour % step:
                continue
            bx = col(i)
            lab = views.fmt_hour(st, off, short=True)
            if bx <= last_end or bx + len(lab) > ix + iw:
                continue
            self.put(ly, bx, lab, "label")
            last_end = bx + len(lab)

    def box_precip(self, y, x, h, w, entry):
        p = views.view_precip(entry)
        g = self.g
        if p["total_in"] is not None:
            right = "total %s %.2f in" % (g["approx"], p["total_in"])
            if p["partial"]:
                right += "+"
            rspec = "value"
        else:
            right, rspec = "total --", "dim"
        self.box(y, x, h, w, "rain %s next 24h" % g["dot"], "precip", right, rspec)
        iy, ix, ih, iw = y + 1, x + 2, h - 2, w - 4
        hours = p["hours"]
        if not hours:
            if entry.get("fatal"):
                msg = [(entry["fatal"], "err")]
            elif self.fetcher.busy:
                msg = [("loading forecast" + g["ell"], "label")]
            elif entry.get("hourly"):
                msg = [("the saved forecast has run out", "value"),
                       (p["hourly_err"] or "it refreshes when NWS can be reached", "warn")]
            else:
                msg = [("no forecast", "value"), (p["hourly_err"] or "", "warn")]
            for i, (t, s) in enumerate(msg[:ih]):
                self.put(iy + i, ix, t[:iw], s)
            return
        show_summary = ih >= 3
        bar_rows = max(1, min(16, ih - (2 if show_summary else 1)))
        self.rain_graph(iy, ix, bar_rows, iw, hours)
        ly = iy + bar_rows
        if show_summary and ly + 1 < iy + ih:
            peak = p["peak"]
            if peak[0] < 10:
                parts = [("dry ", "ok"), ("%s under 10%% all 24 hours" % g["dot"], "label")]
            else:
                parts = [("peak ", "label"), ("%d%%" % peak[0], pop_spec(peak[0])),
                         (" at %s" % views.fmt_hour(peak[1], peak[2]), "label")]
                wet = [hh for hh in hours if hh[2] >= 50]
                extra = "  %s  %d hours at 50%% or more" % (g["dot"], len(wet))
                used = sum(len(t) for t, _ in parts)
                if wet and used + len(extra) <= iw:
                    parts.append((extra, "label"))
            note = None
            if p["age"] is not None and p["age"] > 3 * 3600:
                note = ("forecast %s old" % views.fmt_age(p["age"]), "warn")
            elif p["total_in"] is None and p["qpf_err"]:
                note = ("total unavailable", "dim")
            end = self.runs(ly + 1, ix, parts, iw)
            if note and end + len(note[0]) + 2 < ix + iw:
                self.put(ly + 1, ix + iw - len(note[0]), note[0], note[1])

    # overlays -----------------------------------------------------------
    def clear_area(self, y, x, h, w):
        for r in range(h):
            self.put(y + r, x, " " * w, "value")

    def draw_picker(self, H, W):
        g = self.g
        p = self.pick
        pw = min(W - 4, 66)
        ph = min(H - 4, 20)
        px = (W - pw) // 2
        py = max(1, (H - ph) // 2)
        self.clear_area(py, px, ph, pw)
        self.box(py, px, ph, pw, "location", "pick")
        iy, ix, ih, iw = py + 1, px + 2, ph - 2, pw - 4
        q = p["query"]
        shown_q = q[-(iw - 4):] if len(q) > iw - 4 else q
        x = self.put(iy, ix, g["sel"] + " ", "hot")
        x = self.put(iy, x, shown_q, "title")
        self.put(iy, x, " ", "value", curses.A_REVERSE)
        if not self.places.ready():
            hint = ("loading town list" + g["ell"], "label")
        elif self.places.error and q.strip() and not places_mod.parse_coords(q):
            hint = (self.places.error + " - coordinates still work", "warn")
        else:
            hint = ("town, ZIP, or coordinates like 34.6834, -82.8374", "dim")
        self.put(iy + 1, ix, hint[0][:iw], hint[1])
        self.put(iy + 2, ix, g["h"] * iw, "line")
        list_y, list_h = iy + 3, ih - 3
        items = p["items"]
        if not items:
            if not q.strip():
                lines = ["Type to search. Saved locations show up here too.",
                         "", "Examples:  clemson sc   29631   34.6834, -82.8374"]
            elif self.places.ready() or places_mod.parse_coords(q):
                lines = ["no matches", "", "try 'town ST', a 5-digit ZIP, or lat, lon"]
            else:
                lines = []
            for i, t in enumerate(lines[:list_h]):
                self.put(list_y + i, ix, t[:iw], "label")
            return
        sel = p["sel"]
        if sel < p["top"]:
            p["top"] = sel
        if sel >= p["top"] + list_h:
            p["top"] = sel - list_h + 1
        tags = {"saved": ("saved", "loc"), "town": ("town ", "label"),
                "zip": ("zip  ", "label"), "coords": ("spot ", "label"),
                "follow": ("live ", "ok")}
        for row, i in enumerate(range(p["top"], min(len(items), p["top"] + list_h))):
            kind, loc, detail, hint = items[i]
            yy = list_y + row
            rev = curses.A_REVERSE if i == sel else 0
            if rev:
                self.put(yy, ix - 1, " " * (iw + 2), "value", rev)
            tag, tspec = tags[kind]
            x = self.put(yy, ix, tag + " ", tspec, rev)
            right = hint or detail
            if kind == "saved" and detail and hint:
                right = detail + "  " + hint
            if loc is None:
                name = "following now" if self.follow else "follow my live location"
            else:
                name = loc["name"]
            room = iw - (x - ix) - (len(right) + 2 if right else 0)
            if room < 8:
                right, room = "", iw - (x - ix)
            if len(name) > room:
                name = name[: max(1, room - 1)] + g["ell"]
            self.put(yy, x, name, "warn" if kind == "coords" and detail else "value", rev)
            if right:
                self.put(yy, ix + iw - len(right), right,
                         "err" if kind == "coords" else "dim", rev)
        if len(items) > list_h:
            more = " %d/%d " % (sel + 1, len(items))
            self.put(py + ph - 1, px + pw - len(more) - 2, more, "label")

    def draw_save(self, H, W):
        g = self.g
        pw = min(W - 4, 50)
        ph = 6
        px, py = (W - pw) // 2, max(1, (H - ph) // 2)
        self.clear_area(py, px, ph, pw)
        renaming = self.store.saved_index(self.save_target or self.loc) >= 0
        self.box(py, px, ph, pw, "rename" if renaming else "save location", "pick")
        iy, ix, iw = py + 1, px + 2, pw - 4
        self.put(iy, ix, "name for this spot:", "label")
        x = self.put(iy + 2, ix, g["sel"] + " ", "hot")
        x = self.put(iy + 2, x, self.save_text[-(iw - 4):], "title")
        self.put(iy + 2, x, " ", "value", curses.A_REVERSE)

    # alerts detail ------------------------------------------------------
    def alert_text_lines(self, entry, width):
        """The alerts screen (a): official alerts over you in full, then every other
        report with what's known about it."""
        g = self.g
        out = self.nws_text_lines(entry, width)
        others = [r for r in self.reports if r["where"] != "here"]
        if others:
            out += [("", "value"), (g["h"] * width, "line"), ("", "value")]
            out.append(("AROUND YOU", "title"))
            out.append(("%s official (an agency said so)   %s reported (news, unconfirmed)" % (
                g["tri"], "●" if g is UNI else "*"), "dim"))
            out.append(("", "value"))
        for r in others:
            spec = self.report_spec(r)
            mark = g["tri"] if r["tier"] == "official" else ("●" if g is UNI else "*")
            out.append(("%s %s" % (mark, r["kind"]), spec))
            rest = [x for x in r["detail"] + [r["where"], r["when"], r["source"]] if x]
            for line in textwrap.wrap((" %s " % g["dot"]).join(rest), width - 2):
                out.append(("  " + line, "value"))
            it = r["item"]
            extra = it.get("headline") or it.get("text") or ""
            if r["tier"] == "reported":
                extra = '"%s"%s' % (it["title"], (" %s %s" % (g["dot"], it["source"]))
                                     if it.get("source") else "")
            elif r["cat"] == "space":
                extra = ("NOAA rates today's geomagnetic storm G%d. Strong storms can make GPS "
                         "positions less accurate for hours." % it["g"])
            for line in textwrap.wrap(extra, width - 2):
                out.append(("  " + line, "label"))
            out.append(("", "value"))
        if self.feed_problems():
            out.append(("couldn't check: " + "; ".join(self.feed_problems()), "dim"))
        vl = self.view_loc()
        pts = self.store.entry(vl).get("points") or {} if vl else {}
        code = geo.same_code(pts.get("county_zone"))
        if code:
            county = self.place_label(vl["lat"], vl["lon"])[1] or "this county"
            out += [("", "value"), ("Weather radio code for %s: %s. Program it into a NOAA "
                                    "weather radio so it only sounds for this county%s." % (
                                        county, code, (" (NWS office %s)" % pts["office"])
                                        if pts.get("office") else ""), "dim")]
        return out

    def nws_text_lines(self, entry, width):
        g = self.g
        a = views.view_alerts(entry)
        out = []
        if not a["known"]:
            out.append(("Alerts haven't been checked yet" +
                        ((": " + a["error"]) if a["error"] else "."), "warn"))
            return out
        if not a["items"]:
            out.append(("No active alerts for this spot.", "ok"))
            out.append(("checked %s" % views.ago(a["age"]), "warn" if not a["trusted"] else "label"))
            return out
        if not a["trusted"]:
            out.append(("checked %s - couldn't re-check since" % views.ago(a["age"]), "warn"))
            out.append(("", "value"))
        for n, it in enumerate(a["items"]):
            spec = sev_spec(it["severity"])
            out.append(("%s %s" % (g["tri"], it["event"].upper()), spec))
            meta = [it["severity"], it["urgency"], it["certainty"]]
            out.append(("  " + (" %s " % g["dot"]).join(m for m in meta if m), "label"))
            when = []
            if it.get("onset"):
                when.append("from " + views.fmt_when(it["onset"]))
            if it.get("ends"):
                when.append("until " + views.fmt_when(it["ends"]))
            if when:
                out.append(("  " + "  ".join(when), "value"))
            if it.get("sender"):
                out.append(("  " + it["sender"], "dim"))
            out.append(("", "value"))
            for text, spec2 in ((it["headline"], "title"), (it["description"], "value")):
                for para in _paragraphs(text):
                    for line in textwrap.wrap(para, width) or [""]:
                        out.append((line, spec2))
                    out.append(("", "value"))
            if it.get("instruction"):
                out.append(("WHAT TO DO", "warn"))
                for para in _paragraphs(it["instruction"]):
                    for line in textwrap.wrap(para, width) or [""]:
                        out.append((line, "value"))
                    out.append(("", "value"))
            if it.get("area"):
                for line in textwrap.wrap("Areas: " + it["area"], width):
                    out.append((line, "dim"))
            if n < len(a["items"]) - 1:
                out.append(("", "value"))
                out.append((g["h"] * width, "line"))
                out.append(("", "value"))
        return out

    def draw_alerts_view(self, y, x, h, w, entry):
        a = views.view_alerts(entry)
        right = ("%d active" % len(a["items"])) if a["items"] else None
        name, label = self.header_name(entry)
        title = "alerts %s %s" % (self.g["dot"], label if self.follow else name)
        self.draw_text_view(y, x, h, w, title, "alerts", right,
                            sev_spec(a["items"][0]["severity"]) if a["items"] else "label",
                            self.alert_text_lines(entry, max(20, w - 5)))

    def draw_text_view(self, y, x, h, w, title, color, right, rspec, lines):
        """A scrolling page of (text, spec) lines in a box."""
        self.box(y, x, h, w, title[: w - 20], color, right, rspec)
        iy, ix, ih, iw = y + 1, x + 2, h - 2, w - 4
        self.alert_lines = len(lines)
        self.alert_top = max(0, min(self.alert_top, max(0, len(lines) - ih)))
        for i, (t, s) in enumerate(lines[self.alert_top: self.alert_top + ih]):
            self.put(iy + i, ix, t[:iw], s)
        if len(lines) > ih:
            pos = " %d%% " % int(100 * min(1, (self.alert_top + ih) / float(len(lines))))
            self.put(y + h - 1, x + w - len(pos) - 2, pos, "label")


def _paragraphs(text):
    """NWS text is hard-wrapped at ~70 columns; rejoin it into paragraphs."""
    if not text:
        return []
    paras = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.strip() for l in block.splitlines()]
        cur = []
        for l in lines:
            if l.startswith("* ") and cur:
                paras.append(" ".join(cur))
                cur = []
            cur.append(l)
        if cur:
            paras.append(" ".join(cur))
    return paras
