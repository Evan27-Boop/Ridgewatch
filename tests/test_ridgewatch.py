"""ridgewatch tests. Run from the project folder:  python -m unittest

Everything runs offline: NWS is replaced by tests/fake_nws.py, GPS by text
written to a pseudo-terminal.
"""

import os
import shutil
import sys
import tempfile
import time
import types
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tests.fake_nws import (FakeNWS, alert, alerts, duke, faa_xml, fires, grid,  # noqa: E402
                            hourly, ipaws_cap, ipaws_feed, lsr_text, obs, points, road_events,
                            stations, usgs)
from ridgewatch import (backlight, feeds, follow, geo, hospitals, locate, nws,  # noqa: E402
                        panels, places, plain, power, settings, sound, sources, sun, theme, ui,
                        views, volume)
from ridgewatch.panels import UNI  # noqa: E402


def nmea(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return "$%s*%02X" % (body, c)


GGA = nmea("GPGGA,123519,3441.0042,N,08250.2452,W,1,09,0.9,265.0,M,-32.0,M,,")
RMC = nmea("GPRMC,123519,A,3441.0042,N,08250.2452,W,40.00,47.0,061126,,,A")


class Places(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ix = places.PlaceIndex()
        cls.ix.load()

    def test_list_loads(self):
        self.assertIsNone(self.ix.error)
        self.assertGreater(len(self.ix.places), 30000)
        self.assertGreater(len(self.ix.zips), 30000)

    def test_search(self):
        self.assertEqual(self.ix.search("clemson sc")[0].title, "Clemson, SC")
        self.assertEqual(self.ix.search("29631")[0].name, "29631")
        self.assertEqual(self.ix.search("salem south carolina")[0].state, "SC")
        st_louis = [p for p in self.ix.search("st louis mo") if p.norm == "saint louis"]
        self.assertEqual(len(st_louis), 1)                 # "St. Louis" and "Saint Louis" merged
        self.assertEqual(self.ix.search("zzqx"), [])

    def test_coordinates(self):
        self.assertEqual(places.parse_coords("34.6834, -82.8374"), (34.6834, -82.8374))
        self.assertEqual(places.parse_coords("34.6834N 82.8374W"), (34.6834, -82.8374))
        self.assertIsNone(places.parse_coords("91, 0"))
        self.assertIsNone(places.parse_coords("hello"))
        self.assertTrue(places.in_nws_area(34.68, -82.83))
        self.assertFalse(places.in_nws_area(34.68, 82.83))     # missing minus sign

    def test_nearest_and_describe(self):
        p, km = self.ix.nearest(34.8879, -82.9752)
        self.assertEqual(p.title, "Salem, SC")
        text, county = places.describe_spot(self.ix, 34.95, -82.88)
        self.assertRegex(text, r"^\d+\.\d mi [NSEW]{1,3} of .+, SC$")
        self.assertEqual(county, "Pickens Co")


class Gps(unittest.TestCase):
    def test_nmea(self):
        s = locate.NmeaState()
        self.assertTrue(s.feed(GGA))
        self.assertTrue(s.feed(RMC))
        f = s.fix()
        self.assertAlmostEqual(f.lat, 34.68340, places=4)
        self.assertAlmostEqual(f.lon, -82.83742, places=4)
        self.assertEqual((f.sats, f.alt_m, f.course), (9, 265.0, 47.0))
        self.assertAlmostEqual(f.speed_mph, 46.0, places=0)

    def test_2d_and_3d_fix(self):
        s = locate.NmeaState()
        s.feed(GGA)
        s.feed(RMC)
        self.assertIsNone(s.fix().mode)                       # no GSA yet: mode unknown
        s.feed(nmea("GPGSA,A,3,01,02,03,04,05,06,07,08,09,,,,1.5,0.9,1.2"))
        self.assertEqual(s.fix().mode, 3)
        s.feed(nmea("GPGSA,A,2,01,02,03,,,,,,,,,,2.5,2.3,0.9"))
        self.assertEqual(s.fix().mode, 2)
        s.feed(nmea("GPGSA,A,1,,,,,,,,,,,,,,,"))             # 1 = no fix
        self.assertIsNone(s.fix().mode)

    def test_nmea_rejects(self):
        s = locate.NmeaState()
        self.assertFalse(s.feed(GGA[:-2] + "00"))                    # bad checksum
        self.assertFalse(s.feed(nmea("GPGGA,123519,3441.0,N,08250.2,W,0,00,,,M,,M,,")))  # no fix
        self.assertFalse(s.feed("garbage \x00\xff"))
        self.assertIsNone(s.fix())

    def test_accuracy_text(self):
        self.assertEqual(locate.fmt_accuracy(4.5), "±15 ft")
        self.assertEqual(locate.fmt_accuracy(3218.5), "±2.0 mi")
        self.assertEqual(locate.fmt_accuracy(40000), "±25 mi")

    def test_reads_a_serial_port(self):
        import pty
        master, slave = pty.openpty()
        reader = locate.GpsReader(os.ttyname(slave))
        try:
            end = time.time() + 5
            while time.time() < end and not reader.current():
                os.write(master, (GGA + "\r\n" + RMC + "\r\n").encode())
                time.sleep(0.3)
            f = reader.current()
            self.assertIsNotNone(f)
            self.assertEqual(f.source, "gps")
        finally:
            reader.stop()
            os.close(master)


class Nws(unittest.TestCase):
    def test_parsers(self):
        now = time.time()
        pts = nws.parse_points(points("http://x", 34.68, -82.83)[1])
        self.assertEqual(pts["office"], "GSP")
        self.assertEqual([s["id"] for s in nws.parse_stations(stations())], ["KXXX", "KCEU"])
        o = nws.parse_obs(obs("KCEU", {}), {"id": "KCEU"})
        self.assertEqual(round(o["temp_f"]), 54)
        self.assertEqual(round(o["wind_mph"]), 8)
        self.assertIsNone(o["feels_f"])                      # wind chill only counts at 50°F or below
        h = nws.parse_hourly(hourly(now), now=now)
        self.assertGreaterEqual(len(h["periods"]), 24)
        q = nws.parse_qpf(grid(now), now=now)
        self.assertTrue(q["intervals"])
        a = nws.parse_alerts(alerts({"alerts": "two"}))
        self.assertEqual([i["event"] for i in a["items"]], ["Flood Watch", "Wind Advisory"])

    def test_rain_total_is_prorated(self):
        now = 1800000000
        e = {"qpf": {"intervals": [[now - 3 * 3600, now + 3 * 3600, 6.0],
                                   [now + 21 * 3600, now + 27 * 3600, 25.4]], "fetched": now}}
        p = views.view_precip(e, now=now)
        self.assertAlmostEqual(p["total_in"], (3.0 + 12.7) / 25.4, places=4)

    def test_alert_updates_point_back(self):
        now = time.time()
        feat = alert("urn:new", "Tornado Warning", "Extreme", now, now + 1800,
                     references=[{"@id": "https://api.weather.gov/alerts/urn:old",
                                  "identifier": "urn:old", "sender": "w-nws.webmaster@noaa.gov"},
                                 {"@id": "https://api.weather.gov/alerts/urn:older"}])
        a = nws.parse_alerts({"features": [feat]})
        self.assertEqual(a["items"][0]["refs"], ["urn:old", "urn:older"])

    def test_uses_less_data(self):
        """Coordinates go out at ~100 m; the big rain download is reused for an hour."""
        now = time.time()
        asked = []

        def getter(url, ua):
            asked.append(url)
            if "/alerts/" in url:
                return alerts({"alerts": "none"})
            if "/points/" in url:
                return points("http://x", 34.68, -82.83)[1]
            if url.endswith("/forecast/hourly"):
                return hourly(now)
            if url.endswith("/stations"):
                return stations()
            if "/observations/" in url:
                return obs(url.split("/")[-3], {})
            return grid(now)

        loc = settings.make_location("Clemson, SC", 34.683412, -82.836123)
        got = {}

        def post(section, data=None, error=None):
            got[section] = data if error is None else error

        nws.fetch_location(loc, {}, post, "ua", getter=getter)
        self.assertTrue(asked[0].endswith("/alerts/active?point=34.683,-82.836"))
        self.assertFalse(any("34.6834" in u for u in asked))      # never more precise
        self.assertEqual(got["qpf"]["at"], [34.683, -82.836])
        snap = {"points": got["points"], "qpf": got["qpf"]}
        asked[:] = []
        nws.fetch_location(loc, snap, post, "ua", getter=getter)
        self.assertFalse(any(u.endswith("/10,20") for u in asked))  # rain amounts reused
        far = settings.make_location("Seneca, SC", 34.85, -82.95)  # ~13 mi away
        asked[:] = []
        nws.fetch_location(far, snap, post, "ua", getter=getter)
        self.assertTrue(any(u.endswith("/10,20") for u in asked))   # too far: fetched again
        snap["qpf"] = dict(got["qpf"], fetched=now - 4000)
        asked[:] = []
        nws.fetch_location(loc, snap, post, "ua", getter=getter)
        self.assertTrue(any(u.endswith("/10,20") for u in asked))   # over an hour old

    def test_expired_alerts_hidden(self):
        now = time.time()
        e = {"alerts": {"fetched": now - 3600, "items": [
            {"event": "Old", "severity": "Minor", "ends": (now - 60, -300), "onset": None},
            {"event": "Live", "severity": "Severe", "ends": (now + 600, -300), "onset": None}]}}
        v = views.view_alerts(e, now)
        self.assertEqual([i["event"] for i in v["items"]], ["Live"])
        self.assertFalse(v["trusted"])                      # an hour without re-checking


class Follow(unittest.TestCase):
    def test_timing(self):
        now, e = 1800000000, {}
        self.assertEqual(follow.follow_due(e, 34.68, -82.83, 900, now), "full")
        follow.follow_mark(e, "full", 34.68, -82.83, now)
        self.assertIsNone(follow.follow_due(e, 34.68, -82.83, 900, now + 10))
        self.assertEqual(follow.follow_due(e, 34.68, -82.83, 900, now + 200), "alerts")
        self.assertEqual(follow.follow_due(e, 34.71, -82.83, 900, now + 30), "alerts")   # ~2 mi
        self.assertEqual(follow.follow_due(e, 34.76, -82.83, 900, now + 30), "full")     # ~5.5 mi
        self.assertEqual(follow.follow_due(e, 34.68, -82.83, 900, now + 960), "full")

    def test_urgent_alerts_once(self):
        items = [{"id": "a", "event": "Wind Advisory"}, {"id": "b", "event": "Tornado Warning"},
                 {"id": "c", "event": "Child Abduction Emergency"}, {"id": "d", "event": "Flood Watch"},
                 {"id": "e", "event": "Evacuation Immediate"}]
        e = {}
        self.assertEqual([i["id"] for i in views.new_urgent(e, items)], ["b", "c", "e"])
        self.assertEqual(views.new_urgent(e, items), [])

    def test_warning_updates_stay_quiet(self):
        e = {"seen": ["old-style-id"]}                          # saved by version 2.0
        svr = {"id": "s1", "event": "Severe Thunderstorm Warning"}
        self.assertEqual(views.new_urgent(e, [svr]), [svr])
        upd = {"id": "s2", "event": "Severe Thunderstorm Warning", "refs": ["s1"]}
        self.assertEqual(views.new_urgent(e, [upd]), [])          # same warning, updated
        tor = {"id": "t1", "event": "Tornado Warning", "refs": ["s2"]}
        self.assertEqual(views.new_urgent(e, [tor]), [tor])       # upgraded: tell me
        odd = {"id": "x2", "event": "Tornado Warning", "refs": ["old-style-id"]}
        self.assertEqual(views.new_urgent(e, [odd]), [odd])       # unsure what it was: tell me


class Offline(unittest.TestCase):
    """The pieces that work with no signal: sun times, nearest ER, the colors."""

    def test_sun_times(self):
        from datetime import datetime, timedelta, timezone
        est = timezone(timedelta(hours=-5))
        rise, set_ = sun.times(47.6062, -122.3321,
                               datetime(2026, 12, 21, 12, tzinfo=timezone(timedelta(hours=-8))).timestamp())
        pst = timezone(timedelta(hours=-8))
        self.assertEqual(datetime.fromtimestamp(rise, pst).strftime("%H:%M")[:4], "07:5")  # Seattle 7:55
        self.assertEqual(datetime.fromtimestamp(set_, pst).strftime("%H:%M")[:4], "16:2")  # 4:20
        noon = datetime(2026, 11, 6, 12, tzinfo=est).timestamp()
        st = sun.status(34.68, -82.84, noon)
        self.assertTrue(st["day"])
        self.assertEqual(st["next"][0], "sunset")
        self.assertFalse(sun.status(34.68, -82.84, noon + 10 * 3600)["day"])
        self.assertEqual(sun.times(71.29, -156.79, noon - 140 * 86400), ("up", "up"))  # June, Alaska

    def test_nearest_er(self):
        idx = hospitals.HospitalIndex()
        idx.load()
        self.assertGreater(len(idx.items), 4000)
        h, km = idx.nearest(34.6834, -82.8374)              # Clemson
        self.assertIn("Oconee Memorial", h.name)
        self.assertTrue(h.listed)
        self.assertLess(km, 20)

    def test_day_colors_read_on_white(self):
        for normal, (day, night) in theme._PAIRS.items():
            contrast = 1.05 / (theme.luminance(day) + 0.05)
            self.assertGreaterEqual(contrast, 4.5, (normal, day))
        self.assertEqual(theme.adapt((253, ""), "day")[1], "bold")
        self.assertEqual(theme.adapt((253, "bold"), "normal"), (253, "bold"))


class LocationExtras(unittest.TestCase):
    def test_grid_and_radio_codes(self):
        self.assertEqual(geo.mgrs(34.6834, -82.8374), "17S LU 3168 3947")   # checked against mgrs lib
        self.assertEqual(geo.mgrs(21.3069, -157.8583), "4Q FJ 1841 5654")
        self.assertIsNone(geo.mgrs(85.0, 0.0))
        self.assertEqual(geo.same_code("SCC077"), "045077")
        self.assertIsNone(geo.same_code("SCZ001"))                          # a forecast zone

    def test_moon(self):
        from datetime import datetime, timezone
        full = datetime(2026, 11, 24, 12, tzinfo=timezone.utc).timestamp()
        new = datetime(2026, 11, 9, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(sun.moon(full), (100, "full moon"))
        self.assertLessEqual(sun.moon(new)[0], 2)

    def test_towns_ahead(self):
        ix = places.PlaceIndex()
        ix.load()
        north = [p.name for p, km in places.ahead(ix, 34.7072, -82.78, 0)]
        west = [p.name for p, km in places.ahead(ix, 34.7072, -82.78, 270)]
        self.assertIn("Norris", north)
        self.assertEqual(west[0], "Seneca")

    def test_trip(self):
        from ridgewatch import ui
        stub = types.SimpleNamespace(trip=ui.new_trip(),
                                     place_label=lambda lat, lon: ("here", ""),
                                     region_check=lambda fix: None)
        t0 = 1800000000
        for i in range(11):                        # 10 s at 60 mph straight north
            ui.UI.trip_add(stub, locate.Fix(34.70 + i * 0.000241, -82.78, "gps",
                                            speed_mph=60, t=t0 + i))
        for i in range(10):                        # parked: GPS wanders, nothing counted
            ui.UI.trip_add(stub, locate.Fix(34.70241 + (i % 2) * 0.00003, -82.78, "gps",
                                            speed_mph=0.4, t=t0 + 11 + i))
        self.assertAlmostEqual(stub.trip["km"], 0.268, delta=0.01)       # 1/6 mile
        self.assertEqual((stub.trip["moving"], stub.trip["top"]), (10, 60))
        self.assertEqual(stub.trip["stopped"], 10)                        # 10 s parked
        self.assertEqual(stub.trip["t0"], t0)

    def test_what_gps_altitude_says(self):
        from ridgewatch import ui
        crumbs = [(i * 0.05, 300.0 + i * 0.05 * 60) for i in range(40)]   # 2 km at a 6% climb
        stub = types.SimpleNamespace(trip={"crumbs": crumbs})
        t = ui.UI.terrain(stub)
        self.assertAlmostEqual(t["grade"], 6.0, delta=0.2)
        self.assertIsNone(t["drop_ft"])                          # under 2.5 km of history
        down = [(i * 0.1, 900.0 - i * 0.1 * 70) for i in range(35)]      # 3.4 km at -7%
        t = ui.UI.terrain(types.SimpleNamespace(trip={"crumbs": down}))
        self.assertLess(t["grade"], -6.5)
        self.assertGreater(t["drop_ft"], 500)                    # "long descent"
        self.assertIsNone(ui.UI.terrain(types.SimpleNamespace(trip={"crumbs": down[:3]})))

    def test_county_line(self):
        from ridgewatch import ui
        labels = iter([("2 mi N of Clemson, SC", "Pickens Co"), ("3 mi W of Clemson, SC", "Oconee Co"),
                       ("1 mi S of Highlands, NC", "Macon Co")])
        stub = types.SimpleNamespace(region=None, entered=None,
                                     places=types.SimpleNamespace(ready=lambda: True),
                                     place_label=lambda lat, lon: next(labels))
        fix = locate.Fix(34.7, -82.8, "gps")
        ui.UI.region_check(stub, fix)
        self.assertIsNone(stub.entered)                          # where we started isn't news
        ui.UI.region_check(stub, fix)
        self.assertEqual(stub.entered[0], "Oconee Co")
        ui.UI.region_check(stub, fix)
        self.assertEqual(stub.entered[0], "Macon Co, NC")        # new state: say which

    def test_hourly_keeps_elevation_and_highs(self):
        now = time.time()
        h = nws.parse_hourly(hourly(now), now=now)
        self.assertEqual(h["elev_ft"], 758)
        p = views.view_precip({"hourly": h}, now)
        (hi, _), (lo, _) = views.temp_range(p)
        self.assertGreater(hi, lo)


class StatusBoxes(unittest.TestCase):
    """The DATA LINK and GPS boxes."""

    def link(self):
        return types.SimpleNamespace(link={"ok": 0.0, "fail": 0.0, "down_since": None,
                                           "msg": "", "trouble": 0.0}, g=UNI, follow=False,
                                     view_loc=lambda: {"lat": 34.7, "lon": -82.8})

    def test_data_link_states(self):
        st = self.link()
        state = lambda now: ui.UI.link_state(st, now)[:2]   # noqa: E731
        note = lambda err, nws_side=False: ui.UI.link_note(st, err, nws_side)  # noqa: E731
        self.assertEqual(state(1000), ("CHECKING", "label"))
        nowhere = self.link()
        nowhere.view_loc = lambda: None                      # no location: nothing to ask about
        self.assertEqual(ui.UI.link_state(nowhere, 1000)[:2], ("STANDBY", "label"))
        note(None)
        now = st.link["ok"]
        self.assertEqual(state(now + 60), ("ONLINE", "ok"))
        self.assertEqual(state(now + 20 * 60), ("STALE", "warn"))      # nothing heard for 20 min
        note("server error 503", nws_side=True)
        self.assertEqual(state(st.link["trouble"]), ("LIMITED", "warn"))
        note("no connection")
        word, spec, detail = ui.UI.link_state(st, st.link["fail"] + 14 * 60)
        self.assertEqual((word, spec), ("OFFLINE", "err"))
        self.assertIn("14", detail)
        note("no connection")                                # still down: the clock keeps running
        self.assertIn("14", ui.UI.link_state(st, st.link["down_since"] + 14 * 60)[2])
        note(None)                                           # a wildfire map answered...
        self.assertEqual(state(time.time()), ("LIMITED", "warn"))   # ...but NWS hasn't yet
        self.assertIsNone(st.link["down_since"])
        note(None, nws_side=True)
        self.assertEqual(state(time.time()), ("ONLINE", "ok"))

    def test_gps_states(self):
        def gps(kind, text, fix=None, follow=True):
            st = types.SimpleNamespace(follow=follow, fix=fix, g=UNI,
                                       locator=types.SimpleNamespace(gps_status=lambda: (kind, text)),
                                       gps_short=ui.UI.gps_short)
            return ui.UI.gps_state(st)[:2]
        fix3 = locate.Fix(34.7, -82.8, source="gps", mode=3)
        fix2 = locate.Fix(34.7, -82.8, source="gps", mode=2)
        self.assertEqual(gps("fix", "/dev/ttyGPS", fix3), ("3D FIX", "ok"))
        self.assertEqual(gps("fix", "/dev/ttyGPS", fix2), ("2D FIX", "warn"))
        self.assertEqual(gps("searching", "no satellite fix yet", None), ("NO FIX", "err"))
        self.assertEqual(gps("none", "no GPS receiver found", None), ("NO GPS", "err"))
        self.assertEqual(gps("fix", "/dev/ttyGPS", fix3, follow=False), ("OFF", "label"))
        self.assertEqual(ui.UI.gps_short("GPS on /dev/ttyGPS: cannot open (PermissionError)"),
                         "Cannot open /dev/ttyGPS")

    def test_status_change_beeps_once(self):
        words = {"link": "CHECKING", "gps": "NO FIX"}
        st = types.SimpleNamespace(status_seen={}, beeps=[],
                                   link_state=lambda now=None: (words["link"], "", ""),
                                   gps_state=lambda: (words["gps"], "", ""))
        tick = lambda t: ui.UI.status_tick(st, t)          # noqa: E731
        for t in range(0, 8):
            tick(t)
        self.assertEqual(st.beeps, [])                      # the first state is just noted
        words["link"] = "ONLINE"
        for t in range(8, 16):
            tick(t)
        self.assertEqual(st.beeps, [])                      # CHECKING -> ONLINE: start-up, quiet
        words["gps"] = "3D FIX"
        tick(16)
        tick(18)
        self.assertEqual(st.beeps, [])                      # not held 5 s yet
        tick(21)
        self.assertEqual(st.beeps, [21])                    # locked on: one beep
        words["link"] = "OFFLINE"
        tick(22)
        words["link"] = "ONLINE"                            # a 1-second flicker
        for t in range(23, 30):
            tick(t)
        self.assertEqual(st.beeps, [21])
        words["link"] = "OFFLINE"
        for t in range(30, 36):
            tick(t)
        self.assertEqual(st.beeps, [21, 35])                # really down: one beep
        words["gps"] = "OFF"                                # pressed f: quiet
        for t in range(36, 42):
            tick(t)
        self.assertEqual(st.beeps, [21, 35])

    def test_temperature_scale_is_fixed(self):
        self.assertEqual(panels.TEMP_SCALE, (20, 100))
        parts = [("54°F", "x"), ("  Mostly Cloudy", "x")]
        wind = [(" · ", "x"), ("wind SW 8 mph", "x")]
        self.assertEqual(panels.first_fit([parts + wind, parts], 40), parts + wind)
        self.assertEqual(panels.first_fit([parts + wind, parts], 25), parts)


class Cluster(unittest.TestCase):
    """Pieces of the gauge-cluster screen."""

    def test_big_digits(self):
        from ridgewatch.cluster import digit_rows
        rows = digit_rows("1,090", 6)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(set(len(r) for r in rows)), 1)      # a clean rectangle
        self.assertEqual(digit_rows("8", 4), ["█▀▀▀█", "█▄▄▄█", "█   █", "█▄▄▄█"])
        self.assertEqual(digit_rows("-", 4)[1].strip(), "▄▄▄▄▄")    # unknown: a dash

    def test_climb_totals_ignore_gps_jitter(self):
        t = ui.new_trip()
        for i in range(200):                                   # parked-flat road, +-6 m noise
            ui.climb_add(t, 300 + (6 if i % 2 else -6))
        self.assertLess(t["up_m"] + t["down_m"], 10)
        t = ui.new_trip()
        for i in range(101):                                   # a real 100 m climb, then 40 m down
            ui.climb_add(t, 300 + i)
        for i in range(41):
            ui.climb_add(t, 400 - i)
        self.assertAlmostEqual(t["up_m"], 100, delta=6)
        self.assertAlmostEqual(t["down_m"], 40, delta=6)

    def test_data_link_points_back_to_the_last_contact(self):
        here = {"lat": 34.75, "lon": -82.78}
        st = types.SimpleNamespace(follow=True, g=UNI, signal_spot=(34.70, -82.78, time.time() - 600),
                                   view_loc=lambda: here, link_state=lambda now=None: ("OFFLINE", "err", ""))
        self.assertEqual(ui.UI.last_online_text(st), "Last online \u22483.5 mi S")  # 0.05 deg of latitude
        st.link_state = lambda now=None: ("ONLINE", "ok", "")
        self.assertEqual(ui.UI.last_online_text(st), "")         # online: nothing to say
        st.link_state = lambda now=None: ("OFFLINE", "err", "")
        st.signal_spot = None
        self.assertEqual(ui.UI.last_online_text(st), "No connection point recorded yet")

    def test_waypoint_targets(self):
        now = time.time()
        trip = ui.new_trip()
        st = types.SimpleNamespace(follow=True, trip=trip, waypoint={"kind": "start"},
                                   signal_spot=None)
        self.assertIsNone(ui.UI.waypoint_target(st)[1])          # no position yet
        trip["start"], trip["t0"] = (34.70, -82.80, "Six Mile, SC"), now
        name, target, note = ui.UI.waypoint_target(st)
        self.assertEqual((name, target), ("Trip start", (34.70, -82.80)))
        self.assertTrue(note.startswith("started "))
        st.waypoint = {"kind": "signal"}
        self.assertIsNone(ui.UI.waypoint_target(st)[1])
        st.signal_spot = (34.71, -82.81, now - 300)
        self.assertEqual(ui.UI.waypoint_target(st)[1], (34.71, -82.81))
        st.waypoint = {"kind": "place", "name": "Camp", "lat": 35.0, "lon": -82.9}
        self.assertEqual(ui.UI.waypoint_target(st)[:2], ("Camp", (35.0, -82.9)))

    def test_compass_letters(self):
        from ridgewatch.cluster import digit_rows
        for rows in (6, 4):
            for word in ("N", "NE", "E", "SE", "S", "SW", "W", "NW"):
                lines = digit_rows(word, rows)
                self.assertEqual(len(lines), rows, word)
                self.assertEqual(len(set(len(l) for l in lines)), 1, word)   # a clean block
                self.assertTrue(any(c != " " for c in "".join(lines)), word)

    def test_old_station_reading_gives_way_to_the_forecast(self):
        now = time.time()
        entry = {"obs": {"time": now - 14 * 3600, "temp_f": 55.0, "desc": "Clear", "station": "KCEU"},
                 "hourly": {"periods": [[now - 600, now + 3000, -300, 68, 0, "Sunny"]]}}
        n = views.view_now(entry, now)
        self.assertTrue(n["forecast"])
        self.assertEqual((n["temp"], n["desc"]), (68, "Sunny"))
        self.assertEqual(views.now_source(n), "forecast now · KCEU station 14h old")
        entry["obs"]["time"] = now - 600                         # a fresh reading wins
        n = views.view_now(entry, now)
        self.assertFalse(n["forecast"])
        self.assertEqual(n["temp"], 55)
        self.assertEqual(views.now_source(n), "KCEU · 10m ago")


class Outlook(unittest.TestCase):
    def test_rain_sentence(self):
        now = 1800000000 - 1800000000 % 3600 + 60
        def p(pops, short="Rain Showers"):
            return {"hours": [(now - 60 + i * 3600, -300, v, 60, short) for i, v in enumerate(pops)]}
        self.assertEqual(views.rain_outlook(p([5] * 24), now)[0], "no rain expected next 24h")
        text = views.rain_outlook(p([0, 0, 20, 40, 70, 80, 50, 10] + [0] * 16), now)[0]
        self.assertTrue(text.startswith("rain likely "), text)
        self.assertTrue(text.endswith("(80%)"), text)
        self.assertTrue(views.rain_outlook(p([45, 40] + [0] * 22, "Chance Thunderstorms"), now)[0]
                        .startswith("chance of storms now until"))

    def test_nearby_warnings(self):
        now = time.time()
        ring = [[-82.85, 34.80], [-82.75, 34.80], [-82.75, 34.85], [-82.85, 34.85], [-82.85, 34.80]]
        e = {"nearby": {"fetched": now, "items": [
            {"id": "t1", "event": "Tornado Warning", "severity": "Extreme", "refs": [],
             "ends": (now + 1800, -300), "onset": None, "rings": [ring]}]}}
        self.assertTrue(geo.inside(34.82, -82.80, ring))
        self.assertEqual(views.view_nearby(e, 34.82, -82.80, None, now), [])   # over you: not "nearby"
        near = views.view_nearby(e, 34.70, -82.80, None, now)
        self.assertEqual((near[0]["dir"], near[0]["ahead"]), ("N", False))
        self.assertAlmostEqual(near[0]["km"], 11.1, delta=0.5)
        ahead = views.view_nearby(e, 34.70, -82.80, 0.0, now)                 # driving north
        self.assertTrue(ahead[0]["ahead"])
        self.assertEqual(views.view_nearby(e, 34.70, -82.80, 180.0, now)[0]["ahead"], False)
        self.assertEqual(views.view_nearby(e, 33.50, -82.80, None, now), [])  # 80 miles: too far

    def test_nearby_request_falls_back(self):
        asked = []

        def getter(url, ua):
            asked.append(url)
            if "severity=" in url:
                raise nws.FetchError("NWS said HTTP 400")
            feat = alert("t1", "Tornado Warning", "Extreme", time.time(), time.time() + 900)
            feat["geometry"] = {"type": "Polygon", "coordinates": [[[-82.8, 34.8], [-82.7, 34.8],
                                                                      [-82.7, 34.9], [-82.8, 34.8]]]}
            return {"features": [feat, alert("w", "Wind Advisory", "Moderate", time.time(),
                                             time.time() + 900)]}
        got = nws.nearby_warnings(["SC", "GA"], getter, "ua")
        self.assertEqual(len(asked), 3)                     # filtered try, then one per state
        self.assertEqual([i["event"] for i in got["items"]], ["Tornado Warning"])  # once
        self.assertEqual(got["items"][0]["description"], "")  # shapes only, no essays


class Reports(unittest.TestCase):
    """The single alerts list: official sources turned into short reports."""

    def test_feeds_parse(self):
        f = feeds.parse_fires(fires({"fires": [[34.86, -82.75, "Table Rock", 850.4, 40]]}))
        self.assertEqual((f[0]["name"], f[0]["acres"], f[0]["contained"]), ("Table Rock", 850.4, 40))
        r = feeds.parse_roads(road_events({"roads": [[34.6, -82.7, "I-85", "Northbound", "crash",
                                                       "2 Right Lanes"]]}), "511GA")
        self.assertEqual(len(r), 1)                          # planned roadwork left out
        self.assertEqual((r[0]["kind"], r[0]["detail"]), ("CRASH", ["I-85 NB", "2 right lanes"]))

    def test_one_list(self):
        now = time.time()
        a = {"items": [{"event": "Wind Advisory", "severity": "Moderate", "id": "w",
                        "ends": (now + 3600, -300), "onset": None}]}
        near = [{"event": "Tornado Warning", "severity": "Extreme", "id": "t", "km": 6.4,
                 "ahead": True, "dir": "N", "ends": (now + 1800, -300), "onset": None}]
        cache = {"fires": {"items": feeds.parse_fires(fires({"fires": [
            [34.86, -82.75, "Table Rock", 850, 40], [36.5, -80.0, "Far Away", 10, 0]]}))}}
        rs = views.build_reports(a, near, cache, 34.68, -82.84, now)
        self.assertEqual([(r["tier"], r["kind"]) for r in rs],
                         [("official", "TORNADO WARNING"), ("official", "WIND ADVISORY"),
                          ("official", "WILDFIRE")])     # a warning ahead outranks an advisory here
        self.assertEqual((rs[0]["where"], rs[0]["do"], rs[0]["sound"]),
                         ("4 mi ahead", "TAKE SHELTER NOW", "warning"))
        self.assertEqual((rs[1]["do"], rs[1]["sound"]), ("watch for debris", "local"))


class Surface(unittest.TestCase):
    """Battery, speaker beeps and the moving guard (the Surface Go 2 setup)."""

    def sysfs(self, entries):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        for name, files in entries.items():
            os.makedirs(os.path.join(base, name))
            for k, v in files.items():
                with open(os.path.join(base, name, k), "w") as f:
                    f.write(v + "\n")
        return base

    def test_battery_levels(self):
        base = self.sysfs({"ADP1": {"type": "Mains", "online": "1"},
                           "BAT1": {"type": "Battery", "capacity": "82", "status": "Charging"}})
        b = power.battery(base)
        self.assertEqual(b, {"pct": 82, "charging": True, "external": True})
        self.assertEqual(power.tag(b), ("BATT 82% CHARGING", "ok"))
        base = self.sysfs({"ADP1": {"type": "Mains", "online": "1"},       # Battery Limit at 50%
                           "BAT1": {"type": "Battery", "capacity": "50", "status": "Not charging"}})
        self.assertEqual(power.tag(power.battery(base)), ("BATT 50% PLUGGED IN", "ok"))
        base = self.sysfs({"ADP1": {"type": "Mains", "online": "0"},
                           "BAT1": {"type": "Battery", "capacity": "64", "status": "Discharging"}})
        self.assertEqual(power.tag(power.battery(base)), ("BATT 64% ON BATTERY", "warn"))
        base = self.sysfs({"BAT1": {"type": "Battery", "capacity": "15", "status": "Discharging"}})
        self.assertEqual(power.tag(power.battery(base))[1], "err")
        base = self.sysfs({"hid-mouse": {"type": "Battery", "scope": "Device", "capacity": "5"}})
        self.assertIsNone(power.battery(base))              # a mouse's battery isn't ours
        self.assertIsNone(power.battery("/no/such/folder"))
        self.assertIsNone(power.tag(None))

    def test_speaker_plays_or_falls_back(self):
        import wave
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        rang = []
        sp = sound.Speaker(home, lambda: rang.append(1), which=lambda p: None)
        sp.beep()
        self.assertEqual(rang, [1])                         # no player: the terminal bell
        true = shutil.which("true")
        sp = sound.Speaker(home, lambda: rang.append(2),
                           which=lambda p: true if p == "aplay" else None)
        self.assertEqual(sp.player, "aplay")
        w = wave.open(os.path.join(home, "sound-local.wav"))
        self.assertEqual((w.getnchannels(), w.getsampwidth()), (1, 2))
        single = w.getnframes()
        frames = w.readframes(single)
        w.close()
        loudest = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True))
                      for i in range(0, len(frames), 2))
        self.assertGreater(loudest, 30000)                   # near full scale
        w = wave.open(os.path.join(home, "sound-warning.wav"))
        self.assertGreater(w.getnframes(), 2 * single)       # beep beep: two beeps and a gap
        w.close()
        for kind in ("warning", "local", "national", "system"):
            self.assertTrue(os.path.exists(os.path.join(home, "sound-%s.wav" % kind)))
            self.assertTrue(sp.beep(kind))
        sp.beep()
        for p in sp.procs:
            p.wait()
        self.assertEqual(rang, [1])                         # played, no bell
        false = shutil.which("false")
        sp = sound.Speaker(home, lambda: rang.append(3),
                           which=lambda p: false if p == "pw-play" else None)
        for _ in range(4):                                  # a player that keeps failing is dropped
            sp.beep()
            for p in sp.procs:
                p.wait()
        self.assertIn(3, rang)

    def test_moving_guard(self):
        now = time.time()
        st = types.SimpleNamespace(driving=True, mode="alerts", follow=True, last_key=now - 10,
                                   view_loc=lambda: {"lat": 1, "lon": 2}, dirty=False)
        ui.UI.moving_guard(st, now)
        self.assertEqual(st.mode, "alerts")                 # still reading
        ui.UI.moving_guard(st, now + 25)
        self.assertEqual(st.mode, "main")                   # 30 s without a tap: back to the gauges
        st.mode, st.driving = "pick", False
        ui.UI.moving_guard(st, now + 600)
        self.assertEqual(st.mode, "pick")                   # parked: stays as long as you like

    def test_first_tap_only_silences_a_warning(self):
        taps = []
        st = types.SimpleNamespace(dirty=False, last_key=0, alarm_until=time.time() + 10,
                                   tap=lambda y, x: taps.append((y, x)))
        st.alarm_on = lambda: time.time() < st.alarm_until
        ui.UI.handle(st, ("tap", 5, 5))
        self.assertEqual(taps, [])                          # silenced, nothing else happened
        ui.UI.handle(st, ("tap", 5, 5))
        self.assertEqual(taps, [(5, 5)])

    def test_bar_buttons(self):
        st = types.SimpleNamespace(mode="main", follow=True, off_since=None)
        st._bar_buttons = lambda: panels.Panels._bar_buttons(st)
        labels = [b[1] for b in panels.Panels.bar_buttons(st)]
        self.assertEqual(labels, ["DISPLAY", "REFRESH", "FOLLOW GPS: ON", "SAVE HERE", "PLACES"])
        st.mode = "alerts"
        self.assertEqual([b[0] for b in panels.Panels.bar_buttons(st)], ["back", "up", "down"])
        st.off_since = time.time()                          # shutdown counting down
        self.assertEqual([b[0] for b in panels.Panels.bar_buttons(st)][:2], ["keep", "back"])

    def power_stub(self, minutes=10):
        st = types.SimpleNamespace(power=None, _power_at=0.0, off_since=None, keep_running=False,
                                   off_warned=False, shutdown_failed=False, beeps=[], dirty=False,
                                   flashes=[], shutdowns=[],
                                   store=types.SimpleNamespace(config={"shutdown_after_min": minutes}))
        st.flash = lambda text, level="ok", secs=4: st.flashes.append(text)
        st.shutdown = lambda: st.shutdowns.append(1)
        st.supply = {"pct": 80, "charging": False, "external": True}
        st.read_battery = lambda: st.supply
        return st

    def test_shutdown_after_power_loss(self):
        st = self.power_stub()
        t = 1000.0
        ui.UI.power_tick(st, t)
        self.assertIsNone(st.off_since)                     # plugged in: nothing happens
        st.supply = {"pct": 80, "charging": False, "external": False}   # ignition off
        ui.UI.power_tick(st, t + 10)
        self.assertEqual(st.off_since, t + 10)
        self.assertEqual(len(st.beeps), 1)
        self.assertIn("Shutting down in 10 minutes", st.flashes[-1])
        ui.UI.power_tick(st, t + 10 + 9 * 60 + 5)           # one minute left: a second beep
        self.assertEqual(len(st.beeps), 2)
        self.assertEqual(st.shutdowns, [])
        ui.UI.power_tick(st, t + 10 + 600)
        self.assertEqual(st.shutdowns, [1])

    def test_power_back_or_keep_running_cancels(self):
        st = self.power_stub()
        st.supply = {"pct": 80, "charging": False, "external": False}
        ui.UI.power_tick(st, 1000.0)
        st.supply = {"pct": 80, "charging": True, "external": True}      # ignition back on
        ui.UI.power_tick(st, 1020.0)
        self.assertIsNone(st.off_since)
        self.assertEqual(st.flashes[-1], "Outside power is back")
        st.supply = {"pct": 80, "charging": False, "external": False}
        ui.UI.power_tick(st, 1040.0)
        st.keep_running, st.off_since = True, None          # tapped KEEP RUNNING
        ui.UI.power_tick(st, 1040.0 + 3600)
        self.assertEqual((st.off_since, st.shutdowns), (None, []))
        st = self.power_stub(minutes=0)                     # shutdown turned off in settings
        st.supply = {"pct": 80, "charging": False, "external": False}
        ui.UI.power_tick(st, 1000.0)
        self.assertIsNone(st.off_since)
        st = self.power_stub()                              # a machine with no battery
        st.read_battery = lambda: None
        ui.UI.power_tick(st, 1000.0)
        self.assertIsNone(st.off_since)

    def test_shutdown_command_failure_is_reported(self):
        st = types.SimpleNamespace(poweroff_cmd=[shutil.which("false")], shutdown_failed=False,
                                   store=types.SimpleNamespace(save_cache=lambda: None), flashes=[])
        st.flash = lambda text, level="ok", secs=4: st.flashes.append(text)
        ui.UI.shutdown(st)
        self.assertTrue(st.shutdown_failed)
        self.assertIn("polkit", st.flashes[-1])

    def test_backlight(self):
        base = self.sysfs({"intel_backlight": {"max_brightness": "7500", "brightness": "7500"}})
        bl = backlight.Backlight(base, which=lambda p: None)
        self.assertTrue(bl.set_pct(20))
        with open(os.path.join(base, "intel_backlight", "brightness")) as f:
            self.assertEqual(f.read().strip(), "1500")
        self.assertFalse(bl.set_pct(20))                    # unchanged: not sent again
        self.assertEqual(backlight.level(0, 7500), 1)       # never fully dark
        base = self.sysfs({"intel_backlight": {"max_brightness": "7500"}})   # can't write: logind
        bl = backlight.Backlight(base, which=lambda p: shutil.which("true"))
        self.assertTrue(bl.set_pct(70))
        bl.proc.wait()
        self.assertFalse(backlight.Backlight("/no/such/folder").available())

    def test_brightness_settings(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        store = settings.Store(home)
        self.assertEqual(store.config["brightness"], {"day": 100, "normal": 70, "night": 20})
        self.assertEqual(store.config["shutdown_after_min"], 10)


class AlertSources(unittest.TestCase):
    """The strict source list: each new feed parsed, filtered and turned into rows."""
    LAT, LON = 34.70, -82.80
    CTX = {"same": "045077", "statefips": "45", "airports": ["GSP", "CLT", "ATL"]}

    def rows(self, cache, now=None):
        a = {"known": True, "trusted": True, "items": []}
        return views.build_reports(a, [], cache, self.LAT, self.LON, now or time.time(), self.CTX)

    def test_storm_reports(self):
        now = time.time()
        text = lsr_text([[34.75, -82.80, "TSTM WND DMG", "TREES DOWN ON SC-183", 30],
                         [34.70, -82.82, "HAIL", "SMALL HAIL", 20],
                         [35.90, -82.80, "FLASH FLOOD", "FAR AWAY", 20],
                         [34.71, -82.81, "FLASH FLOOD", "OLD REPORT", 8 * 60]], now)
        reps = feeds.parse_lsr(text)
        self.assertEqual([r["kind"] for r in reps], ["TREES DOWN", "FLOODING", "FLOODING"])  # no small hail
        rows = self.rows({"lsr": {"products": {"p": {"t": now, "reports": reps}}}}, now)
        self.assertEqual([r["kind"] for r in rows], ["TREES DOWN"])   # far and 8-hour-old reports left out
        r = rows[0]
        self.assertTrue(r["where"].startswith("3 mi N, near Pickens"))
        self.assertTrue(r["when"].startswith("reported "))
        self.assertEqual((r["do"], r["source"], r["sound"]), ("watch for debris", "NWS", "local"))

    def test_fema_alerts(self):
        import xml.etree.ElementTree as ET
        mode = {"ipaws": [["EVI", "045077", "Evacuation order for Six Mile"],
                          ["CAE", "045045", "Amber alert in Greenville County"],
                          ["RWT", "045077", "Required weekly test"]]}
        entries = feeds.parse_ipaws_feed(ET.fromstring(ipaws_feed("http://x", mode)))
        self.assertEqual([(e["event"], e["statefips"]) for e in entries],
                         [("EVI", "45"), ("CAE", "45"), ("RWT", "45")])
        cap = feeds.parse_cap(ET.fromstring(ipaws_cap(mode, 0)))
        self.assertEqual((cap["code"], cap["same"], cap["nws"]), ("EVI", ["045077"], False))
        other = feeds.parse_cap(ET.fromstring(ipaws_cap(mode, 1)))
        statewide = dict(other, same=["045000"], code="CEM")
        from_nws = dict(cap, nws=True)
        rows = self.rows({"ipaws": {"alerts": {"0": cap, "1": other, "2": statewide, "3": from_nws}}})
        self.assertEqual([(r["kind"], r["where"], r["do"], r["sound"]) for r in rows],
                         [("EVACUATION ORDER", "here", "EVACUATE NOW", "warning"),
                          ("CIVIL EMERGENCY", "here", "follow official orders", "warning")])
        ean = dict(other, same=["006037"], code="EAN")                     # national, anywhere
        rows = self.rows({"ipaws": {"alerts": {"9": ean}}})
        self.assertEqual((rows[0]["kind"], rows[0]["scope"]), ("NATIONAL EMERGENCY", "national"))

    def test_power_outages(self):
        few = feeds.parse_outages(duke({"outages": [[34.71, -82.80, 120], [34.80, -82.75, 80]]}))
        self.assertEqual(self.rows({"outages": {"items": few}}), [])        # under 250: quiet
        many = feeds.parse_outages(duke({"outages": [[34.71, -82.80, 900], [34.80, -82.75, 400]]}))
        r = self.rows({"outages": {"items": many}})[0]
        self.assertEqual((r["kind"], r["source"], r["do"]), ("POWER OUT", "DUKE", "signals may be out"))
        self.assertIn("1,300 customers within 15 mi, nearest 1 mi N", r["where"])

    def test_earthquakes(self):
        q = feeds.parse_quakes(usgs({"quakes": [
            [34.90, -82.60, 2.9, "5 km S of Liberty, South Carolina", 60],
            [35.70, -117.50, 6.1, "15 km NE of Ridgecrest, CA", 30],
            [38.30, 142.30, 5.9, "80 km E of Ishinomaki, Japan", 30],
            [38.30, 142.40, 7.2, "90 km E of Ishinomaki, Japan", 10]]}))
        rows = self.rows({"quakes": {"items": q}})
        got = [(r["scope"], r["where"].split(",")[0]) for r in rows]
        self.assertEqual(got, [("local", "M2.9"), ("national", "M6.1"), ("national", "M7.2")])
        self.assertEqual(rows[1]["sound"], "national")

    def test_flights(self):
        import xml.etree.ElementTree as ET
        many = feeds.parse_faa(ET.fromstring(faa_xml({"faa_stops": ["ATL", "BOS", "CLT", "DEN",
                                                                    "JFK", "ORD"]})))
        self.assertNotIn("LAS", [i["arpt"] for i in many])              # private-aircraft closure
        rows = self.rows({"faa": {"items": many}})
        self.assertEqual(rows[0]["where"], "Ground stops at 6 airports, incl. CLT, ATL")
        self.assertEqual((rows[0]["scope"], rows[0]["sound"]), ("national", "national"))
        self.assertEqual(len(rows), 1)                     # ATL and CLT are named in that line
        few = feeds.parse_faa(ET.fromstring(faa_xml({"faa_stops": ["BOS", "CLT"]})))
        rows = self.rows({"faa": {"items": few}})
        self.assertEqual([r["where"].split(",")[0] for r in rows], ["Ground stop at CLT"])

    def test_only_listed_sources_are_asked(self):
        for url in (feeds.DUKE, feeds.IPAWS, feeds.USGS, feeds.FAA, feeds.WFIGS,
                    feeds.ROADS["GA"][1], feeds.ROADS["NC"][1], "https://api.weather.gov/x"):
            self.assertTrue(sources.allowed(url), url)
        with self.assertRaises(nws.FetchError) as e:
            nws.fetch_url("https://news.example.com/top", "ua", lambda b: b, tries=1)
        self.assertIn("not on the source list", str(e.exception))

    def test_new_rows_sound_once(self):
        alarms = []
        st = types.SimpleNamespace(store=types.SimpleNamespace(cache={}), loaded=set(),
                                   sound_ready=set(), cues=[], CAT_SOURCE=ui.UI.CAT_SOURCE,
                                   raise_alarm=lambda items, label: alarms.append(items))

        def row(id_, cat, sound, kind="X"):
            return {"id": id_, "cat": cat, "sound": sound, "kind": kind, "sev": "Extreme"}
        t = 1000.0
        ui.UI.sound_new_rows(st, [row("q1", "quake", "national")], t)      # cached, not loaded
        st.loaded = {"quakes", "outages", "ipaws"}
        ui.UI.sound_new_rows(st, [row("q1", "quake", "national")], t + 1)  # first load: quiet
        self.assertEqual(st.cues, [])
        ui.UI.sound_new_rows(st, [row("q1", "quake", "national"), row("q2", "quake", "national"),
                                  row("d", "power", "local")], t + 2)
        self.assertEqual(sorted(k for _, k in st.cues), ["local", "national"])   # one of each
        st.cues = []
        ui.UI.sound_new_rows(st, [row("q2", "quake", "national")], t + 3)
        self.assertEqual(st.cues, [])                                       # seen: no repeat
        ui.UI.sound_new_rows(st, [row("q2", "quake", "national")], t + 3 + 4000)
        self.assertEqual([k for _, k in st.cues], ["national"])  # gone over an hour, back: again
        st.cues = []
        ui.UI.sound_new_rows(st, [row("e", "ipaws", "warning", "EVACUATION ORDER")], t + 5000)
        self.assertEqual(alarms[0][0]["event"], "Evacuation Order")         # warnings: the alarm


class VolumeKeys(unittest.TestCase):
    DEVICES = ("I: Bus=0003 Vendor=045e Product=096f Version=0111\n"
               "N: Name=\"Microsoft Surface Keyboard Consumer Control\"\n"
               "H: Handlers=kbd event7 \n"
               "B: EV=1f\n"
               "B: KEY=3f000303ff 0 0 483ffff17aff32d bfd4444600000000 1 130c730b17c000 "
               "267bfad9415fed 9e168000004400 10000002\n\n"
               "I: Bus=0018 Vendor=04f3 Product=2a1c Version=0100\n"
               "N: Name=\"ELAN9038:00 04F3:2A1C\"\n"
               "H: Handlers=mouse1 event5 \n"
               "B: KEY=400 0 0 0 0 0\n")

    def amixer(self, state):
        def run(cmd):
            state["calls"].append(cmd[2:])
            if cmd[2] != state["control"]:
                return ""
            v = cmd[3] if len(cmd) > 3 else None
            if v and v.endswith("%+"):
                state["pct"] = min(100, state["pct"] + int(v[:-2]))
            elif v and v.endswith("%-"):
                state["pct"] = max(0, state["pct"] - int(v[:-2]))
            elif v and v.endswith("%"):
                state["pct"] = int(v[:-1])
            if v == "toggle":
                state["on"] = not state["on"]
            elif "unmute" in cmd:
                state["on"] = True
            return "  Front Left: Playback 40 [%d%%] [-10.00dB] [%s]\n" % (
                state["pct"], "on" if state["on"] else "off")
        return run

    def test_finds_the_keys_and_reads_the_level(self):
        self.assertEqual(volume.key_devices(self.DEVICES), ["event7"])   # not the touchscreen
        self.assertEqual(volume.parse_level("Mono: Playback 60 [70%] [-6.00dB] [off]"), (70, False))
        self.assertEqual(volume.parse_level("nothing"), (None, None))

    def test_floor_and_keys(self):
        st = {"control": "Speaker", "pct": 20, "on": False, "calls": []}
        said = []
        v = volume.Volume(lambda p, on: said.append((p, on)), which=lambda n: "/usr/bin/amixer",
                          run=self.amixer(st), floor=50)
        self.assertEqual((v.control, v.level, v.on), ("Speaker", 50, True))   # raised and unmuted
        v.press(volume.KEY_VOLUMEUP)
        v.press(volume.KEY_MUTE)
        self.assertEqual(said, [(55, True), (55, False)])
        v.press(volume.KEY_VOLUMEDOWN)
        self.assertEqual(said[-1], (50, False))           # down doesn't unmute; up does
        st2 = {"control": "Master", "pct": 80, "on": True, "calls": []}
        v2 = volume.Volume(which=lambda n: "/usr/bin/amixer", run=self.amixer(st2), floor=50)
        self.assertEqual(v2.level, 80)                     # left where you set it
        self.assertFalse(any(c[1:] for c in st2["calls"] if c[0] == "Master" and len(c) > 1))
        v3 = volume.Volume(which=lambda n: None)
        self.assertIn("alsa-utils", v3.problem)

    def test_reads_key_events(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        dev = os.path.join(folder, "event7")
        os.mkfifo(dev)
        writer = os.open(dev, os.O_RDWR)
        self.addCleanup(os.close, writer)
        devices = os.path.join(folder, "devices")
        with open(devices, "w") as f:
            f.write(self.DEVICES)
        st = {"control": "Master", "pct": 60, "on": True, "calls": []}
        said = []
        v = volume.Volume(lambda p, on: said.append((p, on)), which=lambda n: "/usr/bin/amixer",
                          run=self.amixer(st), floor=50)
        old = os.environ.pop("XDG_CURRENT_DESKTOP", None)
        try:
            v.start(devices, folder)
        finally:
            if old is not None:
                os.environ["XDG_CURRENT_DESKTOP"] = old
        ev = volume.EVENT.pack(0, 0, volume.EV_KEY, volume.KEY_VOLUMEUP, 1) + \
            volume.EVENT.pack(0, 0, volume.EV_KEY, volume.KEY_VOLUMEUP, 0) + \
            volume.EVENT.pack(0, 0, 0, 0, 0)                 # press, release, sync
        os.write(writer, ev)
        for _ in range(50):
            if said:
                break
            time.sleep(0.05)
        self.assertEqual(said, [(65, True)])


class EndToEnd(unittest.TestCase):
    """The text report against the fake NWS: fetch, parse, save, show."""

    def setUp(self):
        self.fake = FakeNWS().start()
        self.old_api, nws.API = nws.API, self.fake.url
        self.home = tempfile.mkdtemp()

    def tearDown(self):
        nws.API = self.old_api
        self.fake.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def report(self, loc, fetch=True):
        import io
        store = settings.Store(self.home)
        buf = io.StringIO()
        code = plain.run(store, loc, fetch=fetch, out=buf)
        return code, buf.getvalue()

    def test_report_and_offline(self):
        loc = settings.make_location("Clemson, SC", 34.6834, -82.8374)
        code, text = self.report(loc)
        self.assertEqual(code, 0)
        self.assertIn("54", text)
        self.assertIn("Wind Advisory", text)
        self.assertIn("about 0.24 in", text)
        self.assertIn("Oconee Memorial", text)               # nearest ER, worked out offline
        self.assertTrue(all(ua.startswith("(ridgewatch/") for _, ua in self.fake.log))
        self.fake.mode = {"down": True}                      # lose the connection
        code, text = self.report(loc)
        self.assertEqual(code, 0)
        self.assertIn("data link down", text)
        self.assertIn("54", text)                            # saved data still shown

    def test_outside_nws_area(self):
        code, text = self.report(settings.make_location("Ocean", 30.0, -40.0))
        self.assertEqual(code, 1)
        self.assertIn("no forecast", text.lower())

    def test_damaged_settings_set_aside(self):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write("{ broken")
        store = settings.Store(self.home)
        self.assertEqual(store.config["locations"], [])
        self.assertTrue(any(n.endswith(".json") is False for n in os.listdir(self.home)))
        self.assertTrue(store.notes)


class Dashboard(unittest.TestCase):
    """Starts the real full screen in a pseudo-terminal and quits with q."""

    def run_app(self, args, keys=b"", seconds=3.0, size=(47, 156), term="xterm-256color",
                mode=None):
        import pty
        import select
        fake = FakeNWS().start()
        if mode:
            fake.mode = mode
        home = tempfile.mkdtemp()
        try:
            env = dict(os.environ, RIDGEWATCH_API=fake.url, RIDGEWATCH_FEEDS=fake.url,
                       TERM=term, LANG="C.UTF-8", LC_ALL="C.UTF-8", PYTHONPATH=ROOT)
            with warnings.catch_warnings():         # the child execs at once: no deadlock risk
                warnings.simplefilter("ignore", DeprecationWarning)
                pid, fd = pty.fork()
            if pid == 0:
                import fcntl
                import struct
                import termios                        # rows, columns
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
                os.execve(sys.executable, [sys.executable, "-m", "ridgewatch", "--home", home] + args,
                          env)
            # keys: bytes sent halfway through, or a list of chunks sent 0.8 s apart (taps)
            chunks = list(keys) if isinstance(keys, list) else [keys]
            out, end = b"", time.time() + seconds
            next_at = end - seconds / 2
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    try:
                        out += os.read(fd, 65536)
                    except OSError:
                        break
                if chunks and time.time() > next_at:
                    os.write(fd, chunks.pop(0))
                    next_at = time.time() + 0.8
            status = None
            for _ in range(20):                      # q backs out of a view, then quits
                os.write(fd, b"q")
                time.sleep(0.3)
                done, st = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = st
                    break
                try:
                    while select.select([fd], [], [], 0)[0]:
                        out += os.read(fd, 65536)
                except OSError:
                    pass
            if status is None:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                self.fail("the app did not quit")
            crashed = os.path.exists(os.path.join(home, "crash.log"))
            self.fake = fake
            return os.WEXITSTATUS(status), out.decode("utf-8", "replace"), crashed
        finally:
            fake.stop()
            shutil.rmtree(home, ignore_errors=True)

    def test_gauge_cluster(self):
        code, out, crashed = self.run_app(["--place", "clemson sc"])
        self.assertEqual((code, crashed), (0, False))
        for tile in ("SPEED", "ELEV", "COMPASS", "HERE", "GRID", "WAYPOINT", "ER", "ALERTS",
                     "RADIO", "WEATHER", "DATA LINK", "GPS", "RIDGEWATCH"):
            self.assertIn(" %s " % tile, out, tile)
        self.assertIn("Oconee Memorial", out)

    def test_alerts_table(self):
        mode = {"alerts": "none", "lsr": [[34.75, -82.84, "TSTM WND DMG", "TREES DOWN ON SC-133", 20]],
                "outages": [[34.70, -82.83, 1200]], "faa_stops": ["ATL", "BOS", "DEN", "JFK", "ORD"],
                "quakes": [[35.70, -117.50, 6.1, "15 km NE of Ridgecrest, CA", 30]]}
        code, out, crashed = self.run_app(["--place", "clemson sc"], seconds=8, mode=mode)
        self.assertEqual((code, crashed), (0, False))
        for text in ("WHAT", "WHERE", "WHEN", "TREES DOWN", "POWER OUT", "NATIONAL",
                     "Ground stops at 5 airports, incl. ATL", "M6.1, 15 km NE of Ridgecrest, CA"):
            self.assertIn(text, out, text)

    def test_alert_details_screen(self):
        code, out, crashed = self.run_app(["--place", "clemson sc"], keys=b"a")
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("Wind Advisory", out)
        self.assertIn("LINK ONLINE", out)                    # status tags in the top bar there

    def test_day_and_night_looks(self):
        for look in ("day", "night"):
            code, out, crashed = self.run_app(["--place", "clemson sc", "--display", look], keys=b"d")
            self.assertEqual((code, crashed), (0, False), look)
            self.assertIn("Oconee Memorial", out, look)        # nearest ER
            self.assertIn("display:", out, look)              # d switched the look

    def test_follow_with_no_gps(self):
        code, out, crashed = self.run_app(["--follow"])
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("NO LOCATION", out)                    # alerts say why they're empty
        self.assertIn("STANDBY", out)                        # not OFFLINE: nothing to ask yet
        self.assertIn("NO GPS", out)

    @staticmethod
    def tap(y, x):
        """A touch at row y, column x, as foot reports it (an SGR mouse press and release)."""
        return ("\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x + 1, y + 1, x + 1, y + 1)).encode()

    def test_touch_save_here_and_places(self):
        # 156x47: the bar is rows 44-46, five buttons about 31 columns wide
        keys = [self.tap(45, 105), self.tap(45, 140), self.tap(45, 20)]   # SAVE HERE, PLACES, BACK
        code, out, crashed = self.run_app(["--place", "clemson sc"], keys=keys, seconds=6,
                                          mode={"alerts": "none"})   # no alarm to silence first
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("Saved as Clemson, SC. Find it under PLACES.", out)
        self.assertIn(" places ", out)                       # the picker opened
        self.assertIn("Clemson, SC  key 1", out)              # the saved place is listed

    def test_touch_tiles_and_follow(self):
        code, out, crashed = self.run_app(["--place", "clemson sc"],
                                          keys=[self.tap(24, 60), self.tap(45, 20)], seconds=5,
                                          mode={"alerts": "none"})
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("alerts " + UNI["dot"] + " Clemson, SC", out)   # ALERTS tile opened the list
        self.assertIn("BACK", out)
        # PLACES, then tap the first choice (follow the GPS)
        code, out, crashed = self.run_app(["--place", "clemson sc"],
                                          keys=[self.tap(45, 140), self.tap(13, 78)], seconds=5,
                                          mode={"alerts": "none"})
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("FOLLOW GPS: ON", out)

    def test_small_window_asks_for_more_room(self):
        code, out, crashed = self.run_app(["--place", "clemson sc"], size=(24, 80))
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("Window too small. Needs 120x38; this is 80x24.", out)

    def test_needs_256_colors(self):
        code, out, crashed = self.run_app(["--place", "clemson sc"], term="vt100", seconds=1.5)
        self.assertIn("256-color", out)
        self.assertFalse(crashed)


if __name__ == "__main__":
    unittest.main()
