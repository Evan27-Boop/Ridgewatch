"""Volume: the Surface's volume buttons and keyboard keys, with no desktop running.

Under cage nothing else listens to the volume keys, and the terminal never passes them
on, so a small thread reads them straight from the kernel (/dev/input; the user needs to
be in the "input" group) and sets the sound card's volume with amixer (alsa-utils).

At start-up the sound is unmuted and raised to at least "min_volume" percent (50 by
default) so warnings can't go silent; otherwise the level stays where you left it. On a
desktop (XDG_CURRENT_DESKTOP set) the desktop already handles the keys and this stays off.
"""

import os
import re
import select
import shutil
import struct
import subprocess
import threading
import time

KEY_MUTE, KEY_VOLUMEDOWN, KEY_VOLUMEUP = 113, 114, 115
EV_KEY = 1
EVENT = struct.Struct("llHHi")            # struct input_event: time, type, code, value
STEP = 5                                  # percent per press
CONTROLS = ("Master", "Speaker", "PCM")   # the first one the sound card has


def key_devices(text):
    """/proc/bus/input/devices -> ['event3', ...] for devices that have a volume-up key."""
    out = []
    for block in text.split("\n\n"):
        m_h = re.search(r"^H: Handlers=.*?\b(event\d+)\b", block, re.M)
        m_b = re.search(r"^B: KEY=([0-9a-f ]+)$", block, re.M)
        if not (m_h and m_b):
            continue
        words = m_b.group(1).split()[::-1]          # lowest bits last in the file
        i, bit = KEY_VOLUMEUP // 64, KEY_VOLUMEUP % 64       # 64-bit words
        if i < len(words) and (int(words[i], 16) >> bit) & 1:
            out.append(m_h.group(1))
    return out


def parse_level(text):
    """amixer output -> (percent, on) from the first channel line, or (None, None)."""
    m = re.search(r"\[(\d+)%\](?:.*?\[(on|off)\])?", text or "")
    if not m:
        return None, None
    return int(m.group(1)), (m.group(2) != "off")


def under_desktop():
    return bool(os.environ.get("XDG_CURRENT_DESKTOP"))


class Volume(object):
    """Owns the volume: start-up floor, the key thread, and the last level seen.
    `notify(percent, on)` is called after every change (from the key thread)."""

    def __init__(self, notify=None, which=shutil.which, run=None, floor=50):
        self.amixer = which("amixer")
        self.notify = notify or (lambda pct, on: None)
        self.run = run or self._run
        self.control = None
        self.level, self.on = None, None
        self.reading = False                       # the key thread found a device
        self.problem = ""
        if not self.amixer:
            self.problem = "No volume control (install alsa-utils)"
            return
        for c in CONTROLS:
            pct, on = parse_level(self.run([self.amixer, "sget", c]))
            if pct is not None:
                self.control, self.level, self.on = c, pct, on
                break
        if self.control is None:
            self.problem = "No volume control found on the sound card"
            return
        if floor and (not self.on or self.level < floor):
            self.set("%d%%" % max(self.level, floor), unmute=True)

    @staticmethod
    def _run(cmd):
        try:
            return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=3).stdout.decode("utf-8",
                                                                                      "replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    def set(self, value, unmute=False):
        """value: '70%', '5%+', '5%-' or 'toggle' (mute). Returns (percent, on)."""
        cmd = [self.amixer, "sset", self.control, value]
        if unmute and value != "toggle":
            cmd.append("unmute")
        pct, on = parse_level(self.run(cmd))
        if pct is not None:
            self.level, self.on = pct, on
        return self.level, self.on

    def press(self, code):
        if not self.control:
            return
        if code == KEY_VOLUMEUP:
            self.set("%d%%+" % STEP, unmute=True)
        elif code == KEY_VOLUMEDOWN:
            self.set("%d%%-" % STEP)
        elif code == KEY_MUTE:
            self.set("toggle")
        else:
            return
        self.notify(self.level, self.on)

    # ------------------------------------------------------- the key thread --
    def start(self, devices_file="/proc/bus/input/devices", folder="/dev/input"):
        if self.control and not under_desktop():
            threading.Thread(target=self._loop, args=(devices_file, folder),
                             name="volume-keys", daemon=True).start()

    @staticmethod
    def _names(devices_file):
        try:
            with open(devices_file) as f:
                return key_devices(f.read())
        except OSError:
            return []

    def _open(self, devices_file, folder):
        names = self._names(devices_file)
        self._opened = names
        fds = []
        for n in names:
            try:
                fds.append(os.open(os.path.join(folder, n), os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                self.problem = "Volume keys need permission: add this user to the input group"
        return fds

    def _loop(self, devices_file, folder):
        while True:
            fds = self._open(devices_file, folder)
            self.reading = bool(fds)
            if fds:
                self.problem = ""
            try:
                while fds:
                    ready, _, _ = select.select(fds, [], [], 30)
                    if not ready and self._names(devices_file) != self._opened:
                        break                       # keyboard cover attached or removed
                    for fd in ready:
                        data = os.read(fd, EVENT.size * 32)
                        if not data:
                            raise OSError("device closed")
                        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                            _, _, typ, code, value = EVENT.unpack_from(data, i)
                            if typ == EV_KEY and value in (1, 2):      # press or hold
                                self.press(code)
            except OSError:
                pass                                # the keyboard cover came off: look again
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            time.sleep(5)
