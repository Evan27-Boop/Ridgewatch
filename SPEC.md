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
- **A modern terminal:** 256 colors, UTF-8, and a window of at least 120×36. There are
  no fallbacks for anything less; the app says what it needs and stops.
- **A USB GPS receiver** that speaks NMEA (built around the VK-162). Without one, you
  pick a town by name and the GPS gauges show a dash.
- **Internet when available.** It must stay useful with none.

## What it must do

1. **Official weather warnings.** NWS alerts over you and on the road ahead, with a
   master warning light and a beep for new warnings.
2. **Where am I.** A position you can read to a 911 dispatcher: nearest town, county,
   map grid, nearest emergency room, and the way back to where the internet last worked.
3. **Gauges.** Speed, grade and elevation in big digits, plus trip totals. Fixed spots,
   fixed scales, and a border color that acts as the warning light.
4. **Hazards nearby.** Wildfires (NIFC) and road incidents (state 511 feeds, with your
   own key).
5. **Weather.** Current conditions, the next 24 hours, rain outlook, daylight left.
6. **Honesty about staleness.** Every reading shows its age. Old data is labeled, never
   shown as current and never silently dropped.
7. **Radio (planned, not built).** A separate helper program turns scanner calls into
   text and hands them to a RADIO tile. Built only after the core is field-tested.

## What it will never do

- **No cloud, accounts or tracking.** Everything runs on the machine. The only network
  traffic is requests to the public data sources above.
- **No key presses needed while driving.** Everything that matters is visible or audible
  without touching it. Menus lock while moving.

## Wording

Messages on screen are professional, clear and concise:

- A status is one capitalized word (ONLINE, STANDBY, NO GPS). The line under it says
  what is true; the next line says what to do about it, if anything.
- Full words, sentence case, no slang, no ellipses, no contractions.
- A message names the real cause. "No location set" is not "data link down".

## Input

Keyboard now. Touch (tap a tile to open its details) comes with the tablet setup.

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

1. Drive recorder and a rolling error log.
2. Check each live feed against the real service; fix what's wrong.
3. Appliance hardening: auto-restart, clock-jump-proof timers, startup self-check.
4. Clean text before drawing (control and double-width characters).
5. Split the main controller into smaller parts.
6. Touch: tap a tile to open its details.
7. Radio helper and RADIO tile.
