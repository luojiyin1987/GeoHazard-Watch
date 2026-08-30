"""Historical validation against curated landslide events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any

from .aoi import Region
from .rainfall import query_rainfall
from .terrain import query_terrain


DEFAULT_EVENTS_PATH = Path("validation/events.json")
DEFAULT_AOI_HALF_SIZE_KM = 5.0
DEFAULT_CONTROL_OFFSET_DAYS = 28
KM_PER_DEG_LAT = 111.32


@dataclass(frozen=True)
class ValidationEvent:
    """One curated historical landslide event used as a validation target."""

    id: str
    catalog_event_id: int
    event_date: date
    longitude: float
    latitude: float
    trigger: str
    country: str
    location: str
    source_url: str

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ValidationEvent":
        """Parse and validate one event manifest record."""

        required_strings = ("id", "trigger", "country", "location", "source_url")
        values: dict[str, str] = {}
        for field in required_strings:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"validation event {field!r} must be a non-empty string")
            values[field] = value.strip()

        raw_event_id = payload.get("catalog_event_id")
        if isinstance(raw_event_id, bool) or not isinstance(raw_event_id, int):
            raise ValueError("validation event 'catalog_event_id' must be an integer")

        raw_date = payload.get("event_date")
        if not isinstance(raw_date, str):
            raise ValueError("validation event 'event_date' must use YYYY-MM-DD format")
        try:
            event_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError(
                f"validation event 'event_date' must use YYYY-MM-DD format: {raw_date!r}"
            ) from exc

        longitude = _finite_number(payload.get("longitude"), "longitude")
        latitude = _finite_number(payload.get("latitude"), "latitude")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("validation event longitude must be within [-180, 180]")
        if not -90.0 < latitude < 90.0:
            raise ValueError("validation event latitude must be within (-90, 90)")

        return cls(
            id=values["id"],
            catalog_event_id=raw_event_id,
            event_date=event_date,
            longitude=longitude,
            latitude=latitude,
            trigger=values["trigger"],
            country=values["country"],
            location=values["location"],
            source_url=values["source_url"],
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable event description."""

        return {
            "id": self.id,
            "catalog_event_id": self.catalog_event_id,
            "event_date": self.event_date.isoformat(),
            "longitude": self.longitude,
            "latitude": self.latitude,
            "trigger": self.trigger,
            "country": self.country,
            "location": self.location,
            "source_url": self.source_url,
        }


def _finite_number(value: object, field: str) -> float:
    """Return a finite float while rejecting booleans and non-numeric values."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"validation event {field!r} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"validation event {field!r} must be a finite number")
    return result


def load_events(path: str | Path = DEFAULT_EVENTS_PATH) -> dict[str, ValidationEvent]:
    """Load a curated validation manifest and index events by stable local id."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid validation manifest JSON in {source}: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("validation manifest must contain an 'events' array")

    events: dict[str, ValidationEvent] = {}
    for raw_event in payload["events"]:
        if not isinstance(raw_event, dict):
            raise ValueError("validation manifest events must be JSON objects")
        event = ValidationEvent.from_dict(raw_event)
        if event.id in events:
            raise ValueError(f"duplicate validation event id: {event.id!r}")
        events[event.id] = event

    if not events:
        raise ValueError("validation manifest must contain at least one event")
    return events


def _wrap_longitude(value: float) -> float:
    """Wrap a longitude to [-180, 180], preserving +180 when practical."""

    wrapped = ((value + 180.0) % 360.0) - 180.0
    if wrapped == -180.0 and value > 0:
        return 180.0
    return wrapped


def event_region(
    event: ValidationEvent,
    half_size_km: float = DEFAULT_AOI_HALF_SIZE_KM,
) -> Region:
    """Build an approximately square WGS84 AOI centered on an event point."""

    if not math.isfinite(half_size_km) or half_size_km <= 0:
        raise ValueError("AOI half-size must be a positive finite number")
    if half_size_km > 50.0:
        raise ValueError("AOI half-size must not exceed 50 km for this regional validation")

    lat_delta = half_size_km / KM_PER_DEG_LAT
    south = event.latitude - lat_delta
    north = event.latitude + lat_delta
    if south <= -90.0 or north >= 90.0:
        raise ValueError("validation AOI would cross a geographic pole")

    cos_lat = math.cos(math.radians(event.latitude))
    if cos_lat <= 0:
        raise ValueError("validation AOI longitude spacing is undefined at this latitude")
    lon_delta = half_size_km / (KM_PER_DEG_LAT * cos_lat)
    west = _wrap_longitude(event.longitude - lon_delta)
    east = _wrap_longitude(event.longitude + lon_delta)

    return Region(
        name=f"validation-{event.id}",
        bbox=(west, south, east, north),
    )


def _rainfall_accumulations(result: dict[str, Any]) -> dict[str, float]:
    """Extract numeric 1/3/7-day accumulation values from rainfall evidence."""

    rainfall = result.get("rainfall")
    if not isinstance(rainfall, dict):
        raise ValueError("rainfall evidence is missing the 'rainfall' object")
    accumulation = rainfall.get("accumulation_mean_mm")
    if not isinstance(accumulation, dict):
        raise ValueError("rainfall evidence is missing accumulation_mean_mm")

    values: dict[str, float] = {}
    for window in ("1d", "3d", "7d"):
        value = accumulation.get(window)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"rainfall evidence is missing numeric {window!r} accumulation")
        values[window] = float(value)
    return values


def assemble_validation_result(
    *,
    event: ValidationEvent,
    region: Region,
    terrain: dict[str, Any],
    event_rainfall: dict[str, Any],
    control_rainfall: dict[str, Any],
    control_date: date,
    half_size_km: float,
    control_offset_days: int,
) -> dict[str, Any]:
    """Assemble raw evidence plus a simple event-vs-control rainfall comparison."""

    positive = _rainfall_accumulations(event_rainfall)
    control = _rainfall_accumulations(control_rainfall)
    rainfall_delta = {
        window: round(positive[window] - control[window], 3)
        for window in ("1d", "3d", "7d")
    }

    return {
        "event": event.as_dict(),
        "region": region.as_dict(),
        "design": {
            "aoi_half_size_km": half_size_km,
            "positive_date": event.event_date.isoformat(),
            "temporal_control_date": control_date.isoformat(),
            "control_offset_days": control_offset_days,
            "control_semantics": (
                "same AOI on an earlier date; absence of a cataloged event is not proof "
                "that no landslide occurred"
            ),
        },
        "evidence": {
            "terrain": terrain,
            "event_rainfall": event_rainfall,
            "temporal_control_rainfall": control_rainfall,
        },
        "comparison": {
            "rainfall_accumulation_delta_mm": rainfall_delta,
        },
        "interpretation": {
            "hazard_score": None,
            "statement": (
                "This output compares independently inspectable evidence around a historical "
                "event. It is not a landslide probability or a forecast-skill claim."
            ),
        },
    }


def validate_event(
    event_id: str,
    manifest_path: str | Path = DEFAULT_EVENTS_PATH,
    half_size_km: float = DEFAULT_AOI_HALF_SIZE_KM,
    control_offset_days: int = DEFAULT_CONTROL_OFFSET_DAYS,
) -> dict[str, Any]:
    """Run terrain and rainfall evidence for one event and an earlier temporal control."""

    if control_offset_days < 7:
        raise ValueError("control offset must be at least 7 days")

    events = load_events(manifest_path)
    try:
        event = events[event_id]
    except KeyError as exc:
        available = ", ".join(sorted(events))
        raise ValueError(f"unknown validation event {event_id!r}; available: {available}") from exc

    region = event_region(event, half_size_km=half_size_km)
    control_date = event.event_date - timedelta(days=control_offset_days)

    terrain = query_terrain(region=region)
    event_rainfall = query_rainfall(region=region, target_date=event.event_date.isoformat())
    control_rainfall = query_rainfall(region=region, target_date=control_date.isoformat())

    return assemble_validation_result(
        event=event,
        region=region,
        terrain=terrain,
        event_rainfall=event_rainfall,
        control_rainfall=control_rainfall,
        control_date=control_date,
        half_size_km=half_size_km,
        control_offset_days=control_offset_days,
    )
