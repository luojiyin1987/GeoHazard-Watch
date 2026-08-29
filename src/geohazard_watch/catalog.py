"""Public STAC catalog discovery."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pystac_client
from pystac_client.stac_api_io import StacApiIO

from .aoi import Region


DEFAULT_STAC_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
DEFAULT_STAC_TIMEOUT = (5.0, 30.0)
COLLECTIONS = {
    "sentinel1": "sentinel-1-grd",
    "sentinel2": "sentinel-2-l2a",
}


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format: {value!r}") from exc


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _summarize_collection(
    client: pystac_client.Client,
    collection_id: str,
    bbox: tuple[float, float, float, float],
    datetime_range: str,
) -> dict[str, Any]:
    scene_count = 0
    first_acquired: datetime | None = None
    last_acquired: datetime | None = None

    for item in client.search(
        collections=[collection_id],
        bbox=list(bbox),
        datetime=datetime_range,
    ).items():
        scene_count += 1
        acquired = item.datetime
        if acquired is None:
            continue
        if first_acquired is None or acquired < first_acquired:
            first_acquired = acquired
        if last_acquired is None or acquired > last_acquired:
            last_acquired = acquired

    return {
        "collection": collection_id,
        "scene_count": scene_count,
        "first_acquired": _format_datetime(first_acquired),
        "last_acquired": _format_datetime(last_acquired),
    }


def query_catalog(
    region: Region,
    start: str,
    end: str,
    endpoint: str = DEFAULT_STAC_ENDPOINT,
) -> dict[str, Any]:
    """Query Sentinel metadata intersecting a region and inclusive date range."""

    start_date = _parse_date(start, "start")
    end_date = _parse_date(end, "end")
    if start_date > end_date:
        raise ValueError("start date must be on or before end date")

    datetime_range = (
        f"{start_date.isoformat()}T00:00:00Z/"
        f"{end_date.isoformat()}T23:59:59Z"
    )
    stac_io = StacApiIO(timeout=DEFAULT_STAC_TIMEOUT)
    client = pystac_client.Client.open(endpoint, stac_io=stac_io)

    return {
        "region": region.as_dict(),
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "catalog": {"endpoint": endpoint},
        "datasets": {
            name: _summarize_collection(
                client=client,
                collection_id=collection_id,
                bbox=region.bbox,
                datetime_range=datetime_range,
            )
            for name, collection_id in COLLECTIONS.items()
        },
    }
