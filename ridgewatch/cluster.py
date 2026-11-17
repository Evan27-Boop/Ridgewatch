"""The gauge-cluster main screen (needs 120 columns and 34 rows for the tiles).

Every tile has a fixed spot, so your eye learns where things are:

    SPEED    | ELEV     | COMPASS     big type, the things that change by the second
    HERE                | GRID        where you are, in words and as a map grid
    WAYPOINT | ER                     the way back to a place; nearest emergency room
    ALERTS                            a master light (worst warning) and the list
    RADIO    | WEATHER  | DATA LINK   scanner calls (later); weather in words; internet
                        | GPS         and GPS status

A tile's border color is its warning light. Unknown values show a dim dash and
the reason, and the tile stays where it is.
"""

import curses
import time

from . import geo, hospitals, locate, views

CLUSTER_MIN_W = 120
CLUSTER_MIN_H = 34              # rows between the top and bottom bars

SEG = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
       "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g"}

GRADE_STEEP, GRADE_VERY = 6, 10         # amber, red (percent)
DESCENT_FT = 500                        # this much lost over 2 miles is a long descent
ER_FAR_KM = 48.0                        # nearest ER over ~30 miles: amber


# Big letters for the compass (N, E, S, W): pixel rows, '#' is lit. 12 tall for the
# 6-row size, 8 tall for the 4-row size; E and S reuse the 7-segment digits.
LETTERS = {
    6: {"N": ["##....##", "###...##", "###...##", "####..##", "##.#..##", "##.##.##",
              "##..#.##", "##..####", "##...###", "##...###", "##....##", "##....##"],
        "W": ["##....##", "##....##", "##....##", "##....##", "##.##.##", "##.##.##",
              "##.##.##", "##.##.##", "##.##.##", "########", "###..###", "##....##"]},
    4: {"N": ["#...#", "##..#", "##..#", "#.#.#", "#.#.#", "#..##", "#..##", "#...#"],
        "W": ["#...#", "#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"]},
}
LETTER_SEGS = {"E": "afged", "S": "afgcd"}


def digit_rows(text, rows=6):
    """Big 7-segment digits as `rows` strings of half-block characters.
    6 rows: 8 wide with 2-wide strokes; 4 rows: 5 wide with thin strokes.
    A 1 is just its stroke; commas are a small block."""
    wd, sx, sy = (8, 2, 2) if rows >= 6 else (5, 1, 1)
    hp = rows * 2
    mid = (hp - sy) // 2
    out = [""] * rows
    full, top, bot = "█", "▀", "▄"
    for n, ch in enumerate(text):
        if n:
            out = [r + " " for r in out]
        if ch == ",":
            cw = 2 if rows >= 6 else 1
            out = [r + " " * cw for r in out[:-1]] + [out[-1] + (full if rows >= 6 else bot) * cw]
            continue
        bitmap = LETTERS[6 if rows >= 6 else 4].get(ch)
        if bitmap:
            for i in range(rows):
                t, b = bitmap[2 * i], bitmap[2 * i + 1]
                out[i] += "".join(full if t[c] == "#" and b[c] == "#" else top if t[c] == "#"
                                  else bot if b[c] == "#" else " " for c in range(len(t)))
            continue
        segs = SEG.get(ch) or LETTER_SEGS.get(ch)
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
        drows = 6 if h >= 38 else 4
        ah = 8 if h >= 43 else 7 if h >= 39 else 6
        gh = drows + 5
        mid = 5 if h >= 40 else 4                 # WAYPOINT and ER need two lines
        wh = h - gh - 5 - mid - ah
        return {"drows": drows, "gauges": gh, "here": 5, "mid": mid, "alerts": ah, "bottom": wh}

    def draw_cluster(self, y, x, h, w, entry):
        p = self.cluster_plan(h)
        sw = w // 3                          # right column: COMPASS, GRID, DATA LINK / GPS
        lw = w - sw
        g1 = lw // 2                         # left and middle columns
        gh = p["gauges"]
        self.gauge_speed(y, x, gh, g1, p["drows"])
        self.gauge_elev(y, x + g1, gh, lw - g1, p["drows"])
        self.gauge_compass(y, x + lw, gh, sw, p["drows"])
        y += gh
        self.tile_here(y, x, p["here"], lw)
        self.tile_grid(y, x + lw, p["here"], sw)
        y += p["here"]
        self.tile_waypoint(y, x, p["mid"], g1)
        self.hits.append(("waypoint", y, x, p["mid"], g1))
        self.tile_er(y, x + g1, p["mid"], w - g1)
        y += p["mid"]
        self.cluster_alerts(y, x, p["alerts"], w, entry)
        self.hits.append(("alerts", y, x, p["alerts"], w))
        y += p["alerts"]
        self.tile_radio(y, x, p["bottom"], g1)
        self.tile_weather(y, x + g1, p["bottom"], lw - g1, entry)
        self.hits.append(("weather", y, x + g1, p["bottom"], lw - g1))
        self.box_status(y, x + lw, p["bottom"], sw)

    # ------------------------------------------------------------ helpers --
    def big_number(self, y, x, text, spec, rows, maxw):
        """Big digits at (y, x); smaller digits if they'd overflow maxw. Returns the
        column just past them, and the rows actually used."""
        for t, r in ((text, rows), (text.replace(",", ""), rows), (text, 4), (text.replace(",", ""), 4)):
            if r > rows:
                continue
            lines = digit_rows(t, r)
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
            return "Follow mode is off. Tap FOLLOW GPS."
        if fix is None:
            return "No GPS position"
        return "No speed from the GPS"

    # ------------------------------------------------------------- gauges --
    def gauge_speed(self, y, x, h, w, drows):
        """Speed in big digits; the trip in two lines under it."""
        dot = " %s " % self.g["dot"]
        fix = self.fix if self.follow else None
        gps = fix is not None and fix.source == "gps" and fix.speed_mph is not None
        t = self.trip if self.follow else None
        side, line1, line2 = [], [], []
        num = None
        if gps:
            num = "%d" % round(fix.speed_mph)
            if t and t.get("stopped_at") and fix.speed_mph < 2:
                side.append([("stopped", "label")])
                side.append([(fmt_dur(time.time() - t["stopped_at"]), "value")])
        else:
            line2 = [(self.no_speed_reason(fix), "label")]
        if t and t.get("start") and (t["km"] >= 0.1 or t["moving"] >= 60):
            mi = t["km"] / 1.609344
            line1 = [("trip ", "label"), ("%.1f mi" % mi, "title"), (dot, "label"),
                     (fmt_dur(t["moving"]), "value"), (" moving", "label")]
            if t["stopped"] >= 60:
                line1 += [(dot, "label"), (fmt_dur(t["stopped"]), "value"), (" stopped", "label")]
            line1 = first_fit([line1, line1[:5]], w - 5)
            hrs = t["moving"] / 3600.0
            # average moving speed (never above the top speed: early on, a few fixes skew it)
            avg_mph = min(mi / hrs, t["top"]) if t["moving"] >= 60 else None
            line2 = [("avg ", "label"), ("%d" % round(avg_mph) if avg_mph is not None else "-", "value"),
                     (dot + "top ", "label"), ("%d mph" % round(t["top"]), "value")]
            if t.get("t0"):
                when = time.strftime("%I:%M %p", time.localtime(t["t0"])).lstrip("0")
                line2 = first_fit([line2 + [(dot + "since ", "label"), (when, "value")], line2], w - 5)
        elif gps:
            line1 = [("Trip starts when the vehicle moves", "label")]
        self.gauge(y, x, h, w, "SPEED", "where", num, "title", "mph", side, line1, line2, drows)

    def gauge_elev(self, y, x, h, w, drows):
        """Elevation in big digits. Under it: the grade, then the trip's climb and descent.
        The border is the steep-road light: amber at 6%, red at 10% or on a long descent."""
        dot = " %s " % self.g["dot"]
        fix = self.fix if self.follow else None
        gps = fix is not None and fix.source == "gps"
        terrain = self.terrain() if gps else None
        t = self.trip if self.follow else None
        spec, num, side, line1, line2 = "where", None, [], [], []
        alt = terrain["alt_ft"] if terrain else \
            fix.alt_m * locate.M_TO_FT if gps and fix.alt_m is not None else None
        gr = terrain["grade"] if terrain else None
        drop = terrain["drop_ft"] if terrain else None
        long_desc = drop is not None and drop >= DESCENT_FT
        if gr is not None:
            a = abs(gr)
            spec = "err" if a >= GRADE_VERY or long_desc else "warn" if a >= GRADE_STEEP else "where"
        if alt is not None:
            num = "{:,}".format(int(round(alt)))
            if gps and fix.mode == 2:
                spec = "warn"
                line2 = [("2D fix: elevation is unreliable", "warn")]
        else:
            vl = self.view_loc()
            elev = (self.store.entry(vl).get("hourly") or {}).get("elev_ft") if vl else None
            if elev is not None:
                num = "{:,}".format(int(elev))
                line1 = [("Forecast-grid elevation, not measured", "label")]
                if self.follow:
                    line2 = [("Connect a GPS to measure it", "label")]
        if gr is not None and not line2:
            a = abs(gr)
            gspec = spec if spec != "where" else "value"
            if a < 2:
                line1 = [("level road", "value")]
            else:
                line1 = [("%s %d%% %s" % ("▲" if gr > 0 else "▼", round(a),
                                          "climbing" if gr > 0 else "descending"), gspec)]
            if terrain.get("mile_ft") is not None:
                line1 = first_fit([line1 + [(dot + "last mile ", "label"),
                                            ("%+d ft" % round(terrain["mile_ft"]), "value")], line1], w - 5)
            if long_desc:
                line1 = [("▼ long descent: %s ft in 2 mi" % "{:,}".format(int(drop)), "err")]
                line2 = [("Use a lower gear to save the brakes", "err")]
        elif gps and alt is not None and not line1:
            line1 = [("Grade needs more distance", "label")]
        if t and (t["up_m"] or t["down_m"]) and not line2:
            up, dn = t["up_m"] * locate.M_TO_FT, t["down_m"] * locate.M_TO_FT
            line2 = [("trip ", "label"), ("▲ " + "{:,} ft".format(int(round(up))), "value"),
                     ("  ", "label"), ("▼ " + "{:,} ft".format(int(round(dn))), "value")]
        if terrain:                                   # the shape of the last few miles, if room
            spark = terrain["spark"]
            if len(spark) >= 8 and terrain["span_km"] >= 1.0:
                side = [[(self.sparkline([ft for _, ft in spark], 12), "now")],
                        [("last %d mi" % max(1, round(terrain["span_km"] / 1.609)), "label")]]
        if num is None and not line2:
            line2 = [(self.no_speed_reason(fix), "label")]
        self.gauge(y, x, h, w, "ELEV", spec, num, spec if spec != "where" else "title", "ft",
                   side, line1, line2, drows)

    def gauge_compass(self, y, x, h, w, drows):
        """Heading as big letters (N, NE...) with the degrees beside them. GPS heading is
        noise at walking pace, so when stopped it keeps the last heading, dimmed."""
        fix = self.fix if self.follow else None
        gps = fix is not None and fix.source == "gps"
        t = self.trip if self.follow else None
        last = t.get("heading") if t else None
        moving = gps and fix.course is not None and (fix.speed_mph or 0) >= 3
        course = fix.course if moving else last[0] if last else None
        side, line1, line2, num, numspec = [], [], [], None, "title"
        if course is not None:
            num = COMPASS8[int((course % 360) / 45 + 0.5) % 8]
            arrow = "↑↗→↘↓↙←↖"[int((course % 360) / 45 + 0.5) % 8]
            deg = "%d%s" % (round(course) % 360, self.g["deg"])
            side = [[(arrow + " " + deg, "title" if moving else "label")]]
            if geo.compass(course) != num:                # NNW, ENE...: the finer point
                side.append([(geo.compass(course), "value" if moving else "label")])
            if not moving:
                numspec = "dim"
                line2 = [("Stopped. Last heading shown.", "label")]
            else:
                line2 = [("GPS heading, true north", "label")]
        elif gps:
            line2 = [("Heading appears once moving", "label")]
        else:
            line2 = [(self.no_speed_reason(fix), "label")]
        self.gauge(y, x, h, w, "COMPASS", "where", num, numspec, "", side, line1, line2, drows)

    # -------------------------------------------------------------- tiles --
    def tile_here(self, y, x, h, w):
        g = self.g
        dot = " %s " % g["dot"]
        vl = self.view_loc()
        fix = self.fix if self.follow else None
        if not vl:
            self.box(y, x, h, w, "HERE", "warn")
            self.put(y + 1, x + 2, "No position"[: w - 4], "warn")
            status = dot.join(self.locator.describe()) if self.locator else ""
            self.put(y + 2, x + 2, (status + ". Tap PLACES to set a location.")[: w - 4], "label")
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
            line2 = [("Picked location, not live. Tap FOLLOW GPS to use the receiver.", "label")]
        elif fix:
            line2 = [("Stationary. Towns ahead appear while moving.", "label")]
        else:
            line2 = [("GPS fix lost. Showing the last known position.", "warn")]
        self.runs(y + 2, x + 2, line2, iw)
        if self.follow and self.entered and time.time() - self.entered[1] < 600:
            self.put(y + 3, x + 2, ("entered %s %s" % (self.entered[0],
                                                       views.ago(time.time() - self.entered[1])))[:iw],
                     "hot")

    def tile_grid(self, y, x, h, w):
        self.box(y, x, h, w, "GRID", "where")
        vl = self.view_loc()
        if not vl:
            self.put(y + 1, x + 2, "—", "dim")
            return
        iw = w - 4
        grid = geo.mgrs(vl["lat"], vl["lon"])
        self.put(y + 1, x + 2, (grid or "")[:iw], "title")
        self.put(y + 2, x + 2, ("%.4f, %.4f" % (vl["lat"], vl["lon"]))[:iw], "value")
        fix = self.fix if self.follow else None
        if fix:
            src = "GPS"
            acc = locate.fmt_accuracy(fix.acc_m) if fix.acc_m else ""
            text = ("%s %s %s" % (acc, self.g["dot"], src)) if acc else src
        elif self.follow:
            text = "Last known position"
        else:
            text = "Picked location"
        self.put(y + 3, x + 2, text[:iw], "label")

    def tile_waypoint(self, y, x, h, w):
        """Distance and direction to the chosen waypoint (tap to choose another)."""
        g = self.g
        name, target, note = self.waypoint_target()
        here = self.view_loc() if self.follow else None
        self.box(y, x, h, w, "WAYPOINT", "where", "tap to change" if w >= 40 else None, "dim")
        iw = w - 4
        if target is None:
            self.runs(y + 1, x + 2, [(name, "value")], iw)
            if h > 3:
                self.put(y + 2, x + 2, note[:iw], "label")
            return
        if not here:
            self.runs(y + 1, x + 2, [("—  ", "dim"), (name, "value")], iw)
            if h > 3:
                self.put(y + 2, x + 2, ("Needs a GPS position" if self.follow
                                        else "Follow mode is off. Tap FOLLOW GPS.")[:iw], "label")
            return
        km = geo.km_between(here["lat"], here["lon"], target[0], target[1])
        mi = km / 1.609344
        if km < 0.1:
            where = [("here", "title")]
        else:
            brg = geo.bearing(here["lat"], here["lon"], target[0], target[1])
            arrow = "↑↗→↘↓↙←↖"[int((brg % 360) / 45 + 0.5) % 8]
            dist = ("%.1f mi" % mi) if mi < 10 else ("%d mi" % round(mi))
            where = [(arrow + " ", "title"), (g["approx"] + dist + " " + geo.compass(brg), "title")]
        self.runs(y + 1, x + 2, where + [("   ", "label"), (name, "value")], iw)
        if h > 3:
            self.put(y + 2, x + 2, ("Straight line" + (" %s %s" % (g["dot"], note) if note else ""))[:iw],
                     "label")

    def tile_radio(self, y, x, h, w):
        """The scanner feed's spot. Until the radio helper exists, it says so."""
        self.box(y, x, h, w, "RADIO", "line")
        self.put(y + 1, x + 2, "Not connected yet"[: w - 4], "label")
        if h > 3:
            self.put(y + 2, x + 2, "Scanner calls will appear here."[: w - 4], "dim")

    def tile_er(self, y, x, h, w):
        vl = self.view_loc()
        self.er_parts(vl["lat"], vl["lon"]) if vl else None      # fills the cache
        val = self._er_val if vl else None
        if not val:
            self.box(y, x, h, w, "ER", "where")
            msg = "Loading hospital data" if vl and not self.hospitals.ready() \
                else "No hospital within 125 mi" if vl else ""
            self.put(y + 1, x + 2, (msg or "—")[: w - 4], "label" if msg else "dim")
            return
        hosp, dist = val
        km = geo.km_between(vl["lat"], vl["lon"], hosp.lat, hosp.lon)
        spec = "warn" if km > ER_FAR_KM else "where"
        self.box(y, x, h, w, "ER", spec)
        iw = w - 4
        name = "%s, %s" % (hospitals.short_name(hosp, max(20, iw - len(dist) - 12)), hosp.city)
        self.runs(y + 1, x + 2, [(dist, spec if spec != "where" else "title"), ("   ", "label"),
                                 (name, "value")], iw)
        note = [("Straight-line distance", "label")]
        if not hosp.listed:
            note.append((" %s emergency room not confirmed" % self.g["dot"], "warn"))
        if km > ER_FAR_KM:
            note.append((" %s far from emergency care" % self.g["dot"], "warn"))
        self.runs(y + 2, x + 2, note, iw)

    # ------------------------------------------------------------- alerts --
    def alert_badge(self, a):
        """(word, spec, line1, line2) for the alerts master light."""
        g = self.g
        if not a["known"]:
            if self.follow and not self.view_loc():
                return "NO LOCATION", "warn", "Alerts need a position", "Connect a GPS or tap PLACES"
            if self.fetcher.busy:
                return "CHECKING", "label", "", ""
            if a["error"] in views.OFFLINE_ERRORS:
                return "DATA LINK DOWN", "warn", "Alerts not checked yet", ""
            return "ALERTS UNKNOWN", "warn", a["error"] or "", ""
        if not a["trusted"]:
            return "NOT UPDATING", "warn", "Last checked %s" % views.ago(a["age"]), \
                "Data link down" if a["offline"] else "NWS is not responding"
        worst = next((r for r in self.reports if r["tier"] == "official" and r["sev"]), None)
        if worst:
            line1 = (" %s " % g["dot"]).join(x for x in (worst["where"], worst["when"]) if x)
            return worst["kind"], self.report_spec(worst), line1, \
                "Alarm sounding" if self.alarm_on() else ""
        n = len(self.reports)
        if n:
            return "NO WARNINGS", "ok", "Checked %s" % views.ago(a["age"]), \
                "%d other item%s listed" % (n, "" if n == 1 else "s")
        return "ALL CLEAR", "ok", "Checked %s" % views.ago(a["age"]), "Nothing active nearby"

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
            right, rspec = "Out of date", "warn"
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
            rows = [([("Nothing active nearby", "label")], "")]
        for i, (parts, source) in enumerate(rows[:list_h]):
            used = sum(len(p[0]) for p in parts)
            if source and used + len(source) + 2 <= lw:
                self.put(y + 1 + i, lx + lw - len(source), source, "dim")
                self.runs(y + 1 + i, lx, parts, lw - len(source) - 2)
            else:
                self.runs(y + 1 + i, lx, parts, lw)


COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def first_fit(candidates, width):
    """The first list of (text, spec) parts that fits in width (or the last one)."""
    for parts in candidates:
        if sum(len(p[0]) for p in parts) <= width:
            return parts
    return candidates[-1]


def fmt_dur(sec):
    if sec < 60:
        return "%ds" % int(sec)
    m = int(sec // 60)
    return ("%dh %02dm" % (m // 60, m % 60)) if m >= 60 else "%dm" % m
