"""Nearest emergency room, offline, from Medicare's hospital list
(hospitals.tsv.gz, built by tools/build_hospitals.py).

Positions are the middle of each hospital's town or ZIP code, so distances are
approximate: usually within a mile or two. Works with no signal.
"""

import gzip
import math
import os
import threading

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hospitals.tsv.gz")


class Hospital(object):
    __slots__ = ("listed", "name", "city", "state", "lat", "lon")

    def __init__(self, kind, name, city, state, lat, lon):
        self.listed = kind == "E"            # False: general hospital, ER not listed
        self.name, self.city, self.state = name, city, state
        self.lat, self.lon = lat, lon


class HospitalIndex(object):
    def __init__(self, path=DATA_FILE):
        self.path = path
        self.items = []
        self.loaded = False
        self.error = None
        self._grid = {}
        self._lock = threading.Lock()

    def load_in_background(self):
        threading.Thread(target=self.load, name="hospitals", daemon=True).start()

    def load(self):
        with self._lock:
            if self.loaded:
                return
            items = []
            try:
                with gzip.open(self.path, "rt", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        p = line.rstrip("\n").split("\t")
                        if len(p) < 6:
                            continue
                        try:
                            items.append(Hospital(p[0], p[1], p[2], p[3], float(p[4]), float(p[5])))
                        except ValueError:
                            continue
            except (OSError, EOFError, UnicodeDecodeError) as e:
                self.error = "hospital list unavailable (%s)" % e.__class__.__name__
            grid = {}
            for h in items:
                grid.setdefault((int(h.lat // 1), int(h.lon // 1)), []).append(h)
            self.items, self._grid = items, grid
            self.loaded = True

    def ready(self):
        return self.loaded

    def nearest(self, lat, lon, max_km=200.0):
        """(Hospital, km) for the closest ER (one listed as having an ER wins over
        an unlisted one unless that's clearly closer), or None."""
        if not self.loaded:
            return None
        cos = math.cos(math.radians(lat))
        cy, cx = int(lat // 1), int(lon // 1)
        best = {True: None, False: None}
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-3, -2, -1, 0, 1, 2, 3):
                for h in self._grid.get((cy + dy, cx + dx), ()):
                    km = 111.2 * math.hypot(h.lat - lat, (h.lon - lon) * cos)
                    b = best[h.listed]
                    if km <= max_km and (b is None or km < b[1]):
                        best[h.listed] = (h, km)
        listed, other = best[True], best[False]
        if listed and other and other[1] < listed[1] - 8:
            return other                     # an unlisted one much closer is still worth naming
        return listed or other


def short_name(h, width=40):
    """'Prisma Health Oconee Memorial Hospital' -> 'Oconee Memorial Hospital' when tight."""
    name = h.name
    for prefix in ("Prisma Health ", "Atrium Health ", "Novant Health ", "Ascension ",
                   "Advocate ", "AdventHealth ", "HCA Florida ", "Baptist Health ",
                   "Mercy Health ", "Sutter ", "Kaiser Foundation Hospital - "):
        if len(name) > width and name.startswith(prefix):
            name = name[len(prefix):]
    return name
