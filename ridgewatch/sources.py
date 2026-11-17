"""The strict source list: every place Ridgewatch is allowed to ask for data.

Each entry is one official, public, keyless (or your-own-key) source. Nothing outside
this list is ever requested; nws.fetch_url refuses any other host. To add a source,
add it to SPEC.md first, then here.
"""

import os
import urllib.parse

SOURCES = [
    # (id, who runs it, host, scope, what it adds)
    ("nws", "National Weather Service", "api.weather.gov", "local",
     "forecasts, conditions, alerts, Local Storm Reports"),
    ("ipaws", "FEMA IPAWS public feed", "apps.fema.gov", "local",
     "evacuations, shelter orders, 911 outages and other public-safety alerts"),
    ("duke", "Duke Energy public outage layer", "services3.arcgis.com", "local",
     "power outages near you"),
    ("nifc", "National Interagency Fire Center", "services3.arcgis.com", "local",
     "wildfires near you"),
    ("usgs", "U.S. Geological Survey", "earthquake.usgs.gov", "local and national",
     "earthquakes"),
    ("511ga", "Georgia 511 (your key)", "511ga.org", "local", "crashes and closures"),
    ("drivenc", "North Carolina DriveNC (your key)", "www.drivenc.gov", "local",
     "crashes and closures"),
    ("faa", "FAA National Airspace System status", "nasstatus.faa.gov", "national",
     "ground stops, delay programs, airport closures"),
]

HOSTS = {s[2] for s in SOURCES}


def allowed(url):
    """True if the URL's host is on the list (or is the local test server)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in HOSTS:
        return True
    for var in ("RIDGEWATCH_API", "RIDGEWATCH_FEEDS"):     # the offline test stand-ins
        base = os.environ.get(var, "")
        if base and host == (urllib.parse.urlsplit(base).hostname or "").lower():
            return True
    return False
