# Phase 1 test kit: decoding on the Surface

Goal: see whether the Surface can decode the Pickens site (024) with one RTL-SDR V3,
and how hard it works doing it. Nothing in Ridgewatch changes yet.

- `talkgroups.csv`: the talkgroups you picked (from RadioReference; may be dated).
- `config.json`: a starting trunk-recorder config for the Pickens site.

## 1. Check the dongle

```
sudo pacman -S rtl-sdr
rtl_test -t
```

It should say "Found 1 device(s)" and name the R820T tuner. If it says the device is
busy, unplug and replug it.

## 2. Install trunk-recorder

It's in the AUR and builds from source. On the Surface's Pentium this can take an hour
or more, and needs a few GB of free space (`df -h ~`).

```
sudo pacman -S --needed base-devel git
git clone https://aur.archlinux.org/trunk-recorder.git
cd trunk-recorder && makepkg -si
```

## 3. Run it

```
mkdir -p ~/radio/calls
cp ~/ridgewatch/docs/radio/config.json ~/ridgewatch/docs/radio/talkgroups.csv ~/radio/
cd ~/radio && trunk-recorder --config=config.json
```

Do this over SSH while Ridgewatch runs on the screen, so the test is honest.

**Good signs in its log:** a steady control channel decode rate (messages per second
around 30–40), then "Starting P25 Recorder" lines as calls happen, and new `.wav` and
`.json` files appearing in `~/radio/calls`.

**If nothing decodes:**
- Change `"modulation"` from `"fsk4"` to `"qpsk"` and restart.
- Try `"gain"` 30 or 49.
- Indoors, sit by a window facing the tower; the basic antenna is the weak link.

## 4. Measure

In a second SSH window:

```
top -d 5                                      # trunk-recorder's CPU %
cat /sys/class/thermal/thermal_zone*/temp     # temperatures, in thousandths of a degree C
ls ~/radio/calls/*/*/*/ | wc -l               # how many calls so far
```

Let it run through a busy evening, then send me: the CPU % it settles at, the highest
temperature, how many calls it caught, and any errors that repeat in the log (for
example "no source covering" means a channel outside the dongle's range).

**Passes if:** CPU averages under about 50%, the temperature stays well under 90 °C,
and calls keep arriving.
