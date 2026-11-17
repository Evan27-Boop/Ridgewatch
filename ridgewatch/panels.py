"""Drawing the dashboard: colors, boxes and each panel (weather, alerts,
location, rain graph, picker, alert text). Mixed into the UI class in ui.py;
it reads the app's state but never changes it.
"""

import curses
import re
import textwrap
import time

from . import __version__, geo, hospitals, locate, power, sun, theme, views
from . import places as places_mod
from .cluster import CLUSTER_MIN_H, CLUSTER_MIN_W, Cluster

BAR_H = 3                       # the touch button bar along the bottom, in rows
MIN_H = CLUSTER_MIN_H + 1 + BAR_H   # header + gauge cluster + button bar

UNI = {
    "h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "bars": " ▁▂▃▄▅▆▇█", "base": "▁", "deg": "°", "dot": "·", "tri": "▲",
    "sel": "›", "ell": "…", "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "approx": "≈", "updown": "↑↓",
}
# name: (256-color number, extra attributes)
THEME = {
    "line": (239, "dim"),
    "title": (231, "bold"),
    "label": (245, "dim"),
    "value": (253, ""),
    "dim": (242, "dim"),
    "hot": (167, "bold"),
    "loc": (179, "bold"),
    "now": (65, ""),
    "alerts": (131, ""),
    "precip": (61, ""),
    "pick": (137, ""),
    "where": (73, ""),
    "ok": (108, ""),
    "warn": (214, "bold"),
    "err": (203, "bold"),
    "button": (245, ""),
    "button_on": (108, ""),
    "button_hit": (179, "bold"),
    "button_alert": (203, "bold"),
}

TEMP_256 = [(-100, 93), (10, 99), (25, 63), (33, 69), (40, 75), (50, 79), (60, 114),
            (70, 148), (78, 184), (85, 214), (92, 208), (100, 196)]
POP_256 = [(0, 60), (20, 67), (30, 68), (40, 74), (50, 39), (60, 33), (70, 27),
           (80, 63), (90, 99)]
SEVERITY = {"Extreme": (201, "bold"), "Severe": (196, "bold"),
            "Moderate": (208, "bold"), "Minor": (220, "")}


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
    return (_stop(TEMP_256, f), "bold" if bold else "")


def pop_spec(p):
    return (_stop(POP_256, p), "bold" if p >= 60 else "")


def sev_spec(sev):
    return SEVERITY.get(sev, (250, "bold"))


class NeedsColors(Exception):
    """The terminal can't show 256 colors."""


class Palette(object):
    """256 colors, in the normal, day (high contrast) or night (dim) look."""

    def __init__(self):
        self.pairs = {}
        self.mode = "normal"
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            raise NeedsColors()
        if getattr(curses, "COLORS", 0) < 256:
            raise NeedsColors()
        self.default_bg = self.bg = -1
        self.max_pairs = getattr(curses, "COLOR_PAIRS", 256)

    def set_mode(self, mode):
        """'normal', 'day' or 'night'. Returns True if the look changed."""
        if mode == self.mode:
            return False
        self.mode = mode
        bg = theme.background(mode)
        self.bg = self.default_bg if bg is None else bg
        self.pairs = {}                    # pair numbers get reused with the new background
        return True

    def base(self):
        """Attribute for empty space: paints the background in day and night looks."""
        return 0 if self.mode == "normal" else self(THEME["value"])

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
        color, flags = theme.adapt(spec, self.mode)
        a = (curses.A_BOLD if "bold" in flags else 0) | (curses.A_DIM if "dim" in flags else 0)
        return a | self._pair(color)


class Panels(Cluster):
    """Drawing methods for UI (see ui.py): the frame, the weather and status boxes,
    and the detail screens. The gauge-cluster main screen is in cluster.py."""

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
        self.hits, self.buttons, self.pick_rows = [], [], []
        H, W = self.scr.getmaxyx()
        ch = H - 1 - BAR_H                       # rows between the header and the button bar
        if not self.cluster_fits(ch, W):
            text = "Window too small. Needs %dx%d; this is %dx%d." % (CLUSTER_MIN_W, MIN_H, W, H)
            self.put(H // 2, max(0, (W - len(text)) // 2), text, "warn")
            self.scr.noutrefresh()
            curses.doupdate()
            return
        vl = self.view_loc()
        entry = self.store.entry(vl) if vl else {}
        self.draw_header(W, entry)
        self.draw_bar(H, W)
        if self.mode == "alerts":
            self.draw_alerts_view(1, 0, ch, W, entry)
        elif self.mode == "rain":
            self.box_precip(1, 0, ch, W, entry)
        else:
            self.draw_main(1, 0, ch, W, entry)
            if self.mode == "pick":
                self.draw_picker(ch + 1, W)
            elif self.mode == "save":
                self.draw_save(ch + 1, W)
        self.scr.noutrefresh()
        curses.doupdate()

    def draw_header(self, W, entry):
        g = self.g
        self.put(0, 0, g["h"] * W, "line")
        x = self.put(0, 2, " RIDGEWATCH ", "title")
        x = self.put(0, x, __version__ + " ", "dim")
        bt = power.tag(self.power)
        left = self.shutdown_left()
        if bt and left is not None:
            when = "%d MIN" % max(1, -(-int(left) // 60)) if left > 60 else "%d S" % int(left)
            bt = ("BATT %d%% %s SHUTDOWN IN %s" % (self.power["pct"], g["dot"], when), "err")
        if bt:
            x = self.put(0, x + 1, " %s " % bt[0], bt[1], curses.A_REVERSE | curses.A_BOLD) + 1
        if self.mode != "main":
            # the status boxes aren't on this screen: show both as colored tags instead
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
            status, level = "No location set", "warn"
        if not flashing:
            level = {"busy": "ok"}.get(level, level)
            if self.msg:                          # a short message takes the status spot
                status, level = self.msg[0], self.msg[1]
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
                return "following", "No GPS position"
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

    def bar_buttons(self):
        """[(action, label, spec)] for the button bar on this screen. While a power-loss
        shutdown is counting down, KEEP RUNNING comes first on every screen."""
        keep = [("keep", "KEEP RUNNING", "button_alert")] \
            if getattr(self, "off_since", None) is not None else []
        return keep + self._bar_buttons()

    def _bar_buttons(self):
        if self.mode == "main":
            return [("display", "DISPLAY", "button"), ("refresh", "REFRESH", "button"),
                    ("follow", "FOLLOW GPS: ON" if self.follow else "FOLLOW GPS: OFF",
                     "button_on" if self.follow else "button"),
                    ("save", "SAVE HERE", "button"), ("places", "PLACES", "button")]
        if self.mode in ("alerts", "pick"):
            return [("back", "BACK", "button"), ("up", "UP", "button"),
                    ("down", "DOWN", "button")]
        if self.mode == "save":
            return [("save_ok", "SAVE", "button"), ("back", "CANCEL", "button")]
        return [("back", "BACK", "button")]

    def draw_bar(self, H, W):
        """Large touch buttons along the bottom, equal widths, BAR_H rows tall. The
        button just tapped lights up for a moment so you know it registered."""
        buttons = self.bar_buttons()
        y, n, gap = H - BAR_H, len(buttons), 1
        bw = (W - 2 - gap * (n - 1)) // n
        x = 1
        hit = self.pressed if self.pressed and time.time() < self.pressed[1] else None
        for i, (action, label, spec) in enumerate(buttons):
            w = bw if i < n - 1 else W - 1 - x          # the last one takes the leftover
            self.big_badge(y, x, w, BAR_H, label, "button_hit" if hit and hit[0] == action else spec)
            self.buttons.append((action, y, x, BAR_H, w))
            x += w + gap

    # main screen --------------------------------------------------------
    def draw_main(self, y, x, h, w, entry):
        """The gauge cluster (cluster.py), or a prompt when no place is picked yet."""
        if not self.view_loc() and not self.follow:
            self.box(y, x, h, w, "RIDGEWATCH", "line")
            self.put(y + 2, x + 3, "No location set.", "value")
            self.put(y + 3, x + 3, "Tap PLACES to pick one, or FOLLOW GPS to use the receiver.", "value")
            return
        self.draw_cluster(y, x, h, w, entry)

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
        hint = {"NO FIX": "Needs a clear view of the sky",
                "NO GPS": "Connect a receiver or check the cable",
                "2D FIX": "Too few satellites for elevation"}.get(gps[0], "")
        boxes = [("DATA LINK", link[0], link[1], link[2], "label", "", ""),
                 ("GPS", gps[0], gps[1], gdetail or "", gspec2,
                  self.gps_port() if self.follow else "", hint)]
        h1 = h // 2
        for (title, word, spec, detail, dspec, right, more), (by, bh) in zip(
                boxes, ((y, h1), (y + h1, h - h1))):
            self.box(by, x, bh, w, title, spec, right, "dim")
            iw = w - 4
            rows = 3 if bh >= 7 else 1              # tall enough: a 3-row block of color
            self.big_badge(by + 1, x + 2, iw, rows, word, spec)
            if bh >= rows + 3:
                self.put(by + 1 + rows, x + 2, detail[:iw], dspec)
            if bh >= rows + 4 and more:
                self.put(by + 2 + rows, x + 2, more[:iw], "label")

    def gps_port(self):
        """'/dev/ttyACM0' while a GPS receiver is talking, else ''."""
        g = self.locator.gps if self.locator else None
        return (g.port or "") if g else ""

    def box_weather(self, y, x, h, w, entry, title="WEATHER", spacer=False):
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
            line1 = [("Loading", "label")]
        else:
            line1 = [("No current conditions", "warn")] + \
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
            rest = [("Saved forecast has expired", "warn")]
        elif not self.fetcher.busy:
            rest = [("No forecast" + ((" (%s)" % p["hourly_err"]) if p["hourly_err"]
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

    def gps_strength(self, fix):
        """(bars 1-5, word) from the GPS's own accuracy estimate (or satellite count)."""
        acc, sats = fix.acc_m, fix.sats or 0
        if acc is not None:
            n = 5 if acc <= 5 else 4 if acc <= 10 else 3 if acc <= 25 else 2 if acc <= 60 else 1
        else:
            n = 4 if sats >= 9 else 3 if sats >= 6 else 2 if sats >= 4 else 1
        return n, ("excellent", "poor", "fair", "fair", "good", "excellent")[n]

    def bar_text(self, n):
        return "▮" * n + "▯" * (5 - n)

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

    def alert_note(self):
        if self.rough():
            return ("Position is approximate (%s)" % self.rough(), "warn")
        if self.follow and not self.fix and self.view_loc():
            return ("For the last known position", "warn")
        return None

    CAT_SPEC = {"road": "pick", "fire": "hot"}

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
            mark = g["tri"] if r["tier"] == "official" else "●"
            rest = [x for x in r["detail"] + [r["where"], r["when"]] if x]
            rows.append(([(mark + " ", spec if r["tier"] == "official" else "label"),
                          (r["kind"], spec), ("  " + (" %s " % g["dot"]).join(rest), "value")],
                         r["source"]))
        if len(rows) > ih and ih >= 1:
            shown = rows[: ih - 1] if ih > 1 else rows[:1]
            hidden = len(rows) - len(shown)
            if ih > 1:
                shown.append(([("+%d more %s tap to see all" % (hidden, g["dot"]), "label")], ""))
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
                msg = [("Saved forecast has expired", "value"),
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
        pw = min(W - 4, 90)
        ph = min(H - 4, 26)
        px = (W - pw) // 2
        py = max(1, (H - ph) // 2)
        self.clear_area(py, px, ph, pw)
        self.box(py, px, ph, pw, "places", "pick")
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
            hint = ("Tap a place. With a keyboard, type a town, ZIP or coordinates.", "dim")
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
        cap = max(1, list_h // 2)            # each choice is 2 rows tall: an easier tap target
        p["page"] = cap
        if sel < p["top"]:
            p["top"] = sel
        if sel >= p["top"] + cap:
            p["top"] = sel - cap + 1
        tags = {"saved": ("saved", "loc"), "town": ("town ", "label"),
                "zip": ("zip  ", "label"), "coords": ("spot ", "label"),
                "follow": ("live ", "ok")}
        for row, i in enumerate(range(p["top"], min(len(items), p["top"] + cap))):
            kind, loc, detail, hint = items[i]
            yy = list_y + 2 * row
            self.pick_rows.append((yy, ix - 1, iw + 2, i))
            rev = curses.A_REVERSE if i == sel else 0
            if rev:
                self.put(yy, ix - 1, " " * (iw + 2), "value", rev)
                self.put(yy + 1, ix - 1, " " * (iw + 2), "value", rev)
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
        if len(items) > cap:
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
            out.append(("", "value"))
        for r in others:
            spec = self.report_spec(r)
            mark = g["tri"] if r["tier"] == "official" else "●"
            out.append(("%s %s" % (mark, r["kind"]), spec))
            rest = [x for x in r["detail"] + [r["where"], r["when"], r["source"]] if x]
            for line in textwrap.wrap((" %s " % g["dot"]).join(rest), width - 2):
                out.append(("  " + line, "value"))
            it = r["item"]
            extra = it.get("headline") or it.get("text") or ""
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
