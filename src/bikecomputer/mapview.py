"""
mapview.py — mbtiles reader, 3×3 tile cache, Mapbox Vector Tile renderer.

Opens the .mbtiles SQLite file, decodes MVT tiles via mapbox-vector-tile,
draws landuse / water / roads in order, caches the rendered PIL Images.

Also handles optional route.gpx: draws it as an orange polyline and
detects proximity to significant heading changes for cue banners.
"""

from __future__ import annotations
import logging
import math
import sqlite3
from typing import Optional

from PIL import Image, ImageDraw

from . import config

log = logging.getLogger(__name__)

# MVT tiles contain geometry in a 4096×4096 coordinate space
_MVT_EXTENT = 4096

# Road layer rendering order and widths (pixels at zoom 14, 256 px tile)
_ROAD_STYLES: dict[str, tuple[int, tuple[int, int, int]]] = {
    "motorway":    (5, (220, 180,  80)),
    "trunk":       (4, (220, 160,  60)),
    "primary":     (4, (200, 200, 100)),
    "secondary":   (3, (180, 180, 180)),
    "tertiary":    (2, (150, 150, 150)),
    "residential": (2, config.CLR_ROAD_MINOR),
    "service":     (1, config.CLR_ROAD_MINOR),
    "path":        (1, (100, 100, 100)),
    "cycleway":    (2, (80,  180,  80)),
}


def _tile_for_ll(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    tx = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    ty = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return tx, ty


def _ll_to_pixel(lat: float, lon: float, zoom: int, tx: int, ty: int, tile_px: int) -> tuple[float, float]:
    """Pixel position of lat/lon within the tile at (tx, ty)."""
    n = 2 ** zoom
    x_frac = (lon + 180.0) / 360.0 * n - tx
    lat_r = math.radians(lat)
    y_frac = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n - ty
    return x_frac * tile_px, y_frac * tile_px


def _mvt_to_pixel(coords, ox: float, oy: float, scale: float):
    """Convert MVT geometry coordinates to pixel positions."""
    return [(x * scale + ox, y * scale + oy) for x, y in coords]


class MapView:
    """
    Maintains a 3×3 cache of rendered 256×256 tile images around the
    current GPS position.  Call render_around() each frame.
    """

    def __init__(self) -> None:
        self._db: Optional[sqlite3.Connection] = None
        self._cache: dict[tuple[int, int, int], Image.Image] = {}  # (z, tx, ty) → Image
        self._route_pixels: list[tuple[int, int]] = []   # lat/lon pairs of route
        self._cue_points: list[tuple[float, float]] = [] # (lat, lon) of heading changes
        self._tile_px = 256

        self._open_db()
        self._load_route()

    def _open_db(self) -> None:
        if not config.MBTILES_PATH.exists():
            log.warning("mbtiles not found at %s — map screen disabled", config.MBTILES_PATH)
            return
        try:
            self._db = sqlite3.connect(str(config.MBTILES_PATH), check_same_thread=False)
            log.info("Opened mbtiles: %s", config.MBTILES_PATH)
        except sqlite3.Error as exc:
            log.error("Cannot open mbtiles: %s", exc)

    @property
    def available(self) -> bool:
        return self._db is not None

    def _load_route(self) -> None:
        if not config.ROUTE_FILE.exists():
            return
        try:
            import gpxpy
            with open(config.ROUTE_FILE) as f:
                gpx = gpxpy.parse(f)
            points = [
                (pt.latitude, pt.longitude)
                for track in gpx.tracks
                for seg in track.segments
                for pt in seg.points
            ]
            self._route_pixels = points
            self._cue_points = self._find_cue_points(points)
            log.info("Loaded route with %d points, %d cues", len(points), len(self._cue_points))
        except Exception as exc:
            log.warning("Could not load route: %s", exc)

    @staticmethod
    def _find_cue_points(
        points: list[tuple[float, float]],
        threshold_deg: float = 30.0,
        window: int = 5,
    ) -> list[tuple[float, float]]:
        """Return points where heading changes by more than threshold_deg."""
        cues = []
        for i in range(window, len(points) - window):
            lat0, lon0 = points[i - window]
            lat1, lon1 = points[i]
            lat2, lon2 = points[i + window]
            brg1 = math.degrees(math.atan2(lon1 - lon0, lat1 - lat0)) % 360
            brg2 = math.degrees(math.atan2(lon2 - lon1, lat2 - lat1)) % 360
            delta = abs((brg2 - brg1 + 180) % 360 - 180)
            if delta >= threshold_deg:
                cues.append((lat1, lon1))
        return cues

    def nearest_cue(self, lat: float, lon: float) -> Optional[str]:
        """Return a cue banner string if within CUE_DISTANCE_M of a turn."""
        from .ride import _haversine
        for clat, clon in self._cue_points:
            d = _haversine(lat, lon, clat, clon)
            if d <= config.CUE_DISTANCE_M:
                return "Turn ahead"
        return None

    # ── Tile fetching ────────────────────────────────────────────────────────

    def _fetch_tile(self, zoom: int, tx: int, ty: int) -> Optional[Image.Image]:
        key = (zoom, tx, ty)
        if key in self._cache:
            return self._cache[key]

        if self._db is None:
            return None

        # MBTiles uses TMS y (flipped)
        tms_y = (2 ** zoom - 1) - ty
        try:
            row = self._db.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (zoom, tx, tms_y),
            ).fetchone()
        except sqlite3.Error:
            return None

        if not row:
            return None

        tile_img = self._decode_mvt(row[0], zoom, tx, ty)
        if tile_img is None:
            return None

        # Evict cache entries beyond 3×3 = 9 tiles
        if len(self._cache) >= 9:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = tile_img
        return tile_img

    def _decode_mvt(self, data: bytes, zoom: int, tx: int, ty: int) -> Optional[Image.Image]:
        try:
            import gzip
            if data[:2] == b'\x1f\x8b':
                data = gzip.decompress(data)
            import mapbox_vector_tile
            tile = mapbox_vector_tile.decode(data)
        except Exception as exc:
            log.debug("MVT decode error z=%d x=%d y=%d: %s", zoom, tx, ty, exc)
            return None

        img = Image.new("RGB", (self._tile_px, self._tile_px), config.CLR_MAP_BG)
        draw = ImageDraw.Draw(img)
        scale = self._tile_px / _MVT_EXTENT

        # Draw order: landuse → water → roads
        self._draw_layer(draw, tile, "landuse",  scale, fill=config.CLR_LANDUSE)
        self._draw_layer(draw, tile, "water",    scale, fill=config.CLR_WATER)
        self._draw_roads(draw, tile, scale)

        return img

    def _draw_layer(
        self,
        draw: ImageDraw.ImageDraw,
        tile: dict,
        layer_name: str,
        scale: float,
        fill: tuple,
    ) -> None:
        layer = tile.get(layer_name)
        if not layer:
            return
        for feature in layer.get("features", []):
            geom = feature.get("geometry", {})
            gtype = geom.get("type")
            coords = geom.get("coordinates", [])
            if gtype == "Polygon":
                for ring in coords:
                    pts = _mvt_to_pixel(ring, 0, 0, scale)
                    if len(pts) >= 3:
                        draw.polygon(pts, fill=fill)
            elif gtype == "MultiPolygon":
                for poly in coords:
                    for ring in poly:
                        pts = _mvt_to_pixel(ring, 0, 0, scale)
                        if len(pts) >= 3:
                            draw.polygon(pts, fill=fill)

    def _draw_roads(self, draw: ImageDraw.ImageDraw, tile: dict, scale: float) -> None:
        layer = tile.get("transportation") or tile.get("roads")
        if not layer:
            return
        for feature in layer.get("features", []):
            props = feature.get("properties", {})
            road_class = props.get("class", props.get("type", "residential"))
            width, colour = _ROAD_STYLES.get(road_class, (1, config.CLR_ROAD_MINOR))

            geom = feature.get("geometry", {})
            gtype = geom.get("type")
            coords = geom.get("coordinates", [])

            if gtype == "LineString":
                pts = _mvt_to_pixel(coords, 0, 0, scale)
                if len(pts) >= 2:
                    draw.line(pts, fill=colour, width=width)
            elif gtype == "MultiLineString":
                for line in coords:
                    pts = _mvt_to_pixel(line, 0, 0, scale)
                    if len(pts) >= 2:
                        draw.line(pts, fill=colour, width=width)

    # ── Composite render ─────────────────────────────────────────────────────

    def render_around(
        self,
        lat: float,
        lon: float,
        width: int,
        height: int,
        zoom: int,
    ) -> Optional[Image.Image]:
        """
        Stitch a 3×3 grid of tiles and crop a width×height viewport
        centred on lat/lon.
        """
        tx, ty = _tile_for_ll(lat, lon, zoom)
        px_frac, py_frac = _ll_to_pixel(lat, lon, zoom, tx, ty,
                                         self._tile_px)[:2]
        # Actually recompute to get sub-tile offsets
        n = 2 ** zoom
        x_abs = (lon + 180.0) / 360.0 * n
        lat_r = math.radians(lat)
        y_abs = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
        sub_x = (x_abs - tx) * self._tile_px
        sub_y = (y_abs - ty) * self._tile_px

        # 3×3 mosaic
        mosaic_size = self._tile_px * 3
        mosaic = Image.new("RGB", (mosaic_size, mosaic_size), config.CLR_MAP_BG)

        for dy in range(-1, 2):
            for dx in range(-1, 2):
                t = self._fetch_tile(zoom, tx + dx, ty + dy)
                if t:
                    mosaic.paste(t, ((dx + 1) * self._tile_px,
                                     (dy + 1) * self._tile_px))

        # Draw route overlay on mosaic
        if self._route_pixels:
            self._draw_route_on_mosaic(mosaic, lat, lon, zoom, tx, ty)

        # Centre of mosaic corresponds to (tx,ty) origin + sub-tile offset
        centre_x = self._tile_px + sub_x   # position within mosaic
        centre_y = self._tile_px + sub_y

        crop_x0 = int(centre_x - width  // 2)
        crop_y0 = int(centre_y - height // 2)
        crop_x1 = crop_x0 + width
        crop_y1 = crop_y0 + height

        # Clamp to mosaic bounds
        crop_x0 = max(0, min(crop_x0, mosaic_size - width))
        crop_y0 = max(0, min(crop_y0, mosaic_size - height))

        return mosaic.crop((crop_x0, crop_y0,
                            crop_x0 + width, crop_y0 + height))

    def _draw_route_on_mosaic(
        self,
        mosaic: Image.Image,
        centre_lat: float,
        centre_lon: float,
        zoom: int,
        tx: int,
        ty: int,
    ) -> None:
        draw = ImageDraw.Draw(mosaic)
        pts = []
        for lat, lon in self._route_pixels:
            n = 2 ** zoom
            x_abs = (lon + 180.0) / 360.0 * n
            lat_r = math.radians(lat)
            y_abs = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
            # position in mosaic (origin = top-left of (tx-1, ty-1) tile)
            mx = (x_abs - (tx - 1)) * self._tile_px
            my = (y_abs - (ty - 1)) * self._tile_px
            pts.append((mx, my))

        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=config.CLR_ROUTE, width=3)
