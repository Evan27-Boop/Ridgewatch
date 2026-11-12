"""Sunrise and sunset, worked out on the laptop (no internet).

Uses the standard sunrise equation (NOAA's simplified form); good to a minute or
two in the US, which is plenty for "how much daylight is left" and for switching
the screen between day and night colors.
"""

import math
import time

J2000 = 2451545.0


def _julian(epoch):
    return epoch / 86400.0 + 2440587.5


def _epoch(jd):
    return (jd - 2440587.5) * 86400.0


def times(lat, lon, when=None):
    """(sunrise, sunset) as epoch seconds for the solar day around `when`.
    (None, None) with the sun up all day returns ("up", "up"); down all day ("down", "down")."""
    when = time.time() if when is None else when
    n = round(_julian(when) - J2000 - 0.0009 + lon / 360.0)
    j_star = n + 0.0009 - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    transit = J2000 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(dec)) / \
        (math.cos(phi) * math.cos(dec))
    if cos_w < -1:
        return "up", "up"
    if cos_w > 1:
        return "down", "down"
    w = math.degrees(math.acos(cos_w)) / 360.0
    return _epoch(transit - w), _epoch(transit + w)


def status(lat, lon, now=None):
    """{'day': bool, 'next': ('sunset'|'sunrise', epoch) or None, 'left': seconds of
    daylight left (None at night)}."""
    now = time.time() if now is None else now
    rise, set_ = times(lat, lon, now)
    if rise in ("up", "down"):
        return {"day": rise == "up", "next": None, "left": None}
    if now < rise:
        return {"day": False, "next": ("sunrise", rise), "left": None}
    if now < set_:
        return {"day": True, "next": ("sunset", set_), "left": set_ - now}
    rise2, _ = times(lat, lon, now + 86400)
    nxt = ("sunrise", rise2) if rise2 not in ("up", "down") else None
    return {"day": False, "next": nxt, "left": None}


def moon(now=None):
    """(percent lit, name) for the moon tonight, e.g. (72, 'waxing gibbous')."""
    now = time.time() if now is None else now
    synodic = 29.530588853
    age = ((now - 947182440) / 86400.0) % synodic       # days since the new moon of 6 Jan 2000
    frac = age / synodic
    lit = int(round((1 - math.cos(2 * math.pi * frac)) / 2 * 100))
    names = ["new moon", "waxing crescent", "first quarter", "waxing gibbous", "full moon",
             "waning gibbous", "last quarter", "waning crescent"]
    return lit, names[int(frac * 8 + 0.5) % 8]
