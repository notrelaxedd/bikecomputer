"""
config.py — All hardware pins, paths, and tuneable constants.
Edit this file to adapt to different wiring or preferences.

Runtime-changeable preferences (units, volume, music source, paired
devices) live in settings.py and are persisted to SETTINGS_FILE; the
values here are hardware facts and first-boot defaults.
"""

from pathlib import Path

# ── SPI display (ILI9488, /dev/spidev0.0) ──────────────────────────────────
SPI_BUS        = 0
SPI_DEVICE     = 0           # CS0 → /dev/spidev0.0
SPI_SPEED_HZ   = 32_000_000

# PORTRAIT orientation: 320 wide × 480 tall.
DISPLAY_WIDTH  = 320
DISPLAY_HEIGHT = 480

# GPIO BCM pin numbers
DC_PIN    = 25   # Data/Command
RESET_PIN = 27   # Hardware reset

# MADCTL byte. Bits: MY=0x80 MX=0x40 MV=0x20 ML=0x10 BGR=0x08 MH=0x04.
# MV (row/column exchange) is what selects landscape, so portrait clears it.
#   0x00 → portrait
#   0x40 → portrait, mirrored horizontally
#   0x80 → portrait, mirrored vertically
#   0xC0 → portrait, rotated 180°   (use this if the buttons end up at the top)
# Colour order is corrected in software (display._image_to_bgr888), so
# leave the BGR bit clear.
MADCTL = 0x00

# ── GPIO buttons ────────────────────────────────────────────────────────────
# Three momentary buttons, each wired between the GPIO and GND.
# Internal pull-ups are enabled, so a press reads LOW.
#
#   UP     — move up a list / previous screen
#   SELECT — confirm; long-press opens the menu or goes back
#   DOWN   — move down a list / next screen
#
# Pins avoid SPI (7-11), the display's DC/RST (25/27) and the GPS UART (14/15).
BUTTON_UP_PIN     = 17
BUTTON_SELECT_PIN = 5
BUTTON_DOWN_PIN   = 6

BUTTON_DEBOUNCE     = 0.04   # seconds; edges closer together are ignored
BUTTON_LONG_PRESS   = 0.6    # seconds held before a press counts as "long"
BUTTON_REPEAT_DELAY = 0.45   # hold UP/DOWN this long before auto-repeat starts
BUTTON_REPEAT_RATE  = 0.12   # seconds between auto-repeats

# ── gpsd ────────────────────────────────────────────────────────────────────
GPSD_HOST = "localhost"
GPSD_PORT = 2947

# ── GPS quality thresholds ──────────────────────────────────────────────────
MIN_SATELLITES  = 0
MAX_HDOP        = 999.0
MAX_SPEED_MS    = 25.0        # reject fixes implying > 90 km/h
AUTOPAUSE_MS    = 1.5         # m/s  (~5.4 km/h)

# ── Map / mbtiles ───────────────────────────────────────────────────────────
MBTILES_PATH     = Path("/opt/bikecomputer/neohio.mbtiles")
MAP_ZOOM         = 15         # tile zoom level for basemap
TILE_CACHE_ROWS  = 3          # 3×3 tile cache
MAP_FPS          = 2
DATA_FPS         = 5
MENU_FPS         = 10         # menus need to feel responsive to button presses

# ── Ride data storage ───────────────────────────────────────────────────────
VAR_DIR          = Path("/var/bikecomputer")
RIDES_DIR        = VAR_DIR / "rides"
GPX_FLUSH_EVERY  = 10         # fixes between gpxpy flush-to-disk
SETTINGS_FILE    = VAR_DIR / "settings.json"

# ── Music ───────────────────────────────────────────────────────────────────
# Drop .mp3 / .m4a / .flac / .ogg / .wav files in here (scp, USB, Samba…).
MUSIC_DIR        = VAR_DIR / "music"
MUSIC_EXTENSIONS = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac")
MPV_BINARY       = "mpv"
MPV_IPC_SOCKET   = Path("/tmp/bikecomputer-mpv.sock")
DEFAULT_VOLUME   = 70         # 0-100

# ── Spotify (optional; requires Spotify Premium) ────────────────────────────
# The Pi runs librespot as a Spotify Connect target and this app drives
# playback through the Spotify Web API.  Run
#   python -m bikecomputer.music.spotify auth
# once to link the account.  Only the Client ID is needed (PKCE flow),
# and it is not a secret.
SPOTIFY_CLIENT_ID    = ""                      # fill in from developer.spotify.com
SPOTIFY_TOKEN_FILE   = VAR_DIR / "spotify_tokens.json"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8766/callback"
SPOTIFY_DEVICE_NAME  = "BikeComputer"          # librespot's --name
SPOTIFY_SCOPES       = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing streaming"
)

# librespot caches its OAuth credential blob here.  Without a cache it
# cannot log in unattended, so the Pi would only ever be visible over
# zeroconf to devices on the same LAN -- never to the Web API.  Do the
# one-time sign-in with:
#     librespot -n BikeComputer -c /var/bikecomputer/librespot -j
SPOTIFY_CACHE_DIR      = VAR_DIR / "librespot"
# Audio backend for librespot. Empty uses whatever it was built with.
# "pulseaudio" routes cleanly through pipewire-pulse, but only if librespot
# was compiled with that backend -- check `librespot --backend ?`.
SPOTIFY_BACKEND        = ""
SPOTIFY_LIBRESPOT_ARGS: list[str] = []   # anything else you want to pass

# ── Bluetooth ───────────────────────────────────────────────────────────────
BT_ADAPTER          = "hci0"
BT_SCAN_SECONDS     = 20       # how long a discovery session runs
BT_CONNECT_TIMEOUT  = 25.0

# Standard GATT assigned numbers
UUID_HEART_RATE_SERVICE  = "0000180d-0000-1000-8000-00805f9b34fb"
UUID_HEART_RATE_MEASURE  = "00002a37-0000-1000-8000-00805f9b34fb"
UUID_CYCLING_CADENCE_SVC = "00001816-0000-1000-8000-00805f9b34fb"
UUID_CSC_MEASUREMENT     = "00002a5b-0000-1000-8000-00805f9b34fb"
UUID_A2DP_SINK           = "0000110b-0000-1000-8000-00805f9b34fb"

HR_STALE_SECONDS  = 10.0       # blank the HR field after this long with no data

# ── Strava OAuth ────────────────────────────────────────────────────────────
# Client secret read from env var STRAVA_CLIENT_SECRET at runtime.
STRAVA_CLIENT_ID    = "YOUR_CLIENT_ID"   # replace; not secret
STRAVA_TOKEN_FILE   = VAR_DIR / "strava_tokens.json"
STRAVA_AUTH_URL     = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL    = "https://www.strava.com/oauth/token"
STRAVA_UPLOAD_URL   = "https://www.strava.com/api/v3/uploads"
STRAVA_REDIRECT_URI = "http://localhost:8765/callback"

# ── Route file ──────────────────────────────────────────────────────────────
ROUTE_FILE           = RIDES_DIR / "route.gpx"
CUE_DISTANCE_M       = 100    # alert when within 100 m of a heading change

# ── Display colours (RGB tuples) ────────────────────────────────────────────
CLR_BG           = (0, 0, 0)
CLR_TEXT         = (255, 255, 255)
CLR_DIM          = (120, 120, 120)
CLR_FAINT        = (60, 60, 60)
CLR_ACCENT       = (255, 200, 0)
CLR_OK           = (60, 200, 90)
CLR_WARN         = (240, 140, 40)
CLR_ERR          = (220, 60, 60)
CLR_HR           = (220, 50, 50)
CLR_CADENCE      = (50, 180, 220)
CLR_PANEL        = (24, 24, 24)
CLR_SELECT       = (255, 200, 0)     # highlight bar in menus
CLR_SELECT_TEXT  = (0, 0, 0)
CLR_MAP_BG       = (30, 30, 30)
CLR_ROAD_MAJOR   = (200, 200, 200)
CLR_ROAD_MINOR   = (120, 120, 120)
CLR_WATER        = (30, 80, 140)
CLR_LANDUSE      = (40, 60, 40)
CLR_ROUTE        = (255, 80, 0)
CLR_POS_MARKER   = (0, 200, 255)

# ── Fonts (bundled with Pillow or system) ───────────────────────────────────
# Set to None to fall back to Pillow's built-in bitmap font.
FONT_PATH        = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_PATH_BOLD   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
