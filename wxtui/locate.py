"""Where am I? Live position for follow mode, from (best first):

1. A USB GPS receiver ("puck"). It shows up as a serial port (COM3 on
   Windows, /dev/ttyACM0 on Linux) and sends plain-text NMEA sentences.
   The port is found automatically. A phone app that sends NMEA over the
   network also works: set "gps_port" to "tcp://PHONE-IP:PORT".
2. Windows' own location (Wi-Fi / internet based, rough), read through the
   PowerShell that comes with Windows. Nothing to install.

Each source runs on its own background thread and can fail on its own.
Standard library only.
"""

import atexit
import glob
import math
import os
import re
import socket
import subprocess
import threading
import time

GPS_FRESH = 5.0          # seconds a GPS fix stays current
WIN_FRESH = 90.0         # Windows location updates slowly
KNOTS_TO_MPH = 1.15078
M_TO_FT = 3.28084


class Fix(object):
    __slots__ = ("lat", "lon", "acc_m", "alt_m", "speed_mph", "course", "sats",
                 "source", "time", "mode")

    def __init__(self, lat, lon, source, acc_m=None, alt_m=None, speed_mph=None,
                 course=None, sats=None, t=None, mode=None):
        self.lat, self.lon, self.source = lat, lon, source
        self.mode = mode                      # 2 or 3 (2D/3D fix) when the GPS says
        self.acc_m, self.alt_m = acc_m, alt_m
        self.speed_mph, self.course, self.sats = speed_mph, course, sats
        self.time = time.time() if t is None else t



def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


# ---------------------------------------------------------------- NMEA ---

def nmea_checksum_ok(line):
    if "*" not in line:
        return True                                   # some devices omit it
    body, _, given = line[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given[:2], 16)
    except ValueError:
        return False


def _coord(value, hemi):
    """'3441.0042','N' -> 34.68340."""
    if not value or "." not in value:
        return None
    try:
        dot = value.index(".")
        deg = float(value[: dot - 2])
        minutes = float(value[dot - 2:])
    except ValueError:
        return None
    v = deg + minutes / 60.0
    if hemi in ("S", "W"):
        v = -v
    return v


class NmeaState(object):
    """Combines GGA (position, altitude, satellites), RMC (speed, heading) and GSA
    (2D or 3D fix)."""

    def __init__(self):
        self.lat = self.lon = None
        self.alt_m = None
        self.sats = None
        self.hdop = None
        self.speed_mph = None
        self.course = None
        self.valid = False
        self.mode = None
        self.last_pos = 0.0
        self.last_sentence = 0.0

    def feed(self, line, now=None):
        """Feed one sentence. Returns True if it produced a fresh position."""
        now = time.time() if now is None else now
        line = line.strip()
        if not line.startswith("$") or not nmea_checksum_ok(line):
            return False
        f = line.split("*")[0].split(",")
        kind = f[0][3:] if len(f[0]) >= 6 else ""
        self.last_sentence = now
        if kind == "GGA" and len(f) >= 10:
            quality = f[6]
            if quality in ("", "0"):
                self.valid = False
                return False
            lat, lon = _coord(f[2], f[3]), _coord(f[4], f[5])
            if lat is None or lon is None:
                return False
            self.lat, self.lon = lat, lon
            self.sats = int(f[7]) if f[7].isdigit() else self.sats
            self.hdop = _num(f[8])
            self.alt_m = _num(f[9])
            self.valid = True
            self.last_pos = now
            return True
        if kind == "GSA" and len(f) >= 3:
            self.mode = int(f[2]) if f[2] in ("1", "2", "3") else None
            return False
        if kind == "RMC" and len(f) >= 9:
            if f[2] != "A":
                self.valid = False
                return False
            lat, lon = _coord(f[3], f[4]), _coord(f[5], f[6])
            if lat is None or lon is None:
                return False
            self.lat, self.lon = lat, lon
            kn = _num(f[7])
            self.speed_mph = kn * KNOTS_TO_MPH if kn is not None else None
            self.course = _num(f[8])
            self.valid = True
            self.last_pos = now
            return True
        return False

    def fix(self):
        if not self.valid or self.lat is None:
            return None
        acc = self.hdop * 5.0 if self.hdop else None     # rough: HDOP x ~5 m
        course = self.course if (self.speed_mph or 0) >= 2 else None
        return Fix(self.lat, self.lon, "gps", acc_m=acc, alt_m=self.alt_m,
                   speed_mph=self.speed_mph, course=course, sats=self.sats,
                   t=self.last_pos, mode=self.mode if self.mode in (2, 3) else None)


# ------------------------------------------------------------ serial ports --

def candidate_ports():
    """Serial ports that might be a GPS, most likely first."""
    if os.name == "nt":
        try:
            import winreg
        except ImportError:
            return []
        ports = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        except OSError:
            return []
        i = 0
        while True:
            try:
                dev, com, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            dev_l = str(dev).lower()
            if "bth" in dev_l or "bluetooth" in dev_l:
                continue                       # Bluetooth ports can hang when opened
            usb = any(s in dev_l for s in ("usbser", "silabser", "prolific", "vcp", "ch34", "usb"))
            ports.append((0 if usb else 1, str(com)))
        winreg.CloseKey(key)
        return [p for _, p in sorted(ports)]
    found = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
    found += sorted(glob.glob("/dev/serial/by-id/*gps*")) + sorted(glob.glob("/dev/serial/by-id/*GPS*"))
    return found


def _open_port(name, baud):
    """Open a serial port for reading as a plain file (no extra packages)."""
    if name.lower().startswith("tcp://"):
        host, _, port = name[6:].rstrip("/").rpartition(":")
        s = socket.create_connection((host, int(port)), timeout=6)
        s.settimeout(15)
        return _SocketReader(s)
    if os.name == "nt":
        path = name if name.startswith("\\\\.\\") else "\\\\.\\" + name
        port = name.replace("\\\\.\\", "")
        try:
            subprocess.run(["mode", port + ":", "BAUD=%d" % baud, "PARITY=n", "DATA=8", "STOP=1"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass                               # USB GPS pucks ignore the speed anyway
        f = open(path, "rb", buffering=0)
        _windows_read_timeouts(f)
        return f
    fd = os.open(name, os.O_RDONLY | os.O_NOCTTY)
    try:
        import termios
        attrs = termios.tcgetattr(fd)
        speed = getattr(termios, "B%d" % baud, termios.B9600)
        attrs[0] = termios.IGNPAR                       # iflag
        attrs[1] = 0                                    # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0                                    # lflag: raw
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN] = 0                      # return after 1 s even if silent
        attrs[6][termios.VTIME] = 10
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except (ImportError, OSError, Exception):
        pass                                            # a pty or odd device: read anyway
    return os.fdopen(fd, "rb", buffering=0)


def _windows_read_timeouts(f):
    """Make reads on a COM port return within a second, data or not."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class COMMTIMEOUTS(ctypes.Structure):
            _fields_ = [("ReadIntervalTimeout", wintypes.DWORD),
                        ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                        ("ReadTotalTimeoutConstant", wintypes.DWORD),
                        ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                        ("WriteTotalTimeoutConstant", wintypes.DWORD)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
        handle = msvcrt.get_osfhandle(f.fileno())
        k32.SetCommTimeouts(handle, ctypes.byref(COMMTIMEOUTS(50, 0, 1000, 0, 0)))
    except Exception:
        pass                                   # reads may block longer; still works


class _SocketReader(object):
    def __init__(self, s):
        self.s = s

    def read(self, n):
        data = self.s.recv(n)
        if not data:
            raise OSError("connection closed")
        return data

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def _read_lines(stream, on_line, should_stop, max_seconds=None):
    """Read NMEA text lines from a stream until it fails or should_stop()."""
    buf = b""
    start = last_data = time.time()
    while not should_stop():
        now = time.time()
        if max_seconds is not None and now - start > max_seconds:
            return
        chunk = stream.read(64)
        if not chunk:
            if now - last_data > 15:
                raise OSError("device stopped sending")
            time.sleep(0.05)
            continue
        last_data = now
        buf += chunk
        if len(buf) > 8192:
            buf = buf[-1024:]
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            on_line(line.decode("ascii", "replace"))


def _looks_like_gps(name, baud, wait=4.0):
    """Open a port briefly and see whether NMEA comes out (in a thread: some ports hang)."""
    result = {"ok": False}

    def probe():
        try:
            s = _open_port(name, baud)
        except Exception:
            return
        try:
            def on_line(line):
                if re.match(r"^\$(G[PNLAB]|BD)(GGA|RMC|GSA|GSV|VTG|GLL)", line.strip()):
                    result["ok"] = True
            _read_lines(s, on_line, lambda: result["ok"], max_seconds=wait)
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(wait + 0.5)
    return result["ok"]


class GpsReader(object):
    """Background thread: find the GPS, read it, reconnect if it's unplugged."""

    def __init__(self, port="auto", baud=9600, candidates=candidate_ports):
        self.port_setting = (port or "auto").strip()
        self.baud = baud
        self.candidates = candidates
        self.state = NmeaState()
        self.lock = threading.Lock()
        self.status = "looking for a GPS receiver"
        self.port = None
        self._stop = False
        self.thread = threading.Thread(target=self._run, name="gps", daemon=True)
        self.thread.start()

    def stop(self):
        self._stop = True

    def current(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            f = self.state.fix()
        if f and now - f.time <= GPS_FRESH:
            return f
        return None

    def describe(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            st = self.state
            if self.port and now - st.last_sentence <= GPS_FRESH:
                if st.valid and now - st.last_pos <= GPS_FRESH:
                    return "GPS on %s" % self.port
                return "GPS on %s: no satellite fix yet" % self.port
        return self.status

    def _set(self, status, port=None):
        with self.lock:
            self.status = status
            self.port = port

    def _run(self):
        fails = 0
        while not self._stop:
            if self.port_setting.lower() == "auto":
                port = None
                for cand in self.candidates():
                    if self._stop:
                        return
                    if _looks_like_gps(cand, self.baud):
                        port = cand
                        break
                if not port:
                    self._set("no GPS receiver found")
                    time.sleep(10)
                    continue
            else:
                port = self.port_setting
            try:
                stream = _open_port(port, self.baud)
            except Exception as e:
                fails += 1
                self._set("GPS on %s: can't open (%s)" % (port, e.__class__.__name__))
                time.sleep(min(10, 2 * fails))
                continue
            fails = 0
            self._set("GPS on %s: waiting for data" % port, port)

            def on_line(line):
                with self.lock:
                    self.state.feed(line)

            try:
                _read_lines(stream, on_line, lambda: self._stop)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
            self._set("GPS unplugged or stopped (%s)" % port)
            time.sleep(2)


# ------------------------------------------------------- Windows location --

# Uses the location service built into Windows through Windows PowerShell 5.1
# (included with Windows 10/11). Prints one line every 5 seconds:
#   fix LAT LON ACCURACY_M ALTITUDE_M SPEED_M_S COURSE      (NaN = unknown)
#   status STATUS PERMISSION
WINDOWS_LOCATION_PS = r"""
$ErrorActionPreference = 'Stop'
$out = [Console]::Out
try { Add-Type -AssemblyName System.Device }
catch { $out.WriteLine('status Unavailable Unknown'); $out.Flush(); exit 3 }
$inv = [Globalization.CultureInfo]::InvariantCulture
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
try { $w.MovementThreshold = 0 } catch { }
[void]$w.TryStart($false, [TimeSpan]::FromSeconds(1))
while ($true) {
  try {
    $loc = $w.Position.Location
    if ($loc -ne $null -and -not $loc.IsUnknown) {
      $vals = @($loc.Latitude, $loc.Longitude, $loc.HorizontalAccuracy, $loc.Altitude, $loc.Speed, $loc.Course)
      $txt = ($vals | ForEach-Object { ([double]$_).ToString('R', $inv) }) -join ' '
      $out.WriteLine('fix ' + $txt)
    } else {
      $out.WriteLine('status ' + $w.Status + ' ' + $w.Permission)
    }
  } catch {
    $out.WriteLine('status Error Unknown')
  }
  $out.Flush()
  Start-Sleep -Seconds 5
}
"""


def _one_line(script):
    """Join the script into one line so it passes through -Command untouched."""
    out = ""
    for line in (l.strip() for l in script.strip().splitlines()):
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("{") or line.startswith(("}", "catch", "else", "finally")):
            out += " " + line
        else:
            out += "; " + line
    return out


def parse_windows_line(line, now=None):
    """-> ('fix', Fix) or ('status', text) or (None, None)."""
    parts = line.split()
    if not parts:
        return None, None
    if parts[0] == "fix" and len(parts) >= 7:
        lat, lon = _num(parts[1]), _num(parts[2])
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None
        acc, alt, spd, crs = (_num(p) for p in parts[3:7])
        speed = spd * 2.23694 if spd is not None and spd >= 0 else None
        course = crs if (crs is not None and speed is not None and speed >= 2) else None
        return "fix", Fix(lat, lon, "windows", acc_m=acc, alt_m=alt, speed_mph=speed,
                          course=course, t=now)
    if parts[0] == "status":
        st = parts[1] if len(parts) > 1 else ""
        perm = parts[2] if len(parts) > 2 else ""
        if perm == "Denied" or st == "Disabled":
            return "status", ("Windows location is off (Settings > Privacy & security > "
                              "Location: turn on 'Let desktop apps access your location')")
        if st == "Unavailable":
            return "status", "Windows location isn't available on this PC"
        if st == "Initializing":
            return "status", "Windows location starting"
        if st == "NoData":
            return "status", "Windows location has no position yet"
        return "status", "Windows location: " + (st or "unknown")
    return None, None


def windows_location_command():
    test = os.environ.get("WEATHER_TUI_WINLOC_CMD")      # for testing without Windows
    if test:
        return test.split("|")
    root = os.environ.get("SystemRoot", r"C:\Windows")
    ps = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(ps):
        ps = "powershell.exe"
    return [ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            _one_line(WINDOWS_LOCATION_PS)]


class WindowsLocation(object):
    """Keeps one quiet PowerShell running that reports Windows' location."""

    def __init__(self, command=windows_location_command):
        self.command = command
        self.lock = threading.Lock()
        self.fix = None
        self.status = "Windows location starting"
        self.proc = None
        self._stop = False
        atexit.register(self.stop)              # never leave PowerShell running behind
        self.thread = threading.Thread(target=self._run, name="winloc", daemon=True)
        self.thread.start()

    def current(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            f = self.fix
        if f and now - f.time <= WIN_FRESH:
            return f
        return None

    def describe(self):
        with self.lock:
            if self.fix and time.time() - self.fix.time <= WIN_FRESH:
                return "Windows location"
            return self.status

    def stop(self):
        self._stop = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _run(self):
        fails = 0
        while not self._stop:
            try:
                self.proc = subprocess.Popen(
                    self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, bufsize=1, universal_newlines=True,
                    encoding="ascii", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, ValueError) as e:
                with self.lock:
                    self.status = "Windows location unavailable (%s)" % e.__class__.__name__
                return
            started = time.time()
            for line in self.proc.stdout:
                if self._stop:
                    break
                kind, val = parse_windows_line(line)
                with self.lock:
                    if kind == "fix":
                        self.fix = val
                        self.status = "Windows location"
                    elif kind == "status":
                        self.status = val
            code = self.proc.wait()
            if self._stop:
                return
            if code == 3:
                with self.lock:
                    self.status = "Windows location isn't available on this PC"
                return
            fails = 0 if time.time() - started > 120 else fails + 1
            if fails >= 5:
                with self.lock:
                    self.status = "Windows location keeps failing"
                return
            time.sleep(min(60, 5 * (fails + 1)))


# ------------------------------------------------------------- together --

class Locator(object):
    """Best current fix: GPS if it has one, else Windows location."""

    def __init__(self, gps_port="auto", baud=9600, windows=True):
        self.gps = None
        self.win = None
        if (gps_port or "").lower() != "off":
            self.gps = GpsReader(gps_port, baud)
        if windows and (os.name == "nt" or os.environ.get("WEATHER_TUI_WINLOC_CMD")):
            self.win = WindowsLocation()

    def current(self, now=None):
        for src in (self.gps, self.win):
            if src:
                f = src.current(now)
                if f:
                    return f
        return None

    def gps_status(self, now=None):
        """('fix'|'searching'|'none', text) for the GPS box."""
        now = time.time() if now is None else now
        g = self.gps
        if not g:
            return "none", "GPS turned off in settings"
        with g.lock:
            st, port, status = g.state, g.port, g.status
            if port and now - st.last_sentence <= GPS_FRESH:
                if st.valid and now - st.last_pos <= GPS_FRESH:
                    return "fix", port
                return "searching", "no satellite fix yet %s %s" % ("\u00b7", port)
        return "none", status

    def describe(self):
        bits = []
        if self.gps:
            bits.append(self.gps.describe())
        if self.win:
            bits.append(self.win.describe())
        if not bits:
            bits.append("no location sources (GPS turned off in settings)")
        return bits

    def stop(self):
        for src in (self.gps, self.win):
            if src:
                src.stop()


# ------------------------------------------------------------- helpers --

def fmt_accuracy(acc_m):
    if acc_m is None:
        return ""
    ft = acc_m * M_TO_FT
    if ft < 1000:
        return "±%d ft" % max(1, int(round(ft, -1 if ft >= 100 else 0)))
    mi = acc_m / 1609.344
    return "±%.1f mi" % mi if mi < 10 else "±%d mi" % int(round(mi))
