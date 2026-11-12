#!/usr/bin/env python3
"""Rebuild ridgewatch's offline town/ZIP list from official Census files.

You only need this if you want to refresh the list. The app already ships
with one (ridgewatch/places.tsv.gz).

1. Download two "Gazetteer" files from the Census Bureau (any recent year):
     https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
     - Places                      (e.g. 2024_Gaz_place_national.zip)
     - ZIP Code Tabulation Areas   (e.g. 2024_Gaz_zcta_national.zip)
   Unzip them.

2. Run:
     python tools/build_places.py --places 2024_Gaz_place_national.txt \
                                  --zcta   2024_Gaz_zcta_national.txt

It writes ridgewatch/places.tsv.gz (a backup of the old one is kept as .bak).
Standard library only.
"""

import argparse
import gzip
import math
import os
import sys

# Words the Census puts after place names ("Clemson city", "Six Mile town").
LSAD_SUFFIXES = [
    "consolidated government (balance)", "unified government (balance)",
    "metropolitan government (balance)", "metro government (balance)",
    "city and borough", "urban county", "municipality", "comunidad",
    "zona urbana", "borough", "village", "city", "town", "CDP",
]

HEADER = "#ridgewatch places v1"


def clean_place_name(raw):
    """'Clemson city' -> ('Clemson', 'city')."""
    name = raw.strip()
    low = name.lower()
    for suf in LSAD_SUFFIXES:
        s = suf.lower()
        if low.endswith(" " + s):
            return name[: -len(s) - 1].strip(), suf
    if low.endswith(" (balance)"):
        return name[: -len(" (balance)")].strip(), ""
    return name, ""


def _rows(path):
    """Read a Census Gazetteer text file (tab separated, header row)."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        header = [h.strip().upper() for h in f.readline().rstrip("\n").split("\t")]
        for line in f:
            parts = [p.strip() for p in line.rstrip("\n").split("\t")]
            if len(parts) < len(header):
                continue
            yield dict(zip(header, parts))


def read_census_places(path):
    out = []
    for r in _rows(path):
        try:
            lat, lon = float(r["INTPTLAT"]), float(r["INTPTLONG"])
        except (KeyError, ValueError):
            continue
        name, lsad = clean_place_name(r.get("NAME", ""))
        if name:
            out.append({"name": name, "state": r.get("USPS", ""), "lat": lat,
                        "lon": lon, "county": "", "type": lsad})
    return out


def read_census_zcta(path):
    out = []
    for r in _rows(path):
        try:
            out.append({"zip": r["GEOID"][-5:], "lat": float(r["INTPTLAT"]),
                        "lon": float(r["INTPTLONG"])})
        except (KeyError, ValueError):
            continue
    return out


def km_between(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180
    x = (b_lon - a_lon) * p * math.cos((a_lat + b_lat) * p / 2)
    y = (b_lat - a_lat) * p
    return 6371 * math.hypot(x, y)


def dedupe_places(places, km=15):
    """Same name + state within `km` counts as one place. Census-typed
    entries (city, town, CDP...) win over untyped ones."""
    groups = {}
    for p in places:
        groups.setdefault((p["name"].lower(), p["state"]), []).append(p)
    out = []
    for items in groups.values():
        items.sort(key=lambda p: (p["type"] == "", p["name"]))
        kept = []
        for p in items:
            if all(km_between(p["lat"], p["lon"], k["lat"], k["lon"]) > km for k in kept):
                kept.append(p)
        out.extend(kept)
    return out


class _Grid:
    """Tiny spatial index so 'nearest town to each ZIP' is fast."""

    def __init__(self, places, cell=0.5):
        self.cell = cell
        self.cells = {}
        for p in places:
            self.cells.setdefault(self._key(p["lat"], p["lon"]), []).append(p)

    def _key(self, lat, lon):
        return (int(math.floor(lat / self.cell)), int(math.floor(lon / self.cell)))

    def nearest(self, lat, lon, max_rings=6):
        cy, cx = self._key(lat, lon)
        best, best_d = None, None
        for ring in range(max_rings + 1):
            for dy in range(-ring, ring + 1):
                for dx in range(-ring, ring + 1):
                    if max(abs(dy), abs(dx)) != ring:
                        continue
                    for p in self.cells.get((cy + dy, cx + dx), ()):
                        d = km_between(lat, lon, p["lat"], p["lon"])
                        if best_d is None or d < best_d:
                            best, best_d = p, d
            if best is not None and ring >= 1:
                break
        return best


def write_places(places, zips, out_path, source_note):
    places = dedupe_places([p for p in places if p["name"] and p["state"]])
    grid = _Grid([p for p in places if p["type"]] or places)
    lines = [HEADER + "\t" + source_note.replace("\t", " ")]
    for p in sorted(places, key=lambda p: (p["state"], p["name"].lower())):
        lines.append("\t".join(["P", p["name"], p["state"], "%.4f" % p["lat"],
                                "%.4f" % p["lon"], p.get("county", ""), p["type"]]))
    for z in sorted(zips, key=lambda z: z["zip"]):
        near = grid.nearest(z["lat"], z["lon"])
        lines.append("\t".join(["Z", z["zip"], near["state"] if near else "",
                                "%.4f" % z["lat"], "%.4f" % z["lon"], "",
                                near["name"] if near else ""]))
    if os.path.exists(out_path):
        os.replace(out_path, out_path + ".bak")
    tmp = out_path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, out_path)
    return len(places), len(zips)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "ridgewatch", "places.tsv.gz")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--places", required=True, help="Census *_Gaz_place_national.txt")
    ap.add_argument("--zcta", required=True, help="Census *_Gaz_zcta_national.txt")
    ap.add_argument("--out", default=default_out)
    a = ap.parse_args(argv)
    places = read_census_places(a.places)
    zips = read_census_zcta(a.zcta)
    if len(places) < 1000 or len(zips) < 1000:
        sys.exit("Those files look wrong (too few rows). Nothing was changed.")
    note = "US Census Bureau Gazetteer: %s, %s" % (os.path.basename(a.places),
                                                   os.path.basename(a.zcta))
    n_p, n_z = write_places(places, zips, os.path.abspath(a.out), note)
    print("Wrote %d places and %d ZIP codes to %s" % (n_p, n_z, os.path.abspath(a.out)))


if __name__ == "__main__":
    main()
