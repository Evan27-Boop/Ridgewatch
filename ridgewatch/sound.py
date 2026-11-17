"""Beeps through the speakers. A terminal bell is silent in many setups (foot under
cage plays nothing), so Ridgewatch makes its own tones and plays them with whatever
audio player the system has. With none, it falls back to the terminal bell.

Two sounds, used the same way everywhere (SPEC.md, Sounds). A beep (high, clean) is
about where you are; a buzz (low, rough) is about somewhere else or the machine. Twice
means act on it:

    warning   beep beep   a new warning or emergency over you or ahead
    local     beep        any other new local item
    national  buzz        a new national item
    system    buzz buzz   DATA LINK or GPS changed, outside power lost

They're written fresh at start-up at near full scale, and the system volume is turned
all the way up (alsa-utils' amixer) unless "max_volume" is false in config.json.
"""

import math
import os
import shutil
import struct
import subprocess
import wave

PLAYERS = ("pw-play", "paplay", "aplay")   # PipeWire, PulseAudio, plain ALSA
RATE = 22050


BEEP_HZ, BUZZ_HZ = 1200, 220      # small tablet speakers carry 1-3 kHz best; the buzz's
GAP_MS = 150                       # harmonics make it audible even where 220 Hz isn't
VOLUME = 0.95                      # of full scale: as loud as it goes without clipping
KINDS = {   # kind: (buzz?, count, ms)
    "warning": (False, 2, 170), "local": (False, 1, 170),
    "national": (True, 1, 320), "system": (True, 2, 200),
}


def _wave(i, freq, buzz):
    t = 2 * math.pi * freq * i / RATE
    if not buzz:
        return math.sin(t)
    # a band-limited square wave: odd harmonics, a rough "buzz"
    return sum(math.sin(k * t) / k for k in (1, 3, 5, 7, 9, 11)) / 1.15


def make_tone(path, freq=BEEP_HZ, ms=170, volume=VOLUME, count=1, gap_ms=GAP_MS, buzz=False):
    """Write `count` short beeps (sine) or buzzes (square-ish) with soft edges (no click),
    `gap_ms` of silence between them, as a 16-bit mono WAV."""
    n = int(RATE * ms / 1000.0)
    edge = max(1, int(RATE * 0.01))
    beep = bytearray()
    for i in range(n):
        env = min(1.0, i / float(edge), (n - 1 - i) / float(edge))
        v = int(32767 * volume * env * max(-1.0, min(1.0, _wave(i, freq, buzz))))
        beep += struct.pack("<h", v)
    gap = b"\x00\x00" * int(RATE * gap_ms / 1000.0)
    frames = bytearray()
    for k in range(count):
        frames += (gap if k else b"") + beep
    tmp = path + ".tmp"
    w = wave.open(tmp, "wb")
    try:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    finally:
        w.close()
    os.replace(tmp, path)


class Speaker(object):
    """Plays the beep without waiting for it. `bell` is called when there's no player
    or the player fails to start."""

    def __init__(self, folder, bell, which=shutil.which, max_volume=True):
        self.bell = bell
        self.procs = []
        self.failures = 0
        found = [(p, which(p)) for p in PLAYERS]
        self.player, self.exe = next(((p, e) for p, e in found if e), (None, None))
        self.paths = {k: os.path.join(folder, "sound-%s.wav" % k) for k in KINDS}
        if self.player:
            try:                               # always rewritten, so a new sound takes effect
                os.makedirs(folder, exist_ok=True)
                for kind, (buzz, count, ms) in KINDS.items():
                    make_tone(self.paths[kind], BUZZ_HZ if buzz else BEEP_HZ, ms,
                              count=count, buzz=buzz)
            except OSError:
                self.player = None
        amixer = which("amixer")
        if max_volume and amixer:
            for control in ("Master", "Speaker"):     # whichever the sound card has
                self._run([amixer, "-q", "sset", control, "100%", "unmute"])

    def _run(self, cmd):
        try:
            self.procs.append(subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True))
            return True
        except OSError:
            return False

    def beep(self, kind="system"):
        """Play one of the four sounds (KINDS)."""
        still = []
        for p in self.procs:                   # reap finished ones; a player that keeps
            rc = p.poll()                      # failing (no sound server) is dropped
            if rc is None:
                still.append(p)
            elif rc != 0 and p.args[0] == self.exe:
                self.failures += 1             # (amixer failing on a control doesn't count)
        self.procs = still
        if self.failures >= 2:
            self.player = None
        if self.player and len(self.procs) < 6:
            path = self.paths.get(kind, self.paths["system"])
            cmd = [self.exe, "-q", path] if self.player == "aplay" else [self.exe, path]
            if self._run(cmd):
                return True
            self.player = None
        self.bell()
        return False
