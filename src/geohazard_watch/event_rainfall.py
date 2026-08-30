"""Event-centered GPM IMERG rainfall windows for historical validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .aoi import Region
from .rainfall import (
    CHUNK_HOURS,
    HALF_HOUR_HOURS,
    IMAGE_SERVER,
    PIXEL_SIZE_DEG,
    PRODUCT,
    PRODUCT_VERSION,
    _bbox_parts,
    _chunk_mean_rate,
    _service_time_extent,
)

EVENT_WINDOW_HOURS = (6, 12, 24, 72)
HALF_HOUR = timedelta(minutes=30)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_event_time_utc(value: datetime) -> datetime:
    """Require an aware, half-hour-aligned event boundary and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("event_time_utc must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    if normalized.second != 0 or normalized.microsecond != 0 or normalized.minute not in (0, 30):
        raise ValueError("event_time_utc must align to a 30-minute IMERG boundary")
    return normalized


def summarize_event_centered_chunks(
    chunk_depth_mm: list[float],
    chunk_sample_counts: list[int],
) -> dict[str, object]:
    """Summarize trailing 6/12/24/72-hour accumulations from twelve six-hour chunks."""

    expected = max(EVENT_WINDOW_HOURS) // CHUNK_HOURS
    if len(chunk_depth_mm) != expected or len(chunk_sample_counts) != expected:
        raise ValueError(f"event-centered rainfall requires exactly {expected} six-hour chunks")
    if any(count <= 0 for count in chunk_sample_counts):
        raise ValueError("event-centered rainfall chunk sample counts must be positive")

    accumulation: dict[str, float] = {}
    min_sample_count: dict[str, int] = {}
    for window_hours in EVENT_WINDOW_HOURS:
        chunk_count = window_hours // CHUNK_HOURS
        key = f"{window_hours}h"
        accumulation[key] = round(sum(chunk_depth_mm[-chunk_count:]), 3)
        min_sample_count[key] = min(chunk_sample_counts[-chunk_count:])

    return {
        "accumulation_before_event_mm": accumulation,
        "min_sample_count": min_sample_count,
    }


def query_event_centered_rainfall(
    region: Region,
    event_time_utc: datetime,
) -> dict[str, object]:
    """Return AOI rainfall accumulated over windows immediately preceding an event time."""

    event_boundary = _normalize_event_time_utc(event_time_utc)
    requested_start = event_boundary - timedelta(hours=max(EVENT_WINDOW_HOURS))
    last_required_sample = event_boundary - HALF_HOUR
    service_start, service_end = _service_time_extent()

    if service_start > requested_start:
        raise ValueError(
            "Requested event-centered rainfall window begins before IMERG service coverage: "
            f"service starts at {_format_utc(service_start)}"
        )
    if service_end < last_required_sample:
        raise ValueError(
            "Requested event-centered rainfall window extends beyond IMERG service coverage: "
            f"service is available through {_format_utc(service_end)}, "
            f"but the event window requires the {_format_utc(last_required_sample)} sample"
        )

    parts = _bbox_parts(region.bbox)
    chunk_depth_mm: list[float] = []
    chunk_sample_counts: list[int] = []
    chunk_ranges: list[dict[str, str]] = []

    chunk_start = requested_start
    while chunk_start < event_boundary:
        chunk_end_exclusive = chunk_start + timedelta(hours=CHUNK_HOURS)
        query_end = chunk_end_exclusive - timedelta(milliseconds=1)
        mean_rate_sum, count = _chunk_mean_rate(parts, chunk_start, query_end)
        chunk_depth_mm.append(mean_rate_sum * HALF_HOUR_HOURS)
        chunk_sample_counts.append(count)
        chunk_ranges.append(
            {
                "start_utc": _format_utc(chunk_start),
                "end_utc_exclusive": _format_utc(chunk_end_exclusive),
            }
        )
        chunk_start = chunk_end_exclusive

    rainfall = summarize_event_centered_chunks(chunk_depth_mm, chunk_sample_counts)

    return {
        "region": region.as_dict(),
        "event_time_utc": _format_utc(event_boundary),
        "source": {
            "provider": "NASA Earthdata GIS",
            "product": PRODUCT,
            "version": PRODUCT_VERSION,
            "run": "Early",
            "spatial_resolution_deg": PIXEL_SIZE_DEG,
            "temporal_resolution": "half-hourly source, aggregated into trailing event windows",
            "service": IMAGE_SERVER,
            "service_available_through": _format_utc(service_end),
        },
        "rainfall": rainfall,
        "method": {
            "windows": [f"{hours}h" for hours in EVENT_WINDOW_HOURS],
            "interval_semantics": (
                "half-open UTC intervals [event_time-window, event_time); the event boundary "
                "itself is excluded"
            ),
            "aggregation": (
                "reuse twelve six-hour chunks for the 72-hour history; each chunk mosaics "
                "12 half-hourly mm/hour rates with MT_SUM and multiplies by 0.5 hour"
            ),
            "min_sample_count": (
                "smallest valid AOI grid-cell count among the six-hour chunks contributing "
                "to each trailing window"
            ),
            "antimeridian": "crossing AOIs are split and recombined by valid-pixel count",
            "chunks": chunk_ranges,
        },
    }
