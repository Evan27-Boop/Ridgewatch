# Changelog

## 2.5.0 (2026-11-10)

- **Gauge-cluster layout** for big windows (120×36 and up, e.g. a maximized laptop
  window): SPEED, GRADE and ELEV in big digits along the top; HERE and GRID; SIGNAL and
  ER; ALERTS with a master warning light; WEATHER with DATA LINK and GPS at the bottom.
  Every tile has a fixed spot and its border color is its warning light. Smaller windows
  keep the compact layout.
- **Trip computer** in the SPEED tile: miles, time moving and stopped, average and top
  speed, where and when you started.
- **Total climb and descent** for the trip in the ELEV tile, smoothed so GPS jitter
  doesn't count; the GRADE tile adds the change over the last mile and a fixed
  -12% to +12% gauge.
- **SIGNAL tile:** while offline, the distance and direction back to where the internet
  last answered.
- **Old station readings no longer pass for "now":** when the nearest station's reading
  is over 2 hours old, the current hour's forecast stands in, marked in amber.
- Elevation and grade ignore 2D fixes (no reliable altitude).

## 2.4.4 (2026-11-09)

- **One beep when DATA LINK or GPS changes** (for example ONLINE to OFFLINE, or NO FIX to
  3D FIX), once the new state has held for 5 seconds. Warnings still beep twice.

## 2.4.3 (2026-11-08)

- **DATA LINK and GPS boxes** in the top right, a third of the width: one big colored word
  each, readable at a glance. DATA LINK: ONLINE, LIMITED (NWS erroring), STALE, OFFLINE,
  with how long it's been down. GPS: 3D FIX, 2D FIX, ROUGH (Windows location), NO FIX,
  NO GPS, OFF, with signal bars, satellites and accuracy. On narrow windows they become
  colored tags in the top bar.
- **2D/3D fix** read from the GPS (NMEA GSA), so you know when elevation and grade are
  less trustworthy.
- **Fixed temperature scale:** the graph always runs 20°F to 100°F, with 60° marked, so a
  bar the same height is always the same temperature.
- The weather lines drop whole pieces (humidity first) instead of cutting words off.
- "NO SIGNAL" is now "DATA LINK DOWN", and "can't reach NWS" is "data link down".
- The GPS row left the location box (the GPS box has it).

## 2.4.2 (2026-11-08)

- **Location box rebuilt for glancing:** labeled rows (HERE, SPEED, ELEV, AHEAD, ER, TRIP,
  GRID, GPS) that stay in the same place.
- **What the GPS tells you, not just what it says:** the grade you're on (climbing 7%),
  a long-descent warning (500+ ft dropped in 2 miles), an elevation graph of the last few
  miles, "stopped 4 min", rough time to the towns ahead, "entered Oconee Co 2m ago" when
  you cross a county or state line, and GPS signal bars.
- Dropped the moon and sunrise/sunset from the location box (sunset is in the weather
  box). The weather radio code moved to the `a` screen.

## 2.4.1 (2026-11-06)

- **Bigger weather box with a 24-hour graph:** chance of rain when rain is likely,
  otherwise temperature, plus the day's high and low.
- **Alerts and location are the same size** and split the rest of the screen.
- **The location box fills up with useful things:** towns ahead on your heading, a trip
  computer (distance, moving time, average and top speed), distance back to your start
  point, GPS details, map grid (MGRS), sunrise/sunset and moon, the county's weather radio
  (SAME) code and NWS office, and the forecast area's elevation for typed-in places.

## 2.4.0 (2026-11-06)

- **One alerts list.** The separate news box is gone. Every line is a short report marked
  ▲ official (an agency said so) or ● reported (news, unconfirmed); only official NWS
  warnings beep. Order: over you, storm warnings, then closest first.
- **News as reports.** Headlines become `CRASH  US 123 NB · lanes closed · Clemson, 2 mi
  SW · 40m · 3 reports`. One that doesn't say what and where is dropped. The headline
  itself is on the `a` screen.
- **Wildfires** near you from the National Interagency Fire Center: name, size, how much
  is contained, distance.
- **Solar storms** from NOAA, shown only when strong enough to affect GPS (G3+).
- **Georgia and North Carolina 511 road incidents** (crashes, closures, blocked lanes)
  when you add a developer key in `config.json` and you're near that state.
- `n` is gone; `a` shows everything. `r` refreshes every source.

## 2.3.0 (2026-11-06)

- **News moved out of the alerts box.** Headlines aren't alerts, so they have their own
  quieter box, "local news · unofficial", under the alerts. The alerts box holds only
  official warnings and sizes itself to them.
- Each story gets a type (ROAD, FIRE, POLICE, MISSING, UTILITY, STORM), and when the
  headline names a nearby town, its distance and direction from you. Those come first,
  closest first.
- The same story from several outlets is shown once with "+ 2 more".
- `n` opens the full news list; `a` is official alerts only.
- **Bigger location box:** one item per line (place; county and coordinates; speed,
  heading and elevation; nearest ER; position source), so it reads at a glance.

## 2.2.0 (2026-11-06)

Built around driving, sunlight and no signal.

- **Smaller weather, bigger alerts.** The weather is two lines on top: conditions, then the
  rain as a sentence ("rain likely 6 PM-3 AM (75%)") with sunset and daylight left.
  Alerts get the middle of the screen. The rain graph is still on `p`.
- **NO SIGNAL.** When alerts are more than 10 minutes old the alerts box turns amber and
  says when they were last checked. It never shows a green "none active" it can't vouch
  for.
- **Storm warnings nearby and ahead.** Tornado, severe thunderstorm and flash flood
  warnings within about 25 miles are listed with distance and direction. While driving,
  one on your heading shows as "4 mi ahead" and, if urgent and within about 15 miles,
  flashes and beeps; it beeps again when you drive into it.
- **Display looks.** Day (high contrast, black on white, bold), night (dim) and normal,
  switching automatically at sunrise and sunset. `d` or `--display` to choose.
- **Driving mode.** Above 5 mph (GPS) the menus and typing lock, and the alert and rain
  screens go back to the main view by themselves.
- **Nearest ER**, offline, from Medicare's hospital list (with
  `tools/build_hospitals.py` to update it).
- **Sunrise, sunset and daylight left**, worked out offline. The text report (`--plain`)
  shows them too, along with the nearest ER.
- The forecast says how old it is once it's more than 90 minutes old.

## 2.1.1 (2026-11-06)

- Headlines: a much stronger sports filter. Sports stories no longer slip through when
  they use safety words ("QB injured", "defense under fire", "closes out the season") or
  a score like 31-14. Real news that sounds a bit like sports ("game wardens rescue
  hunter", "9-1-1 outage", "road closed 7-9 p.m.") still shows.

## 2.1.0 (2026-11-06)

Cleanup and tightening, plus nearby headlines.

- **Nearby headlines.** Under the official alerts, a quiet list of recent public-safety
  news (crashes, closures, fires, police activity, outages...) from a Google News search
  of your county and nearest city. Checked every 20 minutes and after crossing into a
  new county, filtered to safety news, each with its age, gone after 24 hours. Never
  beeps. `a` shows the outlets. Turn off with `"headlines": false`.
- **Alerts every 3 minutes for typed-in places too**, not just while following
  (weather still every 15).
- **Warning updates stay quiet.** NWS re-issues a warning with a new ID when it changes
  the time or area; you're no longer beeped again for the same warning. An upgrade to a
  different warning (severe thunderstorm to tornado) still beeps.
- **Less data.** The hourly rain amounts (the biggest download) are reused for up to an
  hour nearby. Coordinates go to NWS rounded to about 100 m.
- **Easier on the battery.** The screen redraws every 5 seconds when nothing is
  happening, and right away when something changes (new position, new data, a key).
- The rain box is titled "rain".
- Reorganized the code into small single-purpose files (settings, nws, news, views,
  follow, geo, panels, term) and added an offline test suite (`python -m unittest`).
- README: what the app is for and the rules it's built by; corrected the privacy note
  (Windows location uses Microsoft's location service).

## 2.0.0 (2026-11-06)

Built for use on a laptop in a vehicle.

- Follow mode (`f` or `--follow`): weather and alerts for wherever you are, from a USB
  GPS receiver (found automatically) or, as a backup, Windows' own location. Refreshes
  after about 5 miles or 15 minutes; alerts every 3 minutes or 2 miles.
- New layout: weather top left (big temperature, 24-hour rain), alerts top right,
  location bottom (nearest town and county, coordinates, speed, heading, elevation,
  accuracy and source).
- New warnings and public-safety emergencies flash and beep; watches and advisories
  appear quietly, most severe first.
- When the position is rough (Windows location worse than ~3 miles) or lost, the
  alerts box says so.
- `s` while following saves where you are now; `p` shows the full rain graph.
- Still standard library only.

## 1.0.1 (2026-11-06)

- Windows no longer needs `windows-curses`. The app draws its own screen with the
  escape codes Windows Terminal understands, so it works on any Python 3.8 or newer,
  including 3.15, with nothing to install. The look and keys are unchanged.
- Linux still uses Python's built-in curses, and falls back to the same built-in
  screen if curses is missing.
- Fixed: on some terminals, an arrow key could be read as Esc plus a letter.

## 1.0.0 (2026-11-05)

First release.

- Full-screen terminal dashboard (curses) in a bpytop-style layout: current
  conditions, active alerts, and a 24-hour chance-of-rain graph with expected total.
- National Weather Service data only; no API key, no account.
- Locations typed by hand: offline town/ZIP search or exact coordinates. Nothing is
  auto-detected. Save up to 9 for one-key access.
- Opens to the last location and shows saved data instantly, then refreshes every
  15 minutes (sooner retries after a failure).
- Built to keep working: saved data when offline, each section fails on its own,
  retries with backoff, nearest-station fallback, damaged files set aside, plain-text
  fallback if the screen can't run.
- Runs on Windows (Windows Terminal, with windows-curses) and Linux.
