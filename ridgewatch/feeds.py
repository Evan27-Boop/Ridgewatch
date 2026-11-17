"""Official sources beyond the weather forecast (the full list is sources.py):

  storm reports  NWS Local Storm Reports from your forecast office: damage, flooding,
                 tornadoes and large hail as spotters and officials report them.
  ipaws          FEMA's public IPAWS feed: evacuations, shelter orders, 911 outages and
                 other alerts from county and state officials (not the weather service).
  outages        Duke Energy's public outage layer: outages and customers affected.
  wildfires      National Interagency Fire Center: current wildland fires near you
                 (name, size, how contained). Public, no key; updated every 5 min.
  earthquakes    USGS: every quake of magnitude 2.5 or more in the last day.
  511 roads      Georgia (511GA) and North Carolina (DriveNC): crashes, closures and
                 blocked lanes with exact positions. Each needs a developer key from
                 that state, set in config.json. South Carolina has no public feed.
  flights        FAA airport status: ground stops, delay programs and closures.

Everything here is fetched on its own thread so it never delays weather alerts.
"""

import calendar
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

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

DUKE = (_TEST + "/duke") if _TEST else \
    "https://services3.arcgis.com/oX5r75R7mapdoI2F/ArcGIS/rest/services/Outages/FeatureServer/0/query"
IPAWS = (_TEST + "/ipaws/feed") if _TEST else "https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/feed"
USGS = (_TEST + "/usgs") if _TEST else \
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
FAA = (_TEST + "/faa") if _TEST else "https://nasstatus.faa.gov/api/airport-status-information"

LSR_EVERY, LSR_HOURS, LSR_SHOW_KM = 10 * 60, 6, 40.0          # reports within 25 mi, last 6 h
IPAWS_EVERY = 3 * 60                    # FEMA asks for no more than one request every 2 minutes
OUTAGE_EVERY, OUTAGE_MI, OUTAGE_MIN = 10 * 60, 15, 250        # 250+ customers within 15 mi
QUAKE_EVERY, QUAKE_LOCAL_KM = 10 * 60, 160.0                  # local quakes within 100 mi
FAA_EVERY, FAA_MANY = 5 * 60, 5                               # ground stops at 5+ airports
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


def _get_xml(url, who):
    return nws.fetch_url(url, UA, ET.fromstring, accept="application/xml", who=who, tries=2)


def _local(tag):
    """'{namespace}name' -> 'name'."""
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    """First descendant named `name`, whatever its namespace."""
    for e in el.iter():
        if _local(e.tag) == name:
            return e
    return None


def _text(el, name):
    e = _find(el, name) if el is not None else None
    return " ".join((e.text or "").split()) if e is not None and e.text else ""


def _iso(text):
    """ISO 8601 time -> epoch seconds, or None."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?",
                 text or "")
    if not m:
        return None
    t = calendar.timegm(tuple(int(m.group(i)) for i in range(1, 7)) + (0, 0, 0))
    z = m.group(7)
    if z and z != "Z":
        sign = -1 if z[0] == "-" else 1
        hh, mm = int(z[1:3]), int(z[-2:])
        t -= sign * (hh * 3600 + mm * 60)
    return t


# ----------------------------------------------------- NWS storm reports --

LSR_KINDS = {   # NWS event name -> (what Ridgewatch calls it, what to do)
    "TORNADO": ("TORNADO REPORTED", "TAKE SHELTER IF NEAR"),
    "FUNNEL CLOUD": ("FUNNEL CLOUD", "watch the sky"),
    "TSTM WND DMG": ("WIND DAMAGE", "watch for debris"),
    "NON-TSTM WND DMG": ("WIND DAMAGE", "watch for debris"),
    "DOWNBURST": ("WIND DAMAGE", "watch for debris"),
    "FLASH FLOOD": ("FLOODING", "avoid flooded roads"),
    "FLOOD": ("FLOODING", "avoid flooded roads"),
    "COASTAL FLOOD": ("FLOODING", "avoid flooded roads"),
    "DEBRIS FLOW": ("MUDSLIDE", "watch for debris"),
    "HAIL": ("LARGE HAIL", "slow down"),
    "ICE STORM": ("ICE", "avoid travel"),
    "FREEZING RAIN": ("ICE", "avoid travel"),
    "WILDFIRE": ("WILDFIRE", "avoid the area"),
    "LIGHTNING": ("LIGHTNING DAMAGE", ""),
}
_TZ = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8,
       "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10, "UTC": 0, "GMT": 0}
_LSR_LINE1 = re.compile(r"^(\d{3,4}) (AM|PM)\s+(.*?)\s{2,}(.*?)\s+(\d{1,2}\.\d{1,2})([NS]) +"
                        r"(\d{1,3}\.\d{1,2})([EW])\s*$")
_LSR_LINE2 = re.compile(r"^(\d\d)/(\d\d)/(\d{4})\s+(.*)$")


def _sentence(text):
    """ALL-CAPS report text -> sentence case, keeping road names: 'TREES DOWN ON SC-183'
    -> 'Trees down on SC-183'."""
    t = text.lower().capitalize()
    return re.sub(r"\b(sc|us|nc|ga|tn|sr|i|hwy)[- ]?(\d+[a-z]?)\b",
                  lambda m: (m.group(1).upper() if m.group(1) != "hwy" else "Hwy") + "-" + m.group(2).upper(), t)


def parse_lsr(text):
    """Reports in a Local Storm Report product: [{event, kind, do, mag, place, county,
    st, source, remarks, time, lat, lon, id}]. Large hail is 1 inch or more."""
    m = re.search(r"^\s*\d{3,4} (AM|PM) ([A-Z]{3,4}) ", text, re.M)
    offset = _TZ.get(m.group(2), -5) if m else -5
    lines = text.replace("\r", "").split("\n")
    out = []
    for i, line in enumerate(lines):
        m1 = _LSR_LINE1.match(line)
        if not m1 or i + 1 >= len(lines):
            continue
        m2 = _LSR_LINE2.match(lines[i + 1])
        if not m2:
            continue
        event = " ".join(m1.group(3).split()).upper()
        if event not in LSR_KINDS:
            continue
        rest = lines[i + 1][12:]
        mag = " ".join(rest[:17].split())
        county = " ".join(rest[17:36].split()).title()
        st = rest[36:39].strip()
        source = " ".join(rest[41:].split()).title()
        if event == "HAIL":
            hm = re.search(r"(\d+(?:\.\d+)?)", mag)
            if not hm or float(hm.group(1)) < 1.0:
                continue
        remarks = []
        for nxt in lines[i + 2:]:
            if _LSR_LINE1.match(nxt) or nxt.startswith(("&&", "$$")):
                break
            if nxt.strip():
                remarks.append(nxt.strip())
            elif remarks:
                break
        hh, ampm = int(m1.group(1)) // 100, m1.group(2)
        mi = int(m1.group(1)) % 100
        hh = (hh % 12) + (12 if ampm == "PM" else 0)
        mo, dd, yy = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            t = calendar.timegm((yy, mo, dd, hh, mi, 0, 0, 0, 0)) - offset * 3600
        except (ValueError, OverflowError):
            continue
        lat = float(m1.group(5)) * (1 if m1.group(6) == "N" else -1)
        lon = float(m1.group(7)) * (1 if m1.group(8) == "E" else -1)
        kind, do = LSR_KINDS[event]
        text_r = " ".join(remarks)
        if kind == "WIND DAMAGE" and re.search(r"\bTREES?\b", text_r, re.I):
            kind = "TREES DOWN"
        out.append({"id": "lsr-%s-%d-%.2f-%.2f" % (event, t, lat, lon), "event": event,
                    "kind": kind, "do": do, "mag": mag, "place": " ".join(m1.group(4).split()).title(),
                    "county": county, "st": st, "source": source, "remarks": _sentence(text_r),
                    "time": t, "lat": lat, "lon": lon})
    return out


def fetch_lsr(office, known, now=None):
    """Storm reports from the last LSR_HOURS. Products already read (`known`: {product
    id: {"t", "reports"}}) aren't fetched again."""
    now = time.time() if now is None else now
    ua = nws.user_agent()
    lst = nws.get_json("%s/products/types/LSR/locations/%s" % (nws.API, office), ua, tries=2)
    keep = {}
    for p in (lst.get("@graph") or [])[:30]:
        pid, t = p.get("id"), _iso(p.get("issuanceTime"))
        if not pid or not t or now - t > LSR_HOURS * 3600:
            continue
        if pid in known:
            keep[pid] = known[pid]
            continue
        if len([k for k in keep if k not in known]) >= 8:       # plenty for one round
            continue
        prod = nws.get_json("%s/products/%s" % (nws.API, pid), ua, tries=2)
        keep[pid] = {"t": t, "reports": parse_lsr(prod.get("productText") or "")}
    return {"products": keep, "office": office}


def lsr_reports(data, now=None):
    """Every report in the saved products, newest first, without repeats (a summary
    product lists reports already sent one by one)."""
    now = time.time() if now is None else now
    seen, out = set(), []
    for prod in (data or {}).get("products", {}).values():
        for r in prod.get("reports") or []:
            if r["id"] in seen or now - r["time"] > LSR_HOURS * 3600:
                continue
            seen.add(r["id"])
            out.append(r)
    out.sort(key=lambda r: -r["time"])
    return out


# --------------------------------------------------------- FEMA IPAWS --

IPAWS_SKIP = {"RWT", "RMT", "DMO", "NPT", "NMN", "ADR", "NAT", "NST", "NIC"}   # tests, admin
IPAWS_KINDS = {   # SAME event code -> (name, what to do, urgent?)
    "EVI": ("EVACUATION ORDER", "EVACUATE NOW", True),
    "SPW": ("SHELTER IN PLACE", "STAY INDOORS", True),
    "CEM": ("CIVIL EMERGENCY", "follow official orders", True),
    "LAE": ("LOCAL EMERGENCY", "follow official orders", True),
    "LEW": ("LAW ENFORCEMENT WARNING", "follow police orders", True),
    "HMW": ("HAZMAT WARNING", "avoid the area", True),
    "NUW": ("NUCLEAR PLANT WARNING", "follow official orders", True),
    "RHW": ("RADIATION WARNING", "follow official orders", True),
    "FRW": ("FIRE WARNING", "avoid the area", True),
    "TOE": ("911 OUTAGE", "911 down: see details", True),
    "CAE": ("CHILD ABDUCTION", "report sightings to 911", False),
    "BLU": ("BLUE ALERT", "report sightings to 911", False),
    "CDW": ("CIVIL DANGER WARNING", "follow official orders", True),
    "EAN": ("NATIONAL EMERGENCY", "follow official orders", True),
}


def parse_ipaws_feed(root):
    """The Atom list: [{id, url, event, statefips}]."""
    out = []
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        url, cats = "", {}
        for e in entry:
            name = _local(e.tag)
            if name == "link":
                url = e.get("href") or url
            elif name == "id" and not url:
                url = (e.text or "").strip()
            elif name == "category":
                cats[(e.get("label") or "").lower()] = (e.get("term") or "").strip()
        if url:
            out.append({"id": url.rstrip("/").rsplit("/", 1)[-1], "url": url,
                        "event": cats.get("event", "").upper(),
                        "statefips": cats.get("statefips", "").zfill(2)})
    return out


def parse_cap(root):
    """One CAP 1.2 alert -> dict, or None if it isn't a real, current public alert."""
    alert = root if _local(root.tag) == "alert" else _find(root, "alert")
    if alert is None:
        return None
    if _text(alert, "status") != "Actual" or _text(alert, "msgType") == "Cancel":
        return None
    infos = [e for e in alert if _local(e.tag) == "info"]
    info = next((i for i in infos if _text(i, "language").lower().startswith("en")), None) or \
        (infos[0] if infos else None)
    if info is None:
        return None
    code = ""
    for ec in info:
        if _local(ec.tag) == "eventCode" and _text(ec, "valueName") == "SAME":
            code = _text(ec, "value").upper()
    same, polys, areas = [], [], []
    for area in info:
        if _local(area.tag) != "area":
            continue
        areas.append(_text(area, "areaDesc"))
        for g in area:
            if _local(g.tag) == "geocode" and _text(g, "valueName") == "SAME":
                same.append(_text(g, "value").zfill(6))
            elif _local(g.tag) == "polygon" and g.text:
                ring = []
                for pair in g.text.split():
                    try:
                        la, lo = pair.split(",")
                        ring.append((float(la), float(lo)))
                    except ValueError:
                        pass
                if len(ring) >= 3:
                    polys.append(ring)
    sender = _text(alert, "sender") + " " + _text(info, "senderName")
    return {"id": _text(alert, "identifier"), "code": code,
            "event": _text(info, "event"), "headline": _text(info, "headline"),
            "description": _text(info, "description"), "instruction": _text(info, "instruction"),
            "severity": _text(info, "severity") or "Unknown", "urgency": _text(info, "urgency"),
            "sent": _iso(_text(alert, "sent")), "expires": _iso(_text(info, "expires")),
            "sender": _text(info, "senderName") or _text(alert, "sender"),
            "nws": bool(re.search(r"nws|noaa|weather service", sender, re.I)),
            "same": same, "polys": polys, "area": "; ".join(a for a in areas if a)}


def fetch_ipaws(statefips, known, now=None):
    """Public IPAWS alerts for your state (and national ones anywhere). Each alert's full
    text is fetched once; `known` holds what was already read."""
    now = time.time() if now is None else now
    entries = parse_ipaws_feed(_get_xml(IPAWS, "FEMA"))
    keep = {}
    for e in entries:
        if e["event"] in IPAWS_SKIP:
            continue
        if e["statefips"] not in statefips and e["event"] != "EAN":
            continue
        if e["id"] in known:
            keep[e["id"]] = known[e["id"]]
            continue
        cap = parse_cap(_get_xml(e["url"], "FEMA"))
        keep[e["id"]] = cap or {"skip": True}
    for k, v in list(keep.items()):
        if v.get("expires") and v["expires"] < now:
            del keep[k]
    return {"alerts": keep}


# ------------------------------------------------------------- outages --

def outage_url(lat, lon):
    return DUKE + "?" + urllib.parse.urlencode({
        "where": "1=1", "geometry": "%.4f,%.4f" % (lon, lat),
        "geometryType": "esriGeometryPoint", "inSR": "4326", "outSR": "4326",
        "distance": str(OUTAGE_MI), "units": "esriSRUnit_StatuteMile",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "affected_customers,start_time,cause,crew_status",
        "returnGeometry": "true", "f": "json"})


def parse_outages(data):
    out = []
    for f in (data or {}).get("features") or []:
        a, g = f.get("attributes") or {}, f.get("geometry") or {}
        try:
            n = int(a.get("affected_customers") or 0)
            lat, lon = float(g["y"]), float(g["x"])
        except (KeyError, TypeError, ValueError):
            continue
        if n > 0:
            out.append({"n": n, "lat": lat, "lon": lon, "cause": str(a.get("cause") or "").strip(),
                        "status": str(a.get("crew_status") or "").strip(),
                        "start": str(a.get("start_time") or "").strip()})
    return out


# --------------------------------------------------------- earthquakes --

US_STATES = {"Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
             "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
             "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland",
             "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana",
             "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
             "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
             "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
             "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
             "Puerto Rico", "U.S. Virgin Islands", "Guam"}
US_ABBR = {"CA", "NV", "OR", "WA", "ID", "UT", "AK", "HI", "MT", "WY", "NM", "AZ", "OK", "TX",
           "PR", "TN", "KY", "MO", "AR", "SC", "NC", "VA", "GA", "AL", "MS", "CO", "KS", "NE",
           "SD", "ND", "IL", "IN", "OH", "NY", "ME", "NH", "VT", "MA", "CT", "NJ", "PA", "MD",
           "WV", "DE", "FL", "LA", "MI", "WI", "MN", "IA", "RI"}


def in_us(place):
    tail = (place or "").rsplit(",", 1)[-1].strip()
    return tail in US_STATES or tail in US_ABBR


def parse_quakes(data):
    out = []
    for f in (data or {}).get("features") or []:
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        c = g.get("coordinates") or []
        try:
            mag, lat, lon = float(p.get("mag")), float(c[1]), float(c[0])
        except (TypeError, ValueError, IndexError):
            continue
        out.append({"id": "usgs-" + str(f.get("id") or p.get("code")), "mag": mag,
                    "place": str(p.get("place") or "").strip(), "time": _ms(p.get("time")),
                    "lat": lat, "lon": lon, "alert": p.get("alert") or "",
                    "tsunami": bool(p.get("tsunami")), "us": in_us(p.get("place"))})
    return out


# -------------------------------------------------------------- flights --

def parse_faa(root):
    """FAA airport status -> [{type, arpt, reason, until, avg, max}] where type is
    'stop', 'delay program', 'delay' or 'closed'."""
    out = []
    for dt in root.iter():
        if _local(dt.tag) != "Delay_type":
            continue
        name = _text(dt, "Name").lower()
        typ = "stop" if "ground stop" in name else "delay program" if "ground delay" in name \
            else "closed" if "closure" in name else "delay" if "delay" in name else None
        if not typ:
            continue
        for e in dt.iter():
            kids = {_local(k.tag) for k in e}
            if "ARPT" not in kids:
                continue
            item = {"type": typ, "arpt": _text(e, "ARPT").upper(), "reason": _text(e, "Reason"),
                    "until": _text(e, "End_Time") or _text(e, "Reopen"),
                    "avg": _text(e, "Avg"), "max": _text(e, "Max")}
            if typ == "closed" and re.search(r"TRANSIENT|\bGA\b|NON-?SCHEDULED", item["reason"], re.I):
                continue                        # closed only to private aircraft
            out.append(item)
    return out


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
    """Run one job: ('fires', lat, lon) / ('roads', state, key) / ('lsr', office, known) /
    ('ipaws', statefips, known) / ('outages', lat, lon) / ('quakes',) / ('faa',)."""
    if job[0] == "lsr":
        return fetch_lsr(job[1], job[2])
    if job[0] == "ipaws":
        return fetch_ipaws(job[1], job[2])
    if job[0] == "outages":
        return {"items": parse_outages(_get(outage_url(job[1], job[2]), "Duke Energy")),
                "at": [job[1], job[2]]}
    if job[0] == "quakes":
        return {"items": parse_quakes(_get(USGS, "USGS"))}
    if job[0] == "faa":
        return {"items": parse_faa(_get_xml(FAA, "the FAA"))}
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
