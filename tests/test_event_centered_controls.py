"""Offline tests for matched event-centered rainfall controls."""

from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
from unittest.mock import call, patch

from geohazard_watch.validation import (
    ValidationEvent,
    assemble_validation_result,
    event_region,
    validate_event,
)


def _event(**overrides: object) -> ValidationEvent:
    values: dict[str, object] = {
        "id": "glc-test",
        "catalog_event_id": 42,
        "event_date": date(2010, 8, 26),
        "longitude": 40.6167,
        "latitude": 41.0524,
        "trigger": "downpour",
        "country": "Turkey",
        "location": "Rize",
        "source_url": "https://example.invalid/glc",
        "catalog_record_url": "https://example.invalid/coolr/42",
        "metadata_review_status": "reverified",
        "location_accuracy": "1km",
        "event_time": "18:00",
        "event_timezone": "Europe/Istanbul",
        "event_time_reference": "catalog_clock_assumed_local",
        "event_time_utc": datetime(2010, 8, 26, 15, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ValidationEvent(**values)  # type: ignore[arg-type]


def _calendar_rainfall(one: float, three: float, seven: float) -> dict[str, object]:
    return {
        "source": {"product": "test", "spatial_resolution_deg": 0.1},
        "rainfall": {
            "accumulation_mean_mm": {
                "1d": one,
                "3d": three,
                "7d": seven,
            }
        },
    }


def _centered_rainfall(
    six: float,
    twelve: float,
    twenty_four: float,
    seventy_two: float,
) -> dict[str, object]:
    return {
        "source": {"product": "test", "spatial_resolution_deg": 0.1},
        "rainfall": {
            "accumulation_before_event_mm": {
                "6h": six,
                "12h": twelve,
                "24h": twenty_four,
                "72h": seventy_two,
            }
        },
    }


class EventCenteredControlTests(unittest.TestCase):
    def test_assemble_reports_matched_event_centered_deltas(self) -> None:
        event = _event()
        region = event_region(event)
        positive = _centered_rainfall(27.835, 51.67, 93.84, 95.445)
        control = _centered_rainfall(1.0, 3.0, 7.5, 20.0)

        result = assemble_validation_result(
            event=event,
            region=region,
            terrain={"terrain": {"relief_m": 685.0}},
            event_rainfall=_calendar_rainfall(165.61, 196.6, 198.93),
            control_rainfall=_calendar_rainfall(1.995, 3.605, 37.035),
            control_date=date(2010, 7, 29),
            half_size_km=5.0,
            control_offset_days=28,
            event_centered_rainfall=positive,
            event_centered_control_rainfall=control,
        )

        self.assertIs(
            result["evidence"]["event_centered_temporal_control_rainfall"],
            control,
        )
        self.assertEqual(
            result["comparison"]["event_centered_rainfall_delta_mm"],
            {
                "6h": 26.835,
                "12h": 48.67,
                "24h": 86.34,
                "72h": 75.445,
            },
        )
        self.assertEqual(
            result["design"]["event_centered_control_time_utc"],
            "2010-07-29T15:00:00Z",
        )

    @patch("geohazard_watch.validation.query_event_centered_rainfall")
    @patch("geohazard_watch.validation.query_rainfall")
    @patch("geohazard_watch.validation.query_terrain")
    @patch("geohazard_watch.validation.load_events")
    def test_validate_uses_same_utc_clock_boundary_for_centered_control(
        self,
        load_events,
        query_terrain,
        query_rainfall,
        query_event_centered_rainfall,
    ) -> None:
        event = _event()
        load_events.return_value = {event.id: event}
        query_terrain.return_value = {"terrain": {"relief_m": 1.0}}
        query_rainfall.side_effect = [
            _calendar_rainfall(10.0, 20.0, 30.0),
            _calendar_rainfall(1.0, 2.0, 3.0),
        ]
        query_event_centered_rainfall.side_effect = [
            _centered_rainfall(8.0, 12.0, 18.0, 25.0),
            _centered_rainfall(1.0, 2.0, 4.0, 9.0),
        ]

        result = validate_event(event.id, manifest_path="unused.json")

        expected_region = event_region(event)
        self.assertEqual(
            query_event_centered_rainfall.call_args_list,
            [
                call(
                    region=expected_region,
                    event_time_utc=datetime(2010, 8, 26, 15, 0, tzinfo=timezone.utc),
                ),
                call(
                    region=expected_region,
                    event_time_utc=datetime(2010, 7, 29, 15, 0, tzinfo=timezone.utc),
                ),
            ],
        )
        self.assertEqual(
            result["comparison"]["event_centered_rainfall_delta_mm"],
            {"6h": 7.0, "12h": 10.0, "24h": 14.0, "72h": 16.0},
        )

    def test_date_only_events_keep_centered_control_unavailable(self) -> None:
        event = _event(
            event_time=None,
            event_timezone=None,
            event_time_reference=None,
            event_time_utc=None,
        )
        region = event_region(event)

        result = assemble_validation_result(
            event=event,
            region=region,
            terrain={"terrain": {"relief_m": 1.0}},
            event_rainfall=_calendar_rainfall(10.0, 20.0, 30.0),
            control_rainfall=_calendar_rainfall(1.0, 2.0, 3.0),
            control_date=date(2010, 7, 29),
            half_size_km=5.0,
            control_offset_days=28,
        )

        self.assertIsNone(result["design"]["event_centered_control_time_utc"])
        self.assertIsNone(
            result["evidence"]["event_centered_temporal_control_rainfall"]
        )
        self.assertIsNone(
            result["comparison"]["event_centered_rainfall_delta_mm"]
        )


if __name__ == "__main__":
    unittest.main()
