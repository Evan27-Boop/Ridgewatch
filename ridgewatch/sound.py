"""Beeps through the speakers. A terminal bell is silent in many setups (foot under
cage plays nothing), so Ridgewatch makes its own tone and plays it with whatever
audio player the system has. With none, it falls back to the terminal bell.
"""

import math
import os
import shutil
import struct
import subprocess
import wave

PLAYERS = ("pw-play", "paplay", "aplay")   # PipeWire, PulseAudio, plain ALSA
RATE = 22050


def make_tone(path, freq=880, ms=180, volume=0.6):
    """Write a short sine tone with soft edges (no click) as a 16-bit mono WAV."""
    n = int(RATE * ms / 1000.0)
    edge = max(1, int(RATE * 0.01))
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / float(edge), (n - 1 - i) / float(edge))
        v = int(32767 * volume * env * math.sin(2 * math.pi * freq * i / RATE))
        frames += struct.pack("<h", v)
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

    def __init__(self, folder, bell, which=shutil.which):
        self.bell = bell
        self.procs = []
        self.failures = 0
        found = [(p, which(p)) for p in PLAYERS]
        self.player, self.exe = next(((p, e) for p, e in found if e), (None, None))
        self.path = os.path.join(folder, "beep.wav")
        if self.player and not os.path.exists(self.path):
            try:
                os.makedirs(folder, exist_ok=True)
                make_tone(self.path)
            except OSError:
                self.player = None

    def beep(self):
        still = []
        for p in self.procs:                   # reap finished ones; a player that keeps
            rc = p.poll()                      # failing (no sound server) is dropped
            if rc is None:
                still.append(p)
            elif rc != 0:
                self.failures += 1
        self.procs = still
        if self.failures >= 2:
            self.player = None
        if self.player and len(self.procs) < 4:
            cmd = [self.exe, "-q", self.path] if self.player == "aplay" else [self.exe, self.path]
            try:
                self.procs.append(subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True))
                return
            except OSError:
                self.player = None
        self.bell()
