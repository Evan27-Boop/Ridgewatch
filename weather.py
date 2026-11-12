#!/usr/bin/env python3
"""weather-tui: terminal weather from the National Weather Service.

    python weather.py                      full-screen dashboard
    python weather.py --plain              one-shot text report (last location)
    python weather.py --place "clemson sc" pick a town (or ZIP) from the command line
    python weather.py --coords "34.6834, -82.8374"
"""

import argparse
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from wxtui import __version__, follow, places, plain, settings  # noqa: E402


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
    from wxtui import locate
    c = store.config
    finder = locate.Locator(c.get("gps_port", "auto"), c.get("gps_baud", 9600),
                            c.get("windows_location", True))
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
        print("No location yet. Run it once without --plain to pick one, "
              "or use --place / --coords / --follow.")
        return 2
    code = plain.run(store, loc, fetch=fetch)
    for note in store.notes:
        print("note: " + note, file=sys.stderr)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="weather", description="Terminal weather from the National Weather Service.")
    ap.add_argument("--plain", action="store_true", help="print a text report and exit")
    ap.add_argument("--place", metavar="TEXT", help="town or ZIP, e.g. \"clemson sc\" or 29631")
    ap.add_argument("--coords", metavar="LAT,LON", help="e.g. \"34.6834, -82.8374\"")
    ap.add_argument("--follow", action="store_true",
                    help="follow your live location (USB GPS or Windows location)")
    ap.add_argument("--home", metavar="DIR",
                    help="where settings and saved data live (default: your user folder)")
    ap.add_argument("--ascii", action="store_true", help="plain ASCII lines instead of box drawing")
    ap.add_argument("--display", choices=["auto", "day", "night", "normal"],
                    help="screen look: day = high contrast, night = dim, auto = by the sun")
    ap.add_argument("--colors", choices=["auto", "256", "16", "8", "mono"], default="auto",
                    help="force a color level (default: auto)")
    ap.add_argument("--version", action="version", version="weather-tui " + __version__)
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

    ascii_mode = args.ascii
    if not ascii_mode and os.name != "nt":
        import locale
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            pass
        if "utf" not in (locale.getpreferredencoding(False) or "").lower():
            ascii_mode = True

    try:
        from wxtui import ui
        ui.run(store, loc, {"ascii": ascii_mode, "colors": args.colors, "display": args.display})
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log = os.path.join(store.home, "crash.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write("\n=== %s weather-tui %s ===\n" % (time.ctime(), __version__))
                traceback.print_exc(file=f)
            where = log
        except OSError:
            where = None
        why = (": " + str(e)) if type(e).__module__ == "wxtui.screen" else ""
        print("The full screen couldn't run%s%s. Showing the text report instead.\n"
              % (why, (" (details in %s)" % where) if where else ""))
        return _plain(store, store.config.get("last") or loc, fetch=False)
    finally:
        try:
            store.save_cache()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
