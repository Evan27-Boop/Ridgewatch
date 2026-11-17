# Ridgewatch 3.3.1

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
| ALERTS | a master light (worst official warning, ALL CLEAR, or NOT UPDATING) and the list | the warning's own color |
| RADIO | scanner calls as text, once the radio helper exists; until then it says it isn't connected | |
| WEATHER | conditions, high and low, rain outlook, daylight, in words; tap for the 24-hour temperature and rain graphs | amber title when the station is old and the forecast is standing in |
| DATA LINK | ONLINE, LIMITED, STALE, OFFLINE; while offline, the way back to where the internet last answered | |
| GPS | 3D FIX, 2D FIX, NO FIX, NO GPS, OFF | |

Sounds: a double beep for a new official warning; one beep when DATA LINK or GPS
changes. They play through the speakers (`pw-play`, `paplay` or `aplay`, whichever is
installed), or the terminal bell if there's no audio player. At start-up Ridgewatch turns
the system volume all the way up; set `"max_volume": false` in `config.json` to leave it
alone.

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

## Data sources

| What | From | Needs |
|---|---|---|
| Warnings, forecast, conditions | National Weather Service (api.weather.gov) | nothing |
| Wildfires | National Interagency Fire Center | nothing |
| Road incidents | Georgia 511 and North Carolina DriveNC | a free developer key from each state, in `"keys_511": {"GA": "...", "NC": "..."}` |
| Towns, counties, hospitals | bundled files, read once at startup | nothing; they work offline |

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
ridgewatch/sound.py      beeps through the speakers
ridgewatch/power.py      battery level
ridgewatch/backlight.py  screen brightness
ridgewatch/geo.py sun.py theme.py settings.py plain.py   small helpers
tools/                   rebuild the bundled town and hospital files
tests/                   python3 -m unittest
```
