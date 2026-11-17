# Ridgewatch spec

One page. If a feature isn't covered here, it doesn't go in. Change the spec first,
then the code.

## What it is

A vehicle awareness dashboard that runs in a terminal. It keeps watch while you drive:
official weather warnings, where you are, live GPS gauges, and nearby hazards. Later it
will also show police and fire radio calls as text.

## Where it runs

- **Linux only.** Any Linux machine, including WSL on Windows for testing at a desk.
- **Python 3.8 or newer, standard library only.** Nothing to install.
- **A modern terminal:** 256 colors, UTF-8, and a window of at least 120×38. There are
  no fallbacks for anything less; the app says what it needs and stops.
- **A USB GPS receiver** that speaks NMEA (built around the VK-162). Without one, you
  pick a town by name and the GPS gauges show a dash.
- **Reference display:** a Surface Go 2 running Arch, booting straight into Ridgewatch
  (cage + foot, about 130×42 at font size 13). Touchscreen, battery, speakers.
- **Internet when available.** It must stay useful with none.

## What it must do

1. **Official weather warnings.** NWS alerts over you and on the road ahead, with a
   master warning light and a beep for new warnings.
2. **Where am I.** A position you can read to a 911 dispatcher: nearest town, county,
   map grid, nearest emergency room. A waypoint: distance and direction back to where
   the trip started, a saved place, or where the internet last worked.
3. **Gauges.** Speed (with trip totals), elevation (with the grade and a long-descent
   warning) and compass heading in big type. Fixed spots, and a border color that acts
   as the warning light.
4. **Hazards nearby.** Wildfires (NIFC) and road incidents (state 511 feeds, with your
   own key).
5. **Weather.** A short text tile: conditions now, high and low, when rain starts,
   daylight left. The 24-hour graphs are one tap away.
6. **Honesty about staleness.** Every reading shows its age. Old data is labeled, never
   shown as current and never silently dropped.
7. **Touch first.** Tap a tile to open its details, and a bar of large buttons along
   the bottom for the everyday controls. The keyboard still works as a backup.
8. **Power, sound and brightness.** Shows the battery level and whether it is charging.
   Beeps play through the speakers, not only the terminal bell. Screen brightness follows
   the day and night looks. When outside power is lost (ignition off), it shuts down
   cleanly after 10 minutes unless power comes back or you tap KEEP RUNNING.
9. **Radio (planned, not built).** A separate helper program turns scanner calls into
   text and hands them to the RADIO tile, which holds its place on screen until then.
   Built only after the core is field-tested.

## What it will never do

- **No cloud, accounts or tracking.** Everything runs on the machine. The only network
  traffic is requests to the public data sources above.
- **No input needed while driving.** Everything that matters is visible or audible
  without touching it. Controls stay available at any speed, but while moving a detail
  screen returns to the gauges on its own after 30 seconds without a tap.

## Wording

Messages on screen are professional, clear and concise:

- A status is one capitalized word (ONLINE, STANDBY, NO GPS). The line under it says
  what is true; the next line says what to do about it, if anything.
- Full words, sentence case, no slang, no ellipses, no contractions.
- A message names the real cause. "No location set" is not "data link down".

## Input

Touch first, keyboard as a backup. Tiles open their details; the button bar holds
DISPLAY, REFRESH, FOLLOW GPS, SAVE HERE and PLACES. Every touch control works without
typing. Typing a town name stays keyboard-only. There is no quit button.

## What was cut to get here

Windows support, the built-in Windows screen driver, Windows location, ASCII mode,
16-color and mono modes, every layout below 120×36, the Google News scraper, and space
weather.

## How it gets proven

- **Drive recorder (next to build).** Always on: a rolling log of raw GPS and every
  server reply for the last few days. Recordings from real drives become tests.
- **Every feed checked against the real service** at least once, then kept honest by
  recordings.
- **Tests run offline** against a fake server and a simulated GPS:
  `python3 -m unittest`.

## Roadmap, in order

1. Touch interface, battery level and speaker beeps (3.1.0); brightness and
   shutdown on power loss (3.2.0).
2. Drive recorder and a rolling error log.
3. Check each live feed against the real service; fix what's wrong.
4. Appliance hardening: clock-jump-proof timers, startup self-check.
5. Clean text before drawing (control and double-width characters).
6. Split the main controller into smaller parts.
7. Radio helper and RADIO tile.
