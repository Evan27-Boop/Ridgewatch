"""Beeps through the speakers. A terminal bell is silent in many setups (foot under
cage plays nothing), so Ridgewatch makes its own tones and plays them with whatever
audio player the system has. With none, it falls back to the terminal bell.

Two sounds: a single beep (a status change) and a double beep (a new warning). Both are
written fresh at start-up at near full scale, and the system volume is turned all the
way up (alsa-utils' amixer) unless "max_volume" is false in config.json.
"""

import math
import os
import shutil
import struct
import subprocess
import wave

PLAYERS = ("pw-play", "paplay", "aplay")   # PipeWire, PulseAudio, plain ALSA
RATE = 22050


FREQ, BEEP_MS, GAP_MS = 880, 180, 300
VOLUME = 0.95                       # of full scale: as loud as it goes without clipping


def make_tone(path, freq=FREQ, ms=BEEP_MS, volume=VOLUME, count=1, gap_ms=GAP_MS):
    """Write `count` short sine beeps with soft edges (no click), `gap_ms` of silence
    between them, as a 16-bit mono WAV."""
    n = int(RATE * ms / 1000.0)
    edge = max(1, int(RATE * 0.01))
    beep = bytearray()
    for i in range(n):
        env = min(1.0, i / float(edge), (n - 1 - i) / float(edge))
        v = int(32767 * volume * env * math.sin(2 * math.pi * freq * i / RATE))
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
        self.paths = {"status": os.path.join(folder, "beep.wav"),
                      "alert": os.path.join(folder, "alert.wav")}
        if self.player:
            try:                               # always rewritten, so a new sound takes effect
                os.makedirs(folder, exist_ok=True)
                make_tone(self.paths["status"])
                make_tone(self.paths["alert"], count=2)
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

    def beep(self, kind="status"):
        """Play the single (status) or double (alert) beep."""
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
            path = self.paths.get(kind, self.paths["status"])
            cmd = [self.exe, "-q", path] if self.player == "aplay" else [self.exe, path]
            if self._run(cmd):
                return True
            self.player = None
        self.bell()
        return False
