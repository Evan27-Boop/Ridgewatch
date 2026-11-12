"""The full-screen dashboard: what it's showing, follow mode, alarms, keys
and the main loop. Drawing is in panels.py; the screen driver comes from term.py.
"""

import os
import queue
import re
import time

from . import feeds, follow, geo, hospitals, locate, news, nws, settings, sun, theme, views
from . import places as places_mod
from .panels import ASCII, UNI, Palette, Panels
from .term import BUILTIN, curses


STATUS_HOLD = 5.0                   # seconds a new DATA LINK / GPS word must last to beep
STATUS_QUIET = ("CHECKING", "OFF")  # changes to or from these never beep

CLIMB_STEP_M = 3.0                  # elevation must move this much to count (GPS altitude jitters)


def new_trip():
    return {"km": 0.0, "moving": 0.0, "stopped": 0.0, "top": 0.0, "start": None, "t0": None,
            "last": None, "crumbs": [], "stopped_at": None,
            "up_m": 0.0, "down_m": 0.0, "anchor": None, "alts": []}


def climb_add(t, alt_m):
    """Total climbed and descended, from GPS altitude smoothed over the last 5 readings and
    only counted once it has moved CLIMB_STEP_M from the last counted level, so the
    usual 10-30 ft of jitter doesn't add up into phantom hills."""
    a = t["alts"]
    a.append(alt_m)
    del a[:-5]
    level = sum(a) / len(a)
    if t["anchor"] is None:
        t["anchor"] = level
        return
    d = level - t["anchor"]
    if d >= CLIMB_STEP_M:
        t["up_m"] += d
        t["anchor"] = level
    elif d <= -CLIMB_STEP_M:
        t["down_m"] -= d
        t["anchor"] = level

class UI(Panels):
    def __init__(self, scr, store, loc, opts):
        self.scr = scr
        self.store = store
        self.g = ASCII if opts.get("ascii") else UNI
        self.pal = Palette(opts.get("colors", "auto"))
        self.places = places_mod.PlaceIndex()
        self.places.load_in_background()
        self.hospitals = hospitals.HospitalIndex()
        self.hospitals.load_in_background()
        self._er_key, self._er_val = None, None
        self.fetcher = nws.Fetcher(lambda: nws.user_agent(store.config.get("contact")))
        self.loc = loc
        self.mode = "main"
        self.msg = None
        self.next_refresh = 0
        self.next_alerts = 0
        self._fix_seen = 0
        self.last_draw = 0
        self.dirty = True
        self.pick = None
        self.save_text = ""
        self.alert_top = 0
        self.alert_lines = 0
        self.retry = 0
        self.follow = False
        self.locator = None
        self.fix = None                 # latest live position (follow mode)
        self.follow_loc = None
        self.not_before = 0             # follow mode: no fetch before this (backoff)
        self._place_key = None
        self._place_val = ("", "")
        self.alarm_until = 0
        self.alarm_items = []
        self.alarm_label = "NEW"
        # display look: auto follows the sun between day (high contrast) and night (dim)
        self.display = opts.get("display") or store.config.get("display", "auto")
        self.look = None
        self._look_at = 0
        # driving: menus lock while the GPS says you're moving
        self.driving = False
        self._moving_at = 0
        self.last_key = time.time()
        # storm warnings around you (and which ones ahead you've been told about)
        self.nearby_items = []
        self._nearby_at = 0
        self.ahead_memory = {}
        self._states_key, self._states = None, []
        self._nearby_asked = 0
        # wildfires, space weather and state 511 roads: their own thread too
        self.feedsf = feeds.FeedFetcher()
        self.reports = []                       # the alerts list (views.build_reports)
        self.trip = None                        # this follow session: distance, time, top speed
        self.signal_spot = None                 # (lat, lon, time) of the last reply from the internet
        self.region, self.entered = None, None  # (county, state); last county/state line crossed
        # the data link: when anything last got through, and when it last failed
        self.link = {"ok": 0.0, "fail": 0.0, "down_since": None, "msg": "", "trouble": 0.0}
        self._ahead_key, self._ahead_val = None, []
        self.beeps = []
        self.status_seen = {}                   # DATA LINK / GPS word last beeped about
        self.save_target = None
        # nearby headlines: their own thread, so news never holds up weather alerts
        self.newsf = news.NewsFetcher() if store.config.get("headlines") else None
        self.news_area = None
        self.news_wait = 0
        self._area_key = None
        for note in store.notes:
            self.flash(note, "warn", 8)
        if store.config.get("follow"):
            self.start_follow(initial=True)

    # ----------------------------------------------------------- follow --
    def view_loc(self):
        """The location on screen: your live position in follow mode, else the picked one."""
        return self.follow_loc if self.follow else self.loc

    def make_follow_loc(self, lat, lon):
        return {"name": "following", "lat": round(lat, 5), "lon": round(lon, 5),
                "follow": True, "label": ""}

    def start_follow(self, initial=False):
        self.follow = True
        self.trip = new_trip()
        self.signal_spot = None
        self.region, self.entered = None, None
        if self.locator is None:
            c = self.store.config
            self.locator = locate.Locator(c.get("gps_port", "auto"), c.get("gps_baud", 9600),
                                          c.get("windows_location", True))
        last = (self.store.cache["locations"].get("follow") or {}).get("follow_full")
        if last and self.follow_loc is None:
            self.follow_loc = self.make_follow_loc(last["lat"], last["lon"])
        self.not_before = 0
        self.retry = 0
        if not initial:
            self.store.config["follow"] = True
            self.store.save_config()
            self.flash("following your location")
        self.dirty = True

    def stop_follow(self):
        self.follow = False
        if self.locator:
            self.locator.stop()
            self.locator = None
        self.fix = None
        self.store.config["follow"] = False
        self.store.save_config()
        self.dirty = True

    def follow_tick(self, now):
        fix = self.locator.current(now) if self.locator else None
        if (fix is None) != (self.fix is None) or (fix and fix.time != self._fix_seen):
            self.dirty = True                      # new position: redraw speed, heading...
            self._fix_seen = fix.time if fix else 0
            if fix:
                self.trip_add(fix)
        self.fix = fix
        self.driving_tick(now)
        if fix is None:
            return
        fl = self.follow_loc
        if fl is None or abs(fl["lat"] - fix.lat) > 1e-5 or abs(fl["lon"] - fix.lon) > 1e-5:
            self.follow_loc = self.make_follow_loc(fix.lat, fix.lon)
        if self.fetcher.busy or now < self.not_before:
            return
        entry = self.store.entry(self.follow_loc)
        due = follow.follow_due(entry, fix.lat, fix.lon, 60 * self.store.config["refresh_minutes"], now)
        if not due and self.nearby_missing(now):
            due = "alerts"
        if not due:
            return
        follow.follow_mark(entry, due, fix.lat, fix.lon, now)
        if due == "full":
            snap = follow.follow_snapshot(entry, fix.lat, fix.lon)
            snap["states"] = self.nearby_states()
            self.fetcher.request(self.follow_loc, snap)
        else:
            self.fetcher.request(self.follow_loc, {"states": self.nearby_states()}, only="alerts")
        self.dirty = True

    def place_label(self, lat, lon):
        """('2.3 mi NW of Six Mile, SC', 'Pickens Co') from the offline town list."""
        key = (round(lat, 3), round(lon, 3))
        if key == self._place_key:
            return self._place_val
        if not self.places.ready():
            return ("%.4f, %.4f" % (lat, lon), "")
        val = places_mod.describe_spot(self.places, lat, lon)
        self._place_key, self._place_val = key, val
        return val

    def trip_add(self, fix):
        """Count distance and moving time from GPS fixes (Windows location is too rough)."""
        t = self.trip
        if t is None:
            return
        if t["start"] is None:
            t["start"] = (fix.lat, fix.lon, self.place_label(fix.lat, fix.lon)[0])
            t["t0"] = fix.time
        self.region_check(fix)
        if fix.source != "gps":
            return
        spd = fix.speed_mph or 0.0
        t["top"] = max(t["top"], spd)
        if spd < 2:
            t["stopped_at"] = t["stopped_at"] or fix.time
        else:
            t["stopped_at"] = None
        last = t["last"]
        t["last"] = (fix.lat, fix.lon, fix.time)
        if not last:
            return
        dt = fix.time - last[2]
        km = geo.km_between(last[0], last[1], fix.lat, fix.lon)
        if spd < 2 and 0 < dt <= 30:
            t["stopped"] += dt
        # parked GPS wanders a few meters: only count it while actually moving, and skip jumps
        if spd >= 3 and 0 < dt <= 30 and km <= dt * 0.06:
            t["km"] += km
            t["moving"] += dt
            # elevation along the way, for grade and climb totals (a 2D fix has none worth using)
            if fix.alt_m is not None and fix.mode != 2:
                t["crumbs"].append((t["km"], fix.alt_m))
                if len(t["crumbs"]) > 3000:
                    del t["crumbs"][:1000]
                climb_add(t, fix.alt_m)

    def region_check(self, fix):
        """Notice crossing into a new county or state."""
        place, county = self.place_label(fix.lat, fix.lon)
        if not county or not self.places.ready():
            return
        region = (county, place[-2:])
        if self.region and region != self.region:
            self.entered = (county if region[1] == self.region[1] else
                            "%s, %s" % (county, region[1]), time.time())
        self.region = region

    def terrain(self):
        """What the GPS altitude says about the road, from the last few miles driven:
        {'alt_ft', 'grade' (percent, + is uphill), 'spark' [(km, ft)...], 'drop_ft'
        (elevation lost over the last 2 miles, for long descents)}, or None."""
        t = self.trip
        crumbs = t["crumbs"] if t else []
        if len(crumbs) < 5:
            return None
        km_now = crumbs[-1][0]
        recent = [c for c in crumbs if km_now - c[0] <= 0.5]
        grade = None
        if recent and km_now - recent[0][0] >= 0.3:
            n = len(recent)                             # least-squares slope, meters per km
            mx = sum(c[0] for c in recent) / n
            my = sum(c[1] for c in recent) / n
            sxx = sum((c[0] - mx) ** 2 for c in recent)
            if sxx > 0:
                slope = sum((c[0] - mx) * (c[1] - my) for c in recent) / sxx
                grade = slope / 10.0                    # m per km -> percent
        back = [c for c in crumbs if km_now - c[0] <= 3.2]
        drop_ft = (back[0][1] - crumbs[-1][1]) * locate.M_TO_FT if back and \
            km_now - back[0][0] >= 2.5 else None
        spark = [(c[0], c[1] * locate.M_TO_FT) for c in crumbs if km_now - c[0] <= 8.0]
        mile = [c for c in crumbs if km_now - c[0] <= 1.61]
        mile_ft = (crumbs[-1][1] - mile[0][1]) * locate.M_TO_FT if mile and \
            km_now - mile[0][0] >= 1.2 else None
        return {"alt_ft": crumbs[-1][1] * locate.M_TO_FT, "grade": grade, "spark": spark,
                "drop_ft": drop_ft, "span_km": km_now - spark[0][0], "mile_ft": mile_ft}

    def towns_ahead(self):
        """[('Norris', 6.4), ('Liberty', 13.0)] (name, km) on your heading while driving."""
        fix = self.fix
        if not (self.driving and fix and fix.course is not None):
            return []
        key = (round(fix.lat, 2), round(fix.lon, 2), int(fix.course // 10))
        if key != self._ahead_key:
            self._ahead_key = key
            self._ahead_val = [(p.name, km) for p, km in
                               places_mod.ahead(self.places, fix.lat, fix.lon, fix.course)]
        return self._ahead_val

    def driving_tick(self, now):
        """Moving (GPS says 5 mph or more, until 15 s under 3 mph): lock the menus."""
        fix = self.fix if self.follow else None
        spd = fix.speed_mph if fix and fix.source == "gps" else None
        if spd is not None and (spd >= 5 or (self.driving and spd >= 3)):
            self._moving_at = now
        was = self.driving
        self.driving = bool(self.follow and self._moving_at and now - self._moving_at < 15)
        if self.driving and not was:
            if self.mode in ("pick", "save"):
                self.mode = "main"
                self.flash("driving %s menus locked until you stop" % self.g["dot"], "warn")
            self.dirty = True
        elif was and not self.driving:
            self.dirty = True

    def look_tick(self, now):
        """Pick the day, night or normal colors (auto checks the sun every 30 s)."""
        if now - self._look_at < 30 and self.look is not None:
            return
        self._look_at = now
        look = self.display
        if look == "auto":
            vl = self.view_loc()
            if vl:
                look = "day" if sun.status(vl["lat"], vl["lon"], now)["day"] else "night"
            else:
                look = "day" if 7 <= time.localtime(now).tm_hour < 18 else "night"
        if look != self.look:
            self.look = look
            self.pal.set_mode(look)
            self.dirty = True

    def nearby_states(self):
        """The states within about 25 miles, for the nearby-warnings search."""
        vl = self.view_loc()
        if not vl or not self.places.ready():
            return []
        key = (round(vl["lat"], 1), round(vl["lon"], 1))
        if key != self._states_key:
            found = []
            for deg in (None, 0, 45, 90, 135, 180, 225, 270, 315):
                lat, lon = (vl["lat"], vl["lon"]) if deg is None else \
                    geo.destination(vl["lat"], vl["lon"], deg, 40)
                hit = self.places.nearest(lat, lon)
                if hit and hit[1] < 60 and hit[0].state not in found:
                    found.append(hit[0].state)
            self._states_key, self._states = key, found[:4]
        return self._states

    def nearby_missing(self, now):
        """True right after start-up: the first fetch ran before the town list loaded,
        so it couldn't ask about warnings around you yet."""
        vl = self.view_loc()
        if not vl or now - self._nearby_asked < 60 or not self.nearby_states():
            return False
        e = self.store.entry(vl)
        if "nearby" in e or "nearby_err" in e:
            return False
        self._nearby_asked = now
        return True

    def nearby_tick(self, now):
        """Storm warnings around you; beep for a warning newly ahead within ~15 miles."""
        if now - self._nearby_at < 1:
            return
        self._nearby_at = now
        vl = self.view_loc()
        if not vl:
            self.nearby_items, self.reports = [], []
            return
        entry = self.store.entry(vl)
        heading = self.fix.course if (self.driving and self.fix) else None
        items = views.view_nearby(entry, vl["lat"], vl["lon"], heading, now)
        if [i["id"] for i in items] != [i["id"] for i in self.nearby_items] or \
                [i["km"] for i in items] != [i["km"] for i in self.nearby_items]:
            self.dirty = True
        self.nearby_items = items
        n = self.news_view()
        reports = views.build_reports(views.view_alerts(entry, now), items,
                                      n["items"] if n else [], self.store.cache["feeds"],
                                      vl["lat"], vl["lon"], now)
        if [(r["id"], r["where"], r["when"]) for r in reports] != \
                [(r["id"], r["where"], r["when"]) for r in self.reports]:
            self.dirty = True
        self.reports = reports
        close = [i for i in items if i["ahead"] and i["km"] <= 24]
        fresh = views.new_urgent(self.ahead_memory, close)
        if fresh:
            self.raise_alarm(fresh, "AHEAD")

    def feeds_tick(self, now):
        """Ask for wildfires, space weather and state 511 roads when they're due."""
        vl = self.view_loc()
        c = self.store.config
        fc = self.store.cache["feeds"]
        jobs = []
        if vl and c.get("wildfires"):
            at = (fc.get("fires") or {}).get("at")
            moved = at and geo.km_between(at[0], at[1], vl["lat"], vl["lon"]) > feeds.FIRE_MOVE_KM
            jobs.append(("fires", ("fires", round(vl["lat"], 2), round(vl["lon"], 2)),
                         feeds.FIRE_EVERY, moved))
        if c.get("space_weather"):
            jobs.append(("space", ("space",), feeds.SPACE_EVERY, False))
        if vl:
            states = self.nearby_states()
            for st, key in sorted(c.get("keys_511", {}).items()):
                if key and st in feeds.ROADS and st in states:
                    jobs.append(("roads_" + st, ("roads", st, key), feeds.ROAD_EVERY, False))
        for name, job, every, now_please in jobs:
            e = fc.setdefault(name, {})
            if name in self.feedsf.pending:
                continue
            if now_please or now >= e.get("next", 0):
                e["next"] = now + every
                self.feedsf.request(name, job)

    def feed_problems(self):
        """['wildfires: no connection', ...] for sources whose last try failed."""
        names = {"fires": "wildfires", "space": "space weather", "roads_GA": "511GA",
                 "roads_NC": "NCDOT"}
        return ["%s: %s" % (names.get(k, k), v["err"]) for k, v in
                sorted(self.store.cache["feeds"].items()) if v.get("err")]

    def news_tick(self, now):
        """Search headlines for the area on screen: every 20 minutes, and soon
        after you cross into a new county."""
        vl = self.view_loc()
        if not self.newsf or not vl or not self.places.ready():
            return
        key = (round(vl["lat"], 2), round(vl["lon"], 2))     # ~1 km
        if key != self._area_key:
            self._area_key = key
            area = news.area_for(self.places, vl["lat"], vl["lon"])
            if (area and area["key"]) != (self.news_area and self.news_area["key"]):
                self.dirty = True
            self.news_area = area
        area = self.news_area
        if not area or self.newsf.busy or now < self.news_wait:
            return
        e = self.store.cache["news"].setdefault(area["key"], {})
        e["label"] = area["label"]
        if now < e.get("next", 0):
            return
        e["tried"] = now
        e["next"] = now + news.EVERY_SEC
        self.news_wait = now + news.GAP_SEC
        self.newsf.request(area)

    def news_view(self):
        """Headlines for the area on screen, closest first (those that name a town),
        then the rest newest first. None when headlines are off or there's no spot yet."""
        vl = self.view_loc()
        if not self.newsf or not vl:
            return None
        e = self.store.cache["news"].get(self.news_area["key"]) if self.news_area else None
        n = views.view_news(e)
        n["label"] = n["label"] or (self.news_area or {}).get("label", "")
        items = []
        for it in n["items"]:
            it = dict(it, kind=it.get("kind") or news.kind_of(it["title"]), also=it.get("also", 0))
            if it.get("town"):
                km = geo.km_between(vl["lat"], vl["lon"], it["tlat"], it["tlon"])
                mi = km / 1.609344
                it["km"] = km
                it["place"] = ("in %s" % it["town"]) if mi < 1 else "%s, %d mi %s" % (
                    it["town"], round(mi),
                    geo.compass(geo.bearing(vl["lat"], vl["lon"], it["tlat"], it["tlon"])))
            items.append(it)
        items.sort(key=lambda i: (i.get("km") is None, i.get("km") or 0, -i["time"]))
        n["items"] = items
        return n

    def rough(self):
        """Accuracy text if the live position is only approximate, else None."""
        f = self.fix if self.follow else None
        if f and f.acc_m and f.acc_m > follow.ROUGH_KM * 1000:
            return locate.fmt_accuracy(f.acc_m)
        return None

    # ----------------------------------------------------------- alarms --
    def raise_alarm(self, items, label="NEW"):
        now = time.time()
        self.alarm_label = label
        self.alarm_until = now + 15
        self.alarm_items = items
        self.beeps = [now, now + 0.7]
        self.dirty = True

    def ring(self, now):
        while self.beeps and self.beeps[0] <= now:
            self.beeps.pop(0)
            try:
                curses.beep()
            except (curses.error, AttributeError):
                pass

    def status_tick(self, now):
        """One beep when the DATA LINK or GPS box settles on a new word (warnings beep
        twice, so the two are easy to tell apart). A new word has to hold for
        STATUS_HOLD seconds first, so a flicker doesn't beep. Starting up (CHECKING) and
        turning follow mode on or off yourself (OFF) stay quiet."""
        for key, word in (("link", self.link_state(now)[0]), ("gps", self.gps_state()[0])):
            shown, cand, since = self.status_seen.get(key, (None, None, 0.0))
            if word == shown:
                self.status_seen[key] = (shown, None, 0.0)
            elif word != cand:
                self.status_seen[key] = (shown, word, now)        # start timing the new word
            elif now - since >= STATUS_HOLD:
                if shown is not None and shown not in STATUS_QUIET and word not in STATUS_QUIET:
                    self.beeps = sorted(self.beeps + [now])
                self.status_seen[key] = (word, None, 0.0)

    def alarm_on(self):
        return bool(self.alarm_items) and time.time() < self.alarm_until

    # ------------------------------------------------------------ loop --
    def run(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.scr.keypad(True)
        self.scr.timeout(200)
        if self.loc is None and not self.follow:
            self.open_picker()
        try:
            while True:
                self.drain()
                now = time.time()
                if self.follow:
                    self.follow_tick(now)
                elif self.loc and not self.fetcher.busy:
                    if now >= self.next_refresh:
                        self.start_fetch()
                    elif now >= self.next_alerts or self.nearby_missing(now):
                        self.fetcher.request(self.loc, {"states": self.nearby_states()},
                                             only="alerts")
                        self.next_alerts = now + follow.ALERTS_EVERY_SEC
                if not self.follow:
                    self.driving_tick(now)
                if self.driving and self.mode in ("alerts", "rain") and \
                        now - self.last_key > 20:
                    self.mode = "main"                # back to the main view while moving
                    self.dirty = True
                self.look_tick(now)
                self.feeds_tick(now)
                self.nearby_tick(now)
                self.news_tick(now)
                self.status_tick(now)
                self.ring(now)
                # redraw fast while flashing, slowly when idle (easy on a laptop battery)
                every = 0.25 if self.alarm_on() else 1.0 if (self.fetcher.busy or self.msg) else 5.0
                if self.dirty or now - self.last_draw >= every:
                    self.draw()
                key = self.read_key()
                if key is None:
                    continue
                if self.handle(key) == "quit":
                    break
        finally:
            if self.locator:
                self.locator.stop()
            self.store.save_cache()

    def read_key(self):
        try:
            ch = self.scr.get_wch()
        except curses.error:
            return None
        except KeyboardInterrupt:
            return "quit"
        if isinstance(ch, str):
            if ch in ("\n", "\r"):
                return "enter"
            if ch == "\x1b":
                # the built-in screen already turned arrow-key codes into keys
                return "esc" if BUILTIN else self._escape_sequence()
            if ch in ("\x7f", "\x08"):
                return "backspace"
            if ch == "\x15":
                return "clear"
            if ch == "\x03":
                return "quit"
            return ch
        names = {
            curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_PPAGE: "pgup",
            curses.KEY_NPAGE: "pgdn", curses.KEY_HOME: "home", curses.KEY_END: "end",
            curses.KEY_BACKSPACE: "backspace", curses.KEY_ENTER: "enter",
            curses.KEY_DC: "delete", curses.KEY_RESIZE: "resize",
        }
        for extra, name in (("PADENTER", "enter"), ("KEY_A2", "up"), ("KEY_C2", "down")):
            if hasattr(curses, extra):
                names.setdefault(getattr(curses, extra), name)
        return names.get(ch)

    def _escape_sequence(self):
        """A lone Esc, or arrow/page keys a terminal sent as raw escape codes."""
        seq = ""
        self.scr.timeout(15)
        try:
            for _ in range(4):
                try:
                    c = self.scr.get_wch()
                except curses.error:
                    break
                if not isinstance(c, str):
                    break
                seq += c
                if (c.isalpha() or c == "~") and len(seq) > 1:
                    break
        finally:
            self.scr.timeout(200)
        codes = {"[A": "up", "OA": "up", "[B": "down", "OB": "down", "[5~": "pgup",
                 "[6~": "pgdn", "[3~": "delete", "[H": "home", "OH": "home",
                 "[F": "end", "OF": "end", "[1~": "home", "[4~": "end"}
        return codes.get(seq, "esc")

    def drain(self):
        while True:
            try:
                name, data, error = self.feedsf.results.get_nowait()
            except queue.Empty:
                break
            e = self.store.cache["feeds"].setdefault(name, {})
            self.link_note(error)
            if error:
                e["err"] = error                      # keep what we had; retry 2, 4... min
                e["retry"] = min(2 * (e.get("retry") or 60), 30 * 60)
                e["next"] = time.time() + e["retry"]
            else:
                nxt = e.get("next")
                e.clear()
                e.update(data, next=nxt)
            self.dirty = True
        while self.newsf:
            try:
                key, data, error = self.newsf.results.get_nowait()
            except queue.Empty:
                break
            e = self.store.cache["news"].setdefault(key, {})
            self.link_note(error)
            vl = self.view_loc()
            if data and vl and self.places.ready():
                for it in data["items"]:              # put stories that name a town on the map
                    hit = news.place_in(it["title"], self.places, key.split("|")[0],
                                        vl["lat"], vl["lon"])
                    if hit:
                        it["town"], it["tlat"], it["tlon"] = hit
            if error:
                e["err"] = error                      # keep what we had; retry 2, 4... min
                e["retry"] = min(2 * (e.get("retry") or 60), news.EVERY_SEC)
                e["next"] = time.time() + e["retry"]
            else:
                e.update(data)
                e.pop("err", None)
                e["retry"] = 0
            self.dirty = True
        while True:
            try:
                key, section, data, error = self.fetcher.results.get_nowait()
            except Exception:
                return
            entry = self.store.cache["locations"].setdefault(key, {})
            nws.apply_result(entry, section, data, error)
            if section not in ("start", "done"):
                self.link_note(error, nws_side=True)
            vl = self.view_loc()
            current = vl is not None and key == settings.loc_key(vl)
            if section == "alerts" and data and current:
                fresh = views.new_urgent(entry, data.get("items", []))
                if fresh:
                    ahead = {sid for sid, _ in self.ahead_memory.get("seen", [])}
                    # a warning you were told was ahead is now over you: say so
                    self.raise_alarm(fresh, "NOW HERE" if fresh[0]["id"] in ahead else "NEW")
            if section == "done":
                self.store.save_cache()
                rnd = entry.get("round") or {}
                if current:
                    if rnd.get("err") and not rnd.get("ok"):
                        # nothing came through: try again soon, backing off 1, 2, 4... minutes
                        self.retry = min(self.retry * 2 if self.retry else 60,
                                         60 * self.store.config["refresh_minutes"])
                        if key == "follow":
                            self.not_before = time.time() + self.retry
                            for mark in ("follow_full", "follow_alerts"):
                                if entry.get(mark):
                                    entry[mark]["at"] = 0
                        else:
                            self.next_refresh = time.time() + self.retry
                            self.next_alerts = max(self.next_alerts, self.next_refresh)
                    else:
                        self.retry = 0
            self.dirty = True

    def link_note(self, error, nws_side=False):
        """Keep track of the data link from every reply (or failure) that comes back."""
        now = time.time()
        L = self.link
        if not error:
            L["ok"], L["down_since"] = now, None
            if nws_side:
                L["trouble"] = 0.0                 # NWS is answering properly again
            fix = self.fix if self.follow else None
            if fix:                                # the last place the internet worked
                self.signal_spot = (fix.lat, fix.lon, now)
        elif error in views.OFFLINE_ERRORS:
            L["fail"], L["msg"] = now, error
            L["down_since"] = L["down_since"] or now
        elif nws_side and error != "not found":
            L["trouble"] = now                     # NWS answered, but with an error

    def link_state(self, now=None):
        """(word, color spec, detail) for the DATA LINK box."""
        now = time.time() if now is None else now
        L = self.link
        if not L["ok"] and not L["fail"]:
            return "CHECKING", "label", "no contact yet"
        if L["fail"] > L["ok"]:
            since = L["down_since"] or L["fail"]
            return "OFFLINE", "err", "down %s" % views.ago(now - since).replace(" ago", "")
        if now - L["ok"] > 900:
            return "STALE", "warn", "no contact %s" % views.ago(now - L["ok"]).replace(" ago", "")
        if L["trouble"] and now - L["trouble"] < 600:
            return "LIMITED", "warn", "NWS having trouble"
        return "ONLINE", "ok", "contact %s" % views.ago(now - L["ok"])

    def signal_state(self, now=None):
        """(border spec, [(text, spec)...] first line, second line) for the SIGNAL tile:
        while offline, how far and which way back to the last place with internet."""
        now = time.time() if now is None else now
        g = self.g
        if not self.follow:
            return "where", [("—" if g is UNI else "-", "dim")], "needs follow mode (press f)"
        word = self.link_state(now)[0]
        if word == "CHECKING":
            return "where", [("checking" + g["ell"], "label")], ""
        if word in ("ONLINE", "LIMITED"):
            return "where", [("● " if g is UNI else "", "ok"), ("online", "ok")], \
                "if it drops, this points back to it"
        spot, here = self.signal_spot, self.view_loc()
        if not spot or not here:
            return "warn", [("offline", "warn")], "no online spot saved yet"
        km = geo.km_between(here["lat"], here["lon"], spot[0], spot[1])
        if km < 0.3:
            where = "right about here"
        else:
            where = "%s%.1f mi %s" % (g["approx"], km / 1.609344,
                                      geo.compass(geo.bearing(here["lat"], here["lon"], spot[0], spot[1])))
        return "warn", [(where, "warn"), ("   last online %s" % views.ago(now - spot[2]), "label")], \
            "where you last had internet"

    def gps_state(self):
        """(word, color spec, detail) for the GPS box."""
        if not self.follow:
            return "OFF", "label", "press f to follow"
        fix = self.fix
        kind, text = self.locator.gps_status() if self.locator else ("none", "no GPS")
        if fix and fix.source == "gps" and kind == "fix":
            word = {3: "3D FIX", 2: "2D FIX"}.get(fix.mode, "GPS FIX")
            return word, ("warn" if fix.mode == 2 else "ok"), None
        if fix and fix.source != "gps":
            return "ROUGH", "warn", "Windows location %s" % locate.fmt_accuracy(fix.acc_m)
        if kind == "searching":
            return "NO FIX", "err", "no satellites yet"
        return "NO GPS", "err", self.gps_short(text)

    @staticmethod
    def gps_short(text):
        """The GPS reader's status, short enough for the GPS box (the port is in its title)."""
        text = text or "no GPS"
        m = re.match(r"GPS on (\S+): can't open", text)
        if m:
            return "can't open %s" % m.group(1)
        if re.match(r"GPS on \S+: waiting for data", text):
            return "port open, no data"
        if text.startswith("GPS unplugged"):
            return "unplugged or stopped"
        if text == "no GPS receiver found":
            return "none plugged in"
        return text

    def start_fetch(self):
        if self.follow:
            if self.follow_loc:                  # next follow tick does a full refresh
                self.store.entry(self.follow_loc).pop("follow_full", None)
            self.not_before = 0
            self.retry = 0
            return
        if not self.loc:
            return
        entry = self.store.entry(self.loc)
        self.fetcher.request(self.loc, {"points": entry.get("points"), "qpf": entry.get("qpf"),
                                        "states": self.nearby_states()})
        self.next_refresh = time.time() + 60 * self.store.config["refresh_minutes"]
        self.next_alerts = time.time() + follow.ALERTS_EVERY_SEC
        self.dirty = True

    def flash(self, text, level="ok", secs=4):
        self.msg = (text, level, time.time() + secs)
        self.dirty = True

    def set_location(self, loc):
        if self.follow:
            self.stop_follow()
        i = self.store.saved_index(loc)
        if i >= 0:
            loc = self.store.config["locations"][i]
        self.loc = dict(loc)
        self.retry = 0
        self.store.config["last"] = self.loc
        if not self.store.save_config():
            self.flash("couldn't save settings to " + self.store.home, "warn")
        self.mode = "main"
        self.start_fetch()

    # ------------------------------------------------------------ keys --
    def handle(self, k):
        if k == "resize":
            try:
                curses.update_lines_cols()
            except (AttributeError, curses.error):
                pass
            if os.name == "nt":
                try:
                    curses.resize_term(0, 0)
                except curses.error:
                    pass
            self.dirty = True
            return None
        self.dirty = True
        self.last_key = time.time()
        if self.alarm_on():
            self.alarm_until = 0                 # any key acknowledges a new warning
        return getattr(self, "key_" + self.mode)(k)

    def key_main(self, k):
        if k in ("q", "Q", "quit"):
            return "quit"
        if self.driving and isinstance(k, str) and len(k) == 1 and k in "fFlL/sS123456789":
            self.flash("not while driving %s stop first" % self.g["dot"], "warn")
            return None
        if k in ("d", "D"):
            i = theme.MODES.index(self.display) if self.display in theme.MODES else 0
            self.display = theme.MODES[(i + 1) % len(theme.MODES)]
            self.store.config["display"] = self.display
            self.store.save_config()
            self.look = None                         # re-pick the colors now
            self.flash("display: " + theme.NAMES[self.display])
        elif k in ("r", "R"):
            if self.follow and not self.fix:
                self.start_fetch()
                self.flash("waiting for a location fix", "warn")
            elif not self.view_loc():
                self.flash("pick a location first (press l)", "warn")
            elif self.fetcher.busy:
                self.flash("already updating")
            else:
                self.start_fetch()
                if self.news_area:              # headlines too (still at most one a minute)
                    self.store.cache["news"].setdefault(self.news_area["key"], {})["next"] = 0
                for e in self.store.cache["feeds"].values():
                    e["next"] = 0                   # and fires, roads, space weather
        elif k in ("f", "F"):
            if self.follow:
                self.stop_follow()
                if self.loc:
                    self.next_refresh = 0
                    self.flash("follow off · showing %s" % self.loc["name"])
                else:
                    self.open_picker()
            else:
                self.start_follow()
        elif k in ("p", "P"):
            self.mode = "rain"
        elif k in ("l", "L", "/"):
            self.open_picker()
        elif k in ("s", "S"):
            if self.follow:
                if not self.fix:
                    self.flash("no live location to save yet", "warn")
                else:
                    label = self.place_label(self.fix.lat, self.fix.lon)[0]
                    self.save_target = settings.make_location(label, self.fix.lat, self.fix.lon, label)
                    self.save_text = label[:32]
                    self.mode = "save"
            elif not self.loc:
                self.flash("pick a location first (press l)", "warn")
            else:
                self.save_target = self.loc
                i = self.store.saved_index(self.loc)
                self.save_text = self.store.config["locations"][i]["name"] if i >= 0 \
                    else self.loc["name"]
                self.mode = "save"
        elif k in ("a", "A"):
            self.alert_top = 0
            self.mode = "alerts"
        elif isinstance(k, str) and len(k) == 1 and k in "123456789":
            saved = self.store.config["locations"]
            n = int(k)
            if n <= len(saved):
                self.set_location(saved[n - 1])
            else:
                self.flash("no saved location %d yet (press s to save one)" % n, "warn")
        return None

    def key_rain(self, k):
        if k == "quit":
            return "quit"
        if k in ("esc", "q", "Q", "p", "P", "enter", "backspace"):
            self.mode = "main"
        return None

    def key_alerts(self, k):
        page = max(1, self.scr.getmaxyx()[0] - 6)
        if k in ("esc", "q", "Q", "a", "A", "enter", "backspace", "quit"):
            self.mode = "main"
            return None
        moves = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -page, "pgdn": page,
                 " ": page, "home": -10 ** 6, "end": 10 ** 6}
        if k in moves:
            self.alert_top = max(0, min(self.alert_lines - 1, self.alert_top + moves[k]))
        return None

    def key_save(self, k):
        if k == "esc":
            self.mode = "main"
        elif k == "enter":
            target = self.save_target or self.loc
            name = " ".join(self.save_text.split())[:32] or target["name"]
            saved = self.store.save_location(target, name)
            if not self.follow:
                self.loc = saved
                self.store.config["last"] = self.loc
            ok = self.store.save_config()
            i = self.store.saved_index(saved) + 1
            if not ok:
                self.flash("couldn't write settings file", "err")
            elif i <= 9:
                self.flash("saved as %s, press %d to jump here" % (name, i))
            else:
                self.flash("saved as %s (in the location list)" % name)
            self.mode = "main"
        elif k == "backspace":
            self.save_text = self.save_text[:-1]
        elif k == "clear":
            self.save_text = ""
        elif isinstance(k, str) and len(k) == 1 and k.isprintable() and len(self.save_text) < 32:
            self.save_text += k
        elif k == "quit":
            return "quit"
        return None

    # picker ------------------------------------------------------------
    def open_picker(self):
        self.pick = {"query": "", "items": [], "sel": 0, "top": 0}
        self.refresh_pick()
        self.mode = "pick"

    def refresh_pick(self):
        q = self.pick["query"]
        ql = q.strip().lower()
        items = []
        if not ql or ql in "follow my live location gps":
            items.append(("follow", None, "", "key f"))
        for i, l in enumerate(self.store.config["locations"]):
            hay = (l["name"] + " " + (l.get("label") or "")).lower()
            if not ql or ql in hay:
                hint = ("key %d" % (i + 1)) if i < 9 else ""
                items.append(("saved", l, l.get("label") or "", hint))
        c = places_mod.parse_coords(q)
        if c:
            name = "%.4f, %.4f" % c
            inside = places_mod.in_nws_area(*c)
            items.append(("coords", settings.make_location(name, c[0], c[1]),
                          "" if inside else "outside NWS area", ""))
        elif ql and self.places.ready():
            for p in self.places.search(q, limit=40):
                if p.kind == "Z":
                    loc = settings.make_location((p.label + " " + p.name).strip() if p.label
                                             else "ZIP " + p.name, p.lat, p.lon, p.label)
                    items.append(("zip", loc, p.title, p.detail))
                else:
                    loc = settings.make_location(p.title, p.lat, p.lon, p.label)
                    items.append(("town", loc, "", p.detail))
        self.pick["items"] = items
        self.pick["sel"] = 0
        self.pick["top"] = 0

    def key_pick(self, k):
        p = self.pick
        n = len(p["items"])
        if k == "esc":
            if self.loc or self.follow:
                self.mode = "main"
            elif p["query"]:
                p["query"] = ""
                self.refresh_pick()
            else:
                self.flash("pick a location to start (or press Ctrl+C to quit)", "warn")
        elif k == "quit":
            return "quit"
        elif k in ("up", "down", "pgup", "pgdn", "home", "end") and n:
            step = {"up": -1, "down": 1, "pgup": -8, "pgdn": 8, "home": -n, "end": n}[k]
            p["sel"] = max(0, min(n - 1, p["sel"] + step))
        elif k == "enter":
            if not n:
                if p["query"].strip() and not self.places.ready():
                    self.flash("town list still loading, one second", "warn")
                return None
            kind, loc, _, _ = p["items"][p["sel"]]
            if kind == "follow":
                self.mode = "main"
                if not self.follow:
                    self.start_follow()
                return None
            if kind == "coords" and not places_mod.in_nws_area(loc["lat"], loc["lon"]):
                self.flash("that spot is outside the US area NWS covers - check the minus sign",
                           "err", 6)
                return None
            self.set_location(loc)
        elif k == "delete" and n:
            kind, loc, _, _ = p["items"][p["sel"]]
            if kind == "saved":
                self.store.forget_location(loc)
                self.store.save_config()
                self.flash("forgot %s" % loc["name"])
                self.refresh_pick()
        elif k == "backspace":
            p["query"] = p["query"][:-1]
            self.refresh_pick()
        elif k == "clear":
            p["query"] = ""
            self.refresh_pick()
        elif isinstance(k, str) and len(k) == 1 and k.isprintable() and len(p["query"]) < 60:
            p["query"] += k
            self.refresh_pick()
        return None

def run(store, loc, opts):
    os.environ.setdefault("ESCDELAY", "25")

    def _main(scr):
        try:
            curses.set_escdelay(25)
        except (AttributeError, curses.error):
            pass
        UI(scr, store, loc, opts).run()

    curses.wrapper(_main)
