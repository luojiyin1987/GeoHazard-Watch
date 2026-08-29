"""Rainfall features from NASA GPM IMERG Early V07 image services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .aoi import Region


IMAGE_SERVER = (
    "https://gis.earthdata.nasa.gov/image/rest/services/"
    "GESDISC/GPM_3IMERGHHE/ImageServer"
)
PRODUCT = "GPM_3IMERGHHE"
PRODUCT_VERSION = "07"
HTTP_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PIXEL_SIZE_DEG = 0.1
HALF_HOUR_HOURS = 0.5
CHUNK_HOURS = 6
WINDOW_DAYS = (1, 3, 7)


@dataclass(frozen=True)
class DailyRainfall:
    day: date
    mean_mm: float
    min_sample_count: int


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD format: {value!r}") from exc


def _request_json(url: str, params: dict[str, str]) -> dict[str, object]:
    request_url = f"{url}?{urlencode(params)}"
    request = Request(request_url, headers={"User-Agent": "GeoHazard-Watch/0.1"})
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_RESPONSE_BYTES:
                raise OSError(f"Rainfall service response is unexpectedly large: {length} bytes")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise OSError(f"Failed to query NASA rainfall service: {exc}") from exc

    if len(payload) > MAX_RESPONSE_BYTES:
        raise OSError("Rainfall service response exceeded the size limit")

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OSError("Rainfall service returned invalid JSON") from exc

    if not isinstance(result, dict):
        raise OSError("Rainfall service returned an unexpected response")
    if "error" in result:
        raise OSError(f"Rainfall service error: {result['error']}")
    return result


def _bbox_parts(
    bbox: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    west, south, east, north = bbox
    if west < east:
        return [bbox]
    return [(west, south, 180.0, north), (-180.0, south, east, north)]


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _service_time_extent() -> tuple[datetime, datetime]:
    payload = _request_json(IMAGE_SERVER, {"f": "json"})
    time_info = payload.get("timeInfo")
    if not isinstance(time_info, dict):
        raise OSError("Rainfall service does not advertise a time extent")
    extent = time_info.get("timeExtent")
    if not isinstance(extent, list) or len(extent) != 2:
        raise OSError("Rainfall service returned an invalid time extent")

    try:
        start_ms, end_ms = (int(extent[0]), int(extent[1]))
    except (TypeError, ValueError) as exc:
        raise OSError("Rainfall service returned a non-numeric time extent") from exc

    return (
        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
        datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc),
    )


def _chunk_ranges(day: date) -> list[tuple[datetime, datetime]]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    ranges: list[tuple[datetime, datetime]] = []
    for hour in range(0, 24, CHUNK_HOURS):
        chunk_start = start + timedelta(hours=hour)
        # The end is one millisecond before the next chunk so adjacent chunks
        # cannot select the same half-hour slice.
        chunk_end = chunk_start + timedelta(hours=CHUNK_HOURS) - timedelta(milliseconds=1)
        ranges.append((chunk_start, chunk_end))
    return ranges


def _geometry(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    return json.dumps(
        {
            "xmin": west,
            "ymin": south,
            "xmax": east,
            "ymax": north,
            "spatialReference": {"wkid": 4326},
        },
        separators=(",", ":"),
    )


def _chunk_mean_rate(
    bbox_parts: Iterable[tuple[float, float, float, float]],
    start: datetime,
    end: datetime,
) -> tuple[float, int]:
    weighted_sum = 0.0
    total_count = 0

    mosaic_rule = json.dumps(
        {
            "mosaicMethod": "esriMosaicNone",
            "mosaicOperation": "MT_SUM",
        },
        separators=(",", ":"),
    )

    for bbox in bbox_parts:
        payload = _request_json(
            f"{IMAGE_SERVER}/computeStatisticsHistograms",
            {
                "geometry": _geometry(bbox),
                "geometryType": "esriGeometryEnvelope",
                "time": f"{_epoch_ms(start)},{_epoch_ms(end)}",
                "mosaicRule": mosaic_rule,
                "pixelSize": f"{PIXEL_SIZE_DEG},{PIXEL_SIZE_DEG}",
                "processAsMultidimensional": "false",
                "f": "json",
            },
        )

        statistics = payload.get("statistics")
        if not isinstance(statistics, list) or not statistics:
            raise ValueError(
                f"No IMERG statistics available for {start.isoformat()} through {end.isoformat()}"
            )
        first = statistics[0]
        if not isinstance(first, dict):
            raise OSError("Rainfall service returned invalid statistics")

        try:
            mean = float(first["mean"])
            count = int(first["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OSError("Rainfall statistics are missing mean/count") from exc
        if count <= 0:
            continue

        weighted_sum += mean * count
        total_count += count

    if total_count == 0:
        raise ValueError("IMERG contains no valid samples inside the AOI")
    return weighted_sum / total_count, total_count


def _daily_rainfall(
    day: date,
    bbox: tuple[float, float, float, float],
) -> DailyRainfall:
    parts = _bbox_parts(bbox)
    total_mm = 0.0
    sample_counts: list[int] = []

    for start, end in _chunk_ranges(day):
        # IMERG half-hourly precipitation is a rate in mm/hour. MT_SUM adds the
        # 12 rates in each six-hour chunk, so multiply by the 0.5-hour duration
        # represented by each slice to obtain precipitation depth in mm.
        mean_rate_sum, count = _chunk_mean_rate(parts, start, end)
        total_mm += mean_rate_sum * HALF_HOUR_HOURS
        sample_counts.append(count)

    return DailyRainfall(
        day=day,
        mean_mm=total_mm,
        min_sample_count=min(sample_counts),
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
            "min_sample_count": item.min_sample_count,
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
    requested_start = end_day - timedelta(days=6)
    service_start, service_end = _service_time_extent()

    if requested_start < service_start.date():
        raise ValueError(
            "Requested 7-day rainfall window begins before the service record: "
            f"{service_start.date().isoformat()}"
        )
    if end_day > service_end.date():
        raise ValueError(
            f"Rainfall service is currently available through {service_end.date().isoformat()}, "
            f"not {end_day.isoformat()}"
        )

    daily = [
        _daily_rainfall(requested_start + timedelta(days=offset), region.bbox)
        for offset in range(7)
    ]
    summary = summarize_rainfall_days(daily)

    return {
        "region": region.as_dict(),
        "target_date": end_day.isoformat(),
        "source": {
            "provider": "NASA Earthdata GIS",
            "product": PRODUCT,
            "version": PRODUCT_VERSION,
            "run": "Early",
            "spatial_resolution_deg": PIXEL_SIZE_DEG,
            "temporal_resolution": "half-hourly source, aggregated to daily",
            "service": IMAGE_SERVER,
            "service_available_through": service_end.isoformat().replace("+00:00", "Z"),
        },
        "rainfall": summary,
        "method": {
            "daily_mean": (
                "unweighted AOI grid-cell mean precipitation depth; half-hourly mm/hour "
                "rates are summed server-side in six-hour chunks and multiplied by 0.5 hour"
            ),
            "accumulation": "sum of AOI daily-mean precipitation for windows ending on target_date",
            "min_sample_count": "smallest valid grid-cell count among the day's four six-hour chunks",
            "antimeridian": "crossing AOIs are split and recombined by valid-pixel count",
        },
    }
