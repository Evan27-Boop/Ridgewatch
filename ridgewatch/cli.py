"""Ridgewatch: a vehicle awareness dashboard for the terminal. Official weather
warnings, where you are, live GPS gauges, wildfires and road incidents.

    ./rw                        the dashboard
    ./rw --follow               follow the GPS
    ./rw --place "clemson sc"   pick a town (or ZIP) by name
    ./rw --coords "34.6834, -82.8374"
    ./rw --plain                one-shot text report (for checking things from a script)
"""

import argparse
import os
import sys
import time
import traceback

from . import __version__, follow, places, plain, settings


def _location_from_args(args, store):
    if args.coords:
        c = places.parse_coords(args.coords)
        if not c:
            sys.exit("Couldn't read those coordinates. Try: --coords \"34.6834, -82.8374\"")
        if not places.in_nws_area(*c):
            sys.exit("That spot is outside the US area NWS covers (check the minus sign).")
        loc = settings.make_location("%.4f, %.4f" % c, c[0], c[1])
    elif args.place:
        hits = places.PlaceIndex().search(args.place, limit=1)
        if not hits:
            sys.exit("No town or ZIP matched %r. Try adding the state: --place \"salem sc\""
                     % args.place)
        p = hits[0]
        if p.kind == "Z":
            name = (p.label + " " + p.name) if p.label else "ZIP " + p.name
        else:
            name = p.title
        loc = settings.make_location(name, p.lat, p.lon, p.label)
    else:
        return store.config.get("last")
    i = store.saved_index(loc)
    if i >= 0:
        loc = store.config["locations"][i]
    store.config["last"] = loc
    store.save_config()
    return loc


def _follow_loc(store, wait=20.0):
    """Live position for the text report (waits up to `wait` seconds for a fix)."""
    from . import locate
    c = store.config
    finder = locate.Locator(c.get("gps_port", "auto"), c.get("gps_baud", 9600))
    fix, start = None, time.time()
    try:
        while fix is None and time.time() - start < wait:
            fix = finder.current()
            if fix is None:
                time.sleep(0.5)
        problems = finder.describe()
    finally:
        finder.stop()
    index = places.PlaceIndex()
    index.load()
    if fix:
        label = places.describe_spot(index, fix.lat, fix.lon)[0]
        loc = {"name": "following", "lat": round(fix.lat, 5), "lon": round(fix.lon, 5),
               "follow": True, "label": label}
        follow.follow_mark(store.entry(loc), "full", loc["lat"], loc["lon"])
        return loc
    last = (store.cache["locations"].get("follow") or {}).get("follow_full")
    print("No live location (%s)." % "; ".join(problems), file=sys.stderr)
    if last:
        label = places.describe_spot(index, last["lat"], last["lon"])[0]
        print("Using the last known spot.\n", file=sys.stderr)
        return {"name": "following", "lat": last["lat"], "lon": last["lon"],
                "follow": True, "label": "last known: " + label}
    return None


def _plain(store, loc, fetch=True):
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if fetch and store.config.get("follow"):
        loc = _follow_loc(store) or loc
    if not loc:
        print("No location set. Use --place, --coords or --follow, "
              "or run ./rw and press L.")
        return 2
    code = plain.run(store, loc, fetch=fetch)
    for note in store.notes:
        print("note: " + note, file=sys.stderr)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="rw", description="Ridgewatch: a vehicle awareness dashboard for the terminal.")
    ap.add_argument("--plain", action="store_true", help="print a text report and exit")
    ap.add_argument("--place", metavar="TEXT", help="town or ZIP, e.g. \"clemson sc\" or 29631")
    ap.add_argument("--coords", metavar="LAT,LON", help="e.g. \"34.6834, -82.8374\"")
    ap.add_argument("--follow", action="store_true",
                    help="follow your live location (USB GPS)")
    ap.add_argument("--home", metavar="DIR",
                    help="where settings and saved data live (default: your user folder)")
    ap.add_argument("--display", choices=["auto", "day", "night", "normal"],
                    help="screen look: day = high contrast, night = dim, auto = by the sun")
    ap.add_argument("--version", action="version", version="ridgewatch " + __version__)
    args = ap.parse_args(argv)

    store = settings.Store(args.home)
    loc = _location_from_args(args, store)
    if args.place or args.coords:
        store.config["follow"] = False
        store.save_config()
    elif args.follow:
        store.config["follow"] = True
        store.save_config()

    if args.plain or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _plain(store, loc)

    import locale
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    if "utf" not in (locale.getpreferredencoding(False) or "").lower():
        sys.exit("Ridgewatch needs a UTF-8 terminal (try: LANG=C.UTF-8 ./rw).")

    from . import ui
    from .panels import NeedsColors
    try:
        ui.run(store, loc, {"display": args.display})
    except KeyboardInterrupt:
        pass
    except NeedsColors:
        sys.exit("Ridgewatch needs a 256-color terminal (this one reports TERM=%s). "
                 "Try: TERM=xterm-256color ./rw" % os.environ.get("TERM", "?"))
    except Exception:
        log = os.path.join(store.home, "crash.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write("\n=== %s ridgewatch %s ===\n" % (time.ctime(), __version__))
                traceback.print_exc(file=f)
            where = log
        except OSError:
            where = None
        print("Ridgewatch stopped on an error%s. Showing the text report instead.\n"
              % ((" (details in %s)" % where) if where else ""))
        return _plain(store, store.config.get("last") or loc, fetch=False)
    finally:
        try:
            store.save_cache()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
