"""Nearby headlines: recent local news about public safety, from Google News.

This is the quiet second tier under the official NWS alerts. It never flashes
or beeps: news reports are unofficial, often late, and sometimes wrong, so
they're shown as "worth knowing", not "act now".

How it works: search Google News for the county (and nearest city) you're in,
limited to the last day; keep only headlines that read like public-safety
news (crashes, closures, fires, police activity, outages...); show the
newest first with their age; forget anything over 24 hours old.

What leaves your laptop: the county, city and state names in the search.
No coordinates. Turn it off with "headlines": false in config.json.
"""

import email.utils
import html
import math
import os
import queue
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

from . import __version__, nws
from .places import STATES

FEED = os.environ.get("WEATHER_TUI_NEWS", "https://news.google.com/rss/search")
EVERY_SEC = 20 * 60         # re-check an area this often (Google asks for gentle polling)
GAP_SEC = 60                # never two searches closer together than this
KEEP_SEC = 24 * 3600        # headlines older than a day are dropped
MAX_ITEMS = 10
CITY_KM = 20.0              # name the nearest city in the search if it's this close

# A headline must mention one of these...
SAFETY = re.compile(r"\b(" + "|".join([
    # roads and travel
    r"crash(es|ed)?", r"wrecks?", r"collisions?", r"rollover", r"overturn\w*", r"jackknif\w*",
    r"pile-?up", r"hit-and-run", r"pedestrians?", r"clos(ed|es|ure|ures|ing)", r"shut ?down",
    r"reopen\w*", r"detours?", r"lanes?", r"traffic", r"delays?", r"interstate", r"i-\d+",
    r"highways?", r"bridges?", r"sinkholes?", r"derail\w*", r"train",
    # fire and hazards
    r"fires?", r"blaze", r"burn ban", r"smoke", r"wildfires?", r"explosions?", r"explode\w*",
    r"hazmat", r"hazardous", r"spill(s|ed)?", r"leaks?", r"chemicals?", r"fumes",
    # crime and police activity
    r"evacuat\w*", r"shelter[- ]in[- ]place", r"lockdown", r"locked down", r"bomb", r"threats?",
    r"suspicious", r"shoot\w*", r"shots", r"gunfire", r"gunman", r"stabb\w*", r"homicide",
    r"murder\w*", r"killed", r"fatal\w*", r"dead", r"deadly", r"died", r"dies", r"death",
    r"injur\w*", r"assault\w*", r"robber\w*", r"armed", r"carjack\w*", r"kidnap\w*",
    r"abduct\w*", r"missing", r"amber alert", r"silver alert", r"manhunt", r"wanted",
    r"suspects?", r"escape\w*", r"inmates?", r"chase", r"pursuit", r"standoff", r"barricade\w*",
    r"hostage", r"police", r"deputies", r"deputy", r"sheriff\w*", r"troopers?",
    r"highway patrol", r"swat", r"arrest\w*",
    # utilities and health
    r"outages?", r"without power", r"power out", r"boil[- ]water", r"water main",
    r"contaminat\w*", r"air quality", r"advisory",
    # weather damage and emergencies
    r"flood\w*", r"storm damage", r"tornado\w*", r"trees? down", r"downed", r"power lines?",
    r"landslide", r"rockslide", r"mudslide", r"protests?", r"riots?", r"unrest", r"brawl",
    r"disturbance", r"rescue\w*", r"drown\w*", r"emergenc(y|ies)", r"plane",
]) + r")\b", re.I)

# ...and none of these: court follow-ups, looking back...
SKIP = re.compile(r"\b(" + "|".join([
    r"sentenc\w*", r"plead\w*", r"plea", r"trial", r"convict\w*", r"indict\w*", r"lawsuit",
    r"sues", r"sued", r"verdict", r"obituar\w*", r"anniversary", r"years ago", r"remember\w*",
]) + r")\b", re.I)

# ...or sports. This app is for safety and local awareness, and sports stories often
# borrow safety words ("QB injured", "defense under fire", "closes out the season").
# Kept specific so real news survives: "game wardens", "hunting season", "power loss",
# "self-defense", "9-1-1 outage" and "closed 7-9 p.m." all still count.
SPORTS = re.compile(r"\b(" + "|".join([
    r"football", r"basketball", r"baseball", r"softball", r"volleyball", r"soccer",
    r"golf", r"tennis", r"wrestling", r"hockey", r"lacrosse", r"nascar", r"athlet\w*",
    r"ncaa", r"acc", r"sec", r"nfl", r"nba", r"wnba", r"mlb", r"nhl", r"mls",
    r"games?(?!\s+wardens?)", r"regular season", r"preseason", r"postseason",
    r"season (opener|finale)", r"playoffs?", r"tournament", r"championships?",
    r"champions?", r"title", r"bowl", r"heisman", r"transfer portal", r"roster",
    r"quarterbacks?", r"qbs?", r"touchdowns?", r"kickoff", r"halftime", r"overtime",
    r"scrimmage", r"varsity", r"injury report", r"coach\w*", r"recruit\w*",
    r"wins?", r"won", r"victory", r"defeat\w*", r"beats", r"rout(s|ed)?",
    r"loss(es)? (to|at|against)", r"rall(y|ies) past", r"undefeated",
    r"tigers", r"gamecocks", r"paladins", r"chanticleers", r"bulldogs", r"panthers",
    r"hornets", r"braves", r"falcons", r"seminoles", r"wolfpack", r"tar heels",
    r"blue devils", r"demon deacons", r"hokies", r"yellow jackets",
]) + r")\b|(?<![-\d])\b\d{1,3}-\d{1,3}\b(?!-|\s*[ap]\.?m\b)", re.I)       # scores like 31-14


def county_name(county, state):
    """'Pickens' + 'SC' -> 'Pickens County'; handles parishes, boroughs, VA cities."""
    c = (county or "").strip()
    if not c:
        return ""
    if c.endswith(" (CA)"):
        return c[:-5] + " Census Area"
    if c.endswith(" city"):
        return c[:-5]                       # Virginia's independent cities
    if re.search(r"(Area|Borough|Parish|Municipio|Municipality|District)$", c):
        return c
    return c + (" Parish" if state == "LA" else " County")


def area_for(index, lat, lon):
    """The news area for a spot: county, nearest city, state. None if unknown."""
    hit = index.nearest(lat, lon, want=lambda p: bool(p.county))
    if not hit:
        return None
    p = hit[0]
    county = county_name(p.county, p.state)
    if not county:
        return None
    city = index.nearest(lat, lon, want=lambda p: p.extra == "city")
    town = city[0].name if city and city[1] <= CITY_KM and city[0].state == p.state else ""
    state = STATES.get(p.state, p.state).title()
    return {"key": "%s|%s" % (p.state, p.county), "county": county, "town": town,
            "state": state, "abbr": p.state, "label": p.detail + ", " + p.state}


def search_url(area):
    names = ['"%s"' % area["county"]]
    if area.get("town") and area["town"].lower() != area["county"].lower():
        names.append('"%s"' % area["town"])
    q = "(%s) %s when:1d" % (" OR ".join(names), area["state"])
    return FEED + "?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def parse_feed(body):
    """RSS bytes -> [{'title', 'source', 'time'}]."""
    root = ET.fromstring(body)
    out = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        source = html.unescape((item.findtext("source") or "").strip())
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].rstrip()       # "Headline - WYFF" -> "Headline"
        try:
            t = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError, IndexError):
            continue
        if title:
            out.append({"title": " ".join(title.split()), "source": source, "time": t})
    return out


def relevant(title):
    return bool(SAFETY.search(title)) and not SKIP.search(title) and not SPORTS.search(title)


# What kind of story it is, checked in this order (a crash with a death is ROAD,
# a fire with an arrest is FIRE). Anything else safety-related is NEWS.
KINDS = [
    ("MISSING", r"missing|amber alert|silver alert|abduct\w*|kidnap\w*|manhunt|escape\w*"),
    ("FIRE", r"fires?|blaze|wildfires?|smoke|burn ban|explosions?|explode\w*|hazmat|hazardous|"
             r"spill(s|ed)?|chemicals?|fumes|gas leak|evacuat\w*"),
    ("ROAD", r"crash(es|ed)?|wrecks?|collisions?|rollover|overturn\w*|jackknif\w*|pile-?up|"
             r"hit-and-run|pedestrians?|derail\w*|train"),
    ("POLICE", r"shoot\w*|shots|gunfire|gunman|stabb\w*|homicide|murder\w*|assault\w*|"
               r"robber\w*|armed|carjack\w*|standoff|barricade\w*|hostage|lockdown|"
               r"locked down|bomb|threats?|suspicious|police|deputies|deputy|sheriff\w*|"
               r"troopers?|swat|arrest\w*|suspects?|wanted|chase|pursuit|brawl|riots?|unrest"),
    ("UTILITY", r"outages?|without power|power out|boil[- ]water|water main|contaminat\w*"),
    ("STORM", r"flood\w*|storm damage|tornado\w*|trees? down|downed|power lines?|landslide|"
              r"rockslide|mudslide"),
    ("ROAD", r"clos(ed|es|ure|ures|ing)|shut ?down|reopen\w*|detours?|lanes?|traffic|delays?|"
             r"interstate|i-\d+|highways?|bridges?|sinkholes?"),
]
_KINDS = [(k, re.compile(r"\b(%s)\b" % rx, re.I)) for k, rx in KINDS]


def kind_of(title):
    for kind, rx in _KINDS:
        if rx.search(title):
            return kind
    return "NEWS"


def pick(items, now=None):
    """Recent public-safety headlines, newest first. The same story from several
    outlets is kept once, with the count ("also": 2 = two more outlets)."""
    now = time.time() if now is None else now
    seen, out = {}, []
    for it in sorted(items, key=lambda i: -i["time"]):
        if now - it["time"] > KEEP_SEC or not relevant(it["title"]):
            continue
        sig = " ".join(re.sub(r"[^a-z0-9 ]", "", it["title"].lower()).split()[:7])
        if sig in seen:
            first = seen[sig]
            if it["source"] != first["source"]:
                first["also"] = first.get("also", 0) + 1
            continue
        it = dict(it, kind=kind_of(it["title"]), also=0)
        seen[sig] = it
        out.append(it)
    return out[:MAX_ITEMS]


# A town named in a headline, so it can be put on the map: "... near Clemson",
# "Easley man charged ...". Only after words like these, so town names that are
# also ordinary words (Central, Liberty, Six Mile) don't match by accident.
_NAME = r"([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2})"
_PLACE_RX = [re.compile(r"\b(?:in|near|at|outside|of|on)\s+" + _NAME),
             re.compile(r"^" + _NAME + r"\s+(?:man|woman|teen|boy|girl|police|officers?|"
                        r"firefighters|deputies|officials|residents?|family|home|house|"
                        r"school|crash|fire|shooting)\b")]


def place_in(title, index, state, lat, lon, max_km=100.0):
    """(town name, town lat, town lon) for a town in this state named in the
    headline and within max_km of (lat, lon), or None."""
    from .places import normalize                # (places imports nothing from here)
    names = getattr(index, "_by_state", None)
    if names is None:
        names = {}
        for p in index.places:
            names.setdefault((p.state, p.norm), p)
        index._by_state = names
    for rx in _PLACE_RX:
        for m in rx.finditer(title):
            words = m.group(1).split()
            for n in range(len(words), 0, -1):          # "Six Mile Fire" -> "Six Mile"
                if n < len(words) and words[n] in ("County", "Co", "Parish", "Borough"):
                    continue                            # "Pickens County" isn't the town
                p = names.get((state, normalize(" ".join(words[:n]))))
                if p is None:
                    continue
                d = 111.2 * math.hypot(p.lat - lat, (p.lon - lon) * math.cos(math.radians(lat)))
                if d <= max_km:
                    return p.name, p.lat, p.lon
    return None


def fetch_area(area, getter=None):
    getter = getter or (lambda url: nws.fetch_url(
        url, "Mozilla/5.0 (compatible; weather-tui/%s)" % __version__, parse_feed,
        accept="application/rss+xml, application/xml", who="Google News", tries=2))
    items = pick(getter(search_url(area)))
    return {"fetched": time.time(), "items": items}


def current(entry, now=None):
    """The headlines still worth showing from a cached area entry."""
    now = time.time() if now is None else now
    return [it for it in (entry or {}).get("items") or [] if now - it["time"] <= KEEP_SEC]


class NewsFetcher(object):
    """One search at a time on its own thread, so news never delays weather alerts."""

    def __init__(self, getter=None):
        self.getter = getter
        self.results = queue.Queue()
        self._jobs = queue.Queue()
        self.busy = False
        threading.Thread(target=self._run, name="news", daemon=True).start()

    def request(self, area):
        self.busy = True
        self._jobs.put(dict(area))

    def _run(self):
        while True:
            area = self._jobs.get()
            try:
                self.results.put((area["key"], fetch_area(area, self.getter), None))
            except nws.NotFound:
                self.results.put((area["key"], None, "news search not found"))
            except nws.FetchError as e:
                self.results.put((area["key"], None, str(e)))
            except Exception as e:      # the thread must survive anything
                self.results.put((area["key"], None, "unexpected reply (%s)" % e.__class__.__name__))
            self.busy = not self._jobs.empty()


# ------------------------------------------------- headlines -> reports --
# A headline is turned into a short, fixed-format report: what happened, the road,
# the impact and the place. Anything that doesn't say both what and where is dropped.

EVENTS = [
    (r"amber alert", "AMBER ALERT"), (r"silver alert", "SILVER ALERT"),
    (r"missing", "MISSING PERSON"), (r"abduct\w*|kidnap\w*", "ABDUCTION"),
    (r"manhunt|escape\w*", "MANHUNT"),
    (r"wildfires?|brush fires?|woods fires?|forest fires?", "BRUSH FIRE"),
    (r"(house|home|apartment|structure|mobile home|business|barn) fires?|fires? (at|destroys|damages)",
     "STRUCTURE FIRE"),
    (r"explosions?|explode\w*", "EXPLOSION"),
    (r"hazmat|hazardous|chemicals?|spill(s|ed)?|gas leak|fumes", "HAZMAT"),
    (r"fires?|blaze", "FIRE"), (r"evacuat\w*", "EVACUATION"),
    (r"pedestrians?", "PEDESTRIAN STRUCK"),
    (r"crash(es|ed)?|wrecks?|collisions?|rollover|overturn\w*|jackknif\w*|pile-?up|hit-and-run",
     "CRASH"),
    (r"derail\w*|train", "TRAIN INCIDENT"),
    (r"shoot\w*|shots|gunfire|gunman", "SHOOTING"), (r"stabb\w*", "STABBING"),
    (r"homicide|murder\w*", "HOMICIDE"), (r"carjack\w*", "CARJACKING"),
    (r"robber\w*", "ROBBERY"), (r"standoff|barricade\w*|hostage", "STANDOFF"),
    (r"lockdown|locked down", "LOCKDOWN"), (r"bomb|threats?", "THREAT"),
    (r"chase|pursuit", "POLICE CHASE"), (r"drown\w*", "DROWNING"), (r"rescue\w*", "RESCUE"),
    (r"outages?|without power|power out", "POWER OUTAGE"),
    (r"boil[- ]water", "BOIL WATER NOTICE"), (r"water main", "WATER MAIN BREAK"),
    (r"flood\w*", "FLOODING"),
    (r"tornado\w*|storm damage|trees? down|downed|power lines?", "STORM DAMAGE"),
    (r"landslide|rockslide|mudslide", "SLIDE"), (r"sinkholes?", "SINKHOLE"),
    (r"clos(ed|es|ure|ing)|shut ?down|detours?", "ROAD CLOSED"),
    (r"lanes?|traffic|delays?", "TRAFFIC"),
    (r"protests?|riots?|unrest|brawl|disturbance", "DISTURBANCE"),
    (r"police|deputies|deputy|sheriff\w*|troopers?|swat|suspects?|wanted|arrest\w*",
     "POLICE ACTIVITY"),
]
_EVENTS = [(re.compile(r"\b(%s)\b" % rx, re.I), label) for rx, label in EVENTS]

_ROAD = re.compile(
    r"\b(I-\s?\d+|Interstate\s+\d+|U\.?S\.?(?:\s+(?:Highway|Hwy\.?|Route))?[- ]\d+|"
    r"(?:SC|NC|GA|S\.C\.)(?:\s+(?:Highway|Hwy\.?))?[- ]\d+|(?:Highway|Hwy\.?|Route|Rt\.?)\s+\d+|"
    r"(?:[A-Z][a-z]+\s){1,2}(?:Road|Rd|Street|St|Boulevard|Blvd|Drive|Dr|Avenue|Ave|Parkway|Pkwy|"
    r"Highway|Hwy|Lane|Ln|Pike|Bypass))\b")
_DIRS = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB"}


def _road(title):
    m = _ROAD.search(title)
    if not m:
        return ""
    r = " ".join(m.group(1).split())
    r = re.sub(r"^Interstate\s+", "I-", r)
    r = re.sub(r"^I-\s", "I-", r)
    r = re.sub(r"^U\.?S\.?(?:\s+(?:Highway|Hwy\.?|Route))?[- ]", "US ", r)
    r = re.sub(r"^(SC|NC|GA|S\.C\.)(?:\s+(?:Highway|Hwy\.?))?[- ]",
               lambda m: ("SC" if m.group(1) == "S.C." else m.group(1)) + " ", r)
    r = re.sub(r"^(?:Highway|Hwy\.?|Route|Rt\.?)\s+", "Hwy ", r)
    d = re.search(r"\b(north|south|east|west)bound\b", title, re.I)
    return r + (" " + _DIRS[d.group(0).lower()] if d else "")


def report(title, county=""):
    """A headline as a report: {'kind', 'detail': [...], 'county'} or None when it
    doesn't say what happened and where (a road, or a county named in it)."""
    kind = next((label for rx, label in _EVENTS if rx.search(title)), None)
    if not kind:
        return None
    detail = []
    road = _road(title)
    if road:
        detail.append(road)
    t = title.lower()
    deaths = re.search(r"\b(\d+|two|three|four|five)\s+(?:people\s+)?(?:dead|killed)\b", t)
    if deaths:
        detail.append("%s dead" % deaths.group(1))
    elif re.search(r"\b(fatal|killed|dies|died|dead|deadly)\b", t):
        detail.append("fatal")
    if re.search(r"\b(injur\w*|hurt|hospitali[sz]ed)\b", t):
        detail.append("injuries")
    if kind in ("CRASH", "TRAFFIC", "PEDESTRIAN STRUCK", "TRAIN INCIDENT") and \
            re.search(r"\b(clos\w*|block\w*|shut)\b", t):
        detail.append("lanes closed" if "lane" in t else "road closed")
    named = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(County|Co\.)", title)
    return {"kind": kind, "detail": detail, "county": (named.group(1) + " Co") if named else "",
            "road": bool(road)}

