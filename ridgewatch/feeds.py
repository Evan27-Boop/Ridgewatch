"""Official sources beyond the weather service, each optional:

  wildfires      National Interagency Fire Center: current wildland fires near you
                 (name, size, how contained). Public, no key; updated every 5 min.
  511 roads      Georgia (511GA) and North Carolina (DriveNC): crashes, closures and
                 blocked lanes with exact positions. Each needs a developer key from
                 that state, set in config.json. South Carolina has no public feed.

Everything here is fetched on its own thread so it never delays weather alerts.
"""

import json
import os
import queue
import re
import threading
import time
import urllib.parse

from . import __version__, geo, nws

# One switch for tests: RIDGEWATCH_FEEDS=http://127.0.0.1:port points all of these
# at a local fake server (as /wfigs, /511ga, /drivenc).
_TEST = os.environ.get("RIDGEWATCH_FEEDS", "").rstrip("/")
WFIGS = (_TEST + "/wfigs") if _TEST else \
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/" \
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
ROADS = {   # state: (config key name, events URL, label)
    "GA": ("511ga", (_TEST + "/511ga") if _TEST else "https://511ga.org/api/v2/get/event", "511GA"),
    "NC": ("drivenc", (_TEST + "/drivenc") if _TEST else "https://www.drivenc.gov/api/v2/get/event",
           "NCDOT"),
}

FIRE_EVERY = 10 * 60        # re-check fires this often, or after moving FIRE_MOVE_KM
FIRE_MOVE_KM = 30.0
FIRE_SHOW_KM = 80.0         # list fires within about 50 miles
ROAD_EVERY = 5 * 60
ROAD_SHOW_KM = 40.0         # road incidents within about 25 miles

UA = "Mozilla/5.0 (compatible; ridgewatch/%s)" % __version__


def _json(body):
    return json.loads(body.decode("utf-8"))


def _get(url, who):
    return nws.fetch_url(url, UA, _json, accept="application/json", who=who, tries=2)


# ------------------------------------------------------------- wildfires --

def fire_url(lat, lon):
    box = "%.3f,%.3f,%.3f,%.3f" % (lon - 1.3, lat - 1.0, lon + 1.3, lat + 1.0)
    return WFIGS + "?" + urllib.parse.urlencode({
        "where": "1=1", "outFields": "*", "f": "geojson", "outSR": "4326",
        "geometry": box, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects"})


def _ms(v):
    try:
        return float(v) / 1000.0 if v else None
    except (TypeError, ValueError):
        return None


def parse_fires(data):
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        c = g.get("coordinates") or []
        if g.get("type") != "Point" or len(c) < 2:
            continue
        if p.get("FireOutDateTime"):
            continue                                   # declared out
        try:
            acres = float(p.get("DailyAcres") or p.get("IncidentSize") or 0) or None
        except (TypeError, ValueError):
            acres = None
        try:
            pct = p.get("PercentContained")
            pct = None if pct is None else max(0, min(100, int(round(float(pct)))))
        except (TypeError, ValueError):
            pct = None
        name = " ".join(str(p.get("IncidentName") or "").split()).title() or "Unnamed"
        out.append({"id": str(p.get("UniqueFireIdentifier") or p.get("IrwinID") or name),
                    "name": name, "acres": acres, "contained": pct,
                    "found": _ms(p.get("FireDiscoveryDateTime")),
                    "rx": str(p.get("IncidentTypeCategory") or "").upper() == "RX",
                    "lat": round(float(c[1]), 4), "lon": round(float(c[0]), 4)})
    return out


# -------------------------------------------------------------- 511 roads --

_DIR = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB",
        "north": "NB", "south": "SB", "east": "EB", "west": "WB", "both": "both ways",
        "all": "", "none": "", "unknown": ""}


def parse_roads(data, label):
    """State 511 events -> crashes, incidents and closures (planned roadwork is left
    out unless it closes the road)."""
    events = data if isinstance(data, list) else \
        next((v for v in (data or {}).values() if isinstance(v, list)), [])
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        etype = str(e.get("EventType") or "")
        sub = str(e.get("EventSubType") or "").lower()
        full = bool(e.get("IsFullClosure"))
        if etype == "accidentsAndIncidents":
            kind = "CRASH" if re.search(r"crash|accident|collision", sub) else \
                "DISABLED VEHICLE" if "disabled" in sub else "INCIDENT"
        elif etype == "closures" or (etype == "roadwork" and full):
            kind = "ROAD CLOSED"
        else:
            continue
        try:
            lat, lon = float(e.get("Latitude")), float(e.get("Longitude"))
        except (TypeError, ValueError):
            continue
        road = " ".join(str(e.get("RoadwayName") or "").split())
        d = _DIR.get(str(e.get("DirectionOfTravel") or "").lower(), "")
        lanes = " ".join(str(e.get("LanesAffected") or "").split())
        detail = [(road + (" " + d if d else "")).strip()]
        if full:
            detail.append("all lanes closed")
        elif lanes and lanes.lower() not in ("unknown", "none", "no data"):
            detail.append(lanes.lower())
        out.append({"id": "%s-%s" % (label, e.get("ID")), "kind": kind,
                    "detail": [x for x in detail if x], "lat": lat, "lon": lon,
                    "reported": e.get("Reported") or e.get("StartDate"),
                    "text": " ".join(str(e.get("Description") or "").split()),
                    "source": label})
    return out


def near(items, lat, lon, km):
    """Items within km of (lat, lon), with distance and direction added."""
    out = []
    for it in items:
        d = geo.km_between(lat, lon, it["lat"], it["lon"])
        if d <= km:
            out.append(dict(it, km=d, dir=geo.compass(geo.bearing(lat, lon, it["lat"], it["lon"]))))
    return out


# ---------------------------------------------------------------- fetching --

def fetch(job):
    """Run one job: ('fires', lat, lon) / ('roads', state, key)."""
    if job[0] == "fires":
        return {"items": parse_fires(_get(fire_url(job[1], job[2]), "the fire service")),
                "at": [job[1], job[2]]}
    if job[0] == "roads":
        _, url, label = ROADS[job[1]]
        q = urllib.parse.urlencode({"key": job[2], "format": "json"})
        return {"items": parse_roads(_get(url + "?" + q, label), label)}
    raise ValueError(job[0])


class FeedFetcher(object):
    """Jobs run one at a time on their own thread; results land in .results as
    (name, data, error)."""

    def __init__(self, run=fetch):
        self.run = run
        self.results = queue.Queue()
        self._jobs = queue.Queue()
        self.pending = set()
        threading.Thread(target=self._loop, name="feeds", daemon=True).start()

    def request(self, name, job):
        if name in self.pending:
            return
        self.pending.add(name)
        self._jobs.put((name, job))

    def _loop(self):
        while True:
            name, job = self._jobs.get()
            try:
                data = dict(self.run(job), fetched=time.time())
                self.results.put((name, data, None))
            except nws.NotFound:
                self.results.put((name, None, "not found"))
            except nws.FetchError as e:
                self.results.put((name, None, str(e)))
            except Exception as e:      # the thread must survive anything
                self.results.put((name, None, "unexpected reply (%s)" % e.__class__.__name__))
            self.pending.discard(name)
