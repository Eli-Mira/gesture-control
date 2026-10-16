"""Shared Serial Experiments Lain // WIRED theme for gesture_control.

Single source of truth for the Navi-terminal aesthetic used by both the
Tkinter settings console (ui.py) and the OpenCV camera HUD (main.py).

Palette ("present day, present time"):
  VOID ....... #050607  window background, near-black with green tint
  PANEL ...... #0b0e11  group panels
  PANEL_EDGE . #1c2b22  panel borders / separators
  ENTRY ...... #0e1512  text entry background
  PHOSPHOR ... #33ff66  primary phosphor green (active / ok)
  DIM ........ #1a7a3a  dim green (inactive track)
  AMBER ...... #ffb000  warnings / gesture labels
  RED ........ #ff2b2b  errors / paused / no-hand
  GHOST ...... #d7e0d8  primary text, slightly green-tinted white
  MUTED ...... #a0b4a5  secondary text (readable on PANEL)
  GRID ....... #14201a  faint grid / scanline tint

OpenCV uses BGR tuples; Tkinter uses hex strings. Both are provided.
"""

from __future__ import annotations

# --- Tkinter hex palette ---
VOID = "#050607"
PANEL = "#0b0e11"
PANEL_EDGE = "#1c2b22"
ENTRY = "#0e1512"
PHOSPHOR = "#33ff66"
DIM = "#1a7a3a"
AMBER = "#ffb000"
RED = "#ff2b2b"
GHOST = "#d7e0d8"
MUTED = "#a0b4a5"
GRID = "#14201a"

# --- OpenCV BGR palette (same hues, BGR order) ---
BGR_VOID = (7, 6, 5)
BGR_PANEL = (17, 14, 11)
BGR_PHOSPHOR = (102, 255, 51)
BGR_DIM = (58, 122, 26)
BGR_AMBER = (0, 176, 255)
BGR_RED = (43, 43, 255)
BGR_GHOST = (216, 224, 215)
BGR_MUTED = (165, 180, 160)
BGR_GRID = (26, 32, 20)

# --- Fonts (Tkinter) ---
FONT_DISPLAY = ("Consolas", 13, "bold")   # titles / tickers
FONT_LABEL = ("Consolas", 9)              # field labels, micro-labels
FONT_LABEL_BOLD = ("Consolas", 9, "bold") # group headers, status
FONT_DATA = ("Consolas", 8)               # tiny readouts
FONT_BUTTON = ("Consolas", 10, "bold")

# --- Copy ---
TICKER = "PRESENT DAY  -  PRESENT TIME  //  YOU ARE CONNECTED TO THE WIRED"
SUBTITLE = "/// NAVI // PERSONALITY LAYER // GESTURE DAEMON"
HINT_KEYS = "q=quit  esc=exit-fullscreen/quit  f=fullscreen  a=on-top"
