"""Offline location lookup: towns, ZIP codes and typed coordinates.

Nothing in here touches the network. The town/ZIP list ships with the app
as places.tsv.gz (see tools/build_places.py).
"""

import gzip
import math
import os
import re
import threading
import unicodedata

from . import geo

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "places.tsv.gz")

STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
    "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina", "ND": "north dakota",
    "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming", "PR": "puerto rico",
    "VI": "virgin islands", "GU": "guam", "AS": "american samoa",
    "MP": "northern mariana islands", "MH": "marshall islands",
}
_STATE_BY_NAME = {v: k for k, v in STATES.items()}
_TYPE_RANK = {"city": 0, "town": 1, "borough": 2, "village": 2, "municipality": 2,
              "township": 3, "CDP": 3, "": 4}
_WORD_SWAPS = {"st": "saint", "ste": "sainte", "mt": "mount", "ft": "fort", "pt": "point"}


def normalize(text):
    """Lowercase, drop accents and punctuation, expand St./Mt./Ft."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(_WORD_SWAPS.get(w, w) for w in t.split())


class Place(object):
    __slots__ = ("kind", "name", "state", "lat", "lon", "county", "extra", "norm")

    def __init__(self, kind, name, state, lat, lon, county="", extra=""):
        self.kind, self.name, self.state = kind, name, state
        self.lat, self.lon = lat, lon
        self.county, self.extra = county, extra
        self.norm = normalize(name) if kind == "P" else name

    @property
    def title(self):
        if self.kind == "Z":
            return "ZIP " + self.name
        return "%s, %s" % (self.name, self.state)

    @property
    def detail(self):
        if self.kind == "Z":
            return ("near %s, %s" % (self.extra, self.state)) if self.extra else ""
        c = self.county
        if not c:
            return ""
        if re.search(r"(\)|Area|Borough|Parish|Municipio|Municipality|District|city)$", c):
            return c
        return c + (" Parish" if self.state == "LA" else " Co")

    @property
    def label(self):
        """What the town is called once picked (used under the location name)."""
        if self.kind == "Z":
            return ("%s, %s" % (self.extra, self.state)) if self.extra else ""
        return "%s, %s" % (self.name, self.state)


class PlaceIndex(object):
    """Loads the list once (in the background if asked) and searches it."""

    def __init__(self, path=DATA_FILE):
        self.path = path
        self.places = []
        self.zips = []
        self.error = None
        self.loaded = False
        self._lock = threading.Lock()
        self._thread = None

    def load_in_background(self):
        if self._thread is None and not self.loaded:
            self._thread = threading.Thread(target=self.load, name="places", daemon=True)
            self._thread.start()

    def load(self):
        with self._lock:
            if self.loaded:
                return
            places, zips = [], []
            try:
                with gzip.open(self.path, "rt", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        p = line.rstrip("\n").split("\t")
                        if len(p) < 7:
                            continue
                        try:
                            item = Place(p[0], p[1], p[2], float(p[3]), float(p[4]), p[5], p[6])
                        except ValueError:
                            continue
                        (zips if item.kind == "Z" else places).append(item)
            except (OSError, EOFError, UnicodeDecodeError) as e:
                self.error = "town list unavailable (%s)" % e.__class__.__name__
            self.places, self.zips = places, zips
            self.loaded = True

    def ready(self):
        return self.loaded

    def nearest(self, lat, lon, want=None):
        """(Place, km) for the closest town (that want(place) accepts), or None.
        Fast enough to call every second."""
        if not self.loaded:
            return None
        grid = getattr(self, "_grid", None)
        if grid is None:
            grid = {}
            for p in self.places:
                grid.setdefault((int(p.lat // 0.25), int(p.lon // 0.25)), []).append(p)
            self._grid = grid
        cy, cx = int(lat // 0.25), int(lon // 0.25)
        best, best_km = None, None
        cos = math.cos(math.radians(lat))
        for ring in range(0, 9):
            for dy in range(-ring, ring + 1):
                for dx in range(-ring, ring + 1):
                    if max(abs(dy), abs(dx)) != ring:
                        continue
                    for p in grid.get((cy + dy, cx + dx), ()):
                        if want is not None and not want(p):
                            continue
                        km = 111.2 * math.hypot(p.lat - lat, (p.lon - lon) * cos)
                        if best_km is None or km < best_km:
                            best, best_km = p, km
            if best is not None and best_km < ring * 0.25 * 111.2 * cos:
                break
        return (best, best_km) if best else None

    def search(self, query, limit=40):
        """Return Places matching a town name (optionally with state) or ZIP."""
        if not self.loaded:
            self.load()
        q = query.strip()
        if not q:
            return []
        digits = q.replace(" ", "")
        if digits.isdigit():
            if len(digits) > 5:
                return []
            hits = [z for z in self.zips if z.name.startswith(digits)]
            hits.sort(key=lambda z: (z.name != digits, z.name))
            return hits[:limit]
        words = normalize(q).split()
        state = None
        if len(words) >= 2:
            for n in (3, 2, 1):  # "new york", "north carolina", "sc"
                if len(words) > n:
                    tail = " ".join(words[-n:])
                    if n == 1 and len(tail) == 2 and tail.upper() in STATES:
                        state = tail.upper()
                    elif tail in _STATE_BY_NAME:
                        state = _STATE_BY_NAME[tail]
                    if state:
                        words = words[:-n]
                        break
        nq = " ".join(words)
        if not nq:
            return []
        scored = []
        for p in self.places:
            if state and p.state != state:
                continue
            n = p.norm
            if n == nq:
                rank = 0
            elif n.startswith(nq):
                rank = 1
            elif (" " + nq) in n:
                rank = 2
            elif len(nq) >= 3 and nq in n:
                rank = 3
            else:
                continue
            scored.append((rank, _TYPE_RANK.get(p.extra, 4), len(n), p.state, p.name, p))
        scored.sort(key=lambda t: t[:5])
        out = []
        for t in scored:
            p = t[-1]
            # "St. Louis" vs "Saint Louis", or two dots for one city: keep one
            if any(k.norm == p.norm and k.state == p.state and
                   abs(k.lat - p.lat) < 0.2 and abs(k.lon - p.lon) < 0.25 for k in out):
                continue
            out.append(p)
            if len(out) >= limit:
                break
        return out


_COORD_RE = re.compile(
    r"^\s*([+-]?\d{1,3}(?:\.\d+)?)\s*°?\s*([NnSs])?\s*[,;\s]\s*"
    r"([+-]?\d{1,3}(?:\.\d+)?)\s*°?\s*([EeWw])?\s*$")


def parse_coords(text):
    """'34.6834, -82.8374' or '34.6834N 82.8374W' -> (34.6834, -82.8374)."""
    m = _COORD_RE.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(3))
    if m.group(2) and m.group(2) in "Ss":
        lat = -abs(lat)
    if m.group(4) and m.group(4) in "Ww":
        lon = -abs(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 4), round(lon, 4)


# Rough boxes around everywhere NWS forecasts. The NWS itself has the final
# word; this just catches typos (like a missing minus sign) before asking.
_NWS_BOXES = [
    (24.0, 50.0, -125.5, -66.0),     # lower 48
    (51.0, 72.0, -180.0, -129.0),    # Alaska
    (51.0, 54.0, 172.0, 180.0),      # far Aleutians
    (18.5, 22.5, -161.0, -154.5),    # Hawaii
    (17.5, 18.8, -68.0, -64.5),      # Puerto Rico + Virgin Islands
    (13.0, 21.0, 144.0, 146.5),      # Guam + Northern Marianas
    (-14.8, -10.8, -171.5, -168.0),  # American Samoa
]


def ahead(index, lat, lon, heading, max_km=40.0, spread=25.0, limit=2):
    """Towns (incorporated places) ahead on your heading: [(Place, km)], nearest first.
    'Ahead' = within `spread` degrees either side of the heading, 1 to max_km away."""
    if not index.ready() or heading is None:
        return []
    index.nearest(lat, lon)                       # builds the grid if needed
    grid = getattr(index, "_grid", {})
    cos = math.cos(math.radians(lat))
    cy, cx = int(lat // 0.25), int(lon // 0.25)
    reach = int(max_km / (0.25 * 111.2 * max(cos, 0.3))) + 1
    out = []
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            for p in grid.get((cy + dy, cx + dx), ()):
                if p.extra not in ("city", "town", "village", "borough"):
                    continue
                km = 111.2 * math.hypot(p.lat - lat, (p.lon - lon) * cos)
                if not 1.0 <= km <= max_km:
                    continue
                off = (geo.bearing(lat, lon, p.lat, p.lon) - heading + 540) % 360 - 180
                if abs(off) <= spread:
                    out.append((p, km))
    out.sort(key=lambda x: x[1])
    return out[:limit]


def in_nws_area(lat, lon):
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in _NWS_BOXES)


def describe_spot(index, lat, lon):
    """('2.3 mi NW of Six Mile, SC', 'Pickens Co') or ('in Clemson, SC', ...)."""
    plain = ("%.4f, %.4f" % (lat, lon), "")
    r = index.nearest(lat, lon) if index.ready() else None
    if not r:
        return plain
    p, km = r
    mi = km / 1.609344
    if mi < 0.75:
        return "in " + p.title, p.detail
    d = geo.compass(geo.bearing(p.lat, p.lon, lat, lon))
    dist = ("%.1f" % mi) if mi < 10 else "%d" % round(mi)
    return "%s mi %s of %s" % (dist, d, p.title), p.detail
