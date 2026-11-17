"""Screen brightness. With no desktop running, nothing else changes the backlight, so
Ridgewatch sets it to match the day and night looks.

It writes the kernel's backlight file when that is allowed, and otherwise asks logind
(SetBrightness), which lets the person at the screen change it without root.
"""

import os
import shutil
import subprocess

BASE = "/sys/class/backlight"


def find(base=BASE):
    """(name, folder, max brightness) for the first backlight, or None."""
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for n in names:
        folder = os.path.join(base, n)
        try:
            with open(os.path.join(folder, "max_brightness")) as f:
                top = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if top > 0:
            return n, folder, top
    return None


def level(pct, top):
    """Percent -> the backlight's own units, never fully off."""
    return max(1, int(round(top * max(0, min(100, pct)) / 100.0)))


class Backlight(object):
    def __init__(self, base=BASE, which=shutil.which):
        self.dev = find(base)
        self.busctl = which("busctl")
        self.last = None
        self.proc = None

    def available(self):
        return self.dev is not None

    def set_pct(self, pct):
        """Set brightness; returns True if it was sent. Doesn't wait for logind."""
        if not self.dev or pct == self.last:
            return False
        name, folder, top = self.dev
        value = level(pct, top)
        path = os.path.join(folder, "brightness")
        if os.access(path, os.W_OK):
            try:
                with open(path, "w") as f:
                    f.write("%d\n" % value)
                self.last = pct
                return True
            except OSError:
                pass
        if self.busctl:
            if self.proc is not None:
                self.proc.poll()                 # reap the previous call
            try:
                self.proc = subprocess.Popen(
                    [self.busctl, "call", "org.freedesktop.login1",
                     "/org/freedesktop/login1/session/auto", "org.freedesktop.login1.Session",
                     "SetBrightness", "ssu", "backlight", name, str(value)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
                self.last = pct
                return True
            except OSError:
                pass
        return False
