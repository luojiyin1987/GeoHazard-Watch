"""Deterministic tests for the historical validation harness."""

from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import call, patch

from geohazard_watch.validation import (
    ValidationEvent,
    assemble_validation_result,
    event_region,
    validate_event,
)


def _event() -> ValidationEvent:
    return ValidationEvent(
        id="glc-test",
        catalog_event_id=42,
        event_date=date(2010, 8, 26),
        longitude=40.6167,
        latitude=41.0524,
        trigger="downpour",
        country="Turkey",
        location="Rize",
        source_url="https://example.invalid/glc",
    )


def _rainfall(one: float, three: float, seven: float) -> dict[str, object]:
    return {
        "source": {"product": "test"},
        "rainfall": {
            "accumulation_mean_mm": {
                "1d": one,
                "3d": three,
                "7d": seven,
            }
        },
    }


class ValidationTests(unittest.TestCase):
    def test_event_region_builds_ten_kilometre_aoi(self) -> None:
        region = event_region(_event(), half_size_km=5.0)

        west, south, east, north = region.bbox
        self.assertEqual(region.name, "validation-glc-test")
        self.assertAlmostEqual((north - south) * 111.32, 10.0, places=6)
        self.assertAlmostEqual(
            (east - west) * 111.32 * 0.7541,
            10.0,
            places=2,
        )

    def test_assemble_validation_reports_raw_evidence_and_rainfall_delta(self) -> None:
        event = _event()
        region = event_region(event)
        terrain = {"terrain": {"relief_m": 800.0}}
        positive = _rainfall(20.0, 50.0, 80.0)
        control = _rainfall(2.0, 7.0, 15.0)

        result = assemble_validation_result(
            event=event,
            region=region,
            terrain=terrain,
            event_rainfall=positive,
            control_rainfall=control,
            control_date=date(2010, 7, 29),
            half_size_km=5.0,
            control_offset_days=28,
        )

        self.assertIs(result["evidence"]["terrain"], terrain)
        self.assertIs(result["evidence"]["event_rainfall"], positive)
        self.assertIs(result["evidence"]["temporal_control_rainfall"], control)
        self.assertEqual(
            result["comparison"]["rainfall_accumulation_delta_mm"],
            {"1d": 18.0, "3d": 43.0, "7d": 65.0},
        )
        self.assertIsNone(result["interpretation"]["hazard_score"])

    @patch("geohazard_watch.validation.query_rainfall")
    @patch("geohazard_watch.validation.query_terrain")
    @patch("geohazard_watch.validation.load_events")
    def test_validate_event_uses_same_aoi_for_event_and_temporal_control(
        self,
        load_events,
        query_terrain,
        query_rainfall,
    ) -> None:
        event = _event()
        load_events.return_value = {event.id: event}
        query_terrain.return_value = {"terrain": {"relief_m": 1.0}}
        query_rainfall.side_effect = [
            _rainfall(10.0, 20.0, 30.0),
            _rainfall(1.0, 2.0, 3.0),
        ]

        result = validate_event(event.id, manifest_path="unused.json")

        expected_region = event_region(event)
        query_terrain.assert_called_once_with(region=expected_region)
        self.assertEqual(
            query_rainfall.call_args_list,
            [
                call(region=expected_region, target_date="2010-08-26"),
                call(region=expected_region, target_date="2010-07-29"),
            ],
        )
        self.assertEqual(result["design"]["temporal_control_date"], "2010-07-29")

    def test_control_offset_must_separate_seven_day_windows(self) -> None:
        with patch("geohazard_watch.validation.load_events") as load_events:
            with self.assertRaisesRegex(ValueError, "at least 7 days"):
                validate_event("glc-test", control_offset_days=6)
            load_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
