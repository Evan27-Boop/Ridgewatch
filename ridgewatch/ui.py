"""The full-screen dashboard: what it's showing, follow mode, alarms, keys
and the main loop. Drawing is in panels.py and cluster.py.
"""

import curses
import os
import queue
import re
import time

import subprocess

from . import (backlight, feeds, follow, geo, hospitals, locate, nws, power, settings, sound,
               sun, theme, views)
from . import places as places_mod
from .panels import UNI, Palette, Panels


STATUS_HOLD = 5.0                   # seconds a new DATA LINK / GPS word must last to beep
STATUS_QUIET = ("CHECKING", "STANDBY", "OFF")  # changes to or from these never beep

MOVING_RETURN_SEC = 30             # while moving, a detail screen returns to the gauges after this
PRESS_SHOW_SEC = 0.35               # how long a tapped button stays lit
POWER_EVERY_SEC = 10                # how often the battery level is read
POWEROFF_CMD = ["systemctl", "poweroff"]

CLIMB_STEP_M = 3.0                  # elevation must move this much to count (GPS altitude jitters)


def new_trip():
    return {"km": 0.0, "moving": 0.0, "stopped": 0.0, "top": 0.0, "start": None, "t0": None,
            "last": None, "crumbs": [], "stopped_at": None, "heading": None,
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
        self.g = UNI
        self.pal = Palette()
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
        # driving: the GPS says you're moving (detail screens time out back to the gauges)
        self.driving = False
        # touch: where the tappable things were drawn (filled in by draw), the last tap
        self.hits, self.buttons, self.pick_rows = [], [], []
        self.pressed = None                     # (action, lit until)
        self.power, self._power_at = None, 0.0
        self.read_battery = power.battery
        self.off_since = None                   # when outside power was lost (shutdown countdown)
        self.keep_running = False               # KEEP RUNNING tapped: no shutdown until power returns
        self.off_warned = False                 # the one-minute-left beep
        self.shutdown_failed = False
        self.poweroff_cmd = POWEROFF_CMD
        self.speaker = sound.Speaker(store.home, self._bell,
                                     max_volume=store.config.get("max_volume", True))
        self.alert_beeps = []                   # times to play the double beep (new warnings)
        self.backlight = backlight.Backlight() if store.config.get("brightness") else None
        self._moving_at = 0
        self.last_key = time.time()
        # storm warnings around you (and which ones ahead you've been told about)
        self.nearby_items = []
        self._nearby_at = 0
        self.ahead_memory = {}
        self._states_key, self._states = None, []
        self._nearby_asked = 0
        # wildfires and state 511 roads: their own thread too
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
        self.waypoint = {"kind": "start"}       # where WAYPOINT points: start, signal or place
        self.wpick = None
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
            self.locator = locate.Locator(c.get("gps_port", "auto"), c.get("gps_baud", 9600))
        last = (self.store.cache["locations"].get("follow") or {}).get("follow_full")
        if last and self.follow_loc is None:
            self.follow_loc = self.make_follow_loc(last["lat"], last["lon"])
        self.not_before = 0
        self.retry = 0
        if not initial:
            self.store.config["follow"] = True
            self.store.save_config()
            self.flash("Following the GPS")
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
        """Count distance and moving time from GPS fixes."""
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
        if fix.course is not None and spd >= 3:
            t["heading"] = (fix.course, fix.time)   # kept for the compass while stopped
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
                self.flash("Moving. Menus are locked.", "warn")
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
            if self.backlight:
                self.backlight.set_pct(self.store.config["brightness"].get(look, 100))

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
        reports = views.build_reports(views.view_alerts(entry, now), items,
                                      self.store.cache["feeds"], vl["lat"], vl["lon"], now)
        if [(r["id"], r["where"], r["when"]) for r in reports] != \
                [(r["id"], r["where"], r["when"]) for r in self.reports]:
            self.dirty = True
        self.reports = reports
        close = [i for i in items if i["ahead"] and i["km"] <= 24]
        fresh = views.new_urgent(self.ahead_memory, close)
        if fresh:
            self.raise_alarm(fresh, "AHEAD")

    def feeds_tick(self, now):
        """Ask for wildfires and state 511 roads when they're due."""
        vl = self.view_loc()
        c = self.store.config
        fc = self.store.cache["feeds"]
        jobs = []
        if vl and c.get("wildfires"):
            at = (fc.get("fires") or {}).get("at")
            moved = at and geo.km_between(at[0], at[1], vl["lat"], vl["lon"]) > feeds.FIRE_MOVE_KM
            jobs.append(("fires", ("fires", round(vl["lat"], 2), round(vl["lon"], 2)),
                         feeds.FIRE_EVERY, moved))
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
        names = {"fires": "wildfires", "roads_GA": "511GA", "roads_NC": "NCDOT"}
        return ["%s: %s" % (names.get(k, k), v["err"]) for k, v in
                sorted(self.store.cache["feeds"].items()) if v.get("err")]

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
        self.alert_beeps = [now]                # one double beep
        self.dirty = True

    def ring(self, now):
        while self.alert_beeps and self.alert_beeps[0] <= now:
            self.alert_beeps.pop(0)
            if not self.speaker.beep("alert"):
                self.beeps = sorted(self.beeps + [now + 0.5])   # no speaker: two terminal bells
        while self.beeps and self.beeps[0] <= now:
            self.beeps.pop(0)
            self.speaker.beep()

    @staticmethod
    def _bell():
        try:
            curses.beep()
        except (curses.error, AttributeError):
            pass

    def moving_guard(self, now):
        """While moving, a detail screen or list goes back to the gauges after
        MOVING_RETURN_SEC without a tap or key, so the gauges are never hidden for long."""
        if self.driving and self.mode in ("alerts", "rain", "pick", "save", "wpick") and \
                (self.view_loc() or self.follow) and now - self.last_key > MOVING_RETURN_SEC:
            self.mode = "main"
            self.dirty = True

    def power_tick(self, now):
        """Battery level, and the clean shutdown after outside power is lost: the ignition
        turned off. Power coming back, or KEEP RUNNING, calls it off."""
        if now - self._power_at >= POWER_EVERY_SEC:
            self._power_at = now
            b = self.read_battery()
            if b != self.power:
                self.power = b
                self.dirty = True
        limit = self.store.config.get("shutdown_after_min", 10) * 60
        external = self.power["external"] if self.power else True   # no battery: nothing to do
        if external:
            if self.off_since is not None or self.keep_running:
                self.flash("Outside power is back")
            self.off_since, self.keep_running = None, False
            self.off_warned = self.shutdown_failed = False
            return
        if not limit or self.keep_running:
            return
        if self.off_since is None:
            self.off_since = now
            self.beeps = sorted(self.beeps + [now])
            self.flash("Outside power lost. Shutting down in %d minutes." % (limit // 60), "warn", 8)
            return
        left = limit - (now - self.off_since)
        if left <= 60 and not self.off_warned:
            self.off_warned = True
            self.beeps = sorted(self.beeps + [now])
        if left <= 0 and not self.shutdown_failed:
            self.shutdown()

    def shutdown_left(self, now=None):
        """Seconds until the power-loss shutdown, or None if none is coming."""
        if self.off_since is None or self.shutdown_failed:
            return None
        now = time.time() if now is None else now
        limit = self.store.config.get("shutdown_after_min", 10) * 60
        return max(0, limit - (now - self.off_since))

    def shutdown(self):
        self.store.save_cache()
        try:
            rc = subprocess.call(self.poweroff_cmd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except (OSError, subprocess.SubprocessError):
            rc = -1
        if rc != 0:
            self.shutdown_failed = True
            self.flash("Could not shut down. Install polkit on this machine.", "err", 30)

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
        try:                                     # taps arrive as mouse clicks
            curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED)
            curses.mouseinterval(0)              # report the press at once, don't wait for a click
        except (curses.error, AttributeError):
            pass
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
                self.moving_guard(now)
                self.look_tick(now)
                self.power_tick(now)
                self.feeds_tick(now)
                self.nearby_tick(now)
                self.status_tick(now)
                self.ring(now)
                # redraw fast while flashing, slowly when idle (easy on a laptop battery)
                every = 0.25 if self.alarm_on() else 1.0 if (self.fetcher.busy or self.msg) else 5.0
                if self.off_since is not None:
                    every = min(every, 1.0)           # the shutdown countdown
                if self.pressed and now < self.pressed[1] + 0.3:
                    every = 0.1                       # unlight the tapped button on time
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
                return self._escape_sequence()
            if ch in ("\x7f", "\x08"):
                return "backspace"
            if ch == "\x15":
                return "clear"
            if ch == "\x03":
                return "quit"
            return ch
        if ch == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
            except curses.error:
                return None
            if state & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                return ("tap", y, x)
            return None
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
            if not self.view_loc():               # nothing to ask about yet: not a link problem
                return "STANDBY", "label", "No location set"
            return "CHECKING", "label", "No reply yet"
        if L["fail"] > L["ok"]:
            since = L["down_since"] or L["fail"]
            return "OFFLINE", "err", "Down %s" % views.ago(now - since).replace(" ago", "")
        if now - L["ok"] > 900:
            return "STALE", "warn", "No reply for %s" % views.ago(now - L["ok"]).replace(" ago", "")
        if L["trouble"] and now - L["trouble"] < 600:
            return "LIMITED", "warn", "NWS is returning errors"
        return "ONLINE", "ok", "Last reply %s" % views.ago(now - L["ok"])

    def last_online_text(self, now=None):
        """'Last online ≈2.3 mi NE' while offline in follow mode (the way back to signal)."""
        if not self.follow or self.link_state(now)[0] not in ("OFFLINE", "STALE"):
            return ""
        spot, here = self.signal_spot, self.view_loc()
        if not spot or not here:
            return "No connection point recorded yet"
        km = geo.km_between(here["lat"], here["lon"], spot[0], spot[1])
        if km < 0.3:
            return "Last online at this position"
        return "Last online %s%.1f mi %s" % (self.g["approx"], km / 1.609344, geo.compass(
            geo.bearing(here["lat"], here["lon"], spot[0], spot[1])))

    # -------------------------------------------------------- waypoint --
    def waypoint_target(self):
        """(name, (lat, lon) or None, note) for the WAYPOINT tile."""
        wp = self.waypoint
        if wp["kind"] == "signal":
            spot = self.signal_spot
            if not spot:
                return "Last spot with internet", None, "No connection recorded on this trip yet"
            return "Last spot with internet", spot[:2], "online %s" % views.ago(time.time() - spot[2])
        if wp["kind"] == "place":
            return wp["name"], (wp["lat"], wp["lon"]), "saved place"
        t = self.trip if self.follow else None
        if not self.follow:
            return "Trip start", None, "Set when you follow the GPS"
        if not t or not t.get("start"):
            return "Trip start", None, "Set by the first GPS position"
        when = time.strftime("%I:%M %p", time.localtime(t["t0"])).lstrip("0") if t.get("t0") else ""
        return "Trip start", t["start"][:2], ("started %s" % when) if when else ""

    def open_wpick(self):
        items = [("start", "Trip start", "where following the GPS began"),
                 ("signal", "Last spot with internet", "where a reply last came through")]
        for l in self.store.config["locations"]:
            items.append(("place", l, "saved place"))
        cur = self.waypoint
        sel = 0
        for i, (kind, val, _) in enumerate(items):
            if kind == cur["kind"] and (kind != "place" or (val["lat"], val["lon"]) ==
                                        (cur.get("lat"), cur.get("lon"))):
                sel = i
        self.wpick = {"items": items, "sel": sel, "top": 0}
        self.mode = "wpick"

    def key_wpick(self, k):
        p = self.wpick
        n = len(p["items"])
        if k == "quit":
            return "quit"
        if k in ("esc", "q", "Q", "backspace"):
            self.mode = "main"
        elif k in ("up", "down", "pgup", "pgdn", "home", "end"):
            page = p.get("page") or 8
            step = {"up": -1, "down": 1, "pgup": -page, "pgdn": page, "home": -n, "end": n}[k]
            p["sel"] = max(0, min(n - 1, p["sel"] + step))
        elif k == "enter" and n:
            kind, val, _ = p["items"][p["sel"]]
            if kind == "place":
                self.waypoint = {"kind": "place", "name": val["name"], "lat": val["lat"],
                                 "lon": val["lon"]}
                name = val["name"]
            else:
                self.waypoint = {"kind": kind}
                name = "the trip start" if kind == "start" else "the last spot with internet"
            self.flash("Waypoint set to %s" % name)
            self.mode = "main"
        return None

    def gps_state(self):
        """(word, color spec, detail) for the GPS box."""
        if not self.follow:
            return "OFF", "label", "Follow mode is off. Tap FOLLOW GPS."
        fix = self.fix
        kind, text = self.locator.gps_status() if self.locator else ("none", "no GPS")
        if fix and fix.source == "gps" and kind == "fix":
            word = {3: "3D FIX", 2: "2D FIX"}.get(fix.mode, "GPS FIX")
            return word, ("warn" if fix.mode == 2 else "ok"), None
        if kind == "searching":
            return "NO FIX", "err", "Acquiring satellites"
        return "NO GPS", "err", self.gps_short(text)

    @staticmethod
    def gps_short(text):
        """The GPS reader's status, short enough for the GPS box (the port is in its title)."""
        text = text or "no GPS"
        m = re.match(r"GPS on (\S+): cannot open", text)
        if m:
            return "Cannot open %s" % m.group(1)
        if re.match(r"GPS on \S+: waiting for data", text):
            return "Port open, no data"
        if text.startswith("GPS disconnected"):
            return "Receiver disconnected"
        if text == "No GPS receiver detected":
            return "No receiver detected"
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
            self.flash("Could not save settings to " + self.store.home, "warn")
        self.mode = "main"
        self.start_fetch()

    # ------------------------------------------------------------ keys --
    def handle(self, k):
        if k == "resize":
            try:
                curses.update_lines_cols()
            except (AttributeError, curses.error):
                pass
            self.dirty = True
            return None
        self.dirty = True
        self.last_key = time.time()
        if isinstance(k, tuple):
            if self.alarm_on():
                self.alarm_until = 0             # the first tap only silences a new warning
                return None
            return self.tap(k[1], k[2])
        if self.alarm_on():
            self.alarm_until = 0                 # any key acknowledges a new warning
        return getattr(self, "key_" + self.mode)(k)

    # ----------------------------------------------------------- touch --
    @staticmethod
    def _inside(y, x, ry, rx, rh, rw):
        return ry <= y < ry + rh and rx <= x < rx + rw

    def tap(self, y, x):
        """A tap at screen row y, column x: a bar button, a tile, or a row in a list."""
        for action, by, bx, bh, bw in self.buttons:
            if self._inside(y, x, by, bx, bh, bw):
                self.pressed = (action, time.time() + PRESS_SHOW_SEC)
                return self.press(action)
        if self.mode == "main":
            for name, ty, tx, th, tw in self.hits:
                if self._inside(y, x, ty, tx, th, tw):
                    if name == "waypoint":
                        self.open_wpick()
                        return None
                    return self.key_main({"alerts": "a", "weather": "p"}[name])
        elif self.mode in ("pick", "wpick"):
            lst = self.pick if self.mode == "pick" else self.wpick
            for ry, rx, rw, i in self.pick_rows:
                if self._inside(y, x, ry, rx, 2, rw):
                    lst["sel"] = i
                    return getattr(self, "key_" + self.mode)("enter")
        elif self.mode in ("alerts", "rain"):
            return None                          # reading: taps on the text do nothing
        return None

    def press(self, action):
        """What each bar button does. Most reuse the key that does the same thing."""
        if action == "keep":
            self.keep_running, self.off_since = True, None
            self.flash("Shutdown cancelled. It stays on until outside power is lost again.")
            return None
        if action == "save":
            return self.save_here()
        if action == "places":
            self.open_picker()
            return None
        if action == "save_ok":
            return self.key_save("enter")
        if action == "back":
            return getattr(self, "key_" + self.mode)("esc")
        if action in ("up", "down"):
            return getattr(self, "key_" + self.mode)("pgup" if action == "up" else "pgdn")
        return self.key_main({"display": "d", "refresh": "r", "follow": "f"}[action])

    def save_here(self):
        """SAVE HERE: keep this spot under the nearest town's name, without typing."""
        if self.follow:
            if not self.fix:
                self.flash("No GPS position to save yet", "warn")
                return None
            name = self.place_label(self.fix.lat, self.fix.lon)[0]
            target = settings.make_location(name, self.fix.lat, self.fix.lon, name)
        elif self.loc:
            target, name = self.loc, self.loc["name"]
            if self.store.saved_index(self.loc) >= 0:
                self.flash("%s is already saved. Find it under PLACES." % name)
                return None
        else:
            self.flash("Set a location first. Tap PLACES.", "warn")
            return None
        saved = self.store.save_location(target, name[:32])
        if not self.follow:
            self.loc = saved
            self.store.config["last"] = self.loc
        if self.store.save_config():
            self.flash("Saved as %s. Find it under PLACES." % name[:32])
        else:
            self.flash("Could not write the settings file", "err")
        return None

    def key_main(self, k):
        if k in ("q", "Q", "quit"):
            return "quit"
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
                self.flash("Waiting for a GPS fix", "warn")
            elif not self.view_loc():
                self.flash("Set a location first. Tap PLACES.", "warn")
            elif self.fetcher.busy:
                self.flash("Update already in progress")
            else:
                self.start_fetch()
                for e in self.store.cache["feeds"].values():
                    e["next"] = 0                   # and fires and roads
        elif k in ("f", "F"):
            if self.follow:
                self.stop_follow()
                if self.loc:
                    self.next_refresh = 0
                    self.flash("Follow mode off. Showing %s." % self.loc["name"])
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
                    self.flash("No GPS position to save yet", "warn")
                else:
                    label = self.place_label(self.fix.lat, self.fix.lon)[0]
                    self.save_target = settings.make_location(label, self.fix.lat, self.fix.lon, label)
                    self.save_text = label[:32]
                    self.mode = "save"
            elif not self.loc:
                self.flash("Set a location first. Tap PLACES.", "warn")
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
                self.flash("No saved location %d. Tap SAVE HERE to save one." % n, "warn")
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
            if not ok:
                self.flash("Could not write the settings file", "err")
            else:
                self.flash("Saved as %s. Find it under PLACES." % name)
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
                self.flash("Pick a place or follow the GPS to start", "warn")
        elif k == "quit":
            return "quit"
        elif k in ("up", "down", "pgup", "pgdn", "home", "end") and n:
            page = p.get("page") or 8
            step = {"up": -1, "down": 1, "pgup": -page, "pgdn": page, "home": -n, "end": n}[k]
            p["sel"] = max(0, min(n - 1, p["sel"] + step))
        elif k == "enter":
            if not n:
                if p["query"].strip() and not self.places.ready():
                    self.flash("Place data is still loading", "warn")
                return None
            kind, loc, _, _ = p["items"][p["sel"]]
            if kind == "follow":
                self.mode = "main"
                if not self.follow:
                    self.start_follow()
                return None
            if kind == "coords" and not places_mod.in_nws_area(loc["lat"], loc["lon"]):
                self.flash("Outside the NWS coverage area. Check the sign of the longitude.",
                           "err", 6)
                return None
            self.set_location(loc)
        elif k == "delete" and n:
            kind, loc, _, _ = p["items"][p["sel"]]
            if kind == "saved":
                self.store.forget_location(loc)
                self.store.save_config()
                self.flash("Removed %s" % loc["name"])
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
