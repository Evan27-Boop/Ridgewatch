# Radio plan

Police, fire and EMS radio in the RADIO tile. Built in phases; each phase starts with a
test on the real hardware, and nothing gets built until its test passes.

## What we know

- **Local system:** Palmetto 800, South Carolina's statewide P25 trunked system (digital,
  calls hop between channels). Upstate agencies were on P25 Phase I as of 2023, the
  easiest kind to decode. Clemson and Greenville are served by different tower sites.
- **Possible change:** Pickens County approved a $10.3M VHF system to move its emergency
  services off Palmetto 800 (Clemson likely staying). Date and technology unconfirmed.
- **Display:** Surface Go 2, Pentium Gold, **no AVX2**. Too slow for live speech-to-text;
  fine for decoding and playing audio (to be confirmed in Phase 1).
- **Antenna:** QWB760 (760–870 MHz). Right for Palmetto 800; deaf to VHF (NOAA Weather
  Radio, VHF fire, ham).
- **Decoder:** trunk-recorder: runs with no window, light on memory, handles P25 Phase I
  and II, saves each call as its own audio file plus a small JSON description. (SDRTrunk
  also decodes DMR/NXDN, which matters if Pickens moves to one of those.)
- **Encrypted talkgroups are never decoded.** Many sheriff and police channels are;
  fire and EMS dispatch usually aren't.

## Decisions (Nov 17, 2026)

- **Hardware:** one RTL-SDR Blog V3 for now (a second only if Phase 1 shows missed
  calls); a basic indoor antenna for Phase 1; a powered USB-C hub (charging pass-through)
  for the GPS and SDR in the Jeep.
- **Counties:** Pickens, Greenville, Anderson, Oconee.
- **Agencies:** fire and EMS dispatch, sheriff and city police, Highway Patrol, Clemson
  University and DNR.
- **Encrypted talkgroups:** shown as activity only (time and talkgroup, no audio).
- **Audio:** off by default; tap a call to hear it; a LIVE switch for scanner mode.
- **Sound:** none for radio until there's text (Phase 3), then only on keywords.
- **Audio kept:** 24 hours.
- **Text (Phase 3):** decided after Phase 2.
- **Trips (Phase 4):** western NC, Tennessee, Georgia, the rest of SC.
- **NOAA warning codes:** not now.

## Tower sites and the one-dongle limit

From RadioReference's Palmetto 800 listing (control channels marked c). One RTL-SDR V3
covers about 2.4 MHz; a site's channels must fit inside that, or calls get missed.

| Site | County | Spread of its channels | One V3? |
|---|---|---|---|
| 024 Pickens | Pickens | 851.125–853.600 (c 853.600), 2.5 MHz | **just about**: all but the lowest channel |
| 050 Seneca | Oconee | 851.075–853.9625 (c 853.100, 853.9625), 2.9 MHz | most |
| 042 Anderson | Anderson | 854.1125–857.2625 (c 854.6125, 857.0125), 3.2 MHz | most |
| 031 Caesars Head | Greenville | 851.3125–854.4625 (c 853.4625, 853.800), 3.2 MHz | most |
| 035 Greenville Simulcast | Greenville | 851.1875–859.9625 (4 control), 8.8 MHz | **no**: a small share |
| 017 Hogback | Greenville | 851.375–860.8375 (c 851.9625, 853.625), 9.5 MHz | **no** |

So one V3 works well around Clemson (Pickens site) and partly elsewhere. Greenville
needs a wideband SDR (about 10 MHz wide, such as an Airspy R2) or several dongles.
Greenville's simulcast site is also the hardest kind to decode. In the listing, Pickens
County's own fire dispatch and sheriff aren't on Palmetto 800 at all, which fits the
county moving them to VHF.

## How the pieces fit

```
RTL-SDR ─▶ trunk-recorder ─▶ calls/ folder: one .wav + one .json per call ─▶ Ridgewatch
           (its own service)                  (later: + transcript)          RADIO tile
```

Ridgewatch never touches the radio. It only reads the calls folder, so a decoder crash
never takes the dashboard down, and Ridgewatch stays standard-library Python. Radio
stays in the RADIO tile, never in ALERTS: scanner traffic isn't an official source.

## Phase 0: gather (you)

- [ ] SDR model (RTL-SDR v3, v4, other).
- [ ] Antenna and NMO mount on the roof, cable to the Surface.
- [ ] Talkgroups you want (I pull the Palmetto 800 list; you tick them): county fire and
      EMS dispatch, sheriff, Clemson and Greenville city, highway patrol.
- [ ] Whether Pickens County still talks on Palmetto 800 (Phase 1 shows this too).

## Phase 1: prove decoding on the Surface (test only)

Steps and a starting config are in `docs/radio/`.

1. Install trunk-recorder (AUR) on the Surface.
2. Configure the Pickens site (024) and the chosen talkgroups (`docs/radio/`).
3. Run it through a busy evening while Ridgewatch runs.

**Pass if:** the processor averages under about 50%, the Surface doesn't throttle from heat,
and most calls on the chosen talkgroups get recorded. One RTL-SDR covers about 2.4 MHz;
if the site's voice channels spread wider, calls get missed. Then the fix is a second
dongle.
**If it fails:** move straight to the radio box (Phase 3) for decoding too.

## Phase 2: RADIO tile, no text yet

Built only after Phase 1 passes and the hardening items ahead of it on the roadmap are
done, and only under the radio contract in SPEC.md.

- **Calls list:** time, talkgroup name, length, newest first; Fire / EMS / Law colored.
- **Activity:** calls per group over the last 15 minutes, so a busy night stands out.
- **Status word:** RECEIVING, NO SDR, DECODER STOPPED, or NO SYSTEM HERE.
- **Tap a call to hear it** through the speakers. A LIVE switch plays each new call
  as it arrives, like a scanner.
- **Site follows the GPS:** Ridgewatch picks the nearest configured site and restarts
  trunk-recorder on it as you drive between Clemson and Greenville.
- **Housekeeping:** calls older than 24 hours are deleted; the folder is capped in size.
- Spec updated first.

## Phase 3: text, on a radio box

- **Hardware:** a small Intel N100 mini PC (has AVX2; about $120–150 used, runs on
  12 V) behind the dash. trunk-recorder moves there; the Surface becomes display only.
  It sends the calls folder to the Surface over a USB or Ethernet cable.
- **Speech to text:** Whisper (whisper.cpp or faster-whisper, base.en) on each call:
  - trim silence first; skip calls under about 1.5 s, cap at about 20 s
  - steer it with local names from the GPS: county, towns, roads, unit names, 10-codes
  - drop low-confidence text and Whisper's stock phrases ("Thanks for watching")
  - flag keywords (structure fire, wreck, trees down, your street) with a local beep
- **On screen:** each call shows its text, marked as machine transcription; tap to
  hear the original and check it.
- **Test before buying:** run Whisper on about 20 recorded calls on your PC. If the
  text is too poor to be useful, stop at Phase 2.

## Phase 4: trips

A short list of systems for the places you actually go (NC's VIPER, Georgia county
systems), chosen by GPS. Anywhere else, RADIO says "No radio system set up for this area".

## Ideas parked

- **NOAA Weather Radio's digital warning codes** decoded exactly (no Whisper), which means
  official warnings with no cell signal. Needs a VHF antenna and a second dongle.
- **Pickens County's new VHF system,** if it goes live: second antenna, and SDRTrunk if
  it's DMR or NXDN.

## Risks

| Risk | What we'd do |
|---|---|
| One dongle misses calls | second dongle, or a wider SDR |
| Surface too hot or slow even for decoding | radio box does decoding too |
| Key talkgroups encrypted | show activity only for those, or leave them out |
| Pickens leaves Palmetto 800 | VHF antenna; maybe SDRTrunk |
| Transcripts too poor | stop at Phase 2: audio and activity are still useful |
