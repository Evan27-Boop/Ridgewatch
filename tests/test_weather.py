"""weather-tui tests. Run from the project folder:  python -m unittest

Everything runs offline: NWS is replaced by tests/fake_nws.py, GPS by text
written to a pseudo-terminal, and Windows pieces by small fakes.
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

from tests.fake_nws import (FakeNWS, alert, alerts, fires, grid, hourly, obs,  # noqa: E402
                            points, road_events, rss, scales, stations)
from wxtui import (feeds, follow, geo, hospitals, locate, news, nws, panels,  # noqa: E402
                   places, plain, screen, settings, sun, theme, ui, views)
from wxtui.panels import UNI  # noqa: E402


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

    def test_windows_lines(self):
        kind, f = locate.parse_windows_line("fix 34.7 -82.8 3218.5 NaN NaN NaN")
        self.assertEqual((kind, f.lat, f.speed_mph, f.alt_m), ("fix", 34.7, None, None))
        kind, msg = locate.parse_windows_line("status Ready Denied")
        self.assertIn("Settings", msg)
        self.assertEqual(locate.parse_windows_line("fix 1,5 2,3 x NaN NaN NaN"), (None, None))

    def test_windows_script_is_one_safe_line(self):
        one = locate._one_line(locate.WINDOWS_LOCATION_PS)
        self.assertNotIn("\n", one)
        self.assertNotIn('"', one)
        self.assertIn("} catch {", one)

    def test_accuracy_text(self):
        self.assertEqual(locate.fmt_accuracy(4.5), "±15 ft")
        self.assertEqual(locate.fmt_accuracy(3218.5), "±2.0 mi")
        self.assertEqual(locate.fmt_accuracy(40000), "±25 mi")

    @unittest.skipIf(os.name == "nt", "uses a Unix pseudo-terminal")
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

    def test_finds_windows_com_ports(self):
        fake = types.ModuleType("winreg")
        fake.HKEY_LOCAL_MACHINE = 1
        vals = [(r"\Device\BthModem0", "COM4", 1), (r"\Device\Serial0", "COM1", 1),
                (r"\Device\USBSER000", "COM7", 1)]
        fake.OpenKey = lambda k, p: "key"
        fake.CloseKey = lambda k: None

        def enum(k, i):
            if i >= len(vals):
                raise OSError
            return vals[i]
        fake.EnumValue = enum
        old_os, old_mod = locate.os, sys.modules.get("winreg")

        class NT(object):
            name = "nt"

            def __getattr__(self, a):
                return getattr(old_os, a)
        sys.modules["winreg"], locate.os = fake, NT()
        try:
            self.assertEqual(locate.candidate_ports(), ["COM7", "COM1"])   # USB first, no Bluetooth
        finally:
            locate.os = old_os
            if old_mod is None:
                del sys.modules["winreg"]
            else:
                sys.modules["winreg"] = old_mod


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


class Headlines(unittest.TestCase):
    def test_picks_recent_safety_news(self):
        now = time.time()
        got = news.pick(news.parse_feed(rss({}, now)), now)
        self.assertEqual([i["title"] for i in got],
                         ["Crash closes northbound lanes of US 123 near Clemson",
                          "Pickens County deputies search for missing hiker"])
        self.assertEqual(got[0]["source"], "WYFF4")            # " - WYFF4" moved out of the title
        self.assertEqual([i["kind"] for i in got], ["ROAD", "MISSING"])
        self.assertEqual(got[1]["also"], 1)                    # FOX Carolina had it too
        self.assertEqual(news.pick(news.parse_feed(rss({"news": "none"}, now)), now), [])

    def test_no_sports(self):
        """Safety and local awareness only: sports stories stay out even when they
        use safety words, and real news that sounds a bit like sports stays in."""
        sports = ["Clemson shuts down Wake Forest in 31-14 win",
                  "Tigers QB injured in second half, status uncertain",
                  "Clemson closes out regular season at home Saturday",
                  "Gamecocks defense under fire after loss to Texas A&M",
                  "Daniel High wins state title in overtime thriller",
                  "NASCAR fans face traffic delays leaving Darlington"]
        news_items = ["Crash closes I-85 southbound near Anderson",
                      "Homecoming parade closes downtown Clemson roads Friday",
                      "Game wardens rescue lost hunter in Jocassee Gorges",
                      "9-1-1 outage reported across Pickens County",
                      "Road closed 7-9 p.m. for emergency repairs on SC 93",
                      "Self-defense shooting under investigation in Liberty",
                      "Deputies come under fire during traffic stop",
                      "Tiger Boulevard closed after water main break",
                      "2 dead, 3 injured in Highway 11 wreck"]
        self.assertEqual([t for t in sports if news.relevant(t)], [])
        self.assertEqual([t for t in news_items if not news.relevant(t)], [])

    def test_story_types_and_places(self):
        self.assertEqual(news.kind_of("Tiger Boulevard closed after water main break"), "UTILITY")
        self.assertEqual(news.kind_of("Man killed in crash on SC 93"), "ROAD")
        self.assertEqual(news.kind_of("Fire crews battle blaze, deputies investigate"), "FIRE")
        ix = places.PlaceIndex()
        ix.load()
        def where(t):
            hit = news.place_in(t, ix, "SC", 34.68, -82.84)
            return hit and hit[0]
        self.assertEqual(where("Crash closes US 123 near Clemson"), "Clemson")
        self.assertEqual(where("Easley man charged after shooting"), "Easley")
        self.assertIsNone(where("Pickens County deputies search for hiker"))  # county, not town
        self.assertIsNone(where("Central bank raises rates"))                  # not after in/near/at
        self.assertIsNone(where("Crash near Columbia"))                        # 120 mi: too far

    def test_area_and_search(self):
        self.assertEqual(news.county_name("Pickens", "SC"), "Pickens County")
        self.assertEqual(news.county_name("Orleans", "LA"), "Orleans Parish")
        self.assertEqual(news.county_name("Richmond city", "VA"), "Richmond")
        self.assertEqual(news.county_name("Nome (CA)", "AK"), "Nome Census Area")
        ix = places.PlaceIndex()
        ix.load()
        area = news.area_for(ix, 34.72, -82.79)
        self.assertEqual((area["county"], area["town"], area["label"]),
                         ("Pickens County", "Clemson", "Pickens Co, SC"))
        url = news.search_url(area)
        self.assertIn("Pickens+County", url)
        self.assertIn("when%3A1d", url)
        self.assertNotIn("34.7", url)                         # names only, never coordinates

    def test_fetcher_survives_errors(self):
        def broken(url):
            raise nws.FetchError("no connection", True)
        f = news.NewsFetcher(getter=broken)
        f.request({"key": "SC|Pickens", "county": "Pickens County", "town": "", "state": "South Carolina"})
        key, data, error = f.results.get(timeout=5)
        self.assertEqual((key, data, error), ("SC|Pickens", None, "no connection"))


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
        self.assertEqual(theme.adapt((253, 15, ""), "day")[2], "bold")
        self.assertEqual(theme.adapt((253, 15, "bold"), "normal"), (253, 15, "bold"))


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
        from wxtui import ui
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
        from wxtui import ui
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
        from wxtui import ui
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
                                           "msg": "", "trouble": 0.0}, g=UNI, follow=False)

    def test_data_link_states(self):
        st = self.link()
        state = lambda now: ui.UI.link_state(st, now)[:2]   # noqa: E731
        note = lambda err, nws_side=False: ui.UI.link_note(st, err, nws_side)  # noqa: E731
        self.assertEqual(state(1000), ("CHECKING", "label"))
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
        win = locate.Fix(34.7, -82.8, acc_m=3000, source="windows")
        self.assertEqual(gps("fix", "COM3", fix3), ("3D FIX", "ok"))
        self.assertEqual(gps("fix", "COM3", fix2), ("2D FIX", "warn"))
        self.assertEqual(gps("none", "no GPS receiver found", win), ("ROUGH", "warn"))
        self.assertEqual(gps("searching", "no satellite fix yet", None), ("NO FIX", "err"))
        self.assertEqual(gps("none", "no GPS receiver found", None), ("NO GPS", "err"))
        self.assertEqual(gps("fix", "COM3", fix3, follow=False), ("OFF", "label"))
        self.assertEqual(ui.UI.gps_short("GPS on COM3: can't open (PermissionError)"),
                         "can't open COM3")

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
        from wxtui.cluster import digit_rows
        rows = digit_rows("1,090", 6)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(set(len(r) for r in rows)), 1)      # a clean rectangle
        self.assertEqual(digit_rows("8", 4), ["█▀▀▀█", "█▄▄▄█", "█   █", "█▄▄▄█"])
        ascii_rows = digit_rows("8", 6, uni=False)
        self.assertTrue(all(set(r) <= set('#"_ ') for r in ascii_rows))
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

    def test_signal_points_back_to_the_last_contact(self):
        here = {"lat": 34.75, "lon": -82.78}
        st = types.SimpleNamespace(follow=True, g=UNI, signal_spot=(34.70, -82.78, time.time() - 600),
                                   view_loc=lambda: here, link_state=lambda now=None: ("OFFLINE", "err", ""))
        spec, line1, line2 = ui.UI.signal_state(st)
        self.assertEqual(spec, "warn")
        self.assertIn("3.5 mi S", line1[0][0])                   # 0.05 degrees of latitude
        self.assertIn("10m ago", line1[1][0])
        st.link_state = lambda now=None: ("ONLINE", "ok", "")
        self.assertIn("online", "".join(p[0] for p in ui.UI.signal_state(st)[1]))
        st.link_state = lambda now=None: ("OFFLINE", "err", "")
        st.signal_spot = None
        self.assertIn("offline", ui.UI.signal_state(st)[1][0][0])

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
    """The single alerts list: official sources and news turned into short reports."""

    def test_headline_to_report(self):
        r = news.report("Crash closes northbound lanes of US 123 near Clemson")
        self.assertEqual((r["kind"], r["detail"]), ("CRASH", ["US 123 NB", "lanes closed"]))
        r = news.report("Man killed in crash on I-85 southbound in Anderson County")
        self.assertEqual((r["detail"], r["county"]), (["I-85 SB", "fatal"], "Anderson Co"))
        self.assertEqual(news.report("Tiger Boulevard closed after water main break")["kind"],
                         "WATER MAIN BREAK")
        self.assertIsNone(news.report("Library hosts fall book sale"))

    def test_feeds_parse(self):
        f = feeds.parse_fires(fires({"fires": [[34.86, -82.75, "Table Rock", 850.4, 40]]}))
        self.assertEqual((f[0]["name"], f[0]["acres"], f[0]["contained"]), ("Table Rock", 850.4, 40))
        self.assertEqual(feeds.parse_space(scales({"g": 3})), {"g": 3, "text": "strong"})
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
        news_items = [{"title": "Crash closes northbound lanes of US 123 near Clemson",
                       "time": now - 2400, "source": "WYFF4", "also": 2, "km": 3.2,
                       "place": "Clemson, 2 mi SW"},
                      {"title": "Library hosts fall book sale", "time": now, "source": "X"}]
        cache = {"fires": {"items": feeds.parse_fires(fires({"fires": [
                     [34.86, -82.75, "Table Rock", 850, 40], [36.5, -80.0, "Far Away", 10, 0]]}))},
                 "space": dict(feeds.parse_space(scales({"g": 3})), fetched=now)}
        rs = views.build_reports(a, near, news_items, cache, 34.68, -82.84, now)
        self.assertEqual([(r["tier"][0], r["kind"]) for r in rs],
                         [("o", "WIND ADVISORY"), ("o", "TORNADO WARNING"), ("r", "CRASH"),
                          ("o", "WILDFIRE"), ("o", "SOLAR STORM")])   # here, ahead, closest...
        crash = rs[2]
        self.assertEqual((crash["where"], crash["source"]), ("Clemson, 2 mi SW", "3 reports"))
        self.assertEqual(rs[1]["where"], "4 mi ahead")


class ScreenDriver(unittest.TestCase):
    class Console(object):
        def __init__(self):
            self.keys = []

        def read(self, wait):
            out, self.keys = self.keys, []
            return out

    class Out(object):
        def __init__(self):
            self.data = []

        def write(self, s):
            self.data.append(s)

        def flush(self):
            pass

    def setUp(self):
        self.con, self.out = self.Console(), self.Out()
        self.w = screen.Window(self.con, self.out)
        self.w.timeout(100)

    def key(self, *chars):
        self.con.keys = list(chars)
        return self.w.get_wch()

    def test_keys(self):
        self.assertEqual(self.key("\x1b", "[", "B"), screen.KEY_DOWN)
        self.assertEqual(self.key("\x1b", "O", "A"), screen.KEY_UP)
        self.assertEqual(self.key("\x1b", "[", "3", "~"), screen.KEY_DC)
        self.assertEqual(self.key("\x1b"), "\x1b")
        self.assertEqual(self.key("\x1b", "q"), "\x1b")
        self.assertEqual(self.w.get_wch(), "q")              # not swallowed
        with self.assertRaises(screen.error):
            self.w.get_wch()                                 # times out

    def test_paint(self):
        self.w.erase()
        screen.init_pair(1, 196, -1)
        self.w.addstr(0, 0, "éx", screen.color_pair(1) | screen.A_BOLD)
        self.w._paint()
        out = "".join(self.out.data)
        self.assertIn("\x1b[0;1;38;5;196m", out)
        self.assertIn("éx", out)                       # accent stays on its letter


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
        self.assertTrue(all(ua.startswith("(weather-tui/") for _, ua in self.fake.log))
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


@unittest.skipIf(os.name == "nt", "uses a Unix pseudo-terminal")
class Dashboard(unittest.TestCase):
    """Starts the real full screen in a pseudo-terminal and quits with q."""

    def run_app(self, screen_kind, args, keys=b"", seconds=3.0, size=None):
        import pty
        import select
        fake = FakeNWS().start()
        home = tempfile.mkdtemp()
        try:
            env = dict(os.environ, WEATHER_TUI_API=fake.url, WEATHER_TUI_SCREEN=screen_kind,
                       WEATHER_TUI_NEWS=fake.url + "/news", WEATHER_TUI_FEEDS=fake.url,
                       TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")
            with warnings.catch_warnings():         # the child execs at once: no deadlock risk
                warnings.simplefilter("ignore", DeprecationWarning)
                pid, fd = pty.fork()
            if pid == 0:
                if size:                              # rows, columns
                    import fcntl
                    import struct
                    import termios
                    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
                os.execve(sys.executable, [sys.executable, os.path.join(ROOT, "weather.py"),
                                           "--home", home] + args, env)
            out, end, sent = b"", time.time() + seconds, False
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    try:
                        out += os.read(fd, 65536)
                    except OSError:
                        break
                if not sent and time.time() > end - seconds / 2:
                    os.write(fd, keys)
                    sent = True
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

    def test_gauge_cluster_on_a_big_window(self):
        for kind in ("curses", "builtin"):
            code, out, crashed = self.run_app(kind, ["--place", "clemson sc"], size=(47, 156))
            self.assertEqual((code, crashed), (0, False), kind)
            for tile in ("SPEED", "GRADE", "ELEV", "HERE", "GRID", "SIGNAL", "ER", "ALERTS",
                         "WEATHER", "DATA LINK", "GPS"):
                self.assertIn(" %s " % tile, out, (kind, tile))
            self.assertIn("Oconee Memorial", out, kind)

    def test_both_screen_drivers(self):
        for kind in ("curses", "builtin"):
            code, out, crashed = self.run_app(kind, ["--place", "clemson sc"], keys=b"a")
            self.assertEqual((code, crashed), (0, False), kind)
            self.assertIn("Wind Advisory", out, kind)
            self.assertIn("CRASH", out, kind)                     # a news report
            self.assertEqual(self.fake.count("/news?"), 1, kind)  # searched once, not every loop

    def test_day_and_night_looks(self):
        for kind, look in (("curses", "day"), ("builtin", "night")):
            code, out, crashed = self.run_app(kind, ["--place", "clemson sc", "--display", look],
                                              keys=b"d")
            self.assertEqual((code, crashed), (0, False), (kind, look))
            self.assertIn("Oconee Memorial", out, look)        # nearest ER
            self.assertIn("display:", out, look)              # d switched the look
            self.assertIn("reported", out, look)                # news counted as reported

    def test_follow_without_location_sources(self):
        code, out, crashed = self.run_app("builtin", ["--follow"])
        self.assertEqual((code, crashed), (0, False))
        self.assertIn("waiting for", out)


if __name__ == "__main__":
    unittest.main()
