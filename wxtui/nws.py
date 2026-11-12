"""Talking to the National Weather Service: requests with time limits and
retries, turning NWS replies into plain numbers, and fetching everything for a
spot on a background thread. Standard library only.
"""

import gzip
import json
import math
import os
import queue
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import __version__, geo
from .settings import loc_key

API = os.environ.get("WEATHER_TUI_API", "https://api.weather.gov").rstrip("/")

POINTS_MAX_AGE = 7 * 86400      # re-check the location's forecast grid weekly
QPF_REUSE_SEC = 3600            # the rain-total download is the biggest: reuse it for an hour
QPF_REUSE_KM = 15.0             # ...if you're still within about 10 miles of where it was fetched
OBS_FRESH = 2 * 3600            # a station reading older than this tries the next station


class NotFound(Exception):
    pass


class FetchError(Exception):
    def __init__(self, msg, network=False):
        Exception.__init__(self, msg)
        self.network = network        # True = couldn't reach NWS at all (vs. NWS hiccup)


def user_agent(contact=""):
    # NWS asks every app to identify itself. No personal info unless you add it.
    who = (contact or "").strip() or "personal weather app"
    return "(weather-tui/%s, %s)" % (__version__, who)


def fetch_url(url, ua, parse, accept="application/geo+json", who="NWS",
              timeout=10, tries=3, sleep=time.sleep, limit=8 << 20):
    """GET a URL with a time limit and a few patient retries; return parse(body)."""
    problem, network = "no connection", True
    for attempt in range(tries):
        wait = 2 * (attempt + 1)
        network = False
        req = urllib.request.Request(url, headers={
            "User-Agent": ua, "Accept": accept, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(limit + 1)
                if len(body) > limit:
                    raise FetchError("reply from %s too large" % who)
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
            return parse(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url)
            if e.code == 429:
                problem, wait = "rate limited", 5
            elif e.code >= 500:
                problem = "%s server error %d" % (who, e.code)
            else:
                raise FetchError("%s said HTTP %d" % (who, e.code))
        except ssl.SSLError:
            problem, network = "secure connection failed", True
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            reason = getattr(e, "reason", e)
            network = True
            if "timed out" in str(reason) or isinstance(reason, (socket.timeout, TimeoutError)):
                problem = "timed out"
                if attempt >= 1:            # two slow tries is enough
                    break
            else:
                problem = "no connection"
        except (ValueError, EOFError, SyntaxError):    # SyntaxError: bad XML
            problem = "garbled reply from %s" % who
        if attempt < tries - 1:
            sleep(wait)
    raise FetchError(problem, network)


def get_json(url, ua, timeout=10, tries=3, sleep=time.sleep):
    """GET an NWS URL as JSON."""
    return fetch_url(url, ua, lambda body: json.loads(body.decode("utf-8")),
                     timeout=timeout, tries=tries, sleep=sleep)


# ------------------------------------------------------------ parsing --

_DUR_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_time(s):
    """ISO time from NWS -> (unix seconds, UTC offset in minutes) or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.timestamp(), int(d.utcoffset().total_seconds() // 60)


def parse_valid_time(s):
    """'2026-11-05T18:00:00+00:00/PT6H' -> (start, end) unix seconds."""
    try:
        start_s, dur = s.split("/")
    except (AttributeError, ValueError):
        return None
    t = parse_time(start_s)
    m = _DUR_RE.match(dur)
    if not t or not m or not any(m.groups()):
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return t[0], t[0] + d * 86400 + h * 3600 + mi * 60 + se


def _qv(obj):
    """NWS 'quantitative value' -> (value, unit) or (None, '')."""
    if isinstance(obj, dict):
        v = obj.get("value")
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            return float(v), str(obj.get("unitCode") or obj.get("uom") or "")
    return None, ""


def to_f(obj):
    v, u = _qv(obj)
    if v is None:
        return None
    if u.endswith("degF"):
        return v
    if u.endswith(":K"):
        return (v - 273.15) * 9 / 5 + 32
    return v * 9 / 5 + 32                                  # degC (the normal case)


def to_mph(obj):
    v, u = _qv(obj)
    if v is None:
        return None
    if u.endswith("m_s-1"):
        return v * 2.23694
    if u.endswith("mi_h-1"):
        return v
    if u.endswith(":kn") or u.endswith("kt"):
        return v * 1.15078
    return v * 0.621371                                    # km_h-1 (the normal case)


def to_percent(obj):
    v, _ = _qv(obj)
    return v


def parse_points(data):
    p = data.get("properties") or {}
    rel = ((p.get("relativeLocation") or {}).get("properties") or {})
    if not p.get("forecastHourly") and not p.get("forecastGridData"):
        raise NotFound("no forecast for this point")
    return {
        "hourly": p.get("forecastHourly"),
        "grid": p.get("forecastGridData"),
        "stations_url": p.get("observationStations"),
        "office": p.get("gridId") or p.get("cwa") or "",
        "tz": p.get("timeZone") or "",
        "city": rel.get("city") or "",
        "state": rel.get("state") or "",
        # ".../zones/county/SCC077" -> "SCC077": the county code weather radios use
        "county_zone": str(p.get("county") or "").rstrip("/").rsplit("/", 1)[-1],
        "stations": [],
        "fetched": time.time(),
    }


def parse_stations(data, limit=6):
    out = []
    for f in (data.get("features") or [])[:limit]:
        pr = f.get("properties") or {}
        if pr.get("stationIdentifier"):
            out.append({"id": pr["stationIdentifier"], "name": pr.get("name") or ""})
    return out


def parse_obs(data, station):
    p = data.get("properties") or {}
    t = parse_time(p.get("timestamp"))
    temp = to_f(p.get("temperature"))
    feels = None
    hi, wc = to_f(p.get("heatIndex")), to_f(p.get("windChill"))
    if temp is not None:
        if hi is not None and temp >= 80 and hi - temp >= 1:
            feels = hi
        elif wc is not None and temp <= 50 and temp - wc >= 1:
            feels = wc
    wd, _ = _qv(p.get("windDirection"))
    pres, pu = _qv(p.get("barometricPressure"))
    return {
        "station": station.get("id", ""),
        "station_name": station.get("name", ""),
        "time": t[0] if t else None,
        "desc": (p.get("textDescription") or "").strip(),
        "temp_f": temp,
        "dew_f": to_f(p.get("dewpoint")),
        "feels_f": feels,
        "humidity": to_percent(p.get("relativeHumidity")),
        "wind_mph": to_mph(p.get("windSpeed")),
        "gust_mph": to_mph(p.get("windGust")),
        "wind_dir": wd,
        "pressure_inhg": pres / 3386.39 if pres is not None and pu.endswith("Pa") else None,
        "fetched": time.time(),
    }


def parse_hourly(data, now=None, keep=48):
    now = time.time() if now is None else now
    periods = []
    for per in (data.get("properties") or {}).get("periods") or []:
        st, en = parse_time(per.get("startTime")), parse_time(per.get("endTime"))
        if not st or not en or en[0] <= now - 3600:
            continue
        temp = per.get("temperature")
        if isinstance(temp, dict):                         # newer "QV" style
            temp = to_f(temp)
        elif isinstance(temp, (int, float)) and per.get("temperatureUnit") == "C":
            temp = temp * 9 / 5 + 32
        pop = to_percent(per.get("probabilityOfPrecipitation"))
        periods.append([st[0], en[0], st[1], temp, 0 if pop is None else pop,
                        per.get("shortForecast") or ""])
        if len(periods) >= keep:
            break
    if not periods:
        raise FetchError("hourly forecast came back empty")
    elev, unit = _qv((data.get("properties") or {}).get("elevation"))
    if elev is not None and not unit.endswith(("ft", "foot")):
        elev *= 3.28084                                   # NWS gives meters
    return {"periods": periods, "fetched": time.time(),
            "elev_ft": None if elev is None else int(round(elev))}


def parse_qpf(data, now=None):
    now = time.time() if now is None else now
    q = (data.get("properties") or {}).get("quantitativePrecipitation") or {}
    unit = str(q.get("uom") or "wmoUnit:mm")
    scale = 25.4 if unit.endswith(":in") else 1.0           # store everything in mm
    out = []
    for v in q.get("values") or []:
        span = parse_valid_time(v.get("validTime"))
        val = v.get("value")
        if not span or not isinstance(val, (int, float)):
            continue
        if span[1] <= now - 3600 or span[0] >= now + 49 * 3600:
            continue
        out.append([span[0], span[1], float(val) * scale])
    if not out:
        raise FetchError("no rainfall amounts in forecast data")
    return {"intervals": out, "fetched": time.time()}


SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}


def _rings(geom):
    """Outer rings ([[lon, lat], ...]) of a Polygon or MultiPolygon; [] for none."""
    geom = geom or {}
    coords = geom.get("coordinates") or []
    polys = [coords] if geom.get("type") == "Polygon" else \
        coords if geom.get("type") == "MultiPolygon" else []
    out = []
    for poly in polys:
        if poly and len(poly[0]) >= 3:
            out.append([[round(float(x), 4), round(float(y), 4)] for x, y in
                        (pt[:2] for pt in poly[0])])
    return out


def parse_alerts(data, shapes=False):
    """Alerts in a reply, most severe first. shapes=True keeps only alerts drawn
    as an area on the map (storm warnings), with that area, for "nearby"."""
    items = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        if p.get("status") not in (None, "Actual"):
            continue
        if p.get("messageType") == "Cancel":
            continue
        rings = _rings(f.get("geometry")) if shapes else None
        if shapes and not (rings and (p.get("event") or "").endswith("Warning")):
            continue
        items.append({
            "id": p.get("id") or f.get("id") or "",
            "refs": [r.get("identifier") or str(r.get("@id") or "").rsplit("/", 1)[-1]
                     for r in (p.get("references") or []) if isinstance(r, dict)],
            "event": (p.get("event") or "Alert").strip(),
            "severity": p.get("severity") or "Unknown",
            "urgency": p.get("urgency") or "",
            "certainty": p.get("certainty") or "",
            "headline": (p.get("headline") or "").strip(),
            "onset": parse_time(p.get("onset") or p.get("effective")),
            "ends": parse_time(p.get("ends") or p.get("expires")),
            "expires": parse_time(p.get("expires")),
            "area": (p.get("areaDesc") or "").strip(),
            "sender": (p.get("senderName") or "").strip(),
            "description": (p.get("description") or "").strip(),
            "instruction": (p.get("instruction") or "").strip(),
        })
        if shapes:                      # keep the cache small: the shape, not the essay
            items[-1].update(rings=rings, description="", instruction="", area="")
    items.sort(key=lambda a: (SEVERITY_RANK.get(a["severity"], 4),
                              (a["onset"] or (0, 0))[0]))
    return {"items": items, "fetched": time.time()}



# ----------------------------------------------------------- fetching --

def nearby_warnings(states, getter, ua):
    """Severe and extreme warnings across the states around you, with their map
    areas. One small request; if NWS ever refuses the filters, one per state."""
    try:
        data = getter("%s/alerts/active?area=%s&severity=Extreme,Severe"
                      % (API, ",".join(states)), ua)
        return parse_alerts(data, shapes=True)
    except FetchError as e:
        if "HTTP 400" not in str(e):
            raise
    feats, ids = [], set()
    for st in states:
        for f in getter("%s/alerts/active?area=%s" % (API, st), ua).get("features") or []:
            fid = (f.get("properties") or {}).get("id") or f.get("id")
            if fid not in ids:                      # a warning can cover two states
                ids.add(fid)
                feats.append(f)
    return parse_alerts({"features": feats}, shapes=True)


def fetch_location(loc, snapshot, post, ua, getter=get_json, only=None):
    """Fetch everything for one location (or only="alerts"), posting each piece
    as it arrives.

    post(section, data=None, error=None). Each piece is independent: one
    failing never stops the others, and old data is never thrown away here.
    """
    # ~100 m precision is plenty for a 2.5 km forecast grid and keeps your exact spot to yourself
    lat, lon = round(float(loc["lat"]), 3), round(float(loc["lon"]), 3)
    where = "%.3f,%.3f" % (lat, lon)
    raw_getter = getter
    down = []

    def getter(url, ua):
        # once NWS is unreachable, don't spend a minute retrying every piece
        if down:
            raise FetchError(down[0], True)
        try:
            return raw_getter(url, ua)
        except FetchError as e:
            if e.network:
                down.append(str(e))
            raise

    def attempt(section, fn):
        try:
            data = fn()
        except NotFound:
            post(section, error="not available for this spot")
            return None
        except FetchError as e:
            post(section, error=str(e))
            return None
        except Exception as e:          # never let one odd reply kill the rest
            post(section, error="unexpected reply (%s)" % e.__class__.__name__)
            return None
        post(section, data=data)
        return data

    attempt("alerts", lambda: parse_alerts(
        getter("%s/alerts/active?point=%s" % (API, where), ua)))
    states = [st for st in snapshot.get("states") or [] if re.match(r"^[A-Z]{2}$", st)]
    if states:
        attempt("nearby", lambda: nearby_warnings(states, getter, ua))
    if only == "alerts":
        return

    pts = snapshot.get("points")
    if not pts or time.time() - pts.get("fetched", 0) > POINTS_MAX_AGE:
        try:
            pts = parse_points(getter("%s/points/%s" % (API, where), ua))
            pts["at"] = [lat, lon]
            post("points", data=pts)
        except NotFound:
            post("fatal", error="NWS has no forecast for this spot (outside the US?)")
            return
        except (FetchError, Exception) as e:
            if not pts:
                msg = str(e) if isinstance(e, FetchError) else "unexpected reply"
                for s in ("hourly", "obs", "qpf"):
                    post(s, error=msg)
                return
    pts = dict(pts)

    def refresh_points():
        new = parse_points(getter("%s/points/%s" % (API, where), ua))
        new["at"] = [lat, lon]
        new["stations"] = []
        post("points", data=new)
        return new

    # hourly forecast (chance of rain per hour); a 404 usually means the grid moved
    if pts.get("hourly"):
        try:
            data = parse_hourly(getter(pts["hourly"], ua))
            post("hourly", data=data)
        except NotFound:
            try:
                pts = refresh_points()
                attempt("hourly", lambda: parse_hourly(getter(pts["hourly"], ua)))
            except (NotFound, FetchError, Exception):
                post("hourly", error="forecast not available right now")
        except FetchError as e:
            post("hourly", error=str(e))
        except Exception as e:
            post("hourly", error="unexpected reply (%s)" % e.__class__.__name__)
    else:
        post("hourly", error="no hourly forecast for this spot")

    # current conditions: nearest station with a fresh reading
    stations = pts.get("stations") or []
    if not stations and pts.get("stations_url"):
        try:
            stations = parse_stations(getter(pts["stations_url"], ua))
            pts["stations"] = stations
            post("points", data=pts)
        except (NotFound, FetchError, Exception):
            stations = []
    best, problem = None, "no nearby station reported"
    for st in stations[:4]:
        try:
            o = parse_obs(getter("%s/stations/%s/observations/latest" % (API, st["id"]), ua), st)
        except FetchError as e:
            problem = str(e)
            continue
        except (NotFound, Exception):
            continue
        if o["temp_f"] is None:
            continue
        if o["time"] and time.time() - o["time"] <= OBS_FRESH:
            best = o
            break
        if best is None or (o["time"] or 0) > (best["time"] or 0):
            best = o
    if best:
        post("obs", data=best)
    else:
        post("obs", error=problem)

    # rainfall amounts (big download; only feeds the 24 h total)
    if pts.get("grid"):
        old = snapshot.get("qpf") or {}
        if old.get("at") and time.time() - old.get("fetched", 0) < QPF_REUSE_SEC and \
                geo.km_between(old["at"][0], old["at"][1], lat, lon) < QPF_REUSE_KM:
            pass                                    # recent and close enough: keep it
        else:
            attempt("qpf", lambda: dict(parse_qpf(getter(pts["grid"], ua)), at=[lat, lon]))
    else:
        post("qpf", error="no rainfall amounts for this spot")


class Fetcher(object):
    """Runs fetches on a background thread so the screen never freezes.

    Results land in self.results as (location_key, section, data, error).
    """

    def __init__(self, ua_func):
        self.ua_func = ua_func
        self.results = queue.Queue()
        self._lock = threading.Lock()
        self._pending = None
        self._wake = threading.Event()
        self.busy = False
        t = threading.Thread(target=self._run, name="fetcher", daemon=True)
        t.start()

    def request(self, loc, snapshot, only=None):
        snap = json.loads(json.dumps(snapshot))
        with self._lock:
            p = self._pending
            if p and p[2] is None and only and loc_key(p[0]) == loc_key(loc):
                self._pending = (dict(loc), p[1], None)     # a full fetch already covers it
            else:
                self._pending = (dict(loc), snap, only)
            self.busy = True
        self._wake.set()

    def _run(self):
        while True:
            self._wake.wait()
            with self._lock:
                job, self._pending = self._pending, None
                self._wake.clear()
            if not job:
                continue
            loc, snap, only = job
            key = loc_key(loc)

            def post(section, data=None, error=None):
                self.results.put((key, section, data, error))

            post("start")
            try:
                fetch_location(loc, snap, post, self.ua_func(), only=only)
            except Exception as e:      # belt and braces: the thread must survive
                post("fatal", error="fetch crashed (%s)" % e.__class__.__name__)
            with self._lock:
                if self._pending is None:
                    self.busy = False
            post("done")


def apply_result(entry, section, data, error, now=None):
    """Fold one fetch result into a location's cache entry."""
    now = time.time() if now is None else now
    if section == "start":
        entry["fetch_started"] = now
        entry["round"] = {"ok": 0, "err": 0}
        entry.pop("fatal", None)
        return
    if section == "done":
        entry["fetch_done"] = now
        return
    if section == "fatal":
        entry["fatal"] = error
        return
    rnd = entry.setdefault("round", {"ok": 0, "err": 0})
    if section == "points":
        old = entry.get("points") or {}
        if data and not data.get("stations") and old.get("stations") and \
                old.get("grid") == data.get("grid"):
            data["stations"] = old["stations"]
        entry["points"] = data
        return
    if error is not None:
        entry[section + "_err"] = {"msg": error, "at": now}
        rnd["err"] += 1
    else:
        entry[section] = data
        entry.pop(section + "_err", None)
        entry["last_ok"] = now
        rnd["ok"] += 1
