# weather-tui 2.5.0

A terminal weather dashboard built on National Weather Service data, made to run
on a laptop in a vehicle, on or off grid. No ads, no account, no API key. It follows
your live location (USB GPS or Windows location), shows the weather where you are,
flashes and beeps when a warning covers you or lies ahead on your heading, lists
nearby public-safety news, and tells you where you are and where the nearest ER is.

On a big window (120×36 or more) it's laid out like a gauge cluster: speed, grade and
elevation in big digits along the top, every tile in a fixed spot, and each tile's
border color as its warning light. Screenshots: `docs/gauge-cluster.png` (driving into
a tornado warning, normal colors) and `docs/gauge-cluster-day-offline.png` (day colors,
internet just lost).

```
SPEED · GRADE · ELEV        big digits (6 rows tall on a full-screen laptop window)
HERE · GRID                 where you are, in words and as a map grid
SIGNAL · ER                 the way back to the internet; the nearest emergency room
ALERTS                      a master warning light, then the list
WEATHER · DATA LINK / GPS   conditions and a 24-hour graph; internet and GPS status
```

On a smaller window it uses the compact layout:

```
── weather 2.5.0 ─ following 1.1 mi SSW of Central, SC ──── ▲ AHEAD: Tornado Warning 3 mi ahead ──
┌─ weather ───────────────────────────────────── KCEU · 25m ago ─┐┌─ DATA LINK ──────────────────┐
│ 54°F  Mostly Cloudy · wind SW 8 mph, gusts 21 · humidity 60%   ││            ONLINE            │
│ high 64° 4p  low 46° 3a · no rain expected next 24h            ││ contact just now             │
│ 100°                                                           │└──────────────────────────────┘
│  60°      ▆▆▆▆▅▅▅▅▅▅▄▄▃▃▂▂▂▂▁▁▁▁      ▁▁▁▁▂▂▂▂▃▃▄▄▄▄▅▅▅▅▆▆     │┌─ GPS ───────────────── COM3 ─┐
│  20°      ████████████████████████████████████████████████     ││            3D FIX            │
│           4p  6p  8p  10p 12a 2a  4a  6a  8a  10a 12p 2p       ││ ▮▮▮▮▯ · 9 sats · ±15 ft      │
└────────────────────────────────────────────────────────────────┘└──────────────────────────────┘
┌─ alerts ───────────────────────────────────────────────────────────── 2 official · 2 reported ─┐
│ none over you                                                                                  │
│ ▲ TORNADO WARNING  3 mi ahead · until 5:15 PM                                              NWS │
│ ● CRASH  US 123 NB · lanes closed · Clemson, 2 mi SW · 40m                           3 reports │
│ ▲ WILDFIRE  Table Rock · 850 ac · 40% contained · 14 mi NE                                NIFC │
│ ● MISSING PERSON  Pickens Co · 5h                                                    2 reports │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ location ───────────────────────────────────────────────────────────────────────── following ─┐
│ HERE   1.1 mi SSW of Central, SC · Pickens Co                                                  │
│ SPEED  64 mph  ↑ N 8° · climbing 7%                                                            │
│ ELEV   1,090 ft  ▁▁▂▂▃▃▄▄▅▅▆▆▇▇██  last 5 mi 640-1,210 ft                                      │
│ AHEAD  Norris 4 mi ~4 min · Liberty 9 mi ~8 min                                                │
│ ER     ≈11 mi W · Oconee Memorial Hospital, Seneca                                             │
│ TRIP   34.2 mi · 41 min moving · top 66 · start 14 mi SE                                       │
│ GRID   17S LU 3698 4208   34.7078, -82.7800                                                    │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
  DRIVING   a alerts  p rain  d display  r refresh  q quit
```

## What it's for

One glance from the driver's seat should answer three questions:

1. What's the weather here?
2. Is anything dangerous happening here?
3. Where exactly am I?

Everything in the app serves one of those. The rules it's built by:

- **Glanceable.** Laid out like a gauge cluster: every tile always in the same spot, big
  digits for the numbers that change by the second, and a tile's border color as its
  warning light. The most serious alert gets its own block of color; nothing to scroll.
  Details are one key away. Readable in sun and at night.
- **Official or reported, always marked.** Everything in the alerts list is either ▲
  official (an agency said so) or ● reported (news, unconfirmed). Only official NWS
  warnings ever flash and beep.
- **Keeps working.** Each piece fails on its own. No signal, no GPS, a damaged file or a
  terminal that can't draw all have a fallback, and saved data stays on screen. It backs
  off instead of hammering a server when the connection is bad.
- **Honest about uncertainty.** Every reading shows its age. With no internet it says
  DATA LINK DOWN, never a green "none active" it can't vouch for. A rough position or a
  lost GPS is labeled as such.
- **Hands off while moving.** Nothing needs a key press while driving, and the menus
  lock until you stop.
- **Your location stays yours.** The GPS is read on the laptop. Weather requests send
  coordinates rounded to about 100 m (330 ft). News and wildfire searches send only
  place names or a rough area. Each outside service can be turned off (see Settings).
- **Nothing to install.** Python's standard library only, on Windows and Linux. The whole
  thing is one folder you can copy to a USB stick.
- **Small on purpose.** A new feature has to help answer one of the three questions.

## Setup

**Windows 10 or 11** (use Windows Terminal)

1. Python 3.8 or newer. Nothing else to install.
2. Start it: `py weather.py`, or just `weather` from this folder (uses `weather.cmd`).
3. For follow mode without a GPS: turn on Windows location. Settings > Privacy & security >
   Location: turn on **Location services** and **Let desktop apps access your location**.

**Linux**

1. Python 3.8 or newer (already installed on most distros). Nothing else to install.
2. Start it: `python3 weather.py`
3. For a USB GPS: add yourself to the `dialout` group once
   (`sudo usermod -aG dialout $USER`, then log out and back in).

## Follow mode (live location)

Press `f` (or start with `--follow`). The app remembers the choice next time.

Where the position comes from, best first:

1. **USB GPS receiver.** Any USB GPS "puck" that sends standard NMEA text works
   (most u-blox 7/8 based ones, about $15 to $30). Plug it in: the app finds the port by
   itself, gets speed, heading and elevation too, and works with no cell signal.
2. **Windows location.** Rough, from nearby Wi-Fi or your internet connection. Good in
   towns, can be miles off on highways, and it has no speed, heading or elevation.
   The GPS box says ROUGH with its accuracy (like `Windows location ±2 mi`), and when it's worse
   than about 3 miles the alerts box says the location is approximate.

While following:

- Weather refreshes after you've moved about 5 miles, or every 15 minutes. The hourly
  rain totals (the biggest download) are reused for up to an hour within about 10 miles.
- Alerts re-check every 3 minutes, or after you've moved about 2 miles.
- Losing the GPS keeps the last known spot on screen, clearly marked.
- Losing the internet keeps the last weather on screen with its age, and retries
  after 1, 2, 4... minutes (it never hammers NWS).

## Off grid

The weather service needs internet; the app can't change that. What it does instead:

- **No signal is impossible to miss.** The DATA LINK box turns red and says **OFFLINE**
  the moment requests start failing. Once alerts are more than 10 minutes old, the alerts
  box turns amber and says **DATA LINK DOWN · last checked 42m ago**. "None active"
  becomes "none as of 42m ago".
- **The way back to signal.** The SIGNAL tile remembers where the internet last answered.
  While you're offline it shows how far and which way that is (`≈3.2 mi S · last online
  9m ago`), so you know where to drive to make a call. Requests go out every few minutes,
  so it's the last place it *surely* worked; the signal may have lasted a bit further.
- **What still works with no signal:** your position, speed, heading and elevation (GPS),
  the nearest town and county, sunrise, sunset and daylight left, the nearest ER, and the
  last forecast, labeled with its age.
- **Carry a weather radio too.** A NOAA Weather Radio with SAME (about $30 to $60, set to
  your counties) gets warnings over the air with no cell signal. It's the backup this app
  can't be.

## New warnings

When a new **warning** or public-safety emergency covers your location, the alerts box
and the top bar flash and it beeps twice. That includes tornado, severe thunderstorm and
flash flood warnings, and the non-weather alerts NWS relays: law enforcement warnings,
AMBER alerts, evacuations, shelter-in-place and hazmat warnings, 911 outages. Watches
and advisories just appear, most severe at the top. Press any key to stop the flashing.

**Warnings ahead.** Storm warnings (tornado, severe thunderstorm, flash flood and the
like) come with a shape on the map. Every 3 minutes the app gets the ones for your state
and its neighbors, and lists any within about 25 miles with distance and direction
(`Tornado Warning  6 mi NW`). While you're driving it also checks your heading: a
warning within about 20 miles ahead shows as `4 mi ahead`, and if it's an urgent warning
within about 15 miles it flashes and beeps (**AHEAD**). When you drive into it, it beeps
again (**NOW HERE**).

Alerts are checked every 3 minutes whether you're following or using a typed-in place.
When NWS updates a warning you've already been told about (a new expiry time, a changed
area), it stays quiet. If the update makes it a different kind of warning, like a severe
thunderstorm warning becoming a tornado warning, it flashes and beeps again.

No beep? In Windows Terminal: Settings > your profile > Advanced > Bell notification
style should include "Audible".

## The alerts list

One list for everything worth knowing around you, each line a short report:

```
▲ TORNADO WARNING  4 mi ahead · until 4:15 PM                          NWS
● CRASH  US 123 NB · lanes closed · Clemson, 2 mi SW · 40m       3 reports
```

- **▲ official:** an agency said so. Only these can flash and beep (and only NWS
  warnings do).
- **● reported:** a news report, unconfirmed. Never beeps.
- **Order:** alerts over you, then storm warnings (ahead on your heading first), then
  everything else closest first, then things with no distance (nationwide, county-wide).
- `a` shows the full text: official alerts in full, every other line with what's known,
  and for news, the original headline and outlet.

Where each kind of line comes from:

| Line | Source | Shows | Needs |
|---|---|---|---|
| ▲ weather and emergency alerts | National Weather Service | over you, nearby, ahead | nothing |
| ▲ WILDFIRE / PRESCRIBED BURN | National Interagency Fire Center | name, size, % contained, distance (within ~50 mi) | nothing |
| ▲ CRASH / ROAD CLOSED / INCIDENT | Georgia 511 and North Carolina DriveNC | road, lanes, distance (within ~25 mi) | a developer key from that state |
| ▲ SOLAR STORM | NOAA Space Weather Prediction Center | only at G3 and up, when GPS can be less accurate | nothing |
| ● CRASH, FIRE, MISSING PERSON... | local news (Google News search) | type, road, impact, place, age, how many outlets | nothing |

**News as reports.** A headline like "Crash closes northbound lanes of US 123 near
Clemson" becomes `CRASH  US 123 NB · lanes closed · Clemson, 2 mi SW · 40m`. The type
comes from the wording, the place from a town, road or county the headline names. A
headline that doesn't say both what happened and where is dropped. Sports and court
follow-ups are always left out, as is anything older than 24 hours. The same story from
several outlets is one line ("3 reports"). News is searched for your county and nearest
city every 20 minutes, and again shortly after you cross into a new county.

**Not available:** South Carolina's 511 has no public data feed (the Highway Patrol's
live list was shut down too), and no Upstate county publishes live 911 calls. Waze only
shares its data with government partners. If any of that changes, it can be added.

## Display: day, night, normal

- **day:** black on white, everything bold, dark colors only. For bright sun (turn the
  laptop's brightness all the way up too).
- **night:** dark and dim, so it doesn't light up the cab. Warnings stay brighter.
- **normal:** the original dark bpytop look.
- **auto** (the default) switches between day and night at sunrise and sunset, worked out
  on the laptop from where you are.

Press `d` to cycle auto, day, night, normal (it's remembered), or start with
`--display day`.

## Driving mode

When the GPS says you're moving (5 mph or more), the bottom bar shows **DRIVING** and
only the keys that need no reading or typing work: `a`, `p`, `d`, `r`, `q`. The location
picker and saving are locked until you've been stopped for 15 seconds, and the alert
or rain screen returns to the main view after 20 seconds without a key press. Windows
location has no speed, so driving mode needs the GPS.

## Nearest ER

The location box shows the nearest emergency room with its distance and direction
(`nearest ER ≈9 mi W · Oconee Memorial Hospital, Seneca`). It works with no signal.

- It comes from Medicare's official hospital list, which says which hospitals have an
  emergency department. Specialty hospitals without one, psychiatric and children's
  hospitals, and VA and military hospitals are left out.
- The list has no map positions, so each hospital is placed at the middle of its town
  (or ZIP code). Distances are approximate: usually within a mile or two.
- A few big hospitals are marked "no emergency services" in the list by mistake. Those
  are kept and shown as `nearest hospital ... (ER not listed)` when they're the closest.
- In an emergency, call 911. To update the list, see `tools/build_hospitals.py`.

## Keys

| Key | Does |
|---|---|
| `f` | follow your live location (on/off) |
| `l` or `/` | pick a location (town, ZIP, or coordinates) |
| `r` | refresh now (weather, alerts and news) |
| `a` | the whole alerts list with full text, headlines and sources |
| `p` | full-screen 24-hour rain graph |
| `d` | display: auto, day (high contrast), night (dim), normal |
| `s` | save this spot (while following, saves where you are now) |
| `1`-`9` | jump to a saved spot (turns follow off) |
| `q` | quit |

In the picker, type a town (`clemson sc`), a ZIP (`29631`), or coordinates
(`34.6834, -82.8374`). Arrow keys to move, Enter to open, Delete to forget a saved spot,
Esc to go back. "follow my live location" is always at the top.

## What's on the screen

### Big windows: the gauge cluster

At 120×36 or more (a maximized laptop window is about 156×47) the screen is a set of
tiles that never move. Unknown values show a dim dash and the reason, never a gap.

| Tile | Shows | Turns amber / red when |
|---|---|---|
| SPEED | speed in big digits, heading (arrow, compass point, degrees), and the trip: miles, time moving and stopped, average and top speed, where and when you started | |
| GRADE | the grade you're on in big digits, climbing or descending, the change over the last mile, and a gauge on a fixed -12% to +12% scale | amber at 6%, red at 10% or on a **long descent** (500+ ft lost in 2 miles: use a lower gear) |
| ELEV | elevation in big digits, a graph and the low/high of the last few miles, and the trip's total climb and descent | amber on a 2D fix (no reliable elevation) |
| HERE | distance and direction from the nearest town, county, the next towns on your heading, "entered Pickens Co 4m ago" after a county or state line | amber when the GPS is lost (last known spot) |
| GRID | map grid (MGRS / US National Grid), coordinates, accuracy and source | |
| SIGNAL | "online", or while offline, the way back to where the internet last answered | amber while offline |
| ER | the nearest emergency room, straight-line | amber when it's over 30 miles away |
| ALERTS | a master light (the worst official warning, ALL CLEAR, or NOT UPDATING) with the list beside it | the warning's own color |
| WEATHER | as below, with the graph as tall as the space allows | |
| DATA LINK, GPS | big blocks of color, see below | |

Big digits shrink to 4 rows on shorter windows. The total climb and descent only count
a change once it's 3 m (10 ft) past the last counted level, after smoothing, so GPS
altitude jitter doesn't add up into hills that aren't there.

### Smaller windows: the compact layout

- **weather** (top left, two thirds of the width): temperature, conditions, wind and
  humidity; the day's high and low and the rain outlook as a sentence
  (`rain likely 6 PM-3 AM (75%)`) with the expected total; sunset or sunrise; then a
  24-hour graph: **chance of rain** when rain is likely (30% or more in some hour),
  otherwise **temperature**. The temperature graph's scale is fixed at 20°F to 100°F and
  never changes, so a bar the same height always means the same temperature (60° is
  marked in the middle). When space runs out, a line drops whole pieces (humidity first)
  instead of cutting words off. The station and how old its reading is are on the right
  of the frame. If the station's last reading is more than 2 hours old, this hour's
  forecast stands in and the frame says so in amber (`forecast now · KCEU station 14h
  old`). `p` shows the rain graph full screen.
- **DATA LINK and GPS** (top right, one third): two boxes, each one big colored word you
  can read from the driver's seat, with one line of detail under it. See below.
- **alerts** (middle, the biggest part): one list of short reports, ▲ official and
  ● reported, as described above.
- **location** (bottom, the same size as alerts): labeled rows that always sit in the
  same place, so your eye knows where to look. On a short window the least useful rows
  drop out; the rest don't move.

  | Row | Shows |
  |---|---|
  | HERE | distance and direction from the nearest town, county; "entered 2m ago" for 10 minutes after you cross a county or state line |
  | SPEED | speed, heading as an arrow and compass point, and the **grade** you're on (level, climbing 5%, descending 7%), worked out from the GPS altitude over the last third of a mile. "stopped 4 min" when you're parked |
  | ELEV | elevation, a graph of the last few miles, the low and high, and a **long descent** warning when you've dropped 500+ ft in 2 miles (go easy on the brakes) |
  | AHEAD | the next towns on your heading, with straight-line distance and a rough time at your speed |
  | ER | the nearest emergency room |
  | TRIP | distance, time moving and top speed since follow mode started, and the way back to where you started |
  | GRID | map grid (MGRS / US National Grid, what search and rescue and topo maps use), then the coordinates |

  With a typed-in place it shows HERE, the forecast area's elevation, ER and GRID. The
  county's weather radio code is at the bottom of the `a` screen.

The town and county come from an offline list, so they work with no signal. Near a
county line the county can be the neighboring one.

### DATA LINK and GPS boxes

**DATA LINK** is the internet connection, judged from every request the app makes (NWS,
news, wildfires, 511):

| Word | Color | Means |
|---|---|---|
| ONLINE | green | requests are getting through; the detail says when the last one did |
| LIMITED | amber | connected, but NWS itself is answering with errors |
| STALE | amber | nothing has come back in 15 minutes |
| OFFLINE | red | requests are failing (no connection, timeouts); the detail says for how long |
| CHECKING | gray | just started, nothing back yet |

**GPS** is the position source:

| Word | Color | Means |
|---|---|---|
| 3D FIX | green | full fix, elevation included; the detail shows signal bars, satellites and accuracy, and the port is in the title. "fix 30s old" in amber means positions stopped coming in |
| 2D FIX | amber | position but no reliable elevation (too few satellites); grade and elevation are less trustworthy |
| GPS FIX | green | a fix from a receiver that doesn't report 2D or 3D |
| ROUGH | amber | no GPS, using Windows location; the detail shows how rough |
| NO FIX | red | the GPS is talking but hasn't locked onto satellites yet (normal for a minute or so after plugging in, longer indoors) |
| NO GPS | red | no receiver found, it was unplugged, or the port can't be opened |
| OFF | gray | follow mode is off (press `f`) |

**One beep** when either box changes to a new word and holds it for 5 seconds (so a
quick flicker stays quiet): losing the internet, getting it back, the GPS locking on or
losing its fix. Warnings beep twice, so the two are easy to tell apart. Starting up and
turning follow mode on or off yourself don't beep.

On a window narrower than 70 columns the boxes become two colored tags in the top bar
(`LINK ONLINE` `GPS 3D FIX`), and they're always in the top bar on the `a` and `p`
screens.

## When things go wrong

- **No GPS found:** the GPS box says NO GPS. Check the puck is plugged in; on Linux,
  check the `dialout` group. Windows location is used meanwhile (GPS box: ROUGH).
- **Windows location is off:** the GPS box says NO GPS and the location box says why.
- **No internet, or NWS is down:** the DATA LINK box says OFFLINE (or LIMITED if only
  NWS is failing); last saved weather stays on screen with its age; the top bar says
  "data link down" and the alerts box says DATA LINK DOWN once alerts are 10 minutes old.
- **Window too small:** it tells you. The minimum is 40x14.
- **The full screen fails for any reason:** it prints the text report instead and
  writes the details to `crash.log` in the data folder.
- **Text report any time:** `python weather.py --plain` (follow mode waits up to 20 seconds
  for a position, then falls back to the last known spot).
- **Odd-looking lines or boxes:** `--ascii`. **Odd colors:** `--colors 16` (or `8`, `mono`).

## Command-line options

```
--follow                 follow your live location
--plain                  print a text report and exit
--place "salem sc"       use a town or ZIP (turns follow off)
--coords "34.68, -82.84" use exact coordinates (turns follow off)
--home DIR               keep settings and saved data in DIR (e.g. on a USB stick)
--display auto|day|night|normal
--ascii                  plain ASCII instead of box-drawing characters
--colors auto|256|16|8|mono
--version
```

## Settings

Settings and saved data live in `%APPDATA%\weather-tui\` on Windows and
`~/.config/weather-tui/` on Linux (or wherever `--home` points). In `config.json`:

| Setting | Default | What it does |
|---|---|---|
| `"follow"` | `false` | follow mode on at start (the `f` key sets this) |
| `"gps_port"` | `"auto"` | `"auto"`, a port like `"COM3"` or `"/dev/ttyACM0"`, `"off"`, or `"tcp://PHONE-IP:PORT"` for a phone app that sends NMEA over the network |
| `"gps_baud"` | `9600` | serial speed (USB pucks usually ignore it; older ones may need `4800`) |
| `"windows_location"` | `true` | use Windows location as the backup |
| `"headlines"` | `true` | include news reports (searches Google News) |
| `"wildfires"` | `true` | include wildfires near you |
| `"space_weather"` | `true` | include solar storms strong enough to affect GPS |
| `"keys_511"` | `{"GA": "", "NC": ""}` | developer keys for Georgia 511 and North Carolina DriveNC road incidents; leave empty to skip |
| `"display"` | `"auto"` | `"auto"`, `"day"`, `"night"` or `"normal"` (the `d` key sets this) |
| `"refresh_minutes"` | `15` | how often weather refreshes when you're not moving (minimum 5) |
| `"contact"` | `""` | NWS asks apps to identify themselves; this one sends `(weather-tui/2.4.2, personal weather app)`. Add an email only if you want NWS to be able to reach you. |

Other files: `cache.json` (last good weather, safe to delete) and `crash.log` (only if
something went wrong). A damaged settings or cache file is set aside as `*.bad-<number>`
and the app starts fresh.

## Where the data comes from

- **Weather and alerts:** National Weather Service, https://api.weather.gov (public domain).
  Requests carry your coordinates rounded to about 100 m. Nearby storm warnings are asked
  for by state (`SC,GA,NC`), not by position.
- **Nearest ER** (`wxtui/hospitals.tsv.gz`, about 4,400 hospitals): Medicare's Hospital
  General Information list (CMS, public domain), downloaded November 2026, placed using the
  town/ZIP list below. Rebuild it with `tools/build_hospitals.py`.
- **Sunrise, sunset, moon and map grid:** calculated on the laptop (the standard sunrise
  equation, the mean lunar cycle, and the WGS84 UTM/MGRS formulas).
- **News reports:** Google News search results (https://news.google.com). The search
  carries county, city and state names, never coordinates. Headlines belong to the outlets
  that wrote them; the app shows a short report, the outlet and age, and the headline on
  the `a` screen.
- **Wildfires:** National Interagency Fire Center, current wildland fire locations
  (WFIGS, public, updated every 5 minutes). The request carries a box about 140 miles
  across around you.
- **Space weather:** NOAA Space Weather Prediction Center scales (public).
- **Road incidents:** Georgia 511 (511ga.org) and North Carolina DriveNC (drivenc.gov)
  event feeds, with your developer key, only while you're within about 25 miles of that
  state.
- **Position:** your USB GPS is read on the laptop and goes nowhere else. Windows location
  (the backup) comes from Microsoft's location service, which works out the position from
  nearby Wi-Fi networks; set `"windows_location": false` if you'd rather not use it.
- **Town and ZIP list** (`wxtui/places.tsv.gz`, 39,130 places and 33,791 ZIP codes):
  US Census Bureau places and 2022 ZIP Code Tabulation Areas, taken from the npm
  packages `leaflet-city-autocomplete-usa` 1.4.0 (MIT; it also adds some smaller
  communities from GeoNames, CC BY 4.0) and `us-zips` 2022.9.1 (MIT).
  To rebuild it straight from official Census files, see `tools/build_places.py`.

## How it's put together

Standard library only, on both Windows and Linux.

```
weather.py              start here: options, fallbacks, crash log
wxtui/settings.py       your settings and saved data (config.json, cache.json)
wxtui/nws.py            talking to NWS: requests, retries, reading the replies
wxtui/news.py           news: the search, the safety filter, headlines -> reports
wxtui/feeds.py          wildfires, space weather, state 511 road incidents
wxtui/views.py          saved data -> what the screens show (ages, totals, what beeps)
wxtui/follow.py         follow mode timing: when to refresh as you move
wxtui/sun.py            sunrise and sunset, offline
wxtui/hospitals.py      nearest ER, offline
wxtui/hospitals.tsv.gz  the hospital list
wxtui/theme.py          day, night and normal colors
wxtui/locate.py         live position: USB GPS (NMEA) and Windows location
wxtui/geo.py            distances and compass directions
wxtui/places.py         offline town/ZIP/coordinate lookup and nearest town
wxtui/places.tsv.gz     the town/ZIP list
wxtui/ui.py             the dashboard: what's showing, keys, the main loop
wxtui/panels.py         drawing the boxes (the compact layout)
wxtui/cluster.py        the gauge-cluster layout for big windows, big digits
wxtui/term.py           picks the screen driver
wxtui/screen.py         draws the screen on Windows (Linux uses built-in curses)
wxtui/plain.py          the text report
tests/                  offline tests
tools/build_places.py   rebuilds the town list from Census files
tools/build_hospitals.py rebuilds the hospital list from Medicare's file
```

Only `nws.py`, `news.py` and `feeds.py` reach the internet (`locate.py` also reads a GPS over your
local network if you give it a `tcp://` address). Only `ui.py`, `panels.py`, `cluster.py`,
`term.py` and `screen.py` know about the screen, so everything else can be tested without one.

## Testing

From this folder: `python -m unittest`. It runs offline in about 20 seconds. NWS and
Google News are replaced by a local fake server (`tests/fake_nws.py`) and the GPS by
text written to a pseudo-terminal. The full-screen tests need Linux; the rest run
anywhere.

To try things by hand, these environment variables point the app elsewhere:

| Variable | Does |
|---|---|
| `WEATHER_TUI_API` | NWS address (e.g. the fake server) |
| `WEATHER_TUI_NEWS` | news search address |
| `WEATHER_TUI_FEEDS` | one address for the fake wildfire, space weather and 511 feeds |
| `WEATHER_TUI_HOME` | settings folder, like `--home` |
| `WEATHER_TUI_SCREEN=builtin` | use the Windows screen driver on Linux |
| `WEATHER_TUI_WINLOC_CMD` | command to run instead of the Windows location script |
