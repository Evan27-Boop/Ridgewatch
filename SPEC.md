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

1. **Official alerts, from a strict source list.** Warnings over you and on the road
   ahead, nearby hazards, and a short list of national events. Only the sources in the
   table below are ever asked; a new source goes into this spec first. The ALERTS tile
   is a table (WHAT, WHERE, WHEN, DO) with the worst local item as a colored bar on top,
   local items first and NATIONAL under a divider.
2. **Where am I.** A position you can read to a 911 dispatcher: nearest town, county,
   map grid, nearest emergency room. A waypoint: distance and direction back to where
   the trip started, a saved place, or where the internet last worked.
3. **Gauges.** Speed (with trip totals), elevation (with the grade and a long-descent
   warning) and compass heading in big type. Fixed spots, and a border color that acts
   as the warning light.
4. **Hazards nearby.** Storm damage and flooding reports, power outages, wildfires,
   earthquakes and road incidents, each only within a set distance (see the table).
5. **Weather.** A short text tile: conditions now, high and low, when rain starts,
   daylight left. The 24-hour graphs are one tap away.
6. **Honesty about staleness.** Every reading shows its age. Old data is labeled, never
   shown as current and never silently dropped.
7. **Touch first.** Tap a tile to open its details, and a bar of large buttons along
   the bottom for the everyday controls. The keyboard still works as a backup.
8. **Power, sound and brightness.** Shows the battery level and whether it is charging.
   Sounds play through the speakers at full volume (see Sounds). Screen brightness
   follows the day and night looks. When outside power is lost (ignition off), it shuts down
   cleanly after 10 minutes unless power comes back or you tap KEEP RUNNING.
9. **Radio (planned, not built).** A separate helper program turns scanner calls into
   text and hands them to the RADIO tile, which holds its place on screen until then.
   Built only after the core is field-tested.

## Sources (the strict list)

| Source | Scope | Shows when |
|---|---|---|
| NWS alerts (api.weather.gov) | local | over you, or a severe warning nearby or ahead |
| NWS Local Storm Reports (api.weather.gov) | local | damage, flooding, tornado, large hail within 25 mi, last 6 h |
| FEMA IPAWS public feed (apps.fema.gov) | local | evacuations, shelter orders, 911 outages and other public-safety alerts for your county or state; a national alert anywhere |
| Duke Energy outages (Duke's public ArcGIS layer) | local | 250 or more customers out within 15 mi |
| NIFC wildfires | local | within 50 mi |
| USGS earthquakes | local / national | magnitude 2.5+ within 100 mi; nationally 5.5+ in the US or 7.0+ anywhere |
| State 511 (GA, NC; your own key) | local | crashes and closures within 25 mi |
| FAA airport status (nasstatus.faa.gov) | national | ground stops at 5+ airports, or any ground stop, delay program or closure at your airports (GSP, CLT, ATL by default) |

Nothing else: no news, no social media, no company status pages. DHS terrorism bulletins
were left out because DHS publishes no machine-readable feed. Crashes and police or fire
activity in South Carolina come later from the radio helper.

## Sounds

Two sounds, used the same way everywhere. A **beep** (high, clean) is about where you
are; a **buzz** (low, rough) is about somewhere else or the machine itself. Twice means
act on it.

| Sound | Means |
|---|---|
| beep beep | a new warning or emergency that covers you or is ahead of you |
| beep | any other new local item (advisory, storm report, outage, fire, quake) |
| buzz | a new national item |
| buzz buzz | the machine: DATA LINK or GPS changed, outside power lost |

At most one sound of each kind per update, and items already seen never sound again.

## Radio contract

The radio is a guest: it can fail in any way without touching the rest.

1. **Separate programs.** The decoder (trunk-recorder) is its own service, restarted
   by the system if it crashes. Ridgewatch never starts, imports or talks to it.
2. **One way, files only.** The decoder writes one audio file and one description file
   per call into a calls folder; Ridgewatch only reads that folder. No network
   connections or shared memory between them, and no new libraries in Ridgewatch.
3. **A fixed format.** A call is accepted only if its description parses, has every
   required field (talkgroup, start time, length, frequency) and its audio file exists
   and is finished. Anything else is skipped and counted, never guessed at.
4. **Strict limits.** Only talkgroups on your list are shown. Text is cleaned of control
   characters and cut to length. Oversized files are ignored; a fixed number of calls
   is kept in memory.
5. **Contained failure.** All radio code is one module on its own thread; every error is
   caught. The worst a radio fault can do is make the RADIO tile say RADIO ERROR and why.
6. **Budgets.** The decoder runs at lower priority with processor and memory caps, so
   the dashboard always wins. Audio is deleted after 24 hours; the folder has a size cap.
7. **Off switch.** `"radio": false` (the default) means no radio code runs at all.
8. **Tests first.** Recorded sample calls (good, broken, half-written, huge, odd
   characters) and deliberate failures (decoder killed, folder missing, disk full).

Radio never enters ALERTS and never beeps (until text exists, and then only on
keywords). The plan, in phases with a test before each, is `docs/RADIO.md`.

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

Volume: the tablet's volume buttons and the keyboard's volume keys work with no desktop
running, and each change shows the new level. At start-up the sound is unmuted and
raised to at least 50%, so warnings can't go silent; a MUTED tag stays in the top bar
while it's muted.

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

Reliability before features. Radio Phase 1 is a test on the Surface (no code) and can
run alongside any of this.

1. Done: touch, battery and speaker sounds (3.1); brightness and shutdown on power loss
   (3.2); new tiles (3.3); strict alert sources (3.4).
2. Drive recorder and a rolling error log.
3. Check each live feed against the real service, using the recorder; fix what's wrong.
4. Clean text before drawing (control and double-width characters).
5. Appliance hardening: clock-jump-proof timers, startup self-check.
6. Split the main controller into smaller parts.
7. Radio Phase 2: calls and audio in the RADIO tile, under the radio contract (only
   after Phase 1 passes).
8. Radio Phases 3 and 4: text on a separate radio box; other states.
