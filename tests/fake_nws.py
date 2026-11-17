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
  {"lsr": [[lat, lon, "TSTM WND DMG", "TREES DOWN ON SC-183", minutes_ago], ...]}
                                                          /products (NWS storm reports)
  {"ipaws": [["EVI", "045077", "Evacuation order for ..."], ...]}   /ipaws/feed, /ipaws/eas/N
  {"outages": [[lat, lon, customers], ...]}               /duke
  {"quakes": [[lat, lon, mag, "place", minutes_ago], ...]}  /usgs
  {"faa_stops": ["ATL", "CLT", ...]}                       /faa
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


def lsr_text(reports, now=None):
    """A Local Storm Report product in the NWS fixed-column layout (times in EST)."""
    now = time.time() if now is None else now
    head = datetime.fromtimestamp(now, EST)
    lines = ["000", "NWUS52 KGSP", "LSRGSP", "", "PRELIMINARY LOCAL STORM REPORT",
             "NATIONAL WEATHER SERVICE GREENVILLE-SPARTANBURG SC",
             head.strftime("%I%M %p EST %a %b %d %Y").lstrip("0").upper(), "",
             "..TIME...   ...EVENT...      ...CITY LOCATION...     ...LAT.LON...",
             "..DATE...   ....MAG....      ..COUNTY LOCATION..ST.. ...SOURCE....",
             "            ..REMARKS..", ""]
    for lat, lon, event, remarks, mins in reports:
        t = datetime.fromtimestamp(now - mins * 60, EST)
        lines.append("%-12s%-17s%-24s%5.2fN %5.2fW" % (t.strftime("%I%M %p"), event, "2 W PICKENS",
                                                       lat, -lon))
        lines.append("%-12s%-17s%-19s%-5s%s" % (t.strftime("%m/%d/%Y"), "", "PICKENS", "SC",
                                               "PUBLIC"))
        lines += ["", "            " + remarks, ""]
    lines.append("&&")
    return "\n".join(lines)


def ipaws_feed(base, mode):
    entries = "".join(
        '<entry><title>%s</title><link href="%s/ipaws/eas/%d"/><id>%s/ipaws/eas/%d</id>'
        '<updated>2026-11-17T10:00:00Z</updated><category term="%s" label="event"/>'
        '<category term="%s" label="statefips"/></entry>' % (code, base, i, base, i, code, same[1:3])
        for i, (code, same, text) in enumerate(mode.get("ipaws") or []))
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            '<title>IPAWS EAS FEED</title>%s</feed>' % entries)


def ipaws_cap(mode, i, now=None):
    now = time.time() if now is None else now
    code, same, text = (mode.get("ipaws") or [])[i]
    exp = datetime.fromtimestamp(now + 3 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    return ('<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">'
            '<identifier>FEMA-%d</identifier><sender>county-em@example.gov</sender>'
            '<sent>2026-11-17T10:00:00-05:00</sent><status>Actual</status><msgType>Alert</msgType>'
            '<info><language>en-US</language><event>%s</event>'
            '<eventCode><valueName>SAME</valueName><value>%s</value></eventCode>'
            '<urgency>Immediate</urgency><severity>Extreme</severity>'
            '<expires>%s</expires><senderName>Pickens County Emergency Management</senderName>'
            '<headline>%s</headline><description>%s</description>'
            '<instruction>Leave now by US-178 north.</instruction>'
            '<area><areaDesc>Pickens County</areaDesc>'
            '<geocode><valueName>SAME</valueName><value>%s</value></geocode></area>'
            '</info></alert>' % (i, code, code, exp, text, text, same))


def duke(mode):
    return {"features": [{"attributes": {"affected_customers": n, "start_time": "11/17/2026 1:40 PM",
                                         "cause": "Weather", "crew_status": "Crew dispatched"},
                          "geometry": {"x": lon, "y": lat}}
                         for lat, lon, n in mode.get("outages") or []]}


def usgs(mode, now=None):
    now = time.time() if now is None else now
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "us%d" % i,
         "properties": {"mag": mag, "place": place, "time": int((now - mins * 60) * 1000),
                        "alert": None, "tsunami": 0},
         "geometry": {"type": "Point", "coordinates": [lon, lat, 10.0]}}
        for i, (lat, lon, mag, place, mins) in enumerate(mode.get("quakes") or [])]}


def faa_xml(mode):
    stops = "".join("<Program><ARPT>%s</ARPT><Reason>equipment outage</Reason>"
                    "<End_Time>4:30 pm EST</End_Time></Program>" % a
                    for a in mode.get("faa_stops") or [])
    return ('<AIRPORT_STATUS_INFORMATION><Update_Time>now</Update_Time>'
            '<Delay_type><Name>Ground Stop Programs</Name><Ground_Stop_List>%s</Ground_Stop_List>'
            '</Delay_type><Delay_type><Name>Airport Closures</Name><Airport_Closure_List>'
            '<Airport><ARPT>LAS</ARPT><Reason>!LAS 11/123 LAS AD AP CLSD TO NON SKED TRANSIENT GA ACFT'
            '</Reason><Start>Nov 17</Start><Reopen>Nov 18</Reopen></Airport></Airport_Closure_List>'
            '</Delay_type></AIRPORT_STATUS_INFORMATION>' % stops)


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

            def raw(self, code, text, ctype):
                body = text.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
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
                if path == "/products/types/LSR/locations/GSP":
                    graph = [{"id": "lsr-1", "issuanceTime": iso(time.time() - 300)}] \
                        if m.get("lsr") else []
                    return self.reply(200, {"@graph": graph})
                if path == "/products/lsr-1":
                    return self.reply(200, {"productText": lsr_text(m.get("lsr") or [])})
                if path == "/ipaws/feed":
                    return self.raw(200, ipaws_feed(base, m), "application/atom+xml")
                mm = re.match(r"^/ipaws/eas/(\d+)$", path)
                if mm:
                    return self.raw(200, ipaws_cap(m, int(mm.group(1))), "application/xml")
                if path.startswith("/duke?"):
                    return self.reply(200, duke(m))
                if path == "/usgs":
                    return self.reply(200, usgs(m))
                if path == "/faa":
                    return self.raw(200, faa_xml(m), "application/xml")
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
