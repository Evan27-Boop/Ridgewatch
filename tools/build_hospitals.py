#!/usr/bin/env python3
"""Build wxtui/hospitals.tsv.gz (for "nearest ER") from Medicare's hospital list.

    python tools/build_hospitals.py Hospital_General_Information.csv

Get the CSV from https://data.cms.gov/provider-data/dataset/xubh-q36u
("Download full dataset"). It's public domain, updated a few times a year.

Which hospitals count as having an emergency room:
  E  the list says "Emergency Services: Yes", or it's a Critical Access or Rural
     Emergency Hospital (both must run a 24-hour emergency department)
  L  a general acute care hospital the list marks "No" (the flag is wrong for some
     big hospitals, so it's kept, but the app says "ER not listed")
Left out: psychiatric, long-term care, children's, VA and military hospitals,
and specialty hospitals (surgical, heart, orthopedic, eye, rehab, cancer...)
whose ER isn't listed.

The list has street addresses but no map positions, so each hospital is placed
at the middle of its town (from the app's own town/ZIP list), or of its ZIP code
when the town isn't listed or is far from the ZIP. Hospitals are usually in
town, so that's usually within a mile or two; rural ZIP codes are much bigger.
"""

import csv
import gzip
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLACES = os.path.join(ROOT, "wxtui", "places.tsv.gz")
OUT = os.path.join(ROOT, "wxtui", "hospitals.tsv.gz")

SPECIALTY = re.compile(r"\b(SURG\w*|ORTHO\w*|SPINE|HEART|CARDIAC|EYE|EAR|REHAB\w*|SPECIALTY|"
                       r"CANCER|ONCOLOG\w*|BEHAVIORAL|PSYCH\w*|KIDNEY|TRANSPLANT|LTAC|"
                       r"LONG[- ]TERM|PHYSICIANS|WOMEN'?S|BARIATRIC)\b")
SMALL = {"Of", "And", "The", "For", "At", "In", "On"}


def nice_name(name):
    """'PRISMA HEALTH OCONEE MEMORIAL HOSPITAL' -> 'Prisma Health Oconee Memorial Hospital'."""
    words = " ".join(name.split()).title().split(" ")
    out = []
    for i, w in enumerate(words):
        if i and w in SMALL:
            w = w.lower()
        w = re.sub(r"'S\b", "'s", w)
        w = re.sub(r"^(Llc|Inc|Ii|Iii)$", lambda m: m.group(1).upper(), w)
        out.append(w)
    return " ".join(out)


def kind_of(row):
    t, er, name = row["Hospital Type"], row["Emergency Services"], row["Facility Name"].upper()
    if t in ("Critical Access Hospitals", "Rural Emergency Hospital"):
        return "E"
    if t == "Acute Care Hospitals":
        if er == "Yes":
            return "E"
        return None if SPECIALTY.search(name) else "L"
    return None


def main(csv_path):
    zips, towns = {}, {}
    with gzip.open(PLACES, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 7:
                continue
            lat, lon = float(p[3]), float(p[4])
            if p[0] == "Z":
                zips[p[1]] = (lat, lon)
            else:
                towns.setdefault((p[1].upper(), p[2]), (lat, lon))
    rows, placed, by_town, lost = [], 0, 0, []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            kind = kind_of(r)
            if not kind:
                continue
            z = zips.get(r["ZIP Code"].strip()[:5])
            t = towns.get((r["City/Town"].strip().upper(), r["State"].strip()))
            if t and (not z or abs(t[0] - z[0]) + abs(t[1] - z[1]) < 0.25):
                where, by_town = t, by_town + 1        # the town, if it agrees with the ZIP
            elif z:
                where, placed = z, placed + 1
            else:
                lost.append("%s, %s" % (r["Facility Name"], r["State"]))
                continue
            rows.append((kind, nice_name(r["Facility Name"]), r["City/Town"].strip().title(),
                         r["State"].strip(), "%.4f" % where[0], "%.4f" % where[1]))
    rows.sort(key=lambda x: (x[3], x[2], x[1]))
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write("#weather-tui hospitals v1\tMedicare Hospital General Information (CMS, public "
                "domain), placed by town or ZIP code; built from %s\n" % os.path.basename(csv_path))
        for row in rows:
            f.write("\t".join(row) + "\n")
    print("wrote %d hospitals (%d placed by town, %d by ZIP); %d couldn't be placed"
          % (len(rows), by_town, placed, len(lost)))
    for name in lost[:20]:
        print("  not placed:", name)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
