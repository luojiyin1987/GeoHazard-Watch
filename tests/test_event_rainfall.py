from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from geohazard_watch.aoi import Region
from geohazard_watch.event_rainfall import (
    query_event_centered_rainfall,
    summarize_event_centered_chunks,
)
from geohazard_watch.validation import ValidationEvent, event_region, validate_event


class EventRainfallTests(unittest.TestCase):
    def test_trailing_windows_reuse_twelve_six_hour_chunks(self) -> None:
        result = summarize_event_centered_chunks(
            [float(value) for value in range(1, 13)],
            [10 - (index % 3) for index in range(12)],
        )

        self.assertEqual(
            result["accumulation_before_event_mm"],
            {
                "6h": 12.0,
                "12h": 23.0,
                "24h": 42.0,
                "72h": 78.0,
            },
        )
        self.assertEqual(result["min_sample_count"]["6h"], 8)
        self.assertEqual(result["min_sample_count"]["72h"], 8)

    @patch("geohazard_watch.event_rainfall._chunk_mean_rate")
    @patch("geohazard_watch.event_rainfall._service_time_extent")
    def test_query_uses_half_open_window_ending_at_event(
        self, service_time_extent, chunk_mean_rate
    ) -> None:
        service_time_extent.return_value = (
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        chunk_mean_rate.return_value = (2.0, 4)
        region = Region("test", (40.0, 41.0, 40.2, 41.2))
        event_time = datetime(2010, 8, 26, 15, 0, tzinfo=timezone.utc)

        result = query_event_centered_rainfall(region, event_time)

        self.assertEqual(chunk_mean_rate.call_count, 12)
        self.assertEqual(
            result["rainfall"]["accumulation_before_event_mm"],
            {"6h": 1.0, "12h": 2.0, "24h": 4.0, "72h": 12.0},
        )
        self.assertEqual(result["event_time_utc"], "2010-08-26T15:00:00Z")
        last_start = chunk_mean_rate.call_args_list[-1].args[1]
        last_end = chunk_mean_rate.call_args_list[-1].args[2]
        self.assertEqual(last_start, datetime(2010, 8, 26, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(
            last_end,
            datetime(2010, 8, 26, 15, 0, tzinfo=timezone.utc)
            - timedelta(milliseconds=1),
        )

    def test_event_time_must_align_to_half_hour(self) -> None:
        region = Region("test", (40.0, 41.0, 40.2, 41.2))
        with self.assertRaisesRegex(ValueError, "30-minute"):
            query_event_centered_rainfall(
                region,
                datetime(2010, 8, 26, 15, 15, tzinfo=timezone.utc),
            )


class ValidationTemporalMetadataTests(unittest.TestCase):
    @staticmethod
    def _payload(**overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": "glc-test",
            "catalog_event_id": 42,
            "event_date": "2010-08-26",
            "longitude": 40.6167,
            "latitude": 41.0524,
            "trigger": "downpour",
            "country": "Turkey",
            "location": "Rize",
            "source_url": "https://example.invalid/glc",
            "catalog_record_url": "https://example.invalid/coolr/42",
            "metadata_review_status": "reverified",
        }
        payload.update(overrides)
        return payload

    def test_curated_catalog_clock_exposes_utc_boundary(self) -> None:
        event = ValidationEvent.from_dict(
            self._payload(
                event_time="18:00:00",
                event_timezone="Europe/Istanbul",
                event_time_reference="catalog_clock_assumed_local",
                event_time_utc="2010-08-26T15:00:00Z",
            )
        )

        temporal = event.as_dict()["temporal_quality"]

        self.assertEqual(temporal["status"], "curated_timezone_assumption")
        self.assertEqual(temporal["catalog_event_time"], "18:00:00")
        self.assertEqual(temporal["event_time_utc"], "2010-08-26T15:00:00Z")
        self.assertTrue(temporal["event_centered_windows_available"])
        self.assertIn("no timezone field", temporal["warning"])

    def test_date_only_event_skips_event_centered_windows(self) -> None:
        event = ValidationEvent.from_dict(self._payload())

        temporal = event.as_dict()["temporal_quality"]

        self.assertEqual(temporal["status"], "date_only")
        self.assertFalse(temporal["event_centered_windows_available"])

    def test_partial_temporal_metadata_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must provide"):
            ValidationEvent.from_dict(
                self._payload(
                    event_time="18:00:00",
                    event_timezone="Europe/Istanbul",
                )
            )

    @patch("geohazard_watch.validation.query_event_centered_rainfall")
    @patch("geohazard_watch.validation.query_rainfall")
    @patch("geohazard_watch.validation.query_terrain")
    @patch("geohazard_watch.validation.load_events")
    def test_validate_event_adds_centered_windows_when_time_is_curated(
        self,
        load_events,
        query_terrain,
        query_rainfall,
        query_event_centered_rainfall,
    ) -> None:
        event = ValidationEvent.from_dict(
            self._payload(
                event_time="18:00:00",
                event_timezone="Europe/Istanbul",
                event_time_reference="catalog_clock_assumed_local",
                event_time_utc="2010-08-26T15:00:00Z",
            )
        )
        load_events.return_value = {event.id: event}
        query_terrain.return_value = {"terrain": {"relief_m": 1.0}}
        rainfall = {
            "source": {"spatial_resolution_deg": 0.1},
            "rainfall": {
                "accumulation_mean_mm": {"1d": 1.0, "3d": 2.0, "7d": 3.0}
            },
        }
        query_rainfall.side_effect = [rainfall, rainfall]
        centered = {
            "rainfall": {
                "accumulation_before_event_mm": {
                    "6h": 1.0,
                    "12h": 2.0,
                    "24h": 3.0,
                    "72h": 4.0,
                }
            }
        }
        query_event_centered_rainfall.return_value = centered

        result = validate_event(event.id, manifest_path="unused.json")

        expected_region = event_region(event)
        query_event_centered_rainfall.assert_called_once_with(
            region=expected_region,
            event_time_utc=datetime(2010, 8, 26, 15, 0, tzinfo=timezone.utc),
        )
        self.assertIs(result["evidence"]["event_centered_rainfall"], centered)


if __name__ == "__main__":
    unittest.main()
