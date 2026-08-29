"""Terrain features derived from public Copernicus DEM tiles."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable

import numpy as np
import planetary_computer
import pystac
import pystac_client
from pystac_client.stac_api_io import StacApiIO
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform_bounds

from .aoi import Region
from .catalog import DEFAULT_STAC_ENDPOINT, DEFAULT_STAC_TIMEOUT


DEM_COLLECTION = "cop-dem-glo-30"
DEM_ASSET = "data"
ASPECT_LABELS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _bbox_parts(bbox: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """Split a WGS84 bbox when it crosses the antimeridian."""

    west, south, east, north = bbox
    if west < east:
        return [bbox]
    return [(west, south, 180.0, north), (-180.0, south, east, north)]


def _intersect_bounds(
    left: float,
    bottom: float,
    right: float,
    top: float,
    dataset_bounds: rasterio.coords.BoundingBox,
) -> tuple[float, float, float, float] | None:
    result = (
        max(left, dataset_bounds.left),
        max(bottom, dataset_bounds.bottom),
        min(right, dataset_bounds.right),
        min(top, dataset_bounds.top),
    )
    if result[0] >= result[2] or result[1] >= result[3]:
        return None
    return result


def _clip_window(window: Window, width: int, height: int) -> Window:
    """Round and clip a raster window to valid dataset pixels."""

    rounded = window.round_offsets().round_lengths()
    col_off = max(0, int(rounded.col_off))
    row_off = max(0, int(rounded.row_off))
    col_end = min(width, int(rounded.col_off + rounded.width))
    row_end = min(height, int(rounded.row_off + rounded.height))
    return Window(col_off, row_off, max(0, col_end - col_off), max(0, row_end - row_off))


def _pixel_spacing_m(
    transform: rasterio.Affine,
    crs: rasterio.crs.CRS,
    center_latitude: float,
) -> tuple[float, float]:
    """Return approximate horizontal pixel spacing in metres."""

    pixel_x = abs(transform.a)
    pixel_y = abs(transform.e)

    if crs.is_geographic:
        dx = pixel_x * 111_320.0 * math.cos(math.radians(center_latitude))
        dy = pixel_y * 111_132.0
    else:
        try:
            unit_factor = float(crs.linear_units_factor[1])
        except (AttributeError, IndexError, TypeError, ValueError):
            unit_factor = 1.0
        dx = pixel_x * unit_factor
        dy = pixel_y * unit_factor

    if dx <= 0 or dy <= 0:
        raise ValueError("DEM pixel spacing must be positive")
    return dx, dy


def _aspect_counts(aspect: np.ndarray, valid: np.ndarray) -> np.ndarray:
    counts = np.zeros(8, dtype=np.int64)
    values = aspect[valid]
    if values.size == 0:
        return counts

    sector = np.floor((values + 22.5) / 45.0).astype(np.int64) % 8
    counts += np.bincount(sector, minlength=8)[:8]
    return counts


@dataclass
class _TerrainAccumulator:
    elevation_count: int = 0
    elevation_sum: float = 0.0
    elevation_min: float = math.inf
    elevation_max: float = -math.inf
    slope_count: int = 0
    slope_sum: float = 0.0
    slope_max: float = 0.0
    steep_count: int = 0
    flat_count: int = 0
    aspect_counts: np.ndarray = field(default_factory=lambda: np.zeros(8, dtype=np.int64))

    def add(self, elevation: np.ndarray, dx_m: float, dy_m: float) -> None:
        valid_elevation = np.isfinite(elevation)
        values = elevation[valid_elevation]
        if values.size == 0:
            return

        self.elevation_count += int(values.size)
        self.elevation_sum += float(values.sum(dtype=np.float64))
        self.elevation_min = min(self.elevation_min, float(values.min()))
        self.elevation_max = max(self.elevation_max, float(values.max()))

        if elevation.shape[0] < 3 or elevation.shape[1] < 3:
            return

        # Array rows increase southward. The second argument below therefore
        # represents southward spacing; the aspect formula accounts for that
        # orientation while slope magnitude is unaffected by axis direction.
        dz_south, dz_east = np.gradient(elevation, dy_m, dx_m)
        slope = np.degrees(np.arctan(np.hypot(dz_east, dz_south)))
        aspect = (np.degrees(np.arctan2(-dz_east, dz_south)) + 360.0) % 360.0

        interior = np.zeros(elevation.shape, dtype=bool)
        interior[1:-1, 1:-1] = True
        valid_slope = interior & np.isfinite(slope) & valid_elevation
        slope_values = slope[valid_slope]
        if slope_values.size == 0:
            return

        self.slope_count += int(slope_values.size)
        self.slope_sum += float(slope_values.sum(dtype=np.float64))
        self.slope_max = max(self.slope_max, float(slope_values.max()))
        self.steep_count += int(np.count_nonzero(slope_values >= 30.0))

        flat = valid_slope & (slope < 0.5)
        self.flat_count += int(np.count_nonzero(flat))
        aspect_valid = valid_slope & ~flat & np.isfinite(aspect)
        self.aspect_counts += _aspect_counts(aspect, aspect_valid)

    def result(self) -> dict[str, Any]:
        if self.elevation_count == 0:
            raise ValueError("DEM contains no valid elevation pixels for the AOI")

        elevation_mean = self.elevation_sum / self.elevation_count
        result: dict[str, Any] = {
            "sample_count": self.elevation_count,
            "elevation_m": {
                "min": round(self.elevation_min, 3),
                "max": round(self.elevation_max, 3),
                "mean": round(elevation_mean, 3),
            },
            "relief_m": round(self.elevation_max - self.elevation_min, 3),
        }

        if self.slope_count:
            aspect_total = int(self.aspect_counts.sum())
            aspect_pct = {
                label: round(float(count) * 100.0 / aspect_total, 3) if aspect_total else 0.0
                for label, count in zip(ASPECT_LABELS, self.aspect_counts, strict=True)
            }
            result["slope_deg"] = {
                "mean": round(self.slope_sum / self.slope_count, 3),
                "max": round(self.slope_max, 3),
                "fraction_ge_30deg": round(self.steep_count / self.slope_count, 6),
            }
            result["aspect_pct"] = aspect_pct
            result["flat_fraction"] = round(self.flat_count / self.slope_count, 6)
        else:
            result["slope_deg"] = None
            result["aspect_pct"] = None
            result["flat_fraction"] = None

        return result


def summarize_dem_array(elevation: np.ndarray, dx_m: float, dy_m: float) -> dict[str, Any]:
    """Summarize one DEM array; exposed primarily for deterministic tests."""

    if elevation.ndim != 2:
        raise ValueError("DEM array must be two-dimensional")
    accumulator = _TerrainAccumulator()
    accumulator.add(np.asarray(elevation, dtype=np.float64), dx_m, dy_m)
    return accumulator.result()


def _search_dem_items(
    client: pystac_client.Client,
    bbox_parts: Iterable[tuple[float, float, float, float]],
) -> list[pystac.Item]:
    items: dict[str, pystac.Item] = {}
    for bbox in bbox_parts:
        for item in client.search(collections=[DEM_COLLECTION], bbox=list(bbox)).items():
            items[item.id] = item
    return list(items.values())


def _read_item_into_accumulator(
    item: pystac.Item,
    bbox_parts: Iterable[tuple[float, float, float, float]],
    accumulator: _TerrainAccumulator,
) -> None:
    signed = planetary_computer.sign(item)
    asset = signed.assets.get(DEM_ASSET)
    if asset is None:
        raise ValueError(f"DEM item {item.id!r} has no {DEM_ASSET!r} asset")

    try:
        dataset = rasterio.open(asset.href)
    except (OSError, RasterioIOError) as exc:
        raise OSError(f"Failed to open DEM asset for {item.id}: {exc}") from exc

    with dataset as src:
        if src.crs is None:
            raise ValueError(f"DEM item {item.id!r} has no CRS")

        for bbox in bbox_parts:
            projected = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
            intersection = _intersect_bounds(*projected, src.bounds)
            if intersection is None:
                continue

            window = _clip_window(from_bounds(*intersection, transform=src.transform), src.width, src.height)
            if window.width < 1 or window.height < 1:
                continue

            masked = src.read(1, window=window, masked=True).astype(np.float64)
            elevation = np.asarray(masked.filled(np.nan))
            window_transform = src.window_transform(window)
            center_latitude = (bbox[1] + bbox[3]) / 2.0
            dx_m, dy_m = _pixel_spacing_m(window_transform, src.crs, center_latitude)
            accumulator.add(elevation, dx_m=dx_m, dy_m=dy_m)


def query_terrain(
    region: Region,
    endpoint: str = DEFAULT_STAC_ENDPOINT,
) -> dict[str, Any]:
    """Derive terrain summary features from Copernicus DEM GLO-30."""

    stac_io = StacApiIO(timeout=DEFAULT_STAC_TIMEOUT)
    client = pystac_client.Client.open(endpoint, stac_io=stac_io)
    bbox_parts = _bbox_parts(region.bbox)
    items = _search_dem_items(client, bbox_parts)
    if not items:
        raise ValueError(f"No {DEM_COLLECTION} tiles intersect the region")

    accumulator = _TerrainAccumulator()
    for item in items:
        _read_item_into_accumulator(item, bbox_parts, accumulator)

    return {
        "region": region.as_dict(),
        "source": {
            "endpoint": endpoint,
            "collection": DEM_COLLECTION,
            "asset": DEM_ASSET,
            "tile_count": len(items),
        },
        "terrain": accumulator.result(),
        "method": {
            "slope": "finite-difference gradient using approximate metre pixel spacing",
            "aspect": "downslope azimuth, 8 sectors; slopes below 0.5 degrees treated as flat",
            "relief": "maximum minus minimum sampled elevation within the AOI",
        },
    }
