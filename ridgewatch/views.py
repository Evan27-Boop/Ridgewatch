"""What the screen shows: saved NWS data turned into the numbers, ages and
labels the dashboard and the text report display, plus which alerts are
urgent enough to flash. No drawing here. Standard library only.
"""

import time
from datetime import datetime, timedelta, timezone

from . import feeds, geo
from .geo import compass  # noqa: F401  (re-exported for the screens)
from .nws import OBS_FRESH

ALERTS_TRUST = 10 * 60          # alerts re-check every 3 min; older than this = out of date
NEARBY_KM = 40.0                # list warnings within about 25 miles
AHEAD_KM = 32.0                 # "ahead" looks about 20 miles down your heading
OFFLINE_ERRORS = ("no connection", "timed out", "secure connection failed")


def fmt_age(seconds):
    if seconds is None:
        return "?"
    s = max(0, int(seconds))
    if s < 90:
        return "just now"
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400 * 2:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def ago(seconds):
    a = fmt_age(seconds)
    return a if a in ("just now", "?") else a + " ago"


def _local(epoch, offset_min):
    """The time at the forecast location (NWS gives each time's UTC offset)."""
    return datetime.fromtimestamp(epoch, timezone(timedelta(minutes=offset_min)))


def fmt_hour(epoch, offset_min, short=False):
    d = _local(epoch, offset_min)
    h = d.hour % 12 or 12
    ap = ("a" if d.hour < 12 else "p") if short else (" AM" if d.hour < 12 else " PM")
    if d.minute and not short:
        return "%d:%02d%s" % (h, d.minute, ap)
    return "%d%s" % (h, ap)


def fmt_when(t, now=None):
    """(epoch, offset) -> '6 PM Fri' (or '6 PM' if it's within the next 18 h)."""
    if not t:
        return "?"
    now = time.time() if now is None else now
    d = _local(t[0], t[1])
    s = fmt_hour(t[0], t[1])
    if abs(t[0] - now) > 18 * 3600:
        s += " " + d.strftime("%a")
    return s


def section_age(entry, name, now=None):
    now = time.time() if now is None else now
    d = entry.get(name)
    if not d or not d.get("fetched"):
        return None
    return now - d["fetched"]


def forecast_now(entry, now=None):
    """(temp F, short text) for the forecast hour we're in, or None."""
    now = time.time() if now is None else now
    for st, en, off, temp, pop, short in (entry.get("hourly") or {}).get("periods", []):
        if st <= now < en:
            return temp, short or ""
    return None


def view_now(entry, now=None):
    """Current conditions. When the station's reading is old (or missing), this hour's
    forecast stands in, marked forecast=True, so an old number never passes for now."""
    now = time.time() if now is None else now
    o = entry.get("obs")
    err = (entry.get("obs_err") or {}).get("msg")
    age = now - o["time"] if o and o.get("time") else None
    if not o or age is None or age > OBS_FRESH:
        fc = forecast_now(entry, now)
        if fc:
            return {"ok": True, "forecast": True, "temp": None if fc[0] is None else int(round(fc[0])),
                    "desc": fc[1], "feels": None, "dew": None, "humidity": None, "wind": None,
                    "pressure": None, "station": (o or {}).get("station", ""),
                    "station_name": (o or {}).get("station_name", ""), "obs_age": age,
                    "stale": True, "error": err}
    if not o:
        return {"ok": False, "error": err}
    wind = None
    if o.get("wind_mph") is not None:
        spd = int(round(o["wind_mph"]))
        wind = "calm" if spd == 0 else "%s %d mph" % (compass(o.get("wind_dir")), spd)
        wind = wind.replace("  ", " ").strip()
        if o.get("gust_mph") and o["gust_mph"] >= (o["wind_mph"] + 5):
            wind += ", gusts %d" % int(round(o["gust_mph"]))
    r = lambda v: None if v is None else int(round(v))
    return {
        "ok": True, "temp": r(o.get("temp_f")), "desc": o.get("desc") or "",
        "feels": r(o.get("feels_f")), "dew": r(o.get("dew_f")),
        "humidity": r(o.get("humidity")), "wind": wind,
        "pressure": o.get("pressure_inhg"),
        "station": o.get("station", ""), "station_name": o.get("station_name", ""),
        "obs_age": age, "stale": age is None or age > OBS_FRESH, "error": err,
        "forecast": False,
    }


def view_alerts(entry, now=None):
    now = time.time() if now is None else now
    a = entry.get("alerts")
    err = (entry.get("alerts_err") or {}).get("msg")
    if not a:
        return {"known": False, "items": [], "error": err, "age": None}
    items = []
    for it in a.get("items", []):
        end = it.get("ends") or it.get("expires")
        if end and end[0] < now:
            continue                                   # expired while offline
        items.append(it)
    age = now - a["fetched"]
    return {"known": True, "items": items, "error": err, "age": age,
            "trusted": age <= ALERTS_TRUST, "offline": err in OFFLINE_ERRORS}


def alert_when(it, now=None):
    now = time.time() if now is None else now
    if it.get("onset") and it["onset"][0] > now + 60:
        return "from " + fmt_when(it["onset"], now)
    if it.get("ends"):
        return "until " + fmt_when(it["ends"], now)
    return ""


def view_precip(entry, now=None, hours=24):
    now = time.time() if now is None else now
    h = entry.get("hourly")
    q = entry.get("qpf")
    out = {"hours": [], "total_in": None, "partial": False, "peak": None,
           "hourly_err": (entry.get("hourly_err") or {}).get("msg"),
           "qpf_err": (entry.get("qpf_err") or {}).get("msg"),
           "age": section_age(entry, "hourly", now)}
    if h:
        for st, en, off, temp, pop, short in h.get("periods", []):
            if en <= now:
                continue
            out["hours"].append((st, off, max(0, min(100, int(round(pop or 0)))), temp, short))
            if len(out["hours"]) >= hours:
                break
        if out["hours"]:
            best = max(out["hours"], key=lambda x: x[2])
            out["peak"] = (best[2], best[0], best[1])
    if q:
        end = now + hours * 3600
        total, covered = 0.0, 0.0
        for st, en, mm in q.get("intervals", []):
            lo, hi = max(st, now), min(en, end)
            if hi <= lo or en <= st:
                continue
            total += mm * (hi - lo) / (en - st)
            covered += hi - lo
        if covered > 0:
            out["total_in"] = total / 25.4
            out["partial"] = covered < hours * 3600 * 0.9
    return out


def _clock(epoch, offset_min):
    d = _local(epoch, offset_min)
    return "%d %s" % (d.hour % 12 or 12, "AM" if d.hour < 12 else "PM")


def temp_range(p):
    """((high, '3p'), (low, '6a')) over the next 24 hours, or None."""
    hrs = [h for h in p["hours"] if h[3] is not None]
    if not hrs:
        return None
    hi = max(hrs, key=lambda h: h[3])
    lo = min(hrs, key=lambda h: h[3])
    return ((int(round(hi[3])), fmt_hour(hi[0], hi[1], short=True)),
            (int(round(lo[3])), fmt_hour(lo[0], lo[1], short=True)))


def rain_outlook(p, now=None):
    """One line for the next 24 hours: ('rain likely 7-11 PM (75%)', 75) or
    ('no rain expected', 5). None without a forecast."""
    now = time.time() if now is None else now
    hours = p["hours"]
    if not hours:
        return None
    run = []
    for h in hours:
        if h[2] >= 30:
            run.append(h)
        elif run:
            break
    if not run:
        peak = max(h[2] for h in hours)
        if peak >= 15:
            return ("little chance of rain next 24h (%d%% at most)" % peak, peak)
        return ("no rain expected next 24h", peak)
    peak = max(h[2] for h in run)
    shorts = " ".join((h[4] or "").lower() for h in run)
    what = "storms" if "thunder" in shorts else "snow" if "snow" in shorts else "rain"
    word = ("%s likely" % what) if peak >= 60 else ("chance of %s" % what)
    st, off = run[0][0], run[0][1]
    end = run[-1][0] + 3600
    if run[0] is hours[0]:
        when = "now until %s" % _clock(end, off)
    else:
        a, b = _clock(st, off), _clock(end, off)
        when = ("%s-%s" % (a.split()[0], b)) if a.split()[1] == b.split()[1] else "%s-%s" % (a, b)
        if _local(st, off).date() != _local(now, off).date():
            when += " tomorrow"
    return ("%s %s (%d%%)" % (word, when, peak), peak)


def view_nearby(entry, lat, lon, heading=None, now=None):
    """Storm warnings near you but not over you: [{event, severity, km, dir, ahead,
    id, refs, ends}], warnings ahead on your heading first, then the closest.
    `heading` is only given while moving."""
    now = time.time() if now is None else now
    out = []
    for it in (entry.get("nearby") or {}).get("items", []):
        end = it.get("ends") or it.get("expires")
        if end and end[0] < now:
            continue
        rings = it.get("rings") or []
        if not rings or any(geo.inside(lat, lon, r) for r in rings):
            continue                                # over you: it's in the main list
        km, plat, plon = min(geo.nearest_on_ring(lat, lon, r) for r in rings)
        ahead = None
        if heading is not None:
            for d in range(1, int(AHEAD_KM) + 1):
                for off in (0, -12, 12):
                    a, b = geo.destination(lat, lon, heading + off, d)
                    if any(geo.inside(a, b, r) for r in rings):
                        ahead = d
                        break
                if ahead:
                    break
        if km > NEARBY_KM and not ahead:
            continue
        out.append(dict(it, km=float(ahead or km), ahead=bool(ahead),
                        dir=compass(geo.bearing(lat, lon, plat, plon)), rings=None))
    out.sort(key=lambda i: (not i["ahead"], i["km"]))
    return out


def overall_status(entry, busy, now=None):
    """(text, level) for the header: level is 'ok', 'warn' or 'busy'."""
    now = time.time() if now is None else now
    if busy:
        return "updating", "busy"
    if entry.get("fatal"):
        return entry["fatal"], "err"
    last_ok = entry.get("last_ok")
    rnd = entry.get("round") or {"ok": 0, "err": 0}
    if not last_ok:
        if rnd["err"]:
            return "data link down · no data yet", "err"
        return "waiting for data", "warn"
    if rnd["err"] and not rnd["ok"]:
        age = now - last_ok
        if age < 90:
            return "data link down · showing saved data", "warn"
        return "data link down · saved data is %s old" % fmt_age(age), "warn"
    text = "updated " + ago(now - last_ok)
    if rnd["err"]:
        return text + " · some parts failed", "warn"
    return text, "ok"


# ---------------------------------------------------------- urgent alerts --

URGENT_EVENTS = {"Evacuation Immediate", "Civil Emergency Message", "Local Area Emergency",
                 "Child Abduction Emergency", "911 Telephone Outage Emergency",
                 "Blue Alert"}


def is_urgent(item):
    """Warnings and public-safety emergencies (not watches or advisories)."""
    ev = item.get("event") or ""
    return ev.endswith(("Warning", "Emergency")) or ev in URGENT_EVENTS


def new_urgent(entry, items):
    """Urgent alerts you haven't been told about; remembers everything seen.

    NWS gives an updated warning a new ID that points back at the old one. An
    update of a warning you already saw stays quiet, unless it's now a
    different kind of warning (say, severe thunderstorm -> tornado).
    """
    seen = [s if isinstance(s, list) else [s, ""] for s in (entry.get("seen") or [])]
    events = {sid: ev for sid, ev in seen}
    fresh = []
    for it in items:
        aid = it.get("id")
        if not aid or aid in events:
            continue
        repeat = any(events.get(r) == it.get("event") for r in it.get("refs") or [])
        if is_urgent(it) and not repeat:
            fresh.append(it)
        events[aid] = it.get("event") or ""
        seen.append([aid, events[aid]])
    entry["seen"] = seen[-300:]
    return fresh


# ------------------------------------------------------- the alerts list --
# One list for everything worth knowing nearby. Each line is a short report:
#   tier     "official" (an agency said so). Radio calls will be "heard".
#   kind     WIND ADVISORY, WILDFIRE, CRASH...
#   detail   ["US 123 NB", "lanes closed"]
#   where    "here", "4 mi ahead", "12 mi N", "Clemson, 2 mi SW", "Pickens Co"
#   when     "until 4:15 PM", "40m"
#   source   "NWS", "NIFC", "511GA"
# Order: official alerts over you, then storm warnings (ahead first), then
# everything else closest first.

def _mi(km):
    return max(1, int(round(km / 1.609344)))


def build_reports(a, nearby, feed_cache, lat, lon, now=None):
    now = time.time() if now is None else now
    out = []
    for it in a.get("items") or []:                            # NWS, over you
        out.append({"tier": "official", "kind": it["event"].upper(), "sev": it["severity"],
                    "cat": "nws", "detail": [], "where": "here", "when": alert_when(it, now),
                    "source": "NWS", "km": 0.0, "rank": 0, "id": it.get("id"), "item": it})
    for it in nearby or []:                                     # NWS, nearby or ahead
        out.append({"tier": "official", "kind": it["event"].upper(), "sev": it["severity"],
                    "cat": "nws", "detail": [],
                    "where": ("%d mi ahead" % _mi(it["km"])) if it["ahead"] else
                    "%d mi %s" % (_mi(it["km"]), it["dir"]),
                    "when": alert_when(it, now), "source": "NWS", "km": it["km"],
                    "rank": 1 if it["ahead"] else 1.5, "id": it.get("id"), "item": it})
    fc = feed_cache or {}
    for it in feeds.near((fc.get("fires") or {}).get("items") or [], lat, lon, feeds.FIRE_SHOW_KM):
        detail = [it["name"]]
        if it["acres"]:
            detail.append("{:,} ac".format(int(round(it["acres"]))) if it["acres"] >= 1
                          else "under 1 ac")
        if it["contained"] is not None:
            detail.append("%d%% contained" % it["contained"])
        out.append({"tier": "official", "kind": "PRESCRIBED BURN" if it["rx"] else "WILDFIRE",
                    "sev": None, "cat": "fire", "detail": detail,
                    "where": "%d mi %s" % (_mi(it["km"]), it["dir"]),
                    "when": ("found " + ago(now - it["found"])) if it["found"] else "",
                    "source": "NIFC", "km": it["km"], "rank": 2, "id": it["id"], "item": it})
    for st in sorted(feeds.ROADS):
        for it in feeds.near((fc.get("roads_" + st) or {}).get("items") or [], lat, lon,
                             feeds.ROAD_SHOW_KM):
            when = ""
            try:
                when = fmt_age(now - float(it["reported"])) if it.get("reported") else ""
            except (TypeError, ValueError):
                pass
            out.append({"tier": "official", "kind": it["kind"], "sev": None, "cat": "road",
                        "detail": it["detail"], "where": "%d mi %s" % (_mi(it["km"]), it["dir"]),
                        "when": when, "source": it["source"], "km": it["km"], "rank": 2,
                        "id": it["id"], "item": it})
    out.sort(key=lambda r: (r["rank"], r["km"] if r["km"] is not None else 0,
                            0 if r["tier"] == "official" else 1))
    return out



def now_source(n, dot="·"):
    """Where the 'now' numbers came from, for the weather box title:
    'KCEU · 12m ago' or 'forecast now · KCEU station 14h old'."""
    if n.get("forecast"):
        if n.get("station") and n.get("obs_age") is not None:
            return "forecast now %s %s station %s old" % (dot, n["station"],
                                                          ago(n["obs_age"]).replace(" ago", ""))
        return "forecast now %s no station reading" % dot
    return "%s %s %s" % (n["station"], dot, ago(n["obs_age"]))
