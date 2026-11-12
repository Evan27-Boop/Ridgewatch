"""Which screen driver to use: Python's built-in curses on Linux, the app's
own driver (screen.py) on Windows, so Windows needs no extra packages."""

import os

if os.name == "nt" or os.environ.get("WEATHER_TUI_SCREEN") == "builtin":
    from . import screen as curses
else:
    try:
        import curses
    except ImportError:                 # some stripped-down Pythons ship without it
        from . import screen as curses

BUILTIN = curses.__name__.endswith(".screen")
