"""Distance, direction and compass points. Shared by everything that deals in places."""

import math
import re

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def km_between(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180
    h = math.sin((b_lat - a_lat) * p / 2) ** 2 + math.cos(a_lat * p) * math.cos(b_lat * p) * \
        math.sin((b_lon - a_lon) * p / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def bearing(a_lat, a_lon, b_lat, b_lon):
    """Degrees from point a toward point b (0 = north)."""
    p = math.pi / 180
    y = math.sin((b_lon - a_lon) * p) * math.cos(b_lat * p)
    x = math.cos(a_lat * p) * math.sin(b_lat * p) - \
        math.sin(a_lat * p) * math.cos(b_lat * p) * math.cos((b_lon - a_lon) * p)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(deg):
    if deg is None:
        return ""
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def destination(lat, lon, deg, km):
    """The point `km` away from (lat, lon) heading `deg` (flat-earth; fine under ~50 km)."""
    d = km / 111.2
    return (lat + d * math.cos(math.radians(deg)),
            lon + d * math.sin(math.radians(deg)) / max(0.01, math.cos(math.radians(lat))))


def inside(lat, lon, ring):
    """Is the point inside a polygon ring of [lon, lat] pairs?"""
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            hit = not hit
        j = i
    return hit


def nearest_on_ring(lat, lon, ring):
    """(km, lat, lon) of the closest point on a polygon's edge."""
    cos = math.cos(math.radians(lat))
    best = None
    for i in range(len(ring)):
        ax, ay = (ring[i - 1][0] - lon) * cos, ring[i - 1][1] - lat
        bx, by = (ring[i][0] - lon) * cos, ring[i][1] - lat
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
        px, py = ax + t * dx, ay + t * dy
        d = 111.2 * math.hypot(px, py)
        if best is None or d < best[0]:
            best = (d, lat + py, lon + px / cos)
    return best


def mgrs(lat, lon, digits=4):
    """Military grid / US National Grid reference, e.g. '17S LE 1234 5678' (10 m with
    4 digits). What search and rescue and topo maps use. None outside 80S-84N."""
    if not -80 <= lat < 84:
        return None
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    zone = int((lon + 180) // 6) + 1
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * (lam - lon0)
    e4, e6 = e2 * e2, e2 ** 3
    m = a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
             - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
             + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
             - (35 * e6 / 3072) * math.sin(6 * phi))
    east = k0 * n * (A + (1 - t + c) * A ** 3 / 6
                     + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * A ** 5 / 120) + 500000
    north = k0 * (m + n * math.tan(phi) * (A * A / 2 + (5 - t + 9 * c + 4 * c * c) * A ** 4 / 24
                                           + (61 - 58 * t + t * t + 600 * c - 330 * ep2)
                                           * A ** 6 / 720))
    if lat < 0:
        north += 10000000
    band = "CDEFGHJKLMNPQRSTUVWXX"[int((lat + 80) // 8)]
    col = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")[(zone - 1) % 3][int(east // 100000) - 1]
    rows = "ABCDEFGHJKLMNPQRSTUV"
    row = rows[(int(north // 100000) + (5 if zone % 2 == 0 else 0)) % 20]
    scale = 10 ** (5 - digits)
    return "%d%s %s%s %0*d %0*d" % (zone, band, col, row, digits, int(east % 100000) // scale,
                                     digits, int(north % 100000) // scale)


STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "PR": "72", "VI": "78", "GU": "66", "AS": "60", "MP": "69",
}


def same_code(county_zone):
    """NWS county zone 'SCC077' -> '045077', the code a weather radio is set to so it
    only sounds for your county. None if it isn't a county zone."""
    m = re.match(r"^([A-Z]{2})C(\d{3})$", county_zone or "")
    if not m or m.group(1) not in STATE_FIPS:
        return None
    return "0" + STATE_FIPS[m.group(1)] + m.group(2)

