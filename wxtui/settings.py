"""Your settings and saved data, on disk: config.json and cache.json.

Writes are atomic (a crash mid-save can't corrupt a file) and a damaged file is
set aside instead of stopping the app. Standard library only.
"""

import json
import os
import time

DEFAULT_REFRESH_MIN = 15


def default_home():
    env = os.environ.get("WEATHER_TUI_HOME")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "weather-tui")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "weather-tui")


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    for attempt in range(5):           # Windows can briefly lock files (antivirus)
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    os.replace(tmp, path)


class Store(object):
    """config.json = your settings and saved locations; cache.json = last good data."""

    def __init__(self, home=None):
        self.home = os.path.abspath(home or default_home())
        self.notes = []                 # problems worth telling the user about
        try:
            os.makedirs(self.home, exist_ok=True)
        except OSError as e:
            self.notes.append("can't create %s (%s)" % (self.home, e.strerror))
        self.config_path = os.path.join(self.home, "config.json")
        self.cache_path = os.path.join(self.home, "cache.json")
        self.config = self._load(self.config_path, {})
        self.cache = self._load(self.cache_path, {})
        self._normalize()

    def _load(self, path, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            raise ValueError("not an object")
        except (OSError, ValueError) as e:
            bad = "%s.bad-%d" % (path, int(time.time()))
            try:
                os.replace(path, bad)
                self.notes.append("%s was damaged; kept a copy as %s" %
                                  (os.path.basename(path), os.path.basename(bad)))
            except OSError:
                self.notes.append("%s couldn't be read (%s)" % (os.path.basename(path), e))
            return default

    def _normalize(self):
        c = self.config
        c.setdefault("refresh_minutes", DEFAULT_REFRESH_MIN)
        c.setdefault("contact", "")
        c.setdefault("locations", [])
        c.setdefault("last", None)
        c.setdefault("follow", False)
        c.setdefault("gps_port", "auto")
        c.setdefault("gps_baud", 9600)
        c.setdefault("windows_location", True)
        c.setdefault("headlines", True)
        c["headlines"] = bool(c["headlines"])
        if c.get("display") not in ("auto", "day", "night", "normal"):
            c["display"] = "auto"
        c["wildfires"] = bool(c.get("wildfires", True))
        c["space_weather"] = bool(c.get("space_weather", True))
        keys = c.get("keys_511") if isinstance(c.get("keys_511"), dict) else {}
        c["keys_511"] = {"GA": str(keys.get("GA") or "").strip(),
                         "NC": str(keys.get("NC") or "").strip()}
        try:
            c["gps_baud"] = int(c["gps_baud"])
        except (TypeError, ValueError):
            c["gps_baud"] = 9600
        c["follow"] = bool(c["follow"])
        try:
            c["refresh_minutes"] = max(5, int(c["refresh_minutes"]))
        except (TypeError, ValueError):
            c["refresh_minutes"] = DEFAULT_REFRESH_MIN
        c["locations"] = [l for l in c["locations"] if valid_location(l)]
        if not valid_location(c.get("last")):
            c["last"] = None
        if not isinstance(self.cache.get("locations"), dict):
            self.cache = {"locations": {}}
        for name in ("news", "feeds"):
            d = self.cache.get(name)
            self.cache[name] = {k: v for k, v in d.items() if isinstance(v, dict)} \
                if isinstance(d, dict) else {}

    def save_config(self):
        try:
            _write_json_atomic(self.config_path, self.config)
            return True
        except OSError:
            return False

    def save_cache(self):
        locs = self.cache.get("locations", {})
        keep = {loc_key(l) for l in self.config["locations"]} | {"follow"}
        if self.config.get("last"):
            keep.add(loc_key(self.config["last"]))
        # keep saved places plus the 10 most recently used others
        others = sorted((k for k in locs if k not in keep),
                        key=lambda k: locs[k].get("used", 0), reverse=True)
        for k in others[10:]:
            del locs[k]
        news = self.cache.get("news", {})          # headlines: the 6 most recent areas
        for k in sorted(news, key=lambda k: news[k].get("tried", 0), reverse=True)[6:]:
            del news[k]
        try:
            _write_json_atomic(self.cache_path, self.cache)
            return True
        except OSError:
            return False

    def entry(self, loc):
        e = self.cache["locations"].setdefault(loc_key(loc), {})
        e["used"] = time.time()
        return e

    # saved locations -------------------------------------------------------
    def saved_index(self, loc):
        k = loc_key(loc)
        for i, l in enumerate(self.config["locations"]):
            if loc_key(l) == k:
                return i
        return -1

    def save_location(self, loc, name):
        loc = dict(loc, name=name)
        i = self.saved_index(loc)
        if i >= 0:
            self.config["locations"][i] = loc
        else:
            self.config["locations"].append(loc)
        return loc

    def forget_location(self, loc):
        i = self.saved_index(loc)
        if i >= 0:
            del self.config["locations"][i]


def valid_location(l):
    try:
        return (isinstance(l, dict) and isinstance(l.get("name"), str)
                and -90 <= float(l["lat"]) <= 90 and -180 <= float(l["lon"]) <= 180)
    except (KeyError, TypeError, ValueError):
        return False


def loc_key(loc):
    if loc.get("follow"):
        return "follow"                 # follow mode keeps one entry that moves with you
    return "%.4f,%.4f" % (float(loc["lat"]), float(loc["lon"]))


def make_location(name, lat, lon, label=""):
    return {"name": name, "lat": round(float(lat), 4), "lon": round(float(lon), 4),
            "label": label}
