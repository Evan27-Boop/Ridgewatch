"""The gauge-cluster main screen, for big windows (about 120x36 and up).

Every tile has a fixed spot, so your eye learns where things are:

    SPEED | GRADE | ELEV          big digits, the things that change by the second
    HERE            | GRID        where you are, in words and as a map grid
    SIGNAL | ER                   way back to the internet, nearest emergency room
    ALERTS                        a master light (worst warning) and the list
    WEATHER         | DATA LINK   weather and its graph; internet and GPS status
                    | GPS

A tile's border color is its warning light. Unknown values show a dim dash and
the reason, and the tile stays where it is. Smaller windows use the compact
layout in panels.py.
"""

import time

from . import geo, hospitals, locate, views
from .term import curses

CLUSTER_MIN_W = 120
CLUSTER_MIN_H = 34              # rows between the top and bottom bars

SEG = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
       "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g"}

GRADE_STEEP, GRADE_VERY = 6, 10         # amber, red (percent)
DESCENT_FT = 500                        # this much lost over 2 miles is a long descent
ER_FAR_KM = 48.0                        # nearest ER over ~30 miles: amber


def digit_rows(text, rows=6, uni=True):
    """Big 7-segment digits as `rows` strings of half-block characters.
    6 rows: 8 wide with 2-wide strokes; 4 rows: 5 wide with thin strokes.
    A 1 is just its stroke; commas are a small block. ASCII terminals get # " _."""
    wd, sx, sy = (8, 2, 2) if rows >= 6 else (5, 1, 1)
    hp = rows * 2
    mid = (hp - sy) // 2
    out = [""] * rows
    full, top, bot = ("█", "▀", "▄") if uni else ("#", '"', "_")
    for n, ch in enumerate(text):
        if n:
            out = [r + " " for r in out]
        if ch == ",":
            cw = 2 if rows >= 6 else 1
            out = [r + " " * cw for r in out[:-1]] + [out[-1] + (full if rows >= 6 else bot) * cw]
            continue
        segs = SEG.get(ch)
        if segs is None:
            continue
        P = [[0] * wd for _ in range(hp)]

        def fill(r0, r1, c0, c1):
            for r in range(r0, r1):
                for c in range(c0, c1):
                    P[r][c] = 1
        if "a" in segs:
            fill(0, sy, 0, wd)
        if "g" in segs:
            fill(mid, mid + sy, 0, wd)
        if "d" in segs:
            fill(hp - sy, hp, 0, wd)
        if "f" in segs:
            fill(0, mid + sy, 0, sx)
        if "b" in segs:
            fill(0, mid + sy, wd - sx, wd)
        if "e" in segs:
            fill(mid, hp, 0, sx)
        if "c" in segs:
            fill(mid, hp, wd - sx, wd)
        cols = range(wd - sx, wd) if ch == "1" else range(wd)
        for i in range(rows):
            t, b = P[2 * i], P[2 * i + 1]
            out[i] += "".join(full if t[c] and b[c] else top if t[c] else bot if b[c] else " "
                              for c in cols)
    return out


class Cluster(object):
    """Drawing methods for the gauge-cluster screen (mixed into Panels)."""

    def cluster_fits(self, h, w):
        return w >= CLUSTER_MIN_W and h >= CLUSTER_MIN_H

    @staticmethod
    def cluster_plan(h):
        """Row heights for the bands, top to bottom, for `h` rows of screen."""
        drows = 6 if h >= 40 else 4
        ah = 8 if h >= 43 else 7 if h >= 39 else 6
        gh = drows + 5
        wh = h - gh - 10 - ah
        return {"drows": drows, "gauges": gh, "here": 5, "signal": 5, "alerts": ah, "weather": wh}

    def draw_cluster(self, y, x, h, w, entry):
        p = self.cluster_plan(h)
        sw = w // 3                          # right column: GRID, ELEV, DATA LINK / GPS
        lw = w - sw
        g1 = lw // 2
        gh = p["gauges"]
        self.gauge_speed(y, x, gh, g1, p["drows"])
        self.gauge_grade(y, x + g1, gh, lw - g1, p["drows"])
        self.gauge_elev(y, x + lw, gh, sw, p["drows"])
        y += gh
        self.tile_here(y, x, p["here"], lw)
        self.tile_grid(y, x + lw, p["here"], sw)
        y += p["here"]
        self.tile_signal(y, x, p["signal"], g1)
        self.tile_er(y, x + g1, p["signal"], w - g1)
        y += p["signal"]
        self.cluster_alerts(y, x, p["alerts"], w, entry)
        y += p["alerts"]
        self.box_weather(y, x, p["weather"], lw, entry, title="WEATHER", spacer=True)
        self.box_status(y, x + lw, p["weather"], sw)

    # ------------------------------------------------------------ helpers --
    def dash(self):
        return "—" if self.uni() else "-"

    def uni(self):
        return self.g.get("dot") != "-"

    def big_number(self, y, x, text, spec, rows, maxw):
        """Big digits at (y, x); smaller digits if they'd overflow maxw. Returns the
        column just past them, and the rows actually used."""
        for r in (rows, 4):
            if r > rows:
                continue
            lines = digit_rows(text, r, self.uni())
            if len(lines[0]) <= maxw:
                for i, line in enumerate(lines):
                    self.put(y + i, x, line, spec)
                return x + len(lines[0]), r
        self.put(y, x, text, spec)
        return x + len(text), 1

    def gauge(self, y, x, h, w, title, spec, num, numspec, unit, side, line1, line2, drows):
        """One gauge tile: big number top left, unit after it, short side lines to the
        right, two lines along the bottom. Text never leaves its tile."""
        self.box(y, x, h, w, title, spec)
        ix, iw = x + 3, w - 5
        if num is None:
            end, used = self.big_number(y + 1, ix, "-", "dim", drows, iw)
        else:
            end, used = self.big_number(y + 1, ix, num, numspec, drows, iw - len(unit) - 1)
            if unit:
                self.put(y + used, end + 1, unit, numspec, curses.A_BOLD)
                end += len(unit) + 1
        sx = max(end + 3, x + 24)
        room = x + w - 2 - sx
        if room >= 10:
            for i, parts in enumerate(side[: drows]):
                self.runs(y + 1 + i, sx, parts, room)
        ly = y + drows + 2
        if ly < y + h - 1:
            self.runs(ly, ix, line1, iw)
        if ly + 1 < y + h - 1:
            self.runs(ly + 1, ix, line2, iw)

    def no_speed_reason(self, fix):
        if not self.follow:
            return "follow mode off %s press f" % self.g["dot"]
        if fix is None:
            return "no position yet"
        return "needs the GPS (Windows location has no speed)"

    # ------------------------------------------------------------- gauges --
    def gauge_speed(self, y, x, h, w, drows):
        g = self.g
        dot = " %s " % g["dot"]
        fix = self.fix if self.follow else None
        gps = fix is not None and fix.source == "gps" and fix.speed_mph is not None
        t = self.trip if self.follow else None
        side, line1, line2 = [], [], []
        num = None
        if gps:
            num = "%d" % round(fix.speed_mph)
            if fix.course is not None and fix.speed_mph >= 3:
                arrows = "↑↗→↘↓↙←↖" if self.uni() else ""
                arrow = (arrows[int((fix.course % 360) / 45 + 0.5) % 8] + " ") if arrows else ""
                side.append([(arrow + views.compass(fix.course), "title"),
                             (" %d%s" % (round(fix.course) % 360, g["deg"]), "label")])
            elif t and t.get("stopped_at") and fix.speed_mph < 2:
                side.append([("stopped ", "label"),
                             (views.ago(time.time() - t["stopped_at"]).replace(" ago", ""), "value")])
            else:
                side.append([])
            side.append([])
        else:
            line2 = [(self.no_speed_reason(fix), "label")]
        if t and t.get("start") and (t["km"] >= 0.1 or t["moving"] >= 60):
            mi = t["km"] / 1.609344
            side.append([("TRIP  ", "label"), ("%.1f mi" % mi, "title")])
            mv = [(fmt_dur(t["moving"]), "value"), (" moving", "label")]
            if t["stopped"] >= 60:
                mv += [(dot.rstrip() + " " + fmt_dur(t["stopped"]), "value"), (" stopped", "label")]
            side.append(mv)
            hrs = t["moving"] / 3600.0
            # average moving speed (never above the top speed: early on, a few fixes skew it)
            avg_mph = min(mi / hrs, t["top"]) if t["moving"] >= 60 else None
            avg = [("avg ", "label"), ("%d" % round(avg_mph) if avg_mph is not None else "-", "value"),
                   (dot + "top ", "label"), ("%d" % round(t["top"]), "value")]
            side.append(avg)
            s_lat, s_lon, _ = t["start"]
            vl = self.view_loc()
            when = time.strftime("%I:%M %p", time.localtime(t["t0"])).lstrip("0") if t.get("t0") else ""
            km = geo.km_between(vl["lat"], vl["lon"], s_lat, s_lon) if vl else 0
            if km >= 0.8:
                line1 = [("started ", "label"), ("%d mi %s" % (max(1, round(km / 1.609344)), geo.compass(
                    geo.bearing(vl["lat"], vl["lon"], s_lat, s_lon))), "value")]
            else:
                line1 = [("started near here", "label")]
            if when:
                line1.append((" at %s" % when, "label"))
        elif gps:
            side += [[("TRIP", "label")], [("starts when you drive", "label")]]
        self.gauge(y, x, h, w, "SPEED", "where", num, "title", "mph", side, line1, line2, drows)

    def gauge_grade(self, y, x, h, w, drows):
        g = self.g
        fix = self.fix if self.follow else None
        gps = fix is not None and fix.source == "gps"
        terrain = self.terrain() if gps else None
        gr = terrain["grade"] if terrain else None
        drop = terrain["drop_ft"] if terrain else None
        long_desc = drop is not None and drop >= DESCENT_FT
        spec, num, side, line2 = "where", None, [], []
        if gr is not None:
            a = abs(gr)
            spec = "err" if a >= GRADE_VERY or long_desc else "warn" if a >= GRADE_STEEP else "where"
            num = "%d" % round(a)
            if a < 2:
                side.append([("level", "value")])
            else:
                up = gr > 0
                mark = ("▲ " if up else "▼ ") if self.uni() else ""
                side.append([(mark + ("climbing" if up else "descending"),
                              spec if spec != "where" else "value")])
            side.append([])
            if terrain.get("mile_ft") is not None:
                side.append([("last mile ", "label"), ("%+d ft" % round(terrain["mile_ft"]), "value")])
        if long_desc:
            line2 = [("long descent: -%s ft in 2 mi %s lower gear" % (
                "{:,}".format(int(drop)), g["dot"]), "err")]
        elif gps and fix.mode == 2:
            line2 = [("2D fix: elevation and grade unreliable", "warn")]
        elif not gps:
            line2 = [(self.no_speed_reason(fix).replace(" (Windows location has no speed)", ""),
                      "label")]
        elif gr is None:
            line2 = [("needs a few hundred feet of driving", "label")]
        # a fixed -12% .. +12% scale, like a real gauge
        line1 = self.grade_bar(gr, w - 5, spec)
        self.gauge(y, x, h, w, "GRADE", spec, num, spec if spec != "where" else "title", "%",
                   side, line1, line2, drows)

    def grade_bar(self, gr, width, spec):
        uni = self.uni()
        track_w = max(10, width - 10)
        if track_w % 2 == 0:
            track_w -= 1
        half = track_w // 2
        cells = ["━" if uni else "-"] * track_w
        cells[half] = "┃" if uni else "|"
        parts = [("-12 ", "label")]
        if gr is None:
            parts.append(("".join(cells), "line"))
        else:
            pos = half + int(round(max(-12.0, min(12.0, gr)) / 12.0 * half))
            left, right = "".join(cells[:pos]), "".join(cells[pos + 1:])
            parts += [(left, "line"), ("●" if uni else "o", spec if spec != "where" else "title"),
                      (right, "line")]
        parts.append((" +12", "label"))
        return parts

    def gauge_elev(self, y, x, h, w, drows):
        g = self.g
        fix = self.fix if self.follow else None
        gps = fix is not None and fix.source == "gps"
        terrain = self.terrain() if gps else None
        t = self.trip if self.follow else None
        spec, num, line1, line2 = "where", None, [], []
        alt = terrain["alt_ft"] if terrain else \
            fix.alt_m * locate.M_TO_FT if gps and fix.alt_m is not None else None
        if alt is not None:
            num = "{:,}".format(int(round(alt)))
            if gps and fix.mode == 2:
                spec = "warn"
                line2 = [("2D fix: elevation unreliable", "warn")]
        else:
            vl = self.view_loc()
            elev = (self.store.entry(vl).get("hourly") or {}).get("elev_ft") if vl else None
            if elev is not None:
                num = "{:,}".format(int(elev))
                line1 = [("from the map, not measured", "label")]
                if self.follow:
                    line2 = [("plug in the GPS for live elevation", "label")]
        if terrain:
            spark = terrain["spark"]
            if len(spark) >= 8 and terrain["span_km"] >= 1.0:
                lo, hi = min(ft for _, ft in spark), max(ft for _, ft in spark)
                line1 = [(self.sparkline([ft for _, ft in spark], 12), "now"),
                         ("  last %d mi: %s-%s ft" % (max(1, round(terrain["span_km"] / 1.609)),
                                                      "{:,}".format(int(lo)), "{:,}".format(int(hi))),
                          "label")]
        if t and (t["up_m"] or t["down_m"]) and not line2:
            up, dn = t["up_m"] * locate.M_TO_FT, t["down_m"] * locate.M_TO_FT
            arrow_up, arrow_dn = ("↑ ", "↓ ") if self.uni() else ("up ", "down ")
            line2 = [("trip: ", "label"), (arrow_up + "{:,} ft".format(int(round(up))), "value"),
                     (" climbed %s " % g["dot"], "label"),
                     (arrow_dn + "{:,} ft".format(int(round(dn))), "value"), (" descended", "label")]
        if num is None and not line2:
            line2 = [(self.no_speed_reason(fix).replace(" (Windows location has no speed)", ""),
                      "label")]
        self.gauge(y, x, h, w, "ELEV", spec, num, spec if spec != "where" else "title", "ft",
                   [], line1, line2, drows)

    # -------------------------------------------------------------- tiles --
    def tile_here(self, y, x, h, w):
        g = self.g
        dot = " %s " % g["dot"]
        vl = self.view_loc()
        fix = self.fix if self.follow else None
        if not vl:
            self.box(y, x, h, w, "HERE", "warn")
            self.put(y + 1, x + 2, ("waiting for a location" + g["ell"])[: w - 4], "warn")
            status = dot.join(self.locator.describe()) if self.locator else ""
            self.put(y + 2, x + 2, status[: w - 4], "label")
            return
        place, county = self.place_label(vl["lat"], vl["lon"])
        lost = self.follow and not fix
        if not self.follow:
            place = self.loc.get("name") or place
        self.box(y, x, h, w, "HERE", "warn" if lost else "where", county or None, "value")
        iw = w - 4
        self.runs(y + 1, x + 2, [("last known: " if lost else "", "warn"),
                                 (place, "warn" if lost else "loc")], iw)
        towns = self.towns_ahead() if fix and fix.source == "gps" else []
        if towns:
            spd = fix.speed_mph or 0
            parts = [("next: ", "label")]
            for i, (name, km) in enumerate(towns):
                mi = km / 1.609344
                parts += [(dot if i else "", "label"), (name, "value"),
                          (" %d mi" % max(1, round(mi)), "label")]
                if spd >= 10:
                    parts.append((" ~%d min" % max(1, round(mi / spd * 60)), "label"))
            line2 = parts
        elif not self.follow:
            line2 = [("picked location %s press f to follow your GPS" % g["dot"], "label")]
        elif fix:
            line2 = [("not moving %s towns ahead show while driving" % g["dot"], "label")]
        else:
            line2 = [("no fix right now %s showing where you last were" % g["dot"], "warn")]
        self.runs(y + 2, x + 2, line2, iw)
        if self.follow and self.entered and time.time() - self.entered[1] < 600:
            self.put(y + 3, x + 2, ("entered %s %s" % (self.entered[0],
                                                       views.ago(time.time() - self.entered[1])))[:iw],
                     "hot")

    def tile_grid(self, y, x, h, w):
        self.box(y, x, h, w, "GRID", "where")
        vl = self.view_loc()
        if not vl:
            self.put(y + 1, x + 2, self.dash(), "dim")
            return
        iw = w - 4
        grid = geo.mgrs(vl["lat"], vl["lon"])
        self.put(y + 1, x + 2, (grid or "")[:iw], "title")
        self.put(y + 2, x + 2, ("%.4f, %.4f" % (vl["lat"], vl["lon"]))[:iw], "value")
        fix = self.fix if self.follow else None
        if fix:
            src = "GPS" if fix.source == "gps" else "Windows location"
            acc = locate.fmt_accuracy(fix.acc_m) if fix.acc_m else ""
            text = ("%s %s %s" % (acc, self.g["dot"], src)) if acc else src
        elif self.follow:
            text = "last known position"
        else:
            text = "picked location"
        self.put(y + 3, x + 2, text[:iw], "label")

    def tile_signal(self, y, x, h, w):
        spec, line1, line2 = self.signal_state()
        self.box(y, x, h, w, "SIGNAL", spec)
        self.runs(y + 1, x + 2, line1, w - 4)
        self.put(y + 2, x + 2, line2[: w - 4], "label")

    def tile_er(self, y, x, h, w):
        vl = self.view_loc()
        self.er_parts(vl["lat"], vl["lon"]) if vl else None      # fills the cache
        val = self._er_val if vl else None
        if not val:
            self.box(y, x, h, w, "ER", "where")
            msg = "loading the hospital list" + self.g["ell"] if vl and not self.hospitals.ready() \
                else "no hospital within 125 mi" if vl else ""
            self.put(y + 1, x + 2, (msg or self.dash())[: w - 4], "label" if msg else "dim")
            return
        hosp, dist = val
        km = geo.km_between(vl["lat"], vl["lon"], hosp.lat, hosp.lon)
        spec = "warn" if km > ER_FAR_KM else "where"
        self.box(y, x, h, w, "ER", spec)
        iw = w - 4
        name = "%s, %s" % (hospitals.short_name(hosp, max(20, iw - len(dist) - 12)), hosp.city)
        self.runs(y + 1, x + 2, [(dist, spec if spec != "where" else "title"), ("   ", "label"),
                                 (name, "value")], iw)
        note = [("straight-line distance", "label")]
        if not hosp.listed:
            note.append((" %s ER not listed for this hospital" % self.g["dot"], "warn"))
        if km > ER_FAR_KM:
            note.append((" %s far from emergency care" % self.g["dot"], "warn"))
        self.runs(y + 2, x + 2, note, iw)

    # ------------------------------------------------------------- alerts --
    def alert_badge(self, a):
        """(word, spec, line1, line2) for the alerts master light."""
        g = self.g
        if not a["known"]:
            if self.follow and not self.view_loc():
                return "WAITING", "label", "for your location", ""
            if self.fetcher.busy:
                return "CHECKING", "label", "", ""
            if a["error"] in views.OFFLINE_ERRORS:
                return "DATA LINK DOWN", "warn", "alerts not checked yet", ""
            return "ALERTS UNKNOWN", "warn", a["error"] or "", ""
        if not a["trusted"]:
            return "NOT UPDATING", "warn", "last checked %s" % views.ago(a["age"]), \
                "data link down" if a["offline"] else "NWS not answering"
        worst = next((r for r in self.reports if r["tier"] == "official" and r["sev"]), None)
        if worst:
            line1 = (" %s " % g["dot"]).join(x for x in (worst["where"], worst["when"]) if x)
            return worst["kind"], self.report_spec(worst), line1, \
                "beeping" if self.alarm_on() else ""
        n = len(self.reports)
        if n:
            return "NO WARNINGS", "ok", "checked %s" % views.ago(a["age"]), \
                "%d other item%s listed" % (n, "" if n == 1 else "s")
        return "ALL CLEAR", "ok", "checked %s" % views.ago(a["age"]), "official + local reports"

    def big_badge(self, y, x, w, rows, word, spec):
        """A solid colored block, `rows` tall, with the word centered (wrapped if long)."""
        lines, cur = [], ""
        for part in word.split():
            if cur and len(cur) + 1 + len(part) > w - 2:
                lines.append(cur)
                cur = part
            else:
                cur = (cur + " " + part).strip()
        lines.append(cur)
        lines = lines[:rows]
        top = (rows - len(lines)) // 2
        for r in range(rows):
            i = r - top
            text = lines[i] if 0 <= i < len(lines) else ""
            self.put(y + r, x, text[: w].center(w), spec, curses.A_REVERSE | curses.A_BOLD)

    def cluster_alerts(self, y, x, h, w, entry):
        a = views.view_alerts(entry)
        reports = self.reports
        off = sum(1 for r in reports if r["tier"] == "official")
        rep_n = len(reports) - off
        bits = (["%d official" % off] if off else []) + (["%d reported" % rep_n] if rep_n else [])
        right = (" %s " % self.g["dot"]).join(bits) or ("none" if a["known"] else None)
        first = next((r for r in reports if r["sev"]), None)
        rspec = self.report_spec(first) if first else ("label" if bits else "dim")
        if a["known"] and not a["trusted"]:
            right, rspec = "out of date", "warn"
        flash_on = self.alarm_on() and int(time.time() * 2) % 2 == 0
        from .panels import sev_spec
        border = sev_spec(self.alarm_items[0]["severity"]) if flash_on else \
            "warn" if a["known"] and not a["trusted"] else "alerts"
        self.box(y, x, h, w, "ALERTS", border, right, rspec)
        if flash_on and w > 12:
            self.put(y, x + 2, " ALERTS ", border, curses.A_REVERSE)
        ih = h - 2
        bw = 30 if w >= 130 else 24
        word, spec, l1, l2 = self.alert_badge(a)
        brows = 3 if ih >= 5 else 1
        self.big_badge(y + 1, x + 2, bw, brows, word, spec)
        if ih > brows:
            self.put(y + 1 + brows, x + 2, l1[:bw], "value")
        if ih > brows + 1:
            self.put(y + 2 + brows, x + 2, l2[:bw], "label")
        lx = x + 2 + bw + 3
        lw = x + w - 2 - lx
        list_h = ih
        note = self.alert_note()
        if note and list_h >= 2:
            self.put(y + ih, lx, note[0][:lw], note[1])
            list_h -= 1
        rows = self.alert_rows(a, list_h, status=False)
        if not rows and a["known"]:
            rows = [([("nothing active near you", "label")], "")]
        for i, (parts, source) in enumerate(rows[:list_h]):
            used = sum(len(p[0]) for p in parts)
            if source and used + len(source) + 2 <= lw:
                self.put(y + 1 + i, lx + lw - len(source), source, "dim")
                self.runs(y + 1 + i, lx, parts, lw - len(source) - 2)
            else:
                self.runs(y + 1 + i, lx, parts, lw)


def fmt_dur(sec):
    if sec < 60:
        return "%ds" % int(sec)
    m = int(sec // 60)
    return ("%dh %02dm" % (m // 60, m % 60)) if m >= 60 else "%dm" % m
