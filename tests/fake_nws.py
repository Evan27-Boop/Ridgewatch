"""A stand-in for api.weather.gov, for testing without the internet.

Replies use the same shapes as the real NWS API. Start one with
FakeNWS().start(); point the app at it with RIDGEWATCH_API=fake.url.
Change fake.mode while it runs:
  {"down": True}               refuse every request (no connection)
  {"alerts": "none"|"one"|"two"}
  {"warn_north_of": 34.80}     add a Tornado Warning north of that latitude
  {"obs": "stale"}             station reading 5 hours old
  {"dry": True}                no rain in the forecast (the weather box graphs temperature)
  {"storm": [lat, lon, km]}    a Tornado Warning over a square that size around that
                               point (in point alerts inside it, and in area alerts)
It also fakes the other official feeds (RIDGEWATCH_FEEDS=fake.url):
  {"fires": [[lat, lon, "Name", acres, pct_contained], ...]}   /wfigs
  {"roads": [[lat, lon, "I-85", "Northbound", "crash", "2 Right Lanes"], ...]}
                                                          /511ga and /drivenc
"""

import gzip
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EST = timezone(timedelta(hours=-5))


def iso(t, tz=EST):
    return datetime.fromtimestamp(t, tz).isoformat()


def points(base, lat, lon):
    if not (24 <= lat <= 50 and -125 <= lon <= -66):
        return 404, {"title": "Data Unavailable For Requested Point", "status": 404}
    return 200, {"properties": {
        "gridId": "GSP", "gridX": 10, "gridY": 20, "cwa": "GSP",
        "forecastHourly": base + "/gridpoints/GSP/10,20/forecast/hourly",
        "forecastGridData": base + "/gridpoints/GSP/10,20",
        "observationStations": base + "/gridpoints/GSP/10,20/stations",
        "relativeLocation": {"properties": {"city": "Salem", "state": "SC"}},
        "county": "https://api.weather.gov/zones/county/SCC077",
        "timeZone": "America/New_York"}}


def hourly(now=None, dry=False):
    now = time.time() if now is None else now
    start = now - (now % 3600)
    periods = []
    for i in range(48):
        st = start + i * 3600
        x = i - 9
        pop = 5 if dry else int(round(75 * math.exp(-(x * x) / 18.0))) if i < 30 else 5
        periods.append({
            "number": i + 1, "startTime": iso(st), "endTime": iso(st + 3600),
            "temperature": int(round(55 + 9 * math.sin((i + 6) / 24.0 * 2 * math.pi))),
            "temperatureUnit": "F",
            "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": pop},
            "shortForecast": "Chance Rain Showers" if pop >= 30 else "Partly Cloudy"})
    return {"properties": {"units": "us", "periods": periods,
                           "elevation": {"unitCode": "wmoUnit:m", "value": 231.0}}}


def grid(now=None):
    now = time.time() if now is None else now
    start = now - (now % 21600)
    amounts = [0.0, 1.3, 4.1, 0.8, 0.0, 0.0, 0.0, 0.0]
    vals = [{"validTime": iso(start + i * 21600, timezone.utc) + "/PT6H", "value": mm}
            for i, mm in enumerate(amounts)]
    return {"properties": {"quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": vals}}}


def stations():
    return {"features": [{"properties": {"stationIdentifier": sid, "name": name}}
                         for sid, name in (("KXXX", "Broken Sensor Field"),
                                           ("KCEU", "Clemson-Oconee County Airport"))]}


def obs(sid, mode, now=None):
    now = time.time() if now is None else now
    if sid == "KXXX":                         # a station with no temperature
        return {"properties": {"timestamp": iso(now - 9 * 3600, timezone.utc),
                               "temperature": {"unitCode": "wmoUnit:degC", "value": None}}}
    age = 5 * 3600 if mode.get("obs") == "stale" else 25 * 60
    return {"properties": {
        "timestamp": iso(now - age, timezone.utc), "textDescription": "Mostly Cloudy",
        "temperature": {"unitCode": "wmoUnit:degC", "value": 12.2},
        "dewpoint": {"unitCode": "wmoUnit:degC", "value": 4.4},
        "windDirection": {"unitCode": "wmoUnit:degree_(angle)", "value": 225},
        "windSpeed": {"unitCode": "wmoUnit:km_h-1", "value": 12.96},
        "windGust": {"unitCode": "wmoUnit:km_h-1", "value": 33.3},
        "barometricPressure": {"unitCode": "wmoUnit:Pa", "value": 101660},
        "relativeHumidity": {"unitCode": "wmoUnit:percent", "value": 59.6},
        "windChill": {"unitCode": "wmoUnit:degC", "value": 10.1},
        "heatIndex": {"unitCode": "wmoUnit:degC", "value": None}}}


def alert(aid, event, severity, onset, ends, **extra):
    p = {"id": aid, "status": "Actual", "messageType": "Alert", "event": event,
         "severity": severity, "urgency": "Expected", "certainty": "Likely",
         "headline": event + " issued by NWS Greenville-Spartanburg SC",
         "onset": iso(onset), "effective": iso(onset), "expires": iso(ends), "ends": iso(ends),
         "areaDesc": "Oconee Mountains; Pickens Mountains",
         "senderName": "NWS Greenville-Spartanburg SC",
         "description": "* WHAT...Southwest winds 15 to 25 mph.\n\n* WHERE...Oconee and\nPickens counties.",
         "instruction": "Secure outdoor objects."}
    p.update(extra)
    return {"properties": p}


def storm(mode, now=None):
    """The fake Tornado Warning's feature, or None."""
    if not mode.get("storm"):
        return None
    now = time.time() if now is None else now
    lat, lon, km = mode["storm"]
    dy, dx = km / 111.2, km / (111.2 * math.cos(math.radians(lat)))
    f = alert("urn:storm", "Tornado Warning", "Extreme", now - 120, now + 2400,
              instruction="TAKE COVER NOW!",
              headline="Tornado Warning issued until 4:15 PM by NWS Greenville-Spartanburg SC")
    f["geometry"] = {"type": "Polygon", "coordinates": [[
        [lon - dx, lat - dy], [lon + dx, lat - dy], [lon + dx, lat + dy], [lon - dx, lat + dy],
        [lon - dx, lat - dy]]]}
    return f


def area_alerts(mode, now=None):
    feats = [alert("urn:wind", "Wind Advisory", "Moderate", time.time() - 3600,
                   time.time() + 26 * 3600)]                  # no shape: not "nearby"
    s = storm(mode, now)
    if s:
        feats.append(s)
    return {"features": feats}


def alerts(mode, lat=0.0, now=None, lon=None):
    now = time.time() if now is None else now
    kind = mode.get("alerts", "one")
    feats = []
    s = storm(mode, now)
    if s and lon is not None:
        ring = s["geometry"]["coordinates"][0]
        if ring[0][1] <= lat <= ring[2][1] and ring[0][0] <= lon <= ring[1][0]:
            feats.append(s)
    if kind in ("one", "two"):
        feats.append(alert("urn:wind", "Wind Advisory", "Moderate", now - 3600, now + 26 * 3600))
    if kind == "two":
        feats.append(alert("urn:flood", "Flood Watch", "Severe", now + 5 * 3600, now + 30 * 3600))
        feats.append({"properties": {"id": "urn:test", "status": "Test", "event": "Test Message"}})
    if mode.get("warn_north_of") is not None and lat > mode["warn_north_of"]:
        feats.insert(0, alert("urn:tor", "Tornado Warning", "Extreme", now - 60, now + 1800,
                              instruction="TAKE COVER NOW!"))
    return {"features": feats}


def fires(mode, now=None):
    now = time.time() if now is None else now
    feats = []
    for i, (lat, lon, name, acres, pct) in enumerate(mode.get("fires") or []):
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"IncidentName": name.upper(), "DailyAcres": acres,
                                     "PercentContained": pct, "POOState": "US-SC",
                                     "FireDiscoveryDateTime": int((now - 2 * 86400) * 1000),
                                     "UniqueFireIdentifier": "2026-SC-%d" % i}})
    return {"type": "FeatureCollection", "features": feats}


def road_events(mode, now=None):
    now = time.time() if now is None else now
    out = []
    for i, (lat, lon, road, direction, sub, lanes) in enumerate(mode.get("roads") or []):
        out.append({"ID": i + 1, "Organization": "DOT", "RoadwayName": road,
                    "DirectionOfTravel": direction, "Description": "%s on %s" % (sub, road),
                    "Reported": int(now - 1200), "LastUpdated": int(now - 300),
                    "StartDate": int(now - 1200), "PlannedEndDate": None,
                    "LanesAffected": lanes, "Latitude": lat, "Longitude": lon,
                    "EventType": "accidentsAndIncidents", "EventSubType": sub,
                    "IsFullClosure": False, "Severity": "Major"})
    out.append({"ID": 99, "EventType": "roadwork", "EventSubType": "construction",
                "RoadwayName": "I-85", "Latitude": 34.6, "Longitude": -82.8,
                "IsFullClosure": False})                      # planned work: left out
    return out


class FakeNWS(object):
    def __init__(self):
        self.mode = {}
        self.log = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/geo+json")
                if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                    body = gzip.compress(body)
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.log.append((self.path, self.headers.get("User-Agent")))
                m = fake.mode
                if m.get("down"):
                    self.close_connection = True
                    self.connection.close()
                    return
                path, base = self.path, fake.url
                mm = re.match(r"^/points/(-?[\d.]+),(-?[\d.]+)$", path)
                if mm:
                    return self.reply(*points(base, float(mm.group(1)), float(mm.group(2))))
                if path == "/gridpoints/GSP/10,20/forecast/hourly":
                    return self.reply(200, hourly(dry=m.get("dry", False)))
                if path == "/gridpoints/GSP/10,20":
                    return self.reply(200, grid())
                if path == "/gridpoints/GSP/10,20/stations":
                    return self.reply(200, stations())
                mm = re.match(r"^/stations/(\w+)/observations/latest$", path)
                if mm:
                    return self.reply(200, obs(mm.group(1), m))
                if path.startswith("/wfigs?"):
                    return self.reply(200, fires(m))
                if path.startswith(("/511ga?", "/drivenc?")):
                    if "key=" not in path:
                        return self.reply(401, {"error": "key required"})
                    return self.reply(200, road_events(m))
                if path.startswith("/alerts/active?point="):
                    lat, lon = (float(v) for v in path.split("=")[1].split(","))
                    return self.reply(200, alerts(m, lat, lon=lon))
                if path.startswith("/alerts/active?area="):
                    return self.reply(200, area_alerts(m))
                self.reply(404, {"title": "Not Found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, pattern):
        return sum(1 for path, _ in self.log if pattern in path)
