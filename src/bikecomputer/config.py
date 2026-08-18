"""
config.py — All hardware pins, paths, and tuneable constants.
Edit this file to adapt to different wiring or preferences.
"""

from pathlib import Path

# ── SPI display (ILI9488, /dev/spidev0.0) ──────────────────────────────────
SPI_BUS        = 0
SPI_DEVICE     = 0           # CS0 → /dev/spidev0.0
SPI_SPEED_HZ   = 32_000_000
DISPLAY_WIDTH  = 480
DISPLAY_HEIGHT = 320

# GPIO BCM pin numbers
DC_PIN    = 25   # Data/Command
RESET_PIN = 27   # Hardware reset

# MADCTL byte: MV(0x20) | MX(0x40) | BGR(0x08) = landscape, BGR colour order.
# If colours are inverted swap to 0x28; if image is mirrored try 0x68.
MADCTL = 0x20

# ── GPIO user button ────────────────────────────────────────────────────────
BUTTON_PIN       = 17        # BCM; pulled-up, active-low
BUTTON_DEBOUNCE  = 0.05      # seconds

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

# ── Ride data storage ───────────────────────────────────────────────────────
RIDES_DIR        = Path("/var/bikecomputer/rides")
GPX_FLUSH_EVERY  = 10         # fixes between gpxpy flush-to-disk

# ── Strava OAuth ────────────────────────────────────────────────────────────
# Client secret read from env var STRAVA_CLIENT_SECRET at runtime.
STRAVA_CLIENT_ID    = "YOUR_CLIENT_ID"   # replace; not secret
STRAVA_TOKEN_FILE   = Path("/var/bikecomputer/strava_tokens.json")
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
CLR_ACCENT       = (255, 200, 0)
CLR_HR           = (220, 50, 50)
CLR_CADENCE      = (50, 180, 220)
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
