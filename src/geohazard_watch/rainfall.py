"""Rainfall features from NASA GPM IMERG Late Daily V07 on AWS Open Data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
import math
from pathlib import PurePosixPath
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import netCDF4
import numpy as np

from .aoi import Region


AWS_BUCKET_HOST = "https://gesdisc-cumulus-prod-protected.s3.us-west-2.amazonaws.com"
AWS_PRODUCT_PREFIX = "GPM_L3/GPM_3IMERGDL.07"
PRODUCT = "GPM_3IMERGDL"
PRODUCT_VERSION = "07"
PRECIPITATION_VARIABLE = "precipitation"
HTTP_TIMEOUT_SECONDS = 30
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
WINDOW_DAYS = (1, 3, 7)


@dataclass(frozen=True)
class DailyRainfall:
    day: date
    mean_mm: float
    max_mm: float
    sample_count: int
    source_key: str | None = None


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format: {value!r}") from exc


def _request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "GeoHazard-Watch/0.1"})
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_DOWNLOAD_BYTES:
                raise OSError(f"Remote rainfall object is unexpectedly large: {length} bytes")

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise OSError("Remote rainfall object exceeded the download size limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise OSError(f"Failed to fetch rainfall data from {url}: {exc}") from exc


def _list_month_keys(year: int, month: int) -> list[str]:
    prefix = f"{AWS_PRODUCT_PREFIX}/{year:04d}/{month:02d}/"
    query = urlencode({"list-type": "2", "prefix": prefix})
    payload = _request_bytes(f"{AWS_BUCKET_HOST}/?{query}")

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise OSError("AWS rainfall listing returned invalid XML") from exc

    keys = [element.text for element in root.findall(".//{*}Key") if element.text]
    if root.findtext(".//{*}IsTruncated", default="false").lower() == "true":
        raise OSError(f"Rainfall listing was unexpectedly truncated for {year:04d}-{month:02d}")
    return keys


def _find_daily_key(day: date, keys: Iterable[str]) -> str | None:
    token = day.strftime("%Y%m%d")
    matches = [
        key
        for key in keys
        if token in PurePosixPath(key).name and key.endswith(".nc4")
    ]
    return max(matches, default=None)


def _download_key(key: str) -> bytes:
    return _request_bytes(f"{AWS_BUCKET_HOST}/{quote(key, safe='/')}")


def _longitude_mask(lon: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray:
    west, _, east, _ = bbox
    if west < east:
        return (lon >= west) & (lon <= east)
    return (lon >= west) | (lon <= east)


def _extract_aoi_values(payload: bytes, bbox: tuple[float, float, float, float]) -> np.ma.MaskedArray:
    try:
        dataset = netCDF4.Dataset("inmemory.nc", mode="r", memory=payload)
    except OSError as exc:
        raise OSError(f"Downloaded IMERG file is not a readable NetCDF4 dataset: {exc}") from exc

    with dataset:
        for name in ("lon", "lat", PRECIPITATION_VARIABLE):
            if name not in dataset.variables:
                raise ValueError(f"IMERG dataset is missing required variable {name!r}")

        lon = np.asarray(dataset.variables["lon"][:], dtype=np.float64)
        lat = np.asarray(dataset.variables["lat"][:], dtype=np.float64)
        variable = dataset.variables[PRECIPITATION_VARIABLE]

        lon_mask = _longitude_mask(lon, bbox)
        _, south, _, north = bbox
        lat_mask = (lat >= south) & (lat <= north)
        if not np.any(lon_mask) or not np.any(lat_mask):
            raise ValueError("AOI does not intersect any IMERG grid-cell centers")

        values = np.ma.asarray(variable[:], dtype=np.float64)
        dimensions = [name.lower() for name in variable.dimensions]

        if "time" in dimensions:
            time_axis = dimensions.index("time")
            if values.shape[time_axis] != 1:
                raise ValueError("Expected one daily IMERG time step per file")
            values = np.ma.take(values, indices=0, axis=time_axis)
            dimensions.pop(time_axis)

        if "lat" not in dimensions or "lon" not in dimensions:
            raise ValueError(
                f"IMERG precipitation dimensions must include lat and lon, got {variable.dimensions!r}"
            )

        lat_axis = dimensions.index("lat")
        lon_axis = dimensions.index("lon")
        values = np.moveaxis(values, (lat_axis, lon_axis), (0, 1))
        subset = values[np.ix_(lat_mask, lon_mask)]
        return np.ma.masked_invalid(subset)


def _daily_stats(day: date, payload: bytes, bbox: tuple[float, float, float, float], key: str) -> DailyRainfall:
    subset = _extract_aoi_values(payload, bbox)
    compressed = subset.compressed()
    if compressed.size == 0:
        raise ValueError(f"IMERG has no valid precipitation samples for {day.isoformat()}")

    return DailyRainfall(
        day=day,
        mean_mm=float(compressed.mean(dtype=np.float64)),
        max_mm=float(compressed.max()),
        sample_count=int(compressed.size),
        source_key=key,
    )


def summarize_rainfall_days(days: Iterable[DailyRainfall]) -> dict[str, object]:
    """Summarize daily AOI rainfall and 1/3/7-day mean accumulations."""

    ordered = sorted(days, key=lambda item: item.day)
    if len(ordered) < max(WINDOW_DAYS):
        raise ValueError(f"At least {max(WINDOW_DAYS)} daily rainfall values are required")

    for previous, current in zip(ordered, ordered[1:]):
        if current.day - previous.day != timedelta(days=1):
            raise ValueError("Rainfall dates must be consecutive")

    accumulation = {
        f"{window}d": round(sum(item.mean_mm for item in ordered[-window:]), 3)
        for window in WINDOW_DAYS
    }
    daily = [
        {
            "date": item.day.isoformat(),
            "mean_mm": round(item.mean_mm, 3),
            "max_mm": round(item.max_mm, 3),
            "sample_count": item.sample_count,
        }
        for item in ordered
    ]

    return {
        "daily": daily,
        "accumulation_mean_mm": accumulation,
    }


def query_rainfall(region: Region, target_date: str) -> dict[str, object]:
    """Return AOI rainfall features ending at target_date."""

    end_day = _parse_date(target_date)
    requested_days = [end_day - timedelta(days=offset) for offset in range(6, -1, -1)]

    month_cache: dict[tuple[int, int], list[str]] = {}
    daily: list[DailyRainfall] = []
    missing: list[str] = []

    for day in requested_days:
        month_key = (day.year, day.month)
        if month_key not in month_cache:
            month_cache[month_key] = _list_month_keys(*month_key)

        key = _find_daily_key(day, month_cache[month_key])
        if key is None:
            missing.append(day.isoformat())
            continue

        daily.append(_daily_stats(day, _download_key(key), region.bbox, key))

    if missing:
        raise ValueError(
            "IMERG Late Daily data are not available for all required dates: "
            + ", ".join(missing)
        )

    summary = summarize_rainfall_days(daily)
    return {
        "region": region.as_dict(),
        "target_date": end_day.isoformat(),
        "source": {
            "provider": "NASA GES DISC via AWS Open Data",
            "product": PRODUCT,
            "version": PRODUCT_VERSION,
            "run": "Late",
            "spatial_resolution_deg": 0.1,
            "temporal_resolution": "daily",
            "bucket": "gesdisc-cumulus-prod-protected",
            "prefix": AWS_PRODUCT_PREFIX,
        },
        "rainfall": summary,
        "method": {
            "daily_mean": "mean precipitation across IMERG grid-cell centers inside the AOI",
            "accumulation": "sum of AOI daily-mean precipitation for windows ending on target_date",
            "max_mm": "maximum single IMERG grid-cell daily precipitation inside the AOI",
        },
    }
