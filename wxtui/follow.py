"""When to fetch: follow mode refreshes as you move; alerts are checked more
often than the weather because they matter most. Standard library only.
"""

import time

from .geo import km_between

FOLLOW_MOVE_KM = 8.0        # full refresh after moving about 5 miles
ALERTS_EVERY_SEC = 180      # alerts re-check this often, following or not
FOLLOW_ALERTS_KM = 3.0      # ...or after moving about 2 miles
POINTS_REUSE_KM = 2.0       # forecast grid lookup reused within this distance
ROUGH_KM = 4.8              # a fix worse than ~3 miles counts as "approximate"


def follow_due(entry, lat, lon, refresh_sec, now=None):
    """What follow mode should fetch now: "full", "alerts" or None."""
    now = time.time() if now is None else now
    full = entry.get("follow_full")
    if not full:
        return "full"
    if km_between(full["lat"], full["lon"], lat, lon) >= FOLLOW_MOVE_KM:
        return "full"
    if now - full["at"] >= refresh_sec:
        return "full"
    al = entry.get("follow_alerts") or full
    if now - al["at"] >= ALERTS_EVERY_SEC or km_between(al["lat"], al["lon"], lat, lon) >= FOLLOW_ALERTS_KM:
        return "alerts"
    return None


def follow_mark(entry, kind, lat, lon, now=None):
    now = time.time() if now is None else now
    mark = {"lat": lat, "lon": lon, "at": now}
    entry["follow_alerts"] = mark
    if kind == "full":
        entry["follow_full"] = dict(mark)


def follow_snapshot(entry, lat, lon):
    """Reuse the forecast-grid lookup only if it was made near here."""
    pts = entry.get("points")
    snap = {"points": None, "qpf": entry.get("qpf")}
    if pts and pts.get("at") and km_between(pts["at"][0], pts["at"][1], lat, lon) <= POINTS_REUSE_KM:
        snap["points"] = pts
    return snap
