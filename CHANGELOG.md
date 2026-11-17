# Changelog

## 3.4.2 (2026-11-17)

- Volume keys move 10% a press (was 5%).

## 3.4.1 (2026-11-17)

- **Volume buttons and keys work** under cage: the tablet's buttons and the keyboard's
  mute, down and up keys, read straight from the kernel. Each press shows the new level;
  a MUTED tag stays in the top bar while muted. Needs the user in the `input` group.
- Start-up no longer forces 100%: it unmutes and raises the volume to at least 50%
  (`"min_volume"`), otherwise leaving it where you set it.

## 3.4.0 (2026-11-17): strict alert sources

- **A strict source list** (`sources.py`, and a table in SPEC.md). Every request goes
  through one check; any address not on the list is refused.
- **New local sources:** NWS Local Storm Reports (damage, flooding, tornadoes, large
  hail within 25 mi), FEMA's public IPAWS feed (evacuations, shelter orders, 911 outages
  for your county or state), Duke Energy outages (250+ customers within 15 mi), and
  USGS earthquakes within 100 mi.
- **National:** FAA ground stops (5+ airports, or any trouble at GSP, CLT, ATL) and
  significant earthquakes, under a NATIONAL divider in the ALERTS tile.
- **ALERTS is a table:** WHAT, WHERE, WHEN, DO. The worst local item is a colored bar
  on top; each row says what to do in a few words. Tap for the full list, with
  emergencies from officials first.
- **Four standard sounds:** beep beep (warning), beep (new local item), buzz (new
  national item), buzz buzz (DATA LINK, GPS or power). Each item sounds once; a
  source's first load after start-up is quiet.
- More room for ALERTS on the Surface: tighter gauges, HERE, WAYPOINT and bottom row.

## 3.3.1 (2026-11-17)

- **A new warning is one double beep** (beep, short pause, beep) played as a single
  sound, so the spacing is exact. Status changes stay a single beep.
- **Full volume:** the beeps are written at near full scale, and the system volume is
  set to 100% and unmuted at start-up (`"max_volume": false` turns that off).

## 3.3.0 (2026-11-17): new tiles

- **Top row: SPEED, ELEV, COMPASS.** The GRADE tile is gone; the grade is a line in ELEV,
  and ELEV's border is the steep-road light (amber at 6%, red at 10% or on a long
  descent, with "use a lower gear"). Big digits drop the comma before shrinking, so
  4-digit elevations stay full size.
- **COMPASS:** heading in big letters (N, NE, E...) with degrees and the finer point
  (NNW) beside it. While stopped it keeps the last heading, dimmed.
- **WAYPOINT** replaces SIGNAL: distance and direction back to the trip start, or tap it
  to choose a saved place or the last spot with internet.
- **DATA LINK** shows the way back to the last spot with internet while offline (the job
  SIGNAL used to do).
- **RADIO** has its spot on screen, marked "Not connected yet" until the radio helper.
- **WEATHER** is a short text tile: now, wind, high and low, rain, sunset. Tap it for the
  24-hour temperature and rain graphs together.
- SPEED's trip numbers are two lines under the digits; heading moved to COMPASS.

## 3.2.0 (2026-11-17): power and brightness

- **Clean shutdown on power loss.** When outside power goes away (ignition off), a beep
  and a 10-minute countdown in the top bar, another beep at one minute, then a clean
  shutdown. Power coming back or the red KEEP RUNNING button calls it off. Needs polkit.
  `"shutdown_after_min"` in config.json (0 turns it off).
- **Brightness follows the look:** 100% day, 70% normal, 20% night, set through logind so
  it needs no extra permissions. `"brightness"` in config.json (`false` turns it off).

## 3.1.0 (2026-11-17): touch

Built for the Surface Go 2 as a dedicated touchscreen display.

- **Touch.** Tap ALERTS or WEATHER to open their details. A bar of large buttons along
  the bottom: DISPLAY, REFRESH, FOLLOW GPS, SAVE HERE and PLACES. Detail screens get
  BACK, UP and DOWN. A tapped button lights up so you know it registered. The keyboard
  still works.
- **SAVE HERE** saves the spot under the nearest town's name, with no typing.
  **PLACES** lists saved places and following the GPS as rows to tap (two rows tall,
  so they're easy to hit).
- **Nothing locks while moving** any more. Instead, while moving, a detail screen or the
  places list goes back to the gauges after 30 seconds without a tap.
- **The first tap during a new warning only silences it**, so it can't open something
  by accident.
- **Beeps play through the speakers** with `pw-play`, `paplay` or `aplay`; the terminal
  bell is the fallback. (foot under cage doesn't sound the bell.)
- **Battery level** in the top bar on machines with a battery.
- Short messages now show in the top bar, where the update status goes. Messages say
  "tap" instead of naming keys.
- Big digits stay full height down to 38 rows of gauges, so they keep their size with the
  button bar on a 42-line screen. The window now needs 120×38.

## 3.0.1 (2026-11-12)

- **DATA LINK shows STANDBY** ("No location set") when there is no location yet, where it
  used to look like the internet was down. ALERTS shows NO LOCATION with what to do.
- **Messages rewritten** to one style: what is true, then what to do. The rule is in
  `SPEC.md` under Wording.

## 3.0.0 (2026-11-12): Ridgewatch

weather-tui becomes Ridgewatch, with a written spec (`SPEC.md`) and git history.

- **Renamed** to Ridgewatch: the top bar, the `./rw` launcher, the `ridgewatch` package,
  `~/.config/ridgewatch`, and `RIDGEWATCH_*` test variables.
- **Linux only.** Removed Windows support: the built-in screen driver, COM ports,
  Windows location, `weather.cmd`.
- **One layout.** The gauge cluster is the only main screen; it needs 120×36.
  Removed the compact layouts.
- **Modern terminals only.** Removed ASCII mode and the 16-color and mono modes; the app
  says so and stops if the terminal can't do 256 colors or UTF-8.
- **Removed feeds:** the Google News scraper and space weather. Wildfires and 511 roads stay.
- `/dev/ttyGPS` is checked first when looking for the GPS.

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
