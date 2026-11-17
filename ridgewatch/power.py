"""Battery level and charging, read from the kernel (/sys/class/power_supply).
Machines without a battery simply show nothing.
"""

import os

BASE = "/sys/class/power_supply"
LOW_PCT = 20


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def battery(base=BASE):
    """{'pct': 0-100, 'charging': bool, 'external': bool} for the first battery,
    or None if there isn't one. 'external' means power is coming in (charging or full)."""
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    bat, mains = None, None
    for n in names:
        kind = _read(os.path.join(base, n, "type"))
        if kind == "Battery" and bat is None and _read(os.path.join(base, n, "scope")) != "Device":
            bat = n
        elif kind in ("Mains", "USB") and _read(os.path.join(base, n, "online")) == "1":
            mains = n
    if bat is None:
        return None
    try:
        pct = max(0, min(100, int(_read(os.path.join(base, bat, "capacity")))))
    except (TypeError, ValueError):
        return None
    status = _read(os.path.join(base, bat, "status")) or ""
    charging = status == "Charging"
    external = bool(mains) or status in ("Charging", "Full", "Not charging")
    return {"pct": pct, "charging": charging, "external": external}


def tag(b):
    """(text, color spec) for the header, e.g. ('BATT 82% CHARGING', 'ok')."""
    if not b:
        return None
    if b["charging"]:
        return "BATT %d%% CHARGING" % b["pct"], "ok"
    if b["external"]:
        return "BATT %d%% PLUGGED IN" % b["pct"], "ok"
    return "BATT %d%% ON BATTERY" % b["pct"], ("err" if b["pct"] <= LOW_PCT else "warn")
