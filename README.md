# Ridgewatch 3.4.2

A vehicle awareness dashboard for the terminal. It keeps watch while you drive: official
weather warnings, where you are, live GPS gauges, wildfires and road incidents.
Linux only, Python standard library only, nothing to install.

![The dashboard](docs/screen.png)

`SPEC.md` says what Ridgewatch is for and what it will never do. Read that first.
`docs/SURFACE.md` sets up a Surface Go 2 as a dedicated touchscreen display.

## Run it

```
./rw                        the dashboard (last place you used)
./rw --follow               follow the GPS
./rw --place "clemson sc"   pick a town or ZIP by name
./rw --coords "34.68, -82.84"
./rw --plain                one-shot text report
```

It needs Python 3.8+, a 256-color UTF-8 terminal and a window of at least 120×38.
On WSL, Windows Terminal works as is. Settings and saved data live in
`~/.config/ridgewatch` (or wherever `--home` points).

## The screen

Every tile has a fixed spot. A tile's border color is its warning light. Unknown values
show a dim dash and the reason.

| Tile | Shows | Turns amber or red when |
|---|---|---|
| SPEED | speed, and the trip: miles, time moving and stopped, average and top speed | |
| ELEV | elevation, the grade (climbing or descending, change over the last mile), total climb and descent | amber at 6% grade or on a 2D fix, red at 10% or on a long descent (500+ ft lost in 2 miles) |
| COMPASS | heading in big letters (N, NE...) and degrees; the last heading, dimmed, while stopped | |
| HERE | distance and direction from the nearest town, county, next towns ahead | amber when the GPS is lost |
| GRID | map grid (MGRS / US National Grid), coordinates, accuracy | |
| WAYPOINT | distance and direction to where the trip started; tap to point it at a saved place or the last spot with internet | |
| ER | the nearest emergency room, straight-line | amber when over 30 miles away |
| ALERTS | a table: WHAT, WHERE, WHEN, DO. The worst local item is a colored bar on top (or ALL CLEAR, or why alerts aren't current), then the other local items, then NATIONAL | the worst item's color |
| RADIO | scanner calls as text, once the radio helper exists; until then it says it isn't connected | |
| WEATHER | conditions, high and low, rain outlook, daylight, in words; tap for the 24-hour temperature and rain graphs | amber title when the station is old and the forecast is standing in |
| DATA LINK | ONLINE, LIMITED, STALE, OFFLINE; while offline, the way back to where the internet last answered | |
| GPS | 3D FIX, 2D FIX, NO FIX, NO GPS, OFF | |

**Sounds.** A **beep** (high) is about where you are; a **buzz** (low) is about
somewhere else or the machine. Twice means act on it.

| Sound | Means |
|---|---|
| beep beep | a new warning or emergency over you or ahead |
| beep | any other new local item: advisory, storm report, outage, fire, quake |
| buzz | a new national item |
| buzz buzz | DATA LINK or GPS changed, or outside power was lost |

Each item sounds once; one that disappears for over an hour and comes back sounds again.
They play through the speakers (`pw-play`, `paplay` or `aplay`, whichever is installed),
or the terminal bell if there's no audio player.

**Volume.** The tablet's volume buttons and the keyboard's mute and volume keys work even
with no desktop running (your user needs to be in the `input` group). Each press shows
the new level in the top bar; while muted, a MUTED tag stays there. At start-up the sound
is unmuted and raised to at least 50% (`"min_volume"` in `config.json`; 0 leaves it
alone).

On a machine with a battery, the top bar shows the level and whether it's charging:
green when plugged in, amber on battery, red at 20% or less.

**Power loss.** When outside power goes away (the ignition turns off), Ridgewatch beeps
and counts down 10 minutes, then shuts the machine down cleanly. Power coming back calls
it off, and so does the red **KEEP RUNNING** button that appears during the countdown.
Shutting down needs polkit (`sudo pacman -S polkit`). Set `"shutdown_after_min"` in
`config.json` to change the delay, or to 0 to turn it off.

**Brightness** follows the look: 100% in day, 70% in normal, 20% at night. Change the
numbers in `"brightness"` in `config.json`, or set it to `false` to leave the screen alone.
It works on machines with a backlight (laptops and tablets), with no extra setup.

## Touch

Everything day to day works by tapping. Tap **ALERTS** for the full alert list,
**WEATHER** for the 24-hour graphs, and **WAYPOINT** to choose where it points. The bar along the bottom has **DISPLAY** (day, night,
auto), **REFRESH**, **FOLLOW GPS** (on or off), **SAVE HERE** (saves this spot under the
nearest town's name) and **PLACES** (your saved places and following the GPS, as a list
to tap). Detail screens have **BACK**, and **UP** / **DOWN** where there's a list.

The first tap during a new warning only silences it. Controls work at any speed, but
while you're moving a detail screen goes back to the gauges after 30 seconds without a
tap. There is no quit button.

## Keys

A keyboard still works: `f` follow the GPS on/off · `l` pick a place (and type to search
towns, ZIP codes or coordinates) · `a` alert details · `p` rain graph ·
`d` day / night / auto colors · `r` refresh · `s` save this place with a name you type ·
`1`-`9` saved places · `q` quit.

## GPS

Plug in a USB GPS receiver and tap FOLLOW GPS (or press `f`). The port is found
automatically (`/dev/ttyACM0` and friends). Your user needs permission to use serial
ports: the `uucp` group on Arch, `dialout` on Debian and Ubuntu.

```
sudo usermod -aG uucp $USER       # Arch; then log out and back in
```

For a fixed name that survives replugging, add a udev rule that creates `/dev/ttyGPS`;
Ridgewatch looks there first. To use a different port, set `"gps_port"` in
`~/.config/ridgewatch/config.json`.

On WSL the GPS has to be passed through from Windows with `usbipd`; without it,
pick a place with `l` and the GPS gauges show a dash.

## Data sources: the strict list

Ridgewatch only ever asks the sources below (`ridgewatch/sources.py`); any other address
is refused. A new source goes into `SPEC.md` first.

| What | From | Shows when | Needs |
|---|---|---|---|
| Warnings, forecast, conditions | National Weather Service (api.weather.gov) | over you, or a severe warning nearby or ahead | nothing |
| Storm reports: damage, flooding, tornadoes, large hail | NWS Local Storm Reports | within 25 mi, last 6 hours | nothing |
| Evacuations, shelter orders, 911 outages | FEMA IPAWS public feed | your county or state; national alerts anywhere | nothing |
| Power outages | Duke Energy's public outage layer | 250+ customers out within 15 mi | nothing |
| Wildfires | National Interagency Fire Center | within 50 mi | nothing |
| Earthquakes | USGS | 2.5+ within 100 mi; nationally 5.5+ in the US or 7.0+ anywhere | nothing |
| Road incidents | Georgia 511 and North Carolina DriveNC | within 25 mi | a free developer key from each state, in `"keys_511": {"GA": "...", "NC": "..."}` |
| Flights | FAA airport status | ground stops at 5+ airports, or trouble at your airports | nothing; `"airports"` in `config.json` (GSP, CLT, ATL by default) |
| Towns, counties, hospitals | bundled files, read once at startup | | nothing; they work offline |

Not covered by any database: crashes, police and fire activity in South Carolina (no
public feed; the radio helper is the plan), and DHS terrorism bulletins (no
machine-readable feed).

## Code map

```
rw                       launcher
ridgewatch/cli.py        command line, crash log
ridgewatch/ui.py         the main loop, follow mode, alarms, keys and taps
ridgewatch/cluster.py    the gauge-cluster screen and big digits
ridgewatch/panels.py     colors, the frame, weather and status boxes, detail screens
ridgewatch/views.py      turns saved data into what the screen shows
ridgewatch/nws.py        National Weather Service requests (background thread)
ridgewatch/feeds.py      wildfires and 511 roads (background thread)
ridgewatch/locate.py     the GPS reader (background thread, reconnects by itself)
ridgewatch/follow.py     when to refetch as you move
ridgewatch/places.py     offline towns and ZIP codes
ridgewatch/hospitals.py  offline emergency rooms
ridgewatch/sources.py    the strict source list
ridgewatch/sound.py      beeps and buzzes through the speakers
ridgewatch/volume.py     volume buttons and keys
ridgewatch/power.py      battery level
ridgewatch/backlight.py  screen brightness
ridgewatch/geo.py sun.py theme.py settings.py plain.py   small helpers
tools/                   rebuild the bundled town and hospital files
tests/                   python3 -m unittest
```
