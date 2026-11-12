"""Plain-text report: the fallback when the full screen can't run, and the
--plain option. Works in any terminal, any pipe, any log file."""

import sys
import time

from . import __version__, geo, hospitals, nws, sun, views

BARS = " ▁▂▃▄▅▆▇█"


def _can_print(text):
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def report(loc, entry, now=None, unicode_ok=None):
    now = time.time() if now is None else now
    if unicode_ok is None:
        unicode_ok = _can_print("▁█°·")
    deg = "°" if unicode_ok else ""
    dot = " · " if unicode_ok else " - "
    lines = []
    status, _ = views.overall_status(entry, False, now)
    name = loc["name"]
    label = loc.get("label") or ""
    pts = entry.get("points") or {}
    if not label and pts.get("city"):
        label = "near %s, %s" % (pts["city"], pts["state"])
    head = "ridgewatch %s%s%s" % (__version__, dot, name)
    if label and label not in name:
        head += " (%s)" % label
    lines.append(head + dot + status)
    lines.append("")

    n = views.view_now(entry, now)
    if n["ok"]:
        bits = []
        if n["temp"] is not None:
            bits.append("%d%sF %s" % (n["temp"], deg, n["desc"]))
        if n["feels"] is not None:
            bits.append("feels like %d%sF" % (n["feels"], deg))
        if n["wind"]:
            bits.append("wind " + n["wind"])
        if n["humidity"] is not None:
            bits.append("humidity %d%%" % n["humidity"])
        lines.append("NOW     " + dot.join(bits).strip())
        lines.append("        " + views.now_source(n, dot.strip() or "-"))
    else:
        lines.append("NOW     no current conditions" +
                     ((" (%s)" % n["error"]) if n["error"] else ""))
    lines.append("")

    a = views.view_alerts(entry, now)
    if not a["known"]:
        lines.append("ALERTS  unknown" + ((" (%s)" % a["error"]) if a["error"] else ""))
    elif not a["items"]:
        note = "" if a["trusted"] else " (as of %s - couldn't re-check)" % views.ago(a["age"])
        lines.append("ALERTS  none active" + note)
    else:
        for i, it in enumerate(a["items"]):
            when = views.alert_when(it, now)
            lines.append("%s%s [%s]%s" % ("ALERTS  " if i == 0 else "        ",
                                          it["event"], it["severity"],
                                          (dot + when) if when else ""))
        if not a["trusted"]:
            lines.append("        (as of %s - couldn't re-check)" % views.ago(a["age"]))
    lines.append("")

    p = views.view_precip(entry, now)
    if p["hours"]:
        peak = p["peak"]
        total = ("about %.2f in" % p["total_in"]) if p["total_in"] is not None else "unknown"
        if p["partial"] and p["total_in"] is not None:
            total += " (partial)"
        if peak[0] < 10:
            first = "dry, under 10% all 24 hours"
        else:
            first = "peak %d%% at %s" % (peak[0], views.fmt_hour(peak[1], peak[2]))
        lines.append("PRECIP  next 24h%s%s%stotal %s" % (dot, first, dot, total))
        if unicode_ok:
            spark = "".join(BARS[min(8, int(round(h[2] / 12.5)))] for h in p["hours"])
            start = views.fmt_hour(p["hours"][0][0], p["hours"][0][1], short=True)
            lines.append("        %s  (hourly chance from %s)" % (spark, start))
    elif entry.get("hourly"):
        lines.append("PRECIP  Saved forecast has expired" +
                     ((" (%s)" % p["hourly_err"]) if p["hourly_err"] else ""))
    else:
        lines.append("PRECIP  no forecast" +
                     ((" (%s)" % p["hourly_err"]) if p["hourly_err"] else ""))
    lines.append("")
    lines += offline_lines(loc, now, dot)
    return "\n".join(lines)


def offline_lines(loc, now, dot):
    """Sun times and nearest ER: worked out on the laptop, so they print with no signal."""
    out = []
    st = sun.status(loc["lat"], loc["lon"], now)
    if st["next"]:
        clock = time.strftime("%I:%M %p", time.localtime(st["next"][1])).lstrip("0")
        text = "%s %s" % (st["next"][0], clock)
        if st["left"]:
            m = int(st["left"] // 60)
            text += "%s%dh %02dm of daylight left" % (dot, m // 60, m % 60)
        out.append("SUN     " + text)
    idx = hospitals.HospitalIndex()
    idx.load()
    hit = idx.nearest(loc["lat"], loc["lon"])
    if hit:
        h, km = hit
        mi = max(1, round(km / 1.609344))
        d = geo.compass(geo.bearing(loc["lat"], loc["lon"], h.lat, h.lon))
        out.append("ER      about %d mi %s%s%s, %s%s" % (
            mi, d, dot, h.name, h.city, "" if h.listed else " (ER not listed)"))
    return out


def run(store, loc, fetch=True, out=None):
    """Fetch (unless told not to), print the report, return an exit code."""
    out = out or sys.stdout
    entry = store.entry(loc)
    if fetch:
        ua = nws.user_agent(store.config.get("contact"))

        def post(section, data=None, error=None):
            nws.apply_result(entry, section, data, error)

        nws.apply_result(entry, "start", None, None)
        try:
            nws.fetch_location(loc, entry, post, ua)
        except KeyboardInterrupt:
            pass
        nws.apply_result(entry, "done", None, None)
        store.save_cache()
    try:
        out.write(report(loc, entry) + "\n")
    except UnicodeEncodeError:
        out.write(report(loc, entry, unicode_ok=False) + "\n")
    has_any = any(entry.get(s) for s in ("obs", "hourly"))       # alerts alone aren't a forecast
    return 0 if has_any else 1
